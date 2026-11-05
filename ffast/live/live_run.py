"""The server side of the live-run view (ADR 0087).

Opening a run folder registers its frames as a dataset whose fingerprint is
the run ID (``live:<run id>``) and whose frame count grows: the 3D views,
slider and plots work on it as on any dataset. Each new batch of frames is
announced like a live subset whose frames changed (``SUBDATASET_INDICES_CHANGED``),
so every window gets the new count and stale metric results are dropped.

Scores stay apart from geometry: they travel in ``LIVE_RUN_UPDATE``, one
message per change (and every ``RESEND_S`` seconds, for windows that connect
later), holding the whole score series of the run's first analysis.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import numpy as np

from ffast.live import run_folder as rf
from ffast.loaders.dataset import FlatDatasetLoader

logger = logging.getLogger("FFAST")

POLL_S = 1.0
RESEND_S = 10.0
MAX_BYTES_PER_POLL = 64 << 20
LIVE_PREFIX = "live:"


class LiveRunDataset(FlatDatasetLoader):
    """The frames of a live run read so far; grows with ``append``. Geometry only."""

    isVariable = False
    datasetName = "LAMMPS live run"

    def __init__(self, run_dir: Path, run_id: str) -> None:
        super().__init__(str(Path(run_dir) / rf.FRAMES))
        self.run_id = run_id
        self.R_flat = np.zeros((0, 3))
        self.z_flat = None
        self.E = None
        self.F_flat = None
        self.N = 0
        self.molecule_offsets = np.zeros(1, dtype=np.int64)
        self.steps: list[int] = []
        self.times: list[float | None] = []
        self._buf = np.zeros((0, 3))
        self._n_atoms = 0
        self._z = None

    def getFingerprint(self):
        return LIVE_PREFIX + self.run_id

    def initialise(self):
        self.fingerprint = self.getFingerprint()
        self.setName(f"live {self.run_id}")

    def append(self, frames) -> None:
        """Add frames from the dump reader (all with the same atoms)."""
        if not frames:
            return
        if self._z is None:
            from ase.data import atomic_numbers

            self._n_atoms = len(frames[0].elements)
            self._z = np.array([atomic_numbers[e] for e in frames[0].elements], dtype=np.int64)
        need = (self.N + len(frames)) * self._n_atoms
        if need > len(self._buf):
            grown = np.zeros((max(need, 2 * len(self._buf)), 3))
            grown[: len(self.R_flat)] = self.R_flat
            self._buf = grown
        at = self.N * self._n_atoms
        for f in frames:
            self._buf[at: at + self._n_atoms] = f.positions
            at += self._n_atoms
            self.steps.append(f.step)
            self.times.append(f.time)
        self.N += len(frames)
        self.R_flat = self._buf[: self.N * self._n_atoms]
        self.z_flat = np.tile(self._z, self.N)
        self.molecule_offsets = np.arange(self.N + 1, dtype=np.int64) * self._n_atoms


def _series_of(rows: list[dict]) -> dict:
    out = {"frame": [], "step": [], "t2": [], "flag": []}
    for r in rows:
        track = next(iter(r.get("tracks", {}).values()), {})
        out["frame"].append(r.get("frame"))
        out["step"].append(r.get("step"))
        out["t2"].append(track.get("t2"))
        flags = track.get("flags") or {}
        out["flag"].append(bool(any(flags.values())))
    return out


class LiveRun:
    """One opened run folder: follows its dump, scores and state."""

    def __init__(self, env, run_dir: Path, broadcast) -> None:
        run_dir = Path(run_dir).expanduser().resolve()
        if run_dir.name == rf.RUN_JSON:
            run_dir = run_dir.parent
        run = rf.read_json(run_dir / rf.RUN_JSON)
        if not isinstance(run, dict) or run.get("format") != rf.RUN_FORMAT:
            raise ValueError(f"{run_dir} is not a live-run folder (no readable {rf.RUN_JSON})")
        self.env = env
        self.run_dir = run_dir
        self.run_id = str(run["run_id"])
        self.run = run
        self.broadcast = broadcast
        from ffast.live.dump_reader import DumpReader

        self.reader = DumpReader(run_dir / rf.FRAMES)
        self.dataset = LiveRunDataset(run_dir, self.run_id)
        self.dataset.initialise()
        self.registered = False
        self.error: str | None = None
        self.analysis_id: str | None = None
        self.settings: dict = {}
        self.rows: list[dict] = []
        self._tail: rf.ScoresTail | None = None
        self.events: list[dict] = []
        self._last_sent = 0.0
        self.task: asyncio.Task | None = None

    @property
    def fingerprint(self) -> str:
        return self.dataset.fingerprint

    # ── reading (any thread; touches no env state) ─────────────────────────
    def read_files(self) -> dict:
        frames, error = [], None
        try:
            read = 0
            while read < MAX_BYTES_PER_POLL:
                got = self.reader.poll()
                if not got:
                    break
                frames += got
                read += sum(f.length for f in got)
        except Exception as exc:  # replaced or foreign dump: stop following it
            error = f"{type(exc).__name__}: {exc}"
        run = rf.read_json(self.run_dir / rf.RUN_JSON) or self.run
        if self._tail is None:
            adir = self.run_dir / rf.ANALYSIS_DIR
            ids = sorted(p.name for p in adir.iterdir() if p.is_dir()) if adir.is_dir() else []
            if ids:
                self.analysis_id = ids[0]
                self._tail = rf.ScoresTail(adir / ids[0] / rf.SCORES)
        rows = self._tail.poll() if self._tail is not None else []
        if self.analysis_id and not self.settings:
            self.settings = rf.read_json(
                self.run_dir / rf.ANALYSIS_DIR / self.analysis_id / rf.SETTINGS) or {}
        events = [e for e in rf.read_events(self.run_dir)
                  if e.get("kind") in ("refused", "warning", "scorer_failed", "identity")]
        return {"frames": frames, "rows": rows, "run": run, "events": events, "error": error}

    # ── applying (the server's event loop) ─────────────────────────────────
    def apply(self, got: dict) -> bool:
        """Take what ``read_files`` found; return whether anything changed."""
        changed = bool(got["frames"] or got["rows"]) or got["run"] != self.run
        self.run = got["run"]
        self.events = got["events"]
        if got["error"] and not self.error:
            self.error = got["error"]
            changed = True
        if got["frames"]:
            self.dataset.append(got["frames"])
            if not self.registered:
                self.env.datasets.add(self.dataset)
                self.registered = True
            else:
                self.dataset.eventPush("SUBDATASET_INDICES_CHANGED", self.fingerprint)
        self.rows += got["rows"]
        return changed

    def message(self) -> bytes:
        from ffast.protocol import control
        from ffast.protocol.rpc import pack

        progress = (self.run.get("analyses") or {}).get(self.analysis_id or "", {})
        thresholds = next(iter((self.settings.get("thresholds") or {}).values()), {})
        return pack(control.LIVE_RUN_UPDATE, (self.run_id,), {
            "run_id": self.run_id,
            "path": str(self.run_dir),
            "fingerprint": self.fingerprint if self.registered else None,
            "n_frames": self.dataset.N,
            "analysis_id": self.analysis_id,
            "bundle": self.settings.get("bundle"),
            "thresholds": thresholds,
            "series": _series_of(self.rows),
            "progress": progress,
            "producer": self.run.get("producer"),
            "end": self.run.get("end"),
            "events": self.events[-20:],
            "error": self.error,
        })

    def announce(self) -> None:
        self._last_sent = time.monotonic()
        self.broadcast(self.message())

    async def follow(self, poll_s: float = POLL_S) -> None:
        """Read new frames and scores until the dataset is deleted."""
        while True:
            got = await asyncio.to_thread(self.read_files)
            if self.registered and self.env.datasets.get(self.fingerprint) is not self.dataset:
                logger.info("live run %s: dataset closed, no longer followed", self.run_id)
                self.env.live_runs.pop(self.run_id, None)
                return
            if self.apply(got) or time.monotonic() - self._last_sent > RESEND_S:
                self.announce()
            await asyncio.sleep(poll_s)


def open_live_run(env, path: str, broadcast) -> LiveRun:
    """Open (or find already open) the run folder at ``path``, start following it."""
    runs = getattr(env, "live_runs", None)
    if runs is None:
        runs = env.live_runs = {}
    run = LiveRun(env, Path(path), broadcast)
    existing = runs.get(run.run_id)
    if existing is not None and existing.task is not None and not existing.task.done():
        existing.announce()
        return existing
    runs[run.run_id] = run
    run.apply(run.read_files())
    run.announce()
    run.task = asyncio.ensure_future(run.follow())
    return run

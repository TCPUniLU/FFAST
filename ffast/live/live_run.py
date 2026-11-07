"""The server side of the live-run view (ADR 0087).

Opening a run folder registers its frames as a dataset whose fingerprint is
the run ID (``live:<run id>``) and whose frame count grows: the 3D views,
slider and plots work on it as on any dataset. Each new batch of frames is
announced like a live subset whose frames changed (``SUBDATASET_INDICES_CHANGED``),
so every window gets the new count. A metric result is keyed by the dataset's
fingerprint, which stays the same, so the run names its frame count in each
input (``grows``) and makes the result store forget the run when it changes.

Scores stay apart from geometry: they travel in ``LIVE_RUN_UPDATE``, one
message per change (and every ``RESEND_S`` seconds, for windows that connect
later), holding the whole score series of the run's first analysis.

Each open run with frames has a tab of its own in the server's tab list
(``live_tab``), made from the one built-in "Live run" layout (ADR 0089) and
opened with that run ticked in its selector. The tab is made by the server,
never saved, and leaves with the run.
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
    grows = True        # a metric input names the frame count (see InputSource.identity)

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


def tab_name(run_id: str, job_name: str | None = None) -> str:
    """"Live 10:33:35 mel0314" from "20261105-103335-mel0314-17254-dae9";
    "Live aspirin_nvt (10:33:35 mel0314)" for a Slurm job named aspirin_nvt."""
    parts = run_id.split("-")
    if len(parts) >= 3 and len(parts[1]) == 6 and parts[1].isdigit():
        t = parts[1]
        when = f"{t[:2]}:{t[2:4]}:{t[4:]} {parts[2]}"
    else:
        when = run_id
    return f"Live {job_name} ({when})" if job_name else f"Live {when}"


def live_tab(run: "LiveRun", layout):
    """The run's tab: the Live run layout under the run's name. It opens with
    this run ticked in its selector (the server sends the run as ``pinned``)."""
    return layout.model_copy(deep=True, update={"name": run.tab_name, "live_layout": False})


def live_tabs(env, layout) -> list:
    """(tab, run fingerprint) for each open run that has frames, oldest first."""
    return [(live_tab(r, layout), r.fingerprint)
            for r in getattr(env, "live_runs", {}).values() if r.registered]


def _tabs_changed(env) -> None:
    feed = getattr(env, "window_feed", None)
    if feed is not None:
        from ffast.protocol import control

        feed.changed(control.TAB_LAYOUT)


def _v1_exceedance(row: dict, settings: dict) -> tuple[float | None, list | None]:
    """A step-2 row (one track, T² only): T² ÷ its centroid threshold."""
    name, track = next(iter((row.get("tracks") or {}).items()), (None, {}))
    limit = ((settings.get("thresholds") or {}).get(name) or {}).get("centroid")
    t2 = track.get("t2")
    if t2 is None or not limit:
        return None, None
    return t2 / limit, [name, "T2"]


def _series_of(rows: list[dict], settings: dict | None = None) -> dict:
    """Per scored frame: the worst test's score ÷ its threshold, that test, and
    whether the frame is marked. Reads format 2 rows and step-2 (format 1) rows."""
    settings = settings or {}
    out = {"frame": [], "step": [], "exceedance": [], "test": [], "marked": [], "chem": []}
    for r in rows:
        if "exceedance" in r:
            e, test = r.get("exceedance"), r.get("test")
        else:
            e, test = _v1_exceedance(r, settings)
        chem = r.get("chem") or {}
        out["frame"].append(r.get("frame"))
        out["step"].append(r.get("step"))
        out["exceedance"].append(e)
        out["test"].append(test)
        out["marked"].append(bool(r.get("marked", e is not None and e > 1.0)))
        out["chem"].append(bool(chem.get("broken") or chem.get("close")))
    return out


# What the events list shows; the rest (scorer start, drain) is bookkeeping.
SHOWN_EVENTS = ("start", "identity", "planned", "warning", "refused", "scorer_failed", "alarm",
                "stop_policy", "stop_requested", "stop_sent", "stop_observed", "lammps_exit", "end")
EVENTS_KEPT = 200


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
        self.tab_name = tab_name(self.run_id, run.get("slurm_job_name"))
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
        self._listed: tuple = ()        # the values listed as metrics (run_values)
        self.events: list[dict] = []
        self._last_sent = 0.0
        self.task: asyncio.Task | None = None

    @property
    def display_name(self) -> str:
        """The run's name in plots: its tab name without the leading "Live "."""
        return self.tab_name.removeprefix("Live ")

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
        events = [e for e in rf.read_events(self.run_dir) if e.get("kind") in SHOWN_EVENTS]
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
                _tabs_changed(self.env)
            else:
                self.dataset.eventPush("SUBDATASET_INDICES_CHANGED", self.fingerprint)
        self.rows += got["rows"]
        from ffast.metrics import run_values

        if self.registered and run_values.keys_of(self) != list(self._listed):
            self._listed = tuple(run_values.keys_of(self))
            run_values.sync(self.env)
        if got["frames"] or got["rows"]:
            # Results stored before the growth (or a failure for scores that were
            # not there yet) would otherwise be served for the longer run.
            store = getattr(self.env, "results", None)
            if store is not None:
                store.forget_objects(self.fingerprint)
        return changed

    def message(self) -> bytes:
        from ffast.protocol import control
        from ffast.protocol.rpc import pack

        progress = (self.run.get("analyses") or {}).get(self.analysis_id or "", {})
        return pack(control.LIVE_RUN_UPDATE, (self.run_id,), {
            "run_id": self.run_id,
            "path": str(self.run_dir),
            "fingerprint": self.fingerprint if self.registered else None,
            "tab": self.tab_name if self.registered else None,
            "name": self.display_name,
            "steps": list(self.dataset.steps),
            "n_frames": self.dataset.N,
            "analysis_id": self.analysis_id,
            "bundle": self.settings.get("bundle"),
            "role": self.settings.get("role", "centroid"),
            "series": _series_of(self.rows, self.settings),
            "progress": progress,
            "producer": self.run.get("producer"),
            "end": self.run.get("end"),
            "stop": self.run.get("stop"),
            "stop_requested": (self.run_dir / rf.STOP_REQUEST).exists(),
            "stop_on_alarm": self._stop_on_alarm(progress),
            "events": self.events[-EVENTS_KEPT:],
            "error": self.error,
        })

    # ── analysis values (ADR 0090; any thread) ─────────────────────────────
    def analysis_value_problem(self, key) -> str | None:
        """Why this run has no value for ``key`` (see ``parse_run_ref``), or None."""
        if key[0] != "score" or key == ("worst",):
            return None
        _, track, stat = key
        if not self.settings:
            return "it has no scores yet"
        if self.settings.get("scores_format") != 2:
            return "its scores were written before per-test scores were kept"
        if [track, stat] not in [list(t) for t in self.settings.get("tests") or []]:
            return f"its reference has no {track} {'T²' if stat == 'T2' else stat} test"
        return None

    def analysis_value(self, key) -> np.ndarray | None:
        """One number per frame of the run, NaN where no score row has the frame
        (a gap: the viewer never scores a frame itself), or None when the run
        has no such value. ``key``: ``("score", track, stat)``, ``("worst",)`` or
        ``("chem", "broken" | "close")``."""
        if self.analysis_value_problem(key):
            return None
        out = np.full(self.dataset.N, np.nan)
        rows = list(self.rows)          # the event loop appends while a worker reads
        role = self.settings.get("role", "centroid")
        limit = None
        if key[0] == "score" and key != ("worst",):
            _, track, stat = key
            limit = (((self.settings.get("thresholds") or {}).get(track) or {}).get(stat) or {}).get(role)
            if not limit:
                return out
        for r in rows:
            frame = r.get("frame")
            if not isinstance(frame, int) or not 0 <= frame < len(out):
                continue
            if key == ("worst",):
                value = r["exceedance"] if "exceedance" in r else _v1_exceedance(r, self.settings)[0]
            elif key[0] == "chem":
                value = (r.get("chem") or {}).get(key[1])
            else:
                score = ((r.get("tracks") or {}).get(track) or {}).get(stat)
                value = None if score is None else score / limit
            if value is not None:
                out[frame] = value
        return out

    # ── attribution (any thread) ────────────────────────────────────────────
    def _attribution_bundle(self):
        """The run's bundle, loaded once; ``ValueError`` saying why it cannot be used."""
        if getattr(self, "_bundle", None) is not None:
            return self._bundle
        from ffast.live.data_file import bundle_sha256
        from ffast.live.detectana_api import DetectAnaMissing, require_detectana

        path = self.settings.get("bundle")
        if not path:
            raise ValueError("this run has no scores yet, so no bundle to explain them with")
        if not Path(path).is_dir():
            raise ValueError(f"the reference bundle {path} is gone; attribution needs it")
        want = self.settings.get("bundle_sha256")
        if want and bundle_sha256(Path(path)) != want:
            raise ValueError(f"the bundle at {path} changed after this run was scored; "
                             "attribution would explain other scores")
        try:
            detectana = require_detectana()
        except DetectAnaMissing as exc:
            raise ValueError(str(exc)) from exc
        try:
            self._bundle = detectana.load(path)
        except detectana.BundleError as exc:
            raise ValueError(f"DetectAna {detectana.__version__} cannot use the bundle {path}: {exc}") from exc
        return self._bundle

    def attribution(self, frame: int, test: tuple[str, str] | None = None) -> dict:
        """What drove frame ``frame``'s score (ADR 0087): computed now, never stored."""
        from ffast.live.attribution import frame_attribution

        if not 0 <= frame < self.dataset.N:
            raise ValueError(f"frame {frame} is not read yet ({self.dataset.N} frames)")
        bundle = self._attribution_bundle()
        positions = np.array(self.dataset.getCoordinates(frame), dtype=np.float64)
        out = frame_attribution(bundle, positions, self.settings.get("role", "centroid"), test)
        out.update(run_id=self.run_id, frame=frame, step=self.dataset.steps[frame],
                   fingerprint=self.fingerprint)
        return out

    def _stop_on_alarm(self, progress: dict) -> bool:
        """The viewer's last choice (control.json) if any, else the scorer's setting."""
        ctl = rf.read_json(self.run_dir / rf.CONTROL)
        if isinstance(ctl, dict) and "stop_on_alarm" in ctl:
            return bool(ctl["stop_on_alarm"])
        return bool(progress.get("stop_on_alarm"))

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
                _tabs_changed(self.env)
                from ffast.metrics import run_values

                run_values.sync(self.env)
                return
            if self.apply(got) or time.monotonic() - self._last_sent > RESEND_S:
                self.announce()
            await asyncio.sleep(poll_s)


def find_run(env, run_id: str) -> LiveRun:
    """The open run ``run_id``; ``ValueError`` when no window opened it."""
    run = (getattr(env, "live_runs", None) or {}).get(run_id)
    if run is None:
        raise ValueError(f"live run {run_id!r} is not open")
    return run


def stop_run(env, run_id: str) -> bool:
    """"Stop now": ask the run's wrapper to stop LAMMPS. False if a stop was already asked for."""
    run = find_run(env, run_id)
    if (run.run.get("end") or None) is not None:
        raise ValueError(f"live run {run_id} has already ended")
    asked = rf.request_stop(run.run_dir, by="user", reason="Stop now in the FFAST viewer")
    run.announce()
    return asked


def set_stop_on_alarm(env, run_id: str, on: bool) -> None:
    """Turn stop on alarm on or off for a running run (the scorer reads it every poll)."""
    run = find_run(env, run_id)
    rf.set_stop_on_alarm(run.run_dir, bool(on), by="user")
    run.announce()


def open_live_run(env, path: str, broadcast) -> LiveRun:
    """Open (or find already open) the run folder at ``path``, start following it."""
    runs = getattr(env, "live_runs", None)
    if runs is None:
        runs = env.live_runs = {}
    sources = getattr(env, "tab_sources", None)
    if sources is not None and sources.live is None:
        sources.live = lambda layout: live_tabs(env, layout)
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

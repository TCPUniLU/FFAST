"""``ffast-live RUN_DIR``: score a live LAMMPS run's dump as it grows (ADR 0086, 0087).

Started by ``ffast-lammps`` beside LAMMPS. Waits for the include file to say
which bundle to use, follows ``frames.lammpstrj``, checks the first frame
(units, atom order, identity), scores every frame with the bundle and writes
``analysis/<analysis id>/``. When the wrapper writes ``producer_done.json``
(LAMMPS has exited) it reads to the end of the dump and stops.

A scorer that refuses or fails never touches LAMMPS: the dump is the record.
Exit code 0 for done or refused, 1 for a failure.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

import numpy as np

from ffast.live import run_folder as rf

log = logging.getLogger("ffast.live.scorer")

ACCEPTED_UNITS = {"metal": "ps", "real": "fs"}   # unit style → time unit; both Å
HEARTBEAT_S = 10.0
SCORES_FORMAT = 2
# One LAMMPS frame is one geometry, not a set of path-integral beads: the
# centroid thresholds apply. Bead runs (-partition, i-PI) are not watched yet.
ROLE = "centroid"


class Refused(Exception):
    """Scoring this run would be wrong; the reason is user-facing."""


class _Interrupted(Exception):
    pass


class Scorer:
    def __init__(self, run_dir: Path, poll_s: float = 0.5, heartbeat_s: float = HEARTBEAT_S) -> None:
        self.run_dir = Path(run_dir)
        self.poll_s = poll_s
        self.heartbeat_s = heartbeat_s
        self.progress: dict = {"state": "starting", "scored_frames": 0, "scored_through_step": None}
        self.analysis_dir: Path | None = None
        self._last_beat = 0.0

    # ── state ───────────────────────────────────────────────────────────────
    def _producer_done(self) -> bool:
        return (self.run_dir / rf.PRODUCER_DONE).exists()

    def _write_progress(self, **changes) -> None:
        self.progress.update(changes)
        self.progress["heartbeat_utc"] = rf.utc_now()
        self._last_beat = time.monotonic()
        if self.analysis_dir is not None:
            rf.write_json_atomic(self.analysis_dir / rf.PROGRESS, self.progress)

    def _beat(self) -> None:
        if time.monotonic() - self._last_beat >= self.heartbeat_s:
            self._write_progress()

    # ── run ─────────────────────────────────────────────────────────────────
    def run(self) -> int:
        rf.append_event(self.run_dir, "scorer_start")
        try:
            info = self._wait_for_include()
            if info is None:
                return 0
            bundle = self._open_analysis(info)
            self._follow(bundle, int(info.get("every", "0") or 0))
        except Refused as exc:
            log.warning("refused: %s", exc)
            rf.append_event(self.run_dir, "refused", reason=str(exc))
            self._write_progress(state="refused", reason=str(exc))
            return 0
        except _Interrupted:
            rf.append_event(self.run_dir, "warning", text="scorer interrupted by a signal")
            self._write_progress(state="interrupted")
            return 0
        except Exception as exc:  # the dump stays the record; say what broke
            log.exception("scorer failed")
            rf.append_event(self.run_dir, "scorer_failed", reason=f"{type(exc).__name__}: {exc}")
            self._write_progress(state="failed", reason=f"{type(exc).__name__}: {exc}")
            return 1
        return 0

    def _wait_for_include(self) -> dict | None:
        while True:
            done = self._producer_done()
            info = rf.read_include_info(self.run_dir)
            if info is not None:
                if info.get("ffast-include") != "1":
                    raise Refused(f"include file format {info.get('ffast-include')!r}; this FFAST reads 1. "
                                  "Rewrite it with `ffast bundle`.")
                return info
            if done:
                text = ("LAMMPS ended without running the FFAST include file: add "
                        "`include <bundle>.lmp` to the input after the atoms exist")
                log.warning(text)
                rf.append_event(self.run_dir, "warning", text=text)
                return None
            time.sleep(self.poll_s)

    def _open_analysis(self, info: dict):
        from ffast.live.data_file import bundle_sha256
        from ffast.live.detectana_api import DetectAnaMissing, require_detectana

        try:
            detectana = require_detectana()
        except DetectAnaMissing as exc:
            raise Refused(str(exc)) from exc
        bundle_dir = Path(info.get("bundle", ""))
        try:
            bundle = detectana.load(bundle_dir)
        except detectana.BundleError as exc:
            raise Refused(f"cannot use the bundle {bundle_dir}: {exc}") from exc
        settings = {"thresholds": "bundle", "role": ROLE, "every": int(info.get("every", "0") or 0)}
        aid = rf.analysis_id(bundle_dir, settings)
        self.analysis_dir = self.run_dir / rf.ANALYSIS_DIR / aid
        self.analysis_dir.mkdir(parents=True, exist_ok=True)
        self.bundle_sha = bundle_sha256(bundle_dir)
        rf.write_json_atomic(self.analysis_dir / rf.SETTINGS, {
            "analysis_id": aid,
            "bundle": str(bundle_dir),
            "bundle_sha256": self.bundle_sha,
            "detectana_version": bundle.detectana_version,
            "detectana_installed": detectana.__version__,
            "scores_format": SCORES_FORMAT,
            "role": ROLE,
            "tracks": list(bundle.tracks),
            "tests": [list(t) for t in bundle.tests],
            "thresholds": {t: bundle.thresholds(t) for t in bundle.tracks},
            "settings": settings,
        })
        self.progress["analysis_id"] = aid
        self._write_progress(state="ready")
        rf.append_event(self.run_dir, "scorer_ready", analysis_id=aid, bundle=str(bundle_dir))
        log.info("analysis %s with bundle %s", aid, bundle_dir)
        return bundle

    def _follow(self, bundle, every: int) -> None:
        from ffast.live.dump_reader import DumpReader

        reader = DumpReader(self.run_dir / rf.FRAMES)
        writer = rf.ScoresWriter(self.analysis_dir / rf.SCORES)
        self.identity: str | None = None
        while True:
            done = self._producer_done()           # before reading: then the read sees every byte
            if done:
                frames, tail = reader.finish()
            else:
                frames, tail = reader.poll(), 0
            if frames:
                if self.identity is None:
                    self._check_first(bundle, frames[0])
                self._score(bundle, frames, writer)
            if done:
                if tail:
                    rf.append_event(self.run_dir, "warning",
                                    text=f"the dump ends with an incomplete frame ({tail} bytes)")
                self._write_progress(state="done")
                rf.append_event(self.run_dir, "scorer_done", scored_frames=self.progress["scored_frames"])
                return
            if not frames:
                self._beat()
                time.sleep(self.poll_s)

    def _check_first(self, bundle, frame) -> None:
        from ffast.live.data_file import find_record

        if frame.units not in ACCEPTED_UNITS:
            raise Refused(f"LAMMPS units {frame.units!r}; FFAST scores only metal and real (Å)")
        try:
            check = bundle.validate_frame(frame.elements, frame.positions)
        except Exception as exc:
            raise Refused(f"first frame (step {frame.step}) does not match the bundle: {exc}") from exc
        rec = find_record(frame.positions)
        proven = rec is not None and rec.get("bundle_sha256") == self.bundle_sha
        identity = "proven" if proven else "unverified"
        rf.append_event(self.run_dir, "identity", identity=identity, step=frame.step,
                        data_file=(rec or {}).get("data_file"), check=check.summary)
        if not proven and not check.ok:
            raise Refused(
                "atom order unverified and the first frame fails the bond check: either the "
                "atoms are in another order than the bundle's, or the first frame is already "
                f"damaged ({check.summary})"
            )
        self.identity = identity
        self._write_progress(state="scoring", identity=identity, units=frame.units,
                             time_unit=ACCEPTED_UNITS[frame.units], first_step=frame.step)
        log.info("first frame step %s: identity %s (%s)", frame.step, identity, check.summary)

    def _score(self, bundle, frames, writer) -> None:
        scores = bundle.score(np.stack([f.positions for f in frames]))
        exc = bundle.exceedance(scores, role=ROLE)
        rows = []
        for i, f in enumerate(frames):
            tracks = {
                track: {"T2": _num(values.get("T2"), i), "SPE": _num(values.get("SPE"), i)}
                for track, values in scores.items()
            }
            rows.append({"segment": 0, "frame": f.index, "step": f.step, "time": f.time,
                         "identity": self.identity, "tracks": tracks,
                         "exceedance": float(exc.values[i]), "test": list(exc.tests[i]),
                         "marked": bool(exc.values[i] > 1.0)})
        writer.append(rows)
        self._write_progress(scored_frames=self.progress["scored_frames"] + len(frames),
                             scored_through_step=frames[-1].step)


def _num(values, i):
    """One frame's value of a score, or None when the score is inactive."""
    return None if values is None else float(values[i])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ffast-live", description="Score a live LAMMPS run (started by ffast-lammps)")
    p.add_argument("run_dir")
    p.add_argument("--poll", type=float, default=0.5, help="seconds between reads when idle")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)

    def _stop(signum, _frame):
        raise _Interrupted()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    return Scorer(Path(a.run_dir), poll_s=a.poll).run()


if __name__ == "__main__":
    sys.exit(main())

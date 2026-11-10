"""``ffast-live RUN_DIR``: score a live LAMMPS run's dump as it grows (ADR 0086, 0087).

Started by ``ffast-lammps`` beside LAMMPS. Waits for the include file to say
which bundle to use, follows ``frames.lammpstrj``, checks the first frame
(units, atom order, identity), scores every frame with the bundle and writes
``analysis/<analysis id>/``. When the wrapper writes ``producer_done.json``
(LAMMPS has exited) it reads to the end of the dump and stops.

Alarms (``ffast.live.alarms``): a marked frame is only a mark; the window rule
on the marks, with the persistence set as a time, raises the OOD alarm, and
three chemistry-flagged frames in a row the chemistry alarm. Each alarm gets a
permanent report in ``alarms/``. With stop on alarm (include variable, or
``control.json`` from the viewer) the scorer asks the wrapper to stop LAMMPS;
it never stops LAMMPS itself.

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
from ffast.live.analysis_writer import HEARTBEAT_S, ROLE, AnalysisWriter

log = logging.getLogger("ffast.live.scorer")

ACCEPTED_UNITS = {"metal": "ps", "real": "fs"}   # unit style → time unit; both Å


class Refused(Exception):
    """Scoring this run would be wrong; the reason is user-facing."""


class _Interrupted(Exception):
    pass


def _flag_on(text) -> bool:
    return str(text or "0").strip() not in ("", "0", "no", "false")


class Scorer:
    def __init__(self, run_dir: Path, poll_s: float = 0.5, heartbeat_s: float = HEARTBEAT_S) -> None:
        self.run_dir = Path(run_dir)
        self.poll_s = poll_s
        self.heartbeat_s = heartbeat_s
        self.progress: dict = {"state": "starting", "scored_frames": 0, "scored_through_step": None,
                               "alarms": []}
        self.analysis_dir: Path | None = None
        self.writer: AnalysisWriter | None = None

    # ── state ───────────────────────────────────────────────────────────────
    def _producer_done(self) -> bool:
        return (self.run_dir / rf.PRODUCER_DONE).exists()

    def _write_progress(self, **changes) -> None:
        if self.writer is not None:
            self.writer.write_progress(**changes)
        else:
            self.progress.update(changes)       # no analysis folder yet: nothing to write

    def _beat(self) -> None:
        if self.writer is not None:
            self.writer.beat()

    # ── run ─────────────────────────────────────────────────────────────────
    def run(self) -> int:
        rf.append_event(self.run_dir, "scorer_start")
        try:
            info = self._wait_for_include()
            if info is None:
                return 0
            bundle = self._open_analysis(info)
            self._follow(bundle, info)
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
        from ffast.live.include_file import DEFAULT_PERSIST_PS

        try:
            detectana = require_detectana()
        except DetectAnaMissing as exc:
            raise Refused(str(exc)) from exc
        bundle_dir = Path(info.get("bundle", ""))
        try:
            bundle = detectana.load(bundle_dir)
        except detectana.BundleError as exc:
            raise Refused(f"cannot use the bundle {bundle_dir}: {exc}") from exc
        try:
            self.persist_ps = float(info.get("persist_ps") or DEFAULT_PERSIST_PS)
        except ValueError as exc:
            raise Refused(f"persist_ps {info.get('persist_ps')!r} in the include is not a number") from exc
        if not self.persist_ps > 0:
            raise Refused(f"persist_ps {self.persist_ps:g} must be positive")
        self.every = int(info.get("every", "0") or 0)
        self.stop_on_alarm = _flag_on(info.get("stop_on_alarm"))
        settings = {"thresholds": "bundle", "role": ROLE, "every": self.every, "persist_ps": self.persist_ps}
        aid = rf.analysis_id(bundle_dir, settings)
        self.analysis_dir = self.run_dir / rf.ANALYSIS_DIR / aid
        self.bundle_dir = bundle_dir
        self.bundle_sha = bundle_sha256(bundle_dir)
        self.detectana_version = detectana.__version__
        self.writer = AnalysisWriter(
            self.analysis_dir, bundle, bundle_dir=bundle_dir, bundle_sha=self.bundle_sha,
            detectana_installed=detectana.__version__, settings=settings, persist_ps=self.persist_ps,
            event=lambda kind, **fields: rf.append_event(self.run_dir, kind, **fields),
            positions_of=lambda index: self.reader.frame_at(index).positions,
            base_dir=self.run_dir, on_alarm=self._on_alarm, progress=self.progress,
            heartbeat_s=self.heartbeat_s,
            no_time_text="the dump has no increasing time; alarms are off (frames still scored)",
            planned_frames=lambda: self._planned_frames(self.progress.get("planned_end_step")))
        self.writer.write_settings()
        self._write_progress(state="ready", stop_on_alarm=self.stop_on_alarm, persist_ps=self.persist_ps)
        rf.append_event(self.run_dir, "scorer_ready", analysis_id=aid, bundle=str(bundle_dir),
                        stop_on_alarm=self.stop_on_alarm)
        log.info("analysis %s with bundle %s", aid, bundle_dir)
        return bundle

    def _follow(self, bundle, info: dict) -> None:
        from ffast.live.dump_reader import DumpReader
        from ffast.live.lammps_log import LogFollower

        self.reader = DumpReader(self.run_dir / rf.FRAMES)
        run = rf.read_json(self.run_dir / rf.RUN_JSON) or {}
        self.log = LogFollower(rf.log_path(self.run_dir, run.get("command"), run.get("cwd")))
        self.identity: str | None = None
        self.first: dict | None = None        # first frame's step and time
        while True:
            done = self._producer_done()           # before reading: then the read sees every byte
            self._read_control()
            self._read_log(bundle)
            if done:
                frames, tail = self.reader.finish()
            else:
                frames, tail = self.reader.poll(), 0
            if frames:
                if self.identity is None:
                    self._check_first(bundle, frames[0])
                self.writer.score(frames)
            if done:
                self._read_log(bundle)
                if tail:
                    rf.append_event(self.run_dir, "warning",
                                    text=f"the dump ends with an incomplete frame ({tail} bytes)")
                self._write_progress(state="done")
                rf.append_event(self.run_dir, "scorer_done", scored_frames=self.progress["scored_frames"])
                return
            if not frames:
                self._beat()
                time.sleep(self.poll_s)

    # ── inputs besides the dump ─────────────────────────────────────────────
    def _read_control(self) -> None:
        ctl = rf.read_json(self.run_dir / rf.CONTROL)
        if not isinstance(ctl, dict) or "stop_on_alarm" not in ctl:
            return
        on = bool(ctl["stop_on_alarm"])
        if on != self.stop_on_alarm:
            self.stop_on_alarm = on
            rf.append_event(self.run_dir, "stop_policy", stop_on_alarm=on, by=ctl.get("by"))
            self._write_progress(stop_on_alarm=on)

    def _read_log(self, bundle) -> None:
        changed = False
        for kind, value in self.log.poll():
            if kind == "planned":
                changed = True
            elif kind == "first_run" and not value:
                text = self._restart_warning()
                if text:
                    rf.append_event(self.run_dir, "warning", text=text)
            elif kind == "processes":
                self._write_progress(processes=value)
        if changed:
            self._planned_changed()

    def _restart_warning(self) -> str | None:
        """No restart command before the first run: what the input file says.

        A ``write_restart`` after the run still runs after a clean
        (one-process) stop, but not when several processes are terminated.
        """
        from ffast.live.lammps_log import input_path, restart_in_input

        run = rf.read_json(self.run_dir / rf.RUN_JSON) or {}
        where = restart_in_input(input_path(run.get("command"), run.get("cwd")))
        if where == "before":
            return None
        if where == "after":
            return ("the input writes a restart file only after the run: a run stopped cleanly "
                    "(one process) still writes it, a terminated run (several processes) does not")
        if where == "none":
            return "the input writes no restart file; a stopped run cannot be continued"
        return "no restart command before the first run (input file not readable); a stopped run may not be continuable"

    def _planned_changed(self) -> None:
        if self.first is None:
            return                      # computed when the first frame arrives
        plan = self.log.planned
        end = plan.end_step(self.first["step"])
        if end == self.progress.get("planned_end_step") and self.progress.get("planned_known") == (end is not None):
            return
        if end is None and plan.unknown:
            rf.append_event(self.run_dir, "warning",
                            text=f"planned length unknown: cannot read {plan.unknown!r}")
        elif end is not None:
            rf.append_event(self.run_dir, "planned", end_step=end, frames=self._planned_frames(end))
        self._write_progress(planned_end_step=end, planned_known=end is not None)
        self.writer.update_chance()

    def _planned_frames(self, end: int | None) -> int | None:
        if end is None or self.first is None or self.every < 1:
            return None
        return (end - self.first["step"]) // self.every + 1

    # ── frames ──────────────────────────────────────────────────────────────
    def _identity_source(self, frame) -> tuple[str, dict | None]:
        """Which frame proves atom order, and its data-file record (or None).

        A copy that minimizes first writes ``start.lammpstrj`` right after the
        atoms are loaded and starts the dump only before the first ``run``.
        Minimizing moves atoms but never renumbers them, and both files are
        sorted by atom id, so the snapshot proves the order of every frame
        when its ids and elements equal the first frame's.
        """
        from ffast.live.data_file import find_record
        from ffast.live.dump_reader import DumpReader

        path = self.run_dir / rf.START_SNAPSHOT
        if not path.exists():
            return "first_frame", find_record(frame.positions)
        try:
            snaps, _ = DumpReader(path).finish()
        except Exception as exc:   # an unreadable snapshot proves nothing
            log.warning("start snapshot unreadable: %s", exc)
            return "snapshot", None
        if not snaps:
            return "snapshot", None
        snap = snaps[0]
        if not (np.array_equal(snap.ids, frame.ids) and tuple(snap.elements) == tuple(frame.elements)):
            log.warning("start snapshot has other atoms than the first frame")
            return "snapshot", None
        return "snapshot", find_record(snap.positions)

    def _check_first(self, bundle, frame) -> None:
        if frame.units not in ACCEPTED_UNITS:
            raise Refused(f"LAMMPS units {frame.units!r}; FFAST scores only metal and real (Å)")
        try:
            check = bundle.validate_frame(frame.elements, frame.positions)
        except Exception as exc:
            raise Refused(f"first frame (step {frame.step}) does not match the bundle: {exc}") from exc
        source, rec = self._identity_source(frame)
        proven = rec is not None and rec.get("bundle_sha256") == self.bundle_sha
        identity = "proven" if proven else "unverified"
        rf.append_event(self.run_dir, "identity", identity=identity, step=frame.step,
                        data_file=(rec or {}).get("data_file"), check=check.summary, **{"from": source})
        if not proven and not check.ok:
            raise Refused(
                "atom order unverified and the first frame fails the bond check: either the "
                "atoms are in another order than the bundle's, or the first frame is already "
                f"damaged ({check.summary})"
            )
        self.identity = identity
        self.writer.identity = identity
        self.first = {"step": frame.step, "time": frame.time}
        self.time_unit = ACCEPTED_UNITS[frame.units]
        self.writer.time_unit = self.time_unit
        self._write_progress(state="scoring", identity=identity, units=frame.units,
                             time_unit=self.time_unit, first_step=frame.step)
        self._planned_changed()
        log.info("first frame step %s: identity %s (%s)", frame.step, identity, check.summary)

    def _on_alarm(self, alarm) -> None:
        if self.stop_on_alarm:
            what = "anomaly" if alarm.kind == "ood" else "chemistry"
            rf.request_stop(self.run_dir, by="alarm",
                            reason=f"{what} alarm confirmed at step {alarm.confirmation['step']}")


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

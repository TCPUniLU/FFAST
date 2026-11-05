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

log = logging.getLogger("ffast.live.scorer")

ACCEPTED_UNITS = {"metal": "ps", "real": "fs"}   # unit style → time unit; both Å
HEARTBEAT_S = 10.0
SCORES_FORMAT = 2
# One LAMMPS frame is one geometry, not a set of path-integral beads: the
# centroid thresholds apply. Bead runs (-partition, i-PI) are not watched yet.
ROLE = "centroid"
CHANCE_EVERY = 100          # frames between updates of the false-alarm chance
REPORT_TOP_K = 10


class Refused(Exception):
    """Scoring this run would be wrong; the reason is user-facing."""


class _Interrupted(Exception):
    pass


def _moment(rec: dict) -> dict:
    return {"frame": rec["frame"], "step": rec["step"], "time": rec["time"]}


def _flag_on(text) -> bool:
    return str(text or "0").strip() not in ("", "0", "no", "false")


def log_path(run_dir: Path, run: dict | None) -> Path:
    """Where LAMMPS writes its log: the run folder's, unless the command named one."""
    cmd = (run or {}).get("command") or []
    for i, arg in enumerate(cmd[:-1]):
        if arg in ("-log", "-l"):
            p = Path(cmd[i + 1])
            return p if p.is_absolute() else Path((run or {}).get("cwd") or run_dir) / p
    return run_dir / rf.LOG


class Scorer:
    def __init__(self, run_dir: Path, poll_s: float = 0.5, heartbeat_s: float = HEARTBEAT_S) -> None:
        self.run_dir = Path(run_dir)
        self.poll_s = poll_s
        self.heartbeat_s = heartbeat_s
        self.progress: dict = {"state": "starting", "scored_frames": 0, "scored_through_step": None,
                               "alarms": []}
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
        self.analysis_dir.mkdir(parents=True, exist_ok=True)
        self.bundle_dir = bundle_dir
        self.bundle_sha = bundle_sha256(bundle_dir)
        self.detectana_version = detectana.__version__
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
            "bundle_rule": bundle.onset_rule.to_dict(),
            "settings": settings,
        })
        self.progress["analysis_id"] = aid
        self._write_progress(state="ready", stop_on_alarm=self.stop_on_alarm, persist_ps=self.persist_ps)
        rf.append_event(self.run_dir, "scorer_ready", analysis_id=aid, bundle=str(bundle_dir),
                        stop_on_alarm=self.stop_on_alarm)
        log.info("analysis %s with bundle %s", aid, bundle_dir)
        return bundle

    def _follow(self, bundle, info: dict) -> None:
        from ffast.live.dump_reader import DumpReader
        from ffast.live.lammps_log import LogFollower

        self.reader = DumpReader(self.run_dir / rf.FRAMES)
        writer = rf.ScoresWriter(self.analysis_dir / rf.SCORES)
        self.log = LogFollower(log_path(self.run_dir, rf.read_json(self.run_dir / rf.RUN_JSON)))
        self.identity: str | None = None
        self.first: dict | None = None        # first frame's step and time
        self.tracker = None
        self._early: list[dict] = []          # records scored before the rule is known
        self._since_chance = 0
        self.alpha = bundle.false_flag_rate(ROLE)
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
                self._score(bundle, frames, writer)
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
                rf.append_event(self.run_dir, "warning",
                                text="the input writes no restart file; a stopped run cannot be continued")
            elif kind == "processes":
                self._write_progress(processes=value)
        if changed:
            self._planned_changed()

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
        self._update_chance()

    def _planned_frames(self, end: int | None) -> int | None:
        if end is None or self.first is None or self.every < 1:
            return None
        return (end - self.first["step"]) // self.every + 1

    # ── frames ──────────────────────────────────────────────────────────────
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
        self.first = {"step": frame.step, "time": frame.time}
        self.time_unit = ACCEPTED_UNITS[frame.units]
        self._write_progress(state="scoring", identity=identity, units=frame.units,
                             time_unit=self.time_unit, first_step=frame.step)
        self._planned_changed()
        log.info("first frame step %s: identity %s (%s)", frame.step, identity, check.summary)

    def _score(self, bundle, frames, writer) -> None:
        pos = np.stack([f.positions for f in frames])
        scores = bundle.score(pos)
        exc = bundle.exceedance(scores, role=ROLE)
        chem = bundle.check_chemistry(pos)
        rows = []
        for i, f in enumerate(frames):
            tracks = {
                track: {"T2": _num(values.get("T2"), i), "SPE": _num(values.get("SPE"), i)}
                for track, values in scores.items()
            }
            c = chem[i]
            rows.append({"segment": 0, "frame": f.index, "step": f.step, "time": f.time,
                         "identity": self.identity, "tracks": tracks,
                         "exceedance": float(exc.values[i]), "test": list(exc.tests[i]),
                         "marked": bool(exc.values[i] > 1.0),
                         "chem": {"broken": len(c.broken_bonds), "close": len(c.close_contacts),
                                  "planarity": c.ring_planarity_rmsd}})
        writer.append(rows)
        self._write_progress(scored_frames=self.progress["scored_frames"] + len(frames),
                             scored_through_step=frames[-1].step)
        for r in rows:
            self._push(bundle, {**r, "chem": bool(r["chem"]["broken"] or r["chem"]["close"]),
                                "chem_detail": r["chem"]})

    def _push(self, bundle, rec: dict) -> None:
        if self.tracker is None:
            self._early.append(rec)
            if len(self._early) < 2:
                return
            if not self._make_tracker(bundle):
                return
            early, self._early = self._early, []
            for r in early:
                self._feed(bundle, r)
            return
        self._feed(bundle, rec)

    def _make_tracker(self, bundle) -> bool:
        from ffast.live.alarms import AlarmTracker, rule_from_time

        a, b = self._early[0], self._early[1]
        if a["time"] is None or b["time"] is None or b["time"] <= a["time"]:
            rf.append_event(self.run_dir, "warning",
                            text="the dump has no increasing time; alarms are off (frames still scored)")
            self.tracker = False
            self._early = []
            return False
        rule = rule_from_time(bundle.onset_rule, self.persist_ps, b["time"] - a["time"], self.time_unit)
        if rule.warning:
            rf.append_event(self.run_dir, "warning", text=rule.warning)
        self.tracker = AlarmTracker(rule)
        self._write_progress(rule=rule.to_dict())
        self._update_chance()
        return True

    def _feed(self, bundle, rec: dict) -> None:
        if not self.tracker:
            return
        for alarm in self.tracker.push(rec):
            self._raise(bundle, alarm)
        self._since_chance += 1
        if self._since_chance >= CHANCE_EVERY and "ood" not in self.tracker.alarms:
            self._update_chance()

    def _update_chance(self) -> None:
        from ffast.live.alarms import false_alarm_chance

        if not self.tracker:
            return
        self._since_chance = 0
        planned = self._planned_frames(self.progress.get("planned_end_step"))
        chance = false_alarm_chance(self.alpha, self.tracker.rule, planned, self.tracker.series)
        self._write_progress(false_alarm=chance)

    # ── alarms ──────────────────────────────────────────────────────────────
    def _raise(self, bundle, alarm) -> None:
        n = len(self.progress["alarms"]) + 1
        report = self._write_report(bundle, alarm, n)
        entry = {"n": n, "kind": alarm.kind, "onset": _moment(alarm.onset),
                 "confirmation": _moment(alarm.confirmation),
                 "test": alarm.worst["test"] if alarm.worst else None,
                 "report": str(report.relative_to(self.run_dir))}
        self.progress["alarms"].append(entry)
        self._write_progress()
        fields = {("alarm" if k == "kind" else k): v for k, v in entry.items()}
        rf.append_event(self.run_dir, "alarm", **fields)
        log.warning("%s alarm: onset step %s, confirmed step %s", alarm.kind,
                    alarm.onset["step"], alarm.confirmation["step"])
        if self.stop_on_alarm:
            what = "anomaly" if alarm.kind == "ood" else "chemistry"
            rf.request_stop(self.run_dir, by="alarm",
                            reason=f"{what} alarm confirmed at step {alarm.confirmation['step']}")

    def _write_report(self, bundle, alarm, n: int) -> Path:
        from ffast.live.attribution import frame_attribution

        report = {
            "kind": alarm.kind, "segment": 0,
            "onset": _moment(alarm.onset), "confirmation": _moment(alarm.confirmation),
            "rule": self.tracker.rule.to_dict(), "role": ROLE,
            "false_alarm": self.progress.get("false_alarm"),
            "bundle": str(self.bundle_dir), "bundle_sha256": self.bundle_sha,
            "bundle_detectana_version": bundle.detectana_version,
            "detectana_version": self.detectana_version,
            "written_utc": rf.utc_now(),
        }
        if alarm.kind == "ood":
            worst = alarm.worst
            report["worst"] = {**_moment(worst), "exceedance": worst["exceedance"], "test": worst["test"]}
            try:
                frame = self.reader.frame_at(worst["frame"])
                report["attribution"] = frame_attribution(bundle, frame.positions, ROLE,
                                                          tuple(worst["test"]), k=REPORT_TOP_K)
            except Exception as exc:  # the alarm stands without its explanation
                report["attribution_error"] = f"{type(exc).__name__}: {exc}"
        else:
            frame = self.reader.frame_at(alarm.confirmation["frame"])
            [c] = bundle.check_chemistry(frame.positions)
            report["frames"] = [{**_moment(r), **r["chem_detail"]} for r in alarm.frames]
            report["broken_bonds"] = [list(b) for b in c.broken_bonds]
            report["close_contacts"] = [list(b) for b in c.close_contacts]
        d = self.analysis_dir / rf.ALARMS_DIR
        d.mkdir(exist_ok=True)
        path = d / f"{n}-{alarm.kind}-step{alarm.confirmation['step']}.json"
        rf.write_json_atomic(path, report)
        return path


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

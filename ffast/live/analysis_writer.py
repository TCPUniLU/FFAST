"""Score frames with a bundle and write one analysis folder (ADR 0087, 0093).

The part of ``ffast-live`` that does not care where frames come from. A LAMMPS
run feeds it frames from a dump as they arrive; a scored dataset (ADR 0093)
feeds it frames from a loaded file. Both get the same folder::

    settings.json  scores.jsonl  progress.json  alarms/

The writer scores frames with the bundle, appends one row per frame, runs the
alarm rule on the marks and writes a permanent report for each alarm. It never
reads a dump or a log and never stops anything: the caller passes ``event`` to
record what happened, ``positions_of`` to fetch a frame for a report, and
``on_alarm`` to react to an alarm.

Frames are plain objects with ``index``, ``step``, ``time`` and ``positions``.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

import numpy as np

from ffast.live import run_folder as rf

log = logging.getLogger("ffast.live.analysis_writer")

SCORES_FORMAT = 2
# One frame is one geometry, not a set of path-integral beads: the centroid
# thresholds apply. Bead runs (-partition, i-PI) are not watched yet.
ROLE = "centroid"
HEARTBEAT_S = 10.0
CHANCE_EVERY = 100          # frames between updates of the false-alarm chance
REPORT_TOP_K = 10
NO_TIME_TEXT = "the dump has no increasing time; alarms are off (frames still scored)"


def _moment(rec: dict) -> dict:
    return {"frame": rec["frame"], "step": rec["step"], "time": rec["time"]}


def _num(values, i):
    """One frame's value of a score, or None when the score is inactive."""
    return None if values is None else float(values[i])


class AnalysisWriter:
    def __init__(
        self,
        analysis_dir: Path,
        bundle,
        *,
        bundle_dir: Path,
        bundle_sha: str,
        detectana_installed: str,
        settings: dict,
        persist_ps: float,
        event: Callable[..., None],
        positions_of: Callable[[int], np.ndarray],
        base_dir: Path,
        time_unit: str = "ps",
        on_alarm: Callable[[object], None] | None = None,
        planned_frames: Callable[[], int | None] = lambda: None,
        progress: dict | None = None,
        heartbeat_s: float = HEARTBEAT_S,
        no_time_text: str = NO_TIME_TEXT,
    ) -> None:
        self.analysis_dir = Path(analysis_dir)
        self.bundle = bundle
        self.bundle_dir = Path(bundle_dir)
        self.bundle_sha = bundle_sha
        self.detectana_installed = detectana_installed
        self.settings = settings
        self.persist_ps = persist_ps
        self.event = event
        self.positions_of = positions_of
        self.base_dir = Path(base_dir)         # report paths are written relative to it
        self.time_unit = time_unit
        self.on_alarm = on_alarm
        self.planned_frames = planned_frames
        self.heartbeat_s = heartbeat_s
        self.no_time_text = no_time_text
        self.progress: dict = progress if progress is not None else {
            "state": "starting", "scored_frames": 0, "scored_through_step": None, "alarms": []}
        self.identity: str | None = None
        self.tracker = None                    # None: rule not known yet; False: alarms are off
        self._early: list[dict] = []           # records scored before the rule is known
        self._since_chance = 0
        self._last_beat = 0.0
        self.alpha = bundle.false_flag_rate(ROLE)
        self.analysis_id = self.analysis_dir.name
        self.analysis_dir.mkdir(parents=True, exist_ok=True)
        self._scores = rf.ScoresWriter(self.analysis_dir / rf.SCORES)

    # ── files ───────────────────────────────────────────────────────────────
    def write_settings(self) -> None:
        bundle = self.bundle
        rf.write_json_atomic(self.analysis_dir / rf.SETTINGS, {
            "analysis_id": self.analysis_id,
            "bundle": str(self.bundle_dir),
            "bundle_sha256": self.bundle_sha,
            "detectana_version": bundle.detectana_version,
            "detectana_installed": self.detectana_installed,
            "scores_format": SCORES_FORMAT,
            "role": ROLE,
            "tracks": list(bundle.tracks),
            "tests": [list(t) for t in bundle.tests],
            "thresholds": {t: bundle.thresholds(t) for t in bundle.tracks},
            "bundle_rule": bundle.onset_rule.to_dict(),
            "settings": self.settings,
        })
        self.progress["analysis_id"] = self.analysis_id

    def write_progress(self, **changes) -> None:
        self.progress.update(changes)
        self.progress["heartbeat_utc"] = rf.utc_now()
        self._last_beat = time.monotonic()
        rf.write_json_atomic(self.analysis_dir / rf.PROGRESS, self.progress)

    def beat(self) -> None:
        if time.monotonic() - self._last_beat >= self.heartbeat_s:
            self.write_progress()

    # ── frames ──────────────────────────────────────────────────────────────
    def score(self, frames) -> None:
        bundle = self.bundle
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
        self.append_rows(rows)
        self.write_progress(scored_frames=self.progress["scored_frames"] + len(frames),
                            scored_through_step=frames[-1].step)
        self.replay(rows)

    def replay(self, rows: list[dict]) -> None:
        """Run rows through the alarm rule. Rows already on disk (a scoring that
        continues) come back this way, so the rule sees every frame in order;
        an alarm already reported is not reported twice."""
        for r in rows:
            if "refused" in r:
                continue
            self._push({**r, "chem": bool(r["chem"]["broken"] or r["chem"]["close"]),
                        "chem_detail": r["chem"]})

    def append_rows(self, rows: list[dict]) -> None:
        """Write rows as they are; a caller adds its own kinds of row (a refused frame)."""
        self._scores.append(rows)

    def _push(self, rec: dict) -> None:
        if self.tracker is None:
            self._early.append(rec)
            if len(self._early) < 2:
                return
            if not self._make_tracker():
                return
            early, self._early = self._early, []
            for r in early:
                self._feed(r)
            return
        self._feed(rec)

    def _make_tracker(self) -> bool:
        from ffast.live.alarms import AlarmTracker, rule_from_time

        a, b = self._early[0], self._early[1]
        if a["time"] is None or b["time"] is None or b["time"] <= a["time"]:
            self.event("warning", text=self.no_time_text)
            self.tracker = False
            self._early = []
            return False
        rule = rule_from_time(self.bundle.onset_rule, self.persist_ps, b["time"] - a["time"], self.time_unit)
        if rule.warning:
            self.event("warning", text=rule.warning)
        self.tracker = AlarmTracker(rule)
        self.write_progress(rule=rule.to_dict())
        self.update_chance()
        return True

    def _feed(self, rec: dict) -> None:
        if not self.tracker:
            return
        for alarm in self.tracker.push(rec):
            if alarm.kind == "ood":
                self.update_chance()   # the last update: the chance freezes at the first alarm
            self._raise(alarm)
        self._since_chance += 1
        if self._since_chance >= CHANCE_EVERY and "ood" not in self.tracker.alarms:
            self.update_chance()

    def update_chance(self) -> None:
        from ffast.live.alarms import false_alarm_chance

        if not self.tracker:
            return
        self._since_chance = 0
        chance = false_alarm_chance(self.alpha, self.tracker.rule, self.planned_frames(), self.tracker.series)
        self.write_progress(false_alarm=chance)

    # ── alarms ──────────────────────────────────────────────────────────────
    def _raise(self, alarm) -> None:
        if any(e["kind"] == alarm.kind and e["confirmation"]["frame"] == alarm.confirmation["frame"]
               for e in self.progress["alarms"]):
            return
        n = len(self.progress["alarms"]) + 1
        report = self._write_report(alarm, n)
        entry = {"n": n, "kind": alarm.kind, "onset": _moment(alarm.onset),
                 "confirmation": _moment(alarm.confirmation),
                 "test": alarm.worst["test"] if alarm.worst else None,
                 "report": str(report.relative_to(self.base_dir))}
        self.progress["alarms"].append(entry)
        self.write_progress()
        fields = {("alarm" if k == "kind" else k): v for k, v in entry.items()}
        self.event("alarm", **fields)
        log.warning("%s alarm: onset step %s, confirmed step %s", alarm.kind,
                    alarm.onset["step"], alarm.confirmation["step"])
        if self.on_alarm is not None:
            self.on_alarm(alarm)

    def _write_report(self, alarm, n: int) -> Path:
        from ffast.live.attribution import frame_attribution

        bundle = self.bundle
        report = {
            "kind": alarm.kind, "segment": 0,
            "onset": _moment(alarm.onset), "confirmation": _moment(alarm.confirmation),
            "rule": self.tracker.rule.to_dict(), "role": ROLE,
            "false_alarm": self.progress.get("false_alarm"),
            "bundle": str(self.bundle_dir), "bundle_sha256": self.bundle_sha,
            "bundle_detectana_version": bundle.detectana_version,
            "detectana_version": self.detectana_installed,
            "written_utc": rf.utc_now(),
        }
        if alarm.kind == "ood":
            worst = alarm.worst
            report["worst"] = {**_moment(worst), "exceedance": worst["exceedance"], "test": worst["test"]}
            try:
                report["attribution"] = frame_attribution(bundle, self.positions_of(worst["frame"]), ROLE,
                                                          tuple(worst["test"]), k=REPORT_TOP_K)
            except Exception as exc:  # the alarm stands without its explanation
                report["attribution_error"] = f"{type(exc).__name__}: {exc}"
        else:
            [c] = bundle.check_chemistry(self.positions_of(alarm.confirmation["frame"]))
            report["frames"] = [{**_moment(r), **r["chem_detail"]} for r in alarm.frames]
            report["broken_bonds"] = [list(b) for b in c.broken_bonds]
            report["close_contacts"] = [list(b) for b in c.close_contacts]
        d = self.analysis_dir / rf.ALARMS_DIR
        d.mkdir(exist_ok=True)
        path = d / f"{n}-{alarm.kind}-step{alarm.confirmation['step']}.json"
        rf.write_json_atomic(path, report)
        return path

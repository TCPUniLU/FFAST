"""When a live run raises an alarm (ADR 0086, plan decisions 15–16).

Pure logic, no files: the scorer feeds it one scored frame at a time.

- A frame is **marked** when its worst test exceeds its threshold. A mark alone
  is never an alarm.
- The **OOD alarm** is DetectAna's window rule on the marks, with the window
  set as a persistence time and converted to saved frames here.
- The **chemistry alarm** fires after ``CHEM_STREAK`` flagged frames in a row.
- Each alarm latches: the first one of its kind ends detection of that kind for
  the run segment. It carries the three moments' first two: the **estimated
  onset** (where the bad window or streak starts) and the **confirmation**
  (the frame that completed it). The third, stop observed, comes from LAMMPS.

The false-alarm chance is DetectAna's bound over the planned run length,
always labelled "estimated".
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

CHEM_STREAK = 3
MIN_WINDOW_FRAMES = 10
RHO_MIN_FRAMES = 200            # fewer scored frames: assume uncorrelated frames
PS_PER = {"ps": 1.0, "fs": 1e-3}


@dataclass(frozen=True)
class EffectiveRule:
    """The window rule in saved frames, and the time it was made from."""

    window_frames: int
    step_frames: int
    fraction_threshold: float
    persist_ps: float
    frame_ps: float
    warning: str | None = None

    def to_dict(self) -> dict:
        return {"window_frames": self.window_frames, "step_frames": self.step_frames,
                "fraction_threshold": self.fraction_threshold, "persist_ps": self.persist_ps,
                "frame_ps": self.frame_ps, "warning": self.warning}


def rule_from_time(bundle_rule, persist_ps: float, frame_dt: float, time_unit: str) -> EffectiveRule:
    """Turn a persistence time into a window of saved frames.

    ``bundle_rule`` is the bundle's ``WindowRule`` (fitted in frames): its
    fraction is kept, and its step keeps the same share of the window.
    ``frame_dt`` is the time between two saved frames, in ``time_unit``
    (``ps`` for metal, ``fs`` for real units).
    """
    if time_unit not in PS_PER:
        raise ValueError(f"time unit {time_unit!r}; expected ps or fs")
    frame_ps = float(frame_dt) * PS_PER[time_unit]
    if not frame_ps > 0:
        raise ValueError(f"time between saved frames must be positive, got {frame_dt} {time_unit}")
    if not persist_ps > 0:
        raise ValueError(f"persistence must be positive, got {persist_ps} ps")
    window = max(1, round(persist_ps / frame_ps))
    share = bundle_rule.step_frames / bundle_rule.window_frames
    step = max(1, round(window * share))
    warning = None
    if window < MIN_WINDOW_FRAMES:
        warning = (f"persistence {persist_ps:g} ps is {window} saved frames (under "
                   f"{MIN_WINDOW_FRAMES}); save frames more often or persist longer")
    return EffectiveRule(window, step, float(bundle_rule.fraction_threshold), float(persist_ps),
                         frame_ps, warning)


@dataclass(frozen=True)
class Alarm:
    """One latched alarm. ``onset`` and ``confirmation`` are frame records as
    fed to ``AlarmTracker.push``; ``worst`` is the most exceeded frame of the
    window (OOD) or None (chemistry)."""

    kind: str                       # "ood" or "chem"
    onset: dict
    confirmation: dict
    worst: dict | None = None
    frames: list = field(default_factory=list)


class AlarmTracker:
    """Feed scored frames in order; returns the alarms each frame completes.

    A frame record is a dict with at least ``frame``, ``step``, ``time``,
    ``exceedance`` (worst test ÷ its threshold), ``test``, ``marked`` and
    ``chem`` (true when the chemistry check flags it).
    """

    def __init__(self, rule: EffectiveRule, chem_streak: int = CHEM_STREAK) -> None:
        from detectana.onset import WindowRule

        self.rule = rule
        self._stream = WindowRule(rule.window_frames, rule.step_frames, rule.fraction_threshold).stream()
        self._window: deque = deque(maxlen=rule.window_frames)
        self.chem_streak = chem_streak
        self._streak: list = []
        self.alarms: dict[str, Alarm] = {}
        self.series: list[float] = []      # exceedance before the OOD alarm, for ρ

    def push(self, rec: dict) -> list[Alarm]:
        fired = []
        if "ood" not in self.alarms:
            self._window.append(rec)
            self.series.append(float(rec["exceedance"]))
            if self._stream.push(1.0 if rec["marked"] else 0.0) is not None:
                window = list(self._window)
                worst = max(window, key=lambda r: r["exceedance"])
                fired.append(self._latch(Alarm("ood", window[0], rec, worst)))
        if "chem" not in self.alarms:
            self._streak = self._streak + [rec] if rec.get("chem") else []
            if len(self._streak) >= self.chem_streak:
                fired.append(self._latch(Alarm("chem", self._streak[0], rec, None, list(self._streak))))
        return fired

    def _latch(self, alarm: Alarm) -> Alarm:
        self.alarms[alarm.kind] = alarm
        return alarm


def false_alarm_chance(alpha: float, rule: EffectiveRule, planned_frames: int | None,
                       series: list[float]) -> dict:
    """DetectAna's bound on the window rule firing in a clean run, over the
    planned length (or one window when it is unknown). Always an estimate.

    ρ, the lag-1 correlation of the score series, comes from the run's own
    scores. With fewer than ``RHO_MIN_FRAMES`` the frames are taken as
    uncorrelated; that gives the smallest chance, so the true one is at least
    this (``at_least``).
    """
    from detectana.onset import estimate_lag1_autocorrelation, n_windows, window_false_alarm_probability

    enough = len(series) >= RHO_MIN_FRAMES
    import numpy as np

    rho = float(estimate_lag1_autocorrelation(np.asarray(series, dtype=float))) if enough else 0.0
    if planned_frames:
        windows = max(1, n_windows(int(planned_frames), rule.window_frames, rule.step_frames))
    else:
        windows = 1
    p = window_false_alarm_probability(alpha, rule.window_frames, rule.fraction_threshold,
                                       windows, rho)
    return {"probability": float(p), "rho": rho, "rho_frames": len(series), "at_least": not enough,
            "planned_frames": int(planned_frames) if planned_frames else None,
            "over": "run" if planned_frames else "window", "alpha": float(alpha)}

"""Alarm rules of a live run: marks, window, chemistry streak, false-alarm chance."""

from __future__ import annotations

import numpy as np
import pytest

from ffast.live.alarms import AlarmTracker, EffectiveRule, false_alarm_chance, rule_from_time


class _Rule:
    window_frames, step_frames, fraction_threshold = 500, 50, 0.2


def _rec(k, marked=False, chem=False, e=None):
    return {"frame": k, "step": 100 * k, "time": 0.05 * k,
            "exceedance": (1.5 if marked else 0.5) if e is None else e,
            "test": ["bonds", "T2"], "marked": marked, "chem": chem}


def test_the_persistence_time_becomes_a_window_of_saved_frames():
    r = rule_from_time(_Rule, 5.0, 0.05, "ps")          # metal: 0.05 ps per frame
    assert (r.window_frames, r.step_frames, r.fraction_threshold) == (100, 10, 0.2)
    assert r.warning is None
    r = rule_from_time(_Rule, 5.0, 50.0, "fs")          # real units: the same in fs
    assert (r.window_frames, r.step_frames) == (100, 10)
    r = rule_from_time(_Rule, 0.2, 0.05, "ps")
    assert r.window_frames == 4 and r.step_frames == 1 and "under 10" in r.warning
    with pytest.raises(ValueError):
        rule_from_time(_Rule, 5.0, 0.0, "ps")
    with pytest.raises(ValueError):
        rule_from_time(_Rule, 5.0, 0.05, "lj")


def test_single_marks_never_alarm():
    t = AlarmTracker(EffectiveRule(10, 1, 0.3, 1.0, 0.1))
    fired = [a for k in range(100) for a in t.push(_rec(k, marked=(k % 10 == 0)))]
    assert fired == []


def test_the_ood_alarm_fires_on_detectanas_window_and_latches():
    from detectana.onset import _windowed_onset

    rng = np.random.default_rng(3)
    marks = (rng.random(400) < 0.05).astype(float)
    marks[250:290] = 1.0
    rule = EffectiveRule(20, 5, 0.5, 1.0, 0.05)
    t = AlarmTracker(rule)
    fired = []
    for k, m in enumerate(marks):
        fired += t.push(_rec(k, marked=bool(m), e=1.0 + k / 1000 if m else 0.5))
    want = _windowed_onset(marks, 20, 5, 0.5)
    [a] = fired
    # The estimated onset is the window's first MARKED frame, not its first frame
    # (stop proof 2026-11-06: the window start lay 70 calm frames before the heating).
    assert a.kind == "ood" and a.onset["frame"] == want + int(np.argmax(marks[want:want + 20]))
    assert a.confirmation["frame"] == want + 19
    assert a.worst["frame"] == max(range(want, want + 20), key=lambda k: (marks[k], k))
    assert t.alarms["ood"] is a


def test_chemistry_alarms_after_three_flagged_frames_in_a_row():
    t = AlarmTracker(EffectiveRule(10, 1, 0.9, 1.0, 0.1))
    flags = [1, 1, 0, 1, 1, 0, 1, 1, 1, 1, 1]
    fired = [a for k, f in enumerate(flags) for a in t.push(_rec(k, chem=bool(f)))]
    [a] = fired
    assert a.kind == "chem" and a.onset["frame"] == 6 and a.confirmation["frame"] == 8
    assert [r["frame"] for r in a.frames] == [6, 7, 8]


def test_the_false_alarm_chance_is_detectanas_bound_and_says_how_it_was_made():
    from detectana.onset import n_windows, window_false_alarm_probability

    rule = EffectiveRule(100, 10, 0.2, 5.0, 0.05)
    few = false_alarm_chance(0.01, rule, 10_000, [0.5] * 50)
    assert few["at_least"] and few["rho"] == 0.0 and few["over"] == "run"
    assert few["probability"] == window_false_alarm_probability(
        0.01, 100, 0.2, n_windows(10_000, 100, 10), 0.0)
    rng = np.random.default_rng(0)
    x = np.zeros(500)
    for i in range(1, 500):
        x[i] = 0.6 * x[i - 1] + rng.normal()
    many = false_alarm_chance(0.01, rule, 10_000, list(x))
    assert not many["at_least"] and 0.4 < many["rho"] < 0.8
    assert many["probability"] >= few["probability"]   # correlation leaves fewer real chances
    unknown = false_alarm_chance(0.01, rule, None, [0.5] * 50)
    assert unknown["over"] == "window" and unknown["planned_frames"] is None

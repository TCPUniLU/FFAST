"""The "Live runs" list read from the wrapper's pointer files."""

from __future__ import annotations

import datetime as dt

import pytest

from ffast.live import run_folder as rf
from ffast.live.runs_list import forget_run, list_runs

NOW = dt.datetime(2026, 11, 5, 12, 0, 0, tzinfo=dt.timezone.utc)


def _run(tmp_path, run_id, started, *, end=None, beat_s_ago=3, alarms=(), state="scoring"):
    d = tmp_path / "runs" / run_id
    a = d / rf.ANALYSIS_DIR / "0123456789abcdef"
    a.mkdir(parents=True)
    rf.write_json_atomic(a / rf.SETTINGS, {"bundle": "/b/aspirin_mace.bundle"})
    beat = (NOW - dt.timedelta(seconds=beat_s_ago)).isoformat()
    rf.write_json_atomic(d / rf.RUN_JSON, {
        "format": rf.RUN_FORMAT, "run_id": run_id, "end": end,
        "producer": {"last_step": 412300},
        "analyses": {"0123456789abcdef": {
            "analysis_id": "0123456789abcdef", "state": state, "heartbeat_utc": beat,
            "scored_through_step": 411900, "first_step": 0, "planned_end_step": 1_000_000,
            "alarms": list(alarms)}}})
    pointers = tmp_path / "pointers"
    pointers.mkdir(exist_ok=True)
    rf.write_json_atomic(pointers / f"{run_id}.json",
                         {"run_id": run_id, "run_dir": str(d), "started_utc": started})
    return d


def test_rows_are_newest_first_with_progress_heartbeat_and_alarms(tmp_path):
    _run(tmp_path, "20261105-091202-mel0207-1-aaaa", "2026-11-05T09:12:02+00:00", beat_s_ago=240)
    _run(tmp_path, "20261105-103335-mel0314-2-bbbb", "2026-11-05T10:33:35+00:00",
         alarms=[{"kind": "ood", "confirmation": {"step": 386000}}])
    _run(tmp_path, "20261104-174012-mel0110-3-cccc", "2026-11-04T17:40:12+00:00",
         end={"state": "finished"}, beat_s_ago=60000, state="done")
    rows = list_runs(tmp_path / "pointers", now=NOW)
    assert [r["name"] for r in rows] == ["Live 10:33:35 mel0314", "Live 09:12:02 mel0207",
                                         "Live 17:40:12 mel0110"]
    new, stalled, ended = rows
    assert new["exists"] and new["bundle"] == "aspirin_mace" and new["state"] == "running"
    assert (new["last_step"], new["scored_through_step"], new["planned_end_step"]) == (412300, 411900, 1_000_000)
    assert new["alarms"] == [{"kind": "ood", "step": 386000}] and not new["stalled"]
    assert stalled["stalled"] and stalled["heartbeat_age_s"] == pytest.approx(240)
    assert ended["state"] == "finished" and not ended["stalled"]


def test_a_vanished_folder_is_listed_and_can_be_forgotten(tmp_path):
    d = _run(tmp_path, "20261105-103335-mel0314-2-bbbb", "2026-11-05T10:33:35+00:00")
    (d / rf.RUN_JSON).unlink()
    [row] = list_runs(tmp_path / "pointers", now=NOW)
    assert row["exists"] is False and row["run_dir"] == str(d)
    assert forget_run(tmp_path / "pointers", row["run_id"]) is True
    assert list_runs(tmp_path / "pointers", now=NOW) == []
    assert d.exists()                                  # the folder itself is never touched
    with pytest.raises(ValueError):
        forget_run(tmp_path / "pointers", "../x")


def test_no_pointer_folder_means_no_runs(tmp_path):
    assert list_runs(tmp_path / "none") == []


def test_the_list_is_cut_to_the_newest(tmp_path):
    for k in range(5):
        _run(tmp_path, f"2026110{k}-000000-n-{k}-0000", f"2026-11-0{k + 1}T00:00:00+00:00")
    assert [r["run_id"][:8] for r in list_runs(tmp_path / "pointers", limit=2, now=NOW)] == [
        "20261104", "20261103"]

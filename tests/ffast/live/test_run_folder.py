"""Run folder format for live LAMMPS runs (ADR 0087)."""

import json
import os

import pytest

from ffast.live import run_folder as rf


def test_claiming_twice_gives_two_fresh_folders(tmp_path):
    a_id, a = rf.claim_run_folder(tmp_path / "runs")
    b_id, b = rf.claim_run_folder(tmp_path / "runs")
    assert a != b and a_id != b_id
    assert a.is_dir() and b.is_dir() and a.name == a_id


def test_a_claimed_folder_is_never_reused(tmp_path, monkeypatch):
    ids = iter(["same", "same", "other"])
    monkeypatch.setattr(rf, "new_run_id", lambda: next(ids))
    rf.claim_run_folder(tmp_path)
    run_id, path = rf.claim_run_folder(tmp_path)
    assert run_id == "other"


def test_run_base_uses_the_environment_variable_then_the_working_folder(tmp_path):
    assert rf.run_base({"FFAST_RUN_DIR": str(tmp_path / "x")}, str(tmp_path)) == tmp_path / "x"
    assert rf.run_base({}, str(tmp_path)) == tmp_path / "ffast-runs"


def test_run_base_with_a_space_is_refused_naming_the_variable(tmp_path):
    with pytest.raises(ValueError, match="FFAST_RUN_DIR"):
        rf.run_base({}, str(tmp_path / "with space"))


def test_json_is_replaced_atomically(tmp_path):
    p = tmp_path / "run.json"
    rf.write_json_atomic(p, {"a": 1})
    rf.write_json_atomic(p, {"a": 2})
    assert json.loads(p.read_text()) == {"a": 2}
    assert os.listdir(tmp_path) == ["run.json"]


def test_read_json_of_a_missing_or_broken_file_is_none(tmp_path):
    assert rf.read_json(tmp_path / "nope.json") is None
    (tmp_path / "bad.json").write_text("{")
    assert rf.read_json(tmp_path / "bad.json") is None


def test_events_append_and_a_torn_last_line_is_ignored(tmp_path):
    rf.append_event(tmp_path, "start", run_id="r")
    rf.append_event(tmp_path, "warning", text="x")
    with open(tmp_path / rf.EVENTS, "a") as fh:
        fh.write('{"kind": "en')
    events = rf.read_events(tmp_path)
    assert [e["kind"] for e in events] == ["start", "warning"]
    assert events[0]["run_id"] == "r" and "t" in events[0]


def test_scores_reader_ignores_a_torn_line_and_keeps_the_first_row_per_key(tmp_path):
    path = tmp_path / rf.SCORES
    w = rf.ScoresWriter(path)
    w.append([{"segment": 0, "frame": 0, "v": 1}, {"segment": 0, "frame": 1, "v": 2}])
    w.append([{"segment": 0, "frame": 1, "v": 99}])          # replayed row
    with open(path, "a") as fh:
        fh.write('{"segment": 0, "frame": 2')                  # half-written
    rows = rf.read_scores(path)
    assert [(r["frame"], r["v"]) for r in rows] == [(0, 1), (1, 2)]


def test_scores_tail_returns_only_new_complete_rows(tmp_path):
    path = tmp_path / rf.SCORES
    tail = rf.ScoresTail(path)
    assert tail.poll() == []
    w = rf.ScoresWriter(path)
    w.append([{"segment": 0, "frame": 0}])
    with open(path, "a") as fh:
        fh.write('{"segment": 0, "fr')
    assert [r["frame"] for r in tail.poll()] == [0]
    with open(path, "a") as fh:
        fh.write('ame": 1}\n')
    w.append([{"segment": 0, "frame": 1}])                   # duplicate key
    assert [r["frame"] for r in tail.poll()] == [1]


def _bundle(tmp_path, manifest=b"{}", state=b"S"):
    d = tmp_path / "x.bundle"
    d.mkdir(exist_ok=True)
    (d / "manifest.json").write_bytes(manifest)
    (d / "state.npz").write_bytes(state)
    return d


def test_analysis_id_covers_the_bundle_bytes_and_the_settings(tmp_path):
    b = _bundle(tmp_path)
    one = rf.analysis_id(b, {"every": 100})
    assert one == rf.analysis_id(b, {"every": 100})
    assert one != rf.analysis_id(b, {"every": 200})
    _bundle(tmp_path, state=b"T")
    assert one != rf.analysis_id(b, {"every": 100})
    assert len(one) == 16


def test_include_info_is_read_only_when_complete(tmp_path):
    p = tmp_path / rf.INCLUDE_INFO
    assert rf.read_include_info(tmp_path) is None
    p.write_text("ffast-include 1\nbundle /a/b.bundle\n")
    assert rf.read_include_info(tmp_path) is None
    p.write_text("ffast-include 1\nbundle /a/b.bundle\nevery 100\nend\n")
    assert rf.read_include_info(tmp_path) == {
        "ffast-include": "1", "bundle": "/a/b.bundle", "every": "100"}

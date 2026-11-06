"""`ffast-live` on fake run folders (ADR 0086, 0087)."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from ffast.live import run_folder as rf
from ffast.live.include_file import INCLUDE_FORMAT_VERSION
from ffast.live.scorer import Scorer
from tests.ffast.live.bundle_helpers import initial_atoms, make_bundle
from tests.ffast.live.dump_helpers import ASPIRIN_ELEMENTS, dump_text, frame_text


@pytest.fixture(scope="module")
def bundle_dir(tmp_path_factory):
    return make_bundle(tmp_path_factory.mktemp("b"))[0]


@pytest.fixture
def records(tmp_path, monkeypatch):
    monkeypatch.setenv("FFAST_LAMMPS_DATA_RECORDS", str(tmp_path / "records"))


def _frames(n, seed=0, first_exact=True):
    base = initial_atoms().get_positions()
    rng = np.random.default_rng(seed)
    out = [base + rng.normal(0, 0.02, base.shape) for _ in range(n)]
    if first_exact:
        out[0] = base.copy()
    return out


def _run_dir(tmp_path, bundle_dir, every=100, extra=""):
    d = tmp_path / "run"
    d.mkdir()
    (d / rf.INCLUDE_INFO).write_text(
        f"ffast-include {INCLUDE_FORMAT_VERSION}\nbundle {bundle_dir}\nevery {every}\n"
        f"{extra}elements C H O\nend\n")
    return d


def _done(run_dir):
    rf.write_json_atomic(run_dir / rf.PRODUCER_DONE, {"exit_code": 0})


def _record(positions, bundle_dir):
    from ffast.live.data_file import bundle_sha256, record_data_file

    record_data_file(positions, bundle_sha256=bundle_sha256(bundle_dir), source="test",
                     data_file="run.data")


def _analysis(run_dir):
    [d] = list((run_dir / rf.ANALYSIS_DIR).iterdir())
    return d


def test_a_finished_dump_is_scored_exactly_like_the_bundle_scores_it(tmp_path, bundle_dir, records):
    import detectana

    run = _run_dir(tmp_path, bundle_dir)
    frames = _frames(5)
    _record(frames[0], bundle_dir)
    (run / rf.FRAMES).write_text(dump_text(frames))
    _done(run)
    assert Scorer(run, poll_s=0.01).run() == 0

    a = _analysis(run)
    rows = rf.read_scores(a / rf.SCORES)
    bundle = detectana.load(bundle_dir)
    want = bundle.score(np.stack(frames))
    exc = bundle.exceedance(want, role="centroid")
    assert [r["frame"] for r in rows] == [0, 1, 2, 3, 4]
    assert [r["step"] for r in rows] == [0, 100, 200, 300, 400]
    assert set(rows[0]["tracks"]) == set(bundle.tracks)
    for track, values in want.items():
        for score in ("T2", "SPE"):
            got = [r["tracks"][track][score] for r in rows]
            assert got == ([float(v) for v in values[score]] if score in values else [None] * 5)
    assert [r["exceedance"] for r in rows] == [float(v) for v in exc.values]
    assert [tuple(r["test"]) for r in rows] == list(exc.tests)
    assert [r["marked"] for r in rows] == [bool(v) for v in exc.marked]
    assert all(r["identity"] == "proven" for r in rows)
    progress = rf.read_json(a / rf.PROGRESS)
    assert progress["state"] == "done" and progress["scored_frames"] == 5
    assert progress["scored_through_step"] == 400 and progress["units"] == "metal"
    assert progress["time_unit"] == "ps"
    settings = rf.read_json(a / rf.SETTINGS)
    assert settings["bundle"] == str(bundle_dir) and settings["analysis_id"] == a.name
    kinds = [e["kind"] for e in rf.read_events(run)]
    assert kinds == ["scorer_start", "scorer_ready", "identity", "scorer_done"]


def test_a_first_frame_nobody_recorded_is_unverified(tmp_path, bundle_dir, records):
    run = _run_dir(tmp_path, bundle_dir)
    (run / rf.FRAMES).write_text(dump_text(_frames(2)))
    _done(run)
    Scorer(run, poll_s=0.01).run()
    rows = rf.read_scores(_analysis(run) / rf.SCORES)
    assert [r["identity"] for r in rows] == ["unverified", "unverified"]


def test_a_record_for_another_bundle_does_not_prove_identity(tmp_path, bundle_dir, records):
    from ffast.live.data_file import record_data_file

    run = _run_dir(tmp_path, bundle_dir)
    frames = _frames(1)
    record_data_file(frames[0], bundle_sha256="0" * 64, source="t", data_file="x")
    (run / rf.FRAMES).write_text(dump_text(frames))
    _done(run)
    Scorer(run, poll_s=0.01).run()
    assert rf.read_scores(_analysis(run) / rf.SCORES)[0]["identity"] == "unverified"


def test_units_other_than_metal_or_real_are_refused(tmp_path, bundle_dir, records):
    run = _run_dir(tmp_path, bundle_dir)
    (run / rf.FRAMES).write_text(dump_text(_frames(2), units="lj"))
    _done(run)
    assert Scorer(run, poll_s=0.01).run() == 0
    assert rf.read_json(_analysis(run) / rf.PROGRESS)["state"] == "refused"
    [refused] = [e for e in rf.read_events(run) if e["kind"] == "refused"]
    assert "'lj'" in refused["reason"]
    assert not (_analysis(run) / rf.SCORES).exists()


def test_real_units_are_accepted_with_femtoseconds(tmp_path, bundle_dir, records):
    run = _run_dir(tmp_path, bundle_dir)
    (run / rf.FRAMES).write_text(dump_text(_frames(1), units="real"))
    _done(run)
    Scorer(run, poll_s=0.01).run()
    assert rf.read_json(_analysis(run) / rf.PROGRESS)["time_unit"] == "fs"


def test_another_element_order_is_refused(tmp_path, bundle_dir, records):
    run = _run_dir(tmp_path, bundle_dir)
    els = ("H",) + ASPIRIN_ELEMENTS[1:]
    (run / rf.FRAMES).write_text(dump_text(_frames(1), els))
    _done(run)
    Scorer(run, poll_s=0.01).run()
    [refused] = [e for e in rf.read_events(run) if e["kind"] == "refused"]
    assert "does not match the bundle" in refused["reason"]


def test_an_unverified_damaged_first_frame_is_refused_naming_both_causes(tmp_path, bundle_dir, records):
    run = _run_dir(tmp_path, bundle_dir)
    frames = _frames(1)
    frames[0][13] += 3.0                                        # an H far from its C
    (run / rf.FRAMES).write_text(dump_text(frames))
    _done(run)
    Scorer(run, poll_s=0.01).run()
    [refused] = [e for e in rf.read_events(run) if e["kind"] == "refused"]
    assert "another order" in refused["reason"] and "already damaged" in refused["reason"]


def test_a_proven_damaged_first_frame_is_still_scored(tmp_path, bundle_dir, records):
    run = _run_dir(tmp_path, bundle_dir)
    frames = _frames(1)
    frames[0][13] += 3.0
    _record(frames[0], bundle_dir)
    (run / rf.FRAMES).write_text(dump_text(frames))
    _done(run)
    Scorer(run, poll_s=0.01).run()
    assert rf.read_scores(_analysis(run) / rf.SCORES)[0]["identity"] == "proven"


def test_a_run_without_the_include_ends_with_a_warning(tmp_path, records):
    run = tmp_path / "run"
    run.mkdir()
    _done(run)
    assert Scorer(run, poll_s=0.01).run() == 0
    [w] = [e for e in rf.read_events(run) if e["kind"] == "warning"]
    assert "include" in w["text"]


def test_a_missing_bundle_is_refused(tmp_path, records):
    run = _run_dir(tmp_path, tmp_path / "nowhere.bundle")
    assert Scorer(run, poll_s=0.01).run() == 0
    [refused] = [e for e in rf.read_events(run) if e["kind"] == "refused"]
    assert "nowhere.bundle" in refused["reason"]


def test_a_growing_dump_with_half_written_frames_is_scored_to_the_end(tmp_path, bundle_dir, records):
    run = _run_dir(tmp_path, bundle_dir)
    frames = _frames(6)
    texts = [frame_text(k * 100, p, ASPIRIN_ELEMENTS, units="metal" if k == 0 else None)
             for k, p in enumerate(frames)]
    scorer = Scorer(run, poll_s=0.005)
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("rc", scorer.run()))
    t.start()
    with open(run / rf.FRAMES, "w") as fh:
        for text in texts:
            half = len(text) // 2
            fh.write(text[:half]); fh.flush()
            time.sleep(0.02)
            fh.write(text[half:]); fh.flush()
        fh.write(texts[0][:40]); fh.flush()                   # a torn last frame
    _done(run)
    t.join(10)
    assert result["rc"] == 0
    rows = rf.read_scores(_analysis(run) / rf.SCORES)
    assert [r["step"] for r in rows] == [0, 100, 200, 300, 400, 500]
    assert any("incomplete frame" in e.get("text", "") for e in rf.read_events(run))


def test_a_replaced_dump_fails_the_scorer_not_lammps(tmp_path, bundle_dir, records):
    import os

    run = _run_dir(tmp_path, bundle_dir)
    (run / rf.FRAMES).write_text(dump_text(_frames(2)))
    scorer = Scorer(run, poll_s=0.005)
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("rc", scorer.run()))
    t.start()
    deadline = time.time() + 10
    while not (run / rf.ANALYSIS_DIR).exists() or not list((run / rf.ANALYSIS_DIR).glob("*/scores.jsonl")):
        assert time.time() < deadline
        time.sleep(0.01)
    (tmp_path / "new").write_text(dump_text(_frames(3, seed=5)))
    os.replace(tmp_path / "new", run / rf.FRAMES)
    t.join(10)
    assert result["rc"] == 1
    assert rf.read_json(_analysis(run) / rf.PROGRESS)["state"] == "failed"


# ── alarms (plan step 4) ─────────────────────────────────────────────────────
# 100 steps × 0.0005 ps = 0.05 ps per saved frame; persist 0.5 ps = 10 frames,
# checked every frame (the bundle's 50/500 share), fraction 0.2 → 2 marks in 10.

def _stretch(p, delta):
    """Bond C0–H14 (0-based atom indices, as DetectAna names it) pulled ``delta`` Å longer."""
    out = p.copy()
    u = out[14] - out[0]
    out[14] += delta * u / np.linalg.norm(u)
    return out


def _quiet(n, seed=0):
    """Frames with half the training noise: no test exceeds its threshold by chance."""
    base = initial_atoms().get_positions()
    rng = np.random.default_rng(seed)
    out = [base + rng.normal(0, 0.01, base.shape) for _ in range(n)]
    out[0] = base.copy()
    return out


def _alarm_run(tmp_path, bundle_dir, *, delta=0.6, start=30, n=60, extra="persist_ps 0.5\n"):
    run = _run_dir(tmp_path, bundle_dir, extra=extra)
    frames = _quiet(n)
    _record(frames[0], bundle_dir)
    frames[start:] = [_stretch(p, delta) for p in frames[start:]]
    (run / rf.FRAMES).write_text(dump_text(frames))
    return run


def _events(run, kind):
    return [e for e in rf.read_events(run) if e["kind"] == kind]


def test_a_persisting_anomaly_raises_one_alarm_with_onset_confirmation_and_report(tmp_path, bundle_dir, records):
    run = _alarm_run(tmp_path, bundle_dir)
    _done(run)
    assert Scorer(run, poll_s=0.01).run() == 0
    a = _analysis(run)
    rows = rf.read_scores(a / rf.SCORES)
    assert not any(r["marked"] for r in rows[:30]) and all(r["marked"] for r in rows[30:])
    assert rows[0]["chem"] == {"broken": 0, "close": 0, "planarity": rows[0]["chem"]["planarity"]}
    [alarm] = _events(run, "alarm")
    # Windows start every frame; the first holding 2 marks is frames 22..31, and
    # its first marked frame (the estimated onset) is 30, where the stretch began.
    assert alarm["alarm"] == "ood"
    assert alarm["onset"]["frame"] == 30 and alarm["confirmation"]["frame"] == 31
    assert alarm["confirmation"]["step"] == 3100
    progress = rf.read_json(a / rf.PROGRESS)
    assert progress["rule"]["window_frames"] == 10 and progress["rule"]["step_frames"] == 1
    assert [x["kind"] for x in progress["alarms"]] == ["ood"]
    report = rf.read_json(run / alarm["report"])
    assert report["worst"]["frame"] in (30, 31) and report["bundle_sha256"]
    top = report["attribution"]["groups"][0]
    assert 14 in top["atoms"] and 0 in top["atoms"]
    assert not (run / rf.STOP_REQUEST).exists() and _events(run, "stop_requested") == []


def test_the_false_alarm_chance_counts_the_frames_up_to_the_first_alarm(tmp_path, bundle_dir, records):
    # The chance is updated every 100 frames and frozen at the first alarm; an
    # alarm before frame 100 must still leave the chance measured on its frames.
    run = _alarm_run(tmp_path, bundle_dir)
    _done(run)
    Scorer(run, poll_s=0.01).run()
    fa = rf.read_json(_analysis(run) / rf.PROGRESS)["false_alarm"]
    assert fa["rho_frames"] == 32   # frames 0..31, the confirmation frame included


def test_stop_on_alarm_asks_the_wrapper_to_stop(tmp_path, bundle_dir, records):
    run = _alarm_run(tmp_path, bundle_dir, extra="persist_ps 0.5\nstop_on_alarm 1\n")
    _done(run)
    Scorer(run, poll_s=0.01).run()
    req = rf.read_json(run / rf.STOP_REQUEST)
    assert req["by"] == "alarm" and "step 3100" in req["reason"]
    [ev] = _events(run, "stop_requested")
    assert ev["by"] == "alarm"


def test_the_viewer_turns_stop_on_alarm_on_during_the_run(tmp_path, bundle_dir, records):
    run = _alarm_run(tmp_path, bundle_dir)
    rf.set_stop_on_alarm(run, True, by="user")
    _done(run)
    Scorer(run, poll_s=0.01).run()
    [policy] = _events(run, "stop_policy")
    assert policy["stop_on_alarm"] is True and policy["by"] == "user"
    assert rf.read_json(run / rf.STOP_REQUEST)["by"] == "alarm"


def test_single_marks_do_not_alarm(tmp_path, bundle_dir, records):
    run = _run_dir(tmp_path, bundle_dir, extra="persist_ps 0.5\n")
    frames = _quiet(60)
    _record(frames[0], bundle_dir)
    for k in (10, 25, 40, 55):
        frames[k] = _stretch(frames[k], 0.6)
    (run / rf.FRAMES).write_text(dump_text(frames))
    _done(run)
    Scorer(run, poll_s=0.01).run()
    assert sum(r["marked"] for r in rf.read_scores(_analysis(run) / rf.SCORES)) == 4
    assert _events(run, "alarm") == []


def test_three_broken_bond_frames_in_a_row_raise_a_chemistry_alarm(tmp_path, bundle_dir, records):
    run = _alarm_run(tmp_path, bundle_dir, delta=2.0, start=40, n=45, extra="persist_ps 50\n")
    _done(run)
    Scorer(run, poll_s=0.01).run()
    kinds = {e["alarm"]: e for e in _events(run, "alarm")}
    chem = kinds["chem"]
    assert chem["onset"]["frame"] == 40 and chem["confirmation"]["frame"] == 42
    report = rf.read_json(run / chem["report"])
    assert report["broken_bonds"][0][:2] == [0, 14] and len(report["frames"]) == 3


def test_the_log_gives_the_planned_length_and_the_false_alarm_chance(tmp_path, bundle_dir, records):
    run = _run_dir(tmp_path, bundle_dir, extra="persist_ps 0.5\n")
    frames = _frames(30)
    _record(frames[0], bundle_dir)
    (run / rf.FRAMES).write_text(dump_text(frames))
    (run / rf.LOG).write_text("  1 by 1 by 1 MPI processor grid\nrun ${n}\nrun 2000\nrun 1000\n")
    _done(run)
    Scorer(run, poll_s=0.01).run()
    planned = _events(run, "planned")
    assert planned[-1]["end_step"] == 3000 and planned[-1]["frames"] == 31
    [w] = [e for e in _events(run, "warning") if "restart" in e["text"]]
    p = rf.read_json(_analysis(run) / rf.PROGRESS)
    assert p["planned_end_step"] == 3000 and p["processes"] == 1
    fa = p["false_alarm"]
    assert fa["over"] == "run" and fa["planned_frames"] == 31 and fa["at_least"] is True
    assert 0.0 <= fa["probability"] <= 1.0 and fa["alpha"] == pytest.approx(0.01)

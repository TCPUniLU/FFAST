"""Score a loaded dataset into an item folder (ADR 0093 step 2)."""

from __future__ import annotations

import numpy as np
import pytest
from ase import Atoms
from ase.io import write

from ffast.live import run_folder as rf
from ffast.live import scored_items as si
from ffast.loaders.ase import aseDatasetLoader
from tests.ffast.live.bundle_helpers import initial_atoms, make_bundle, write_frames


@pytest.fixture(scope="module")
def bundle_dir(tmp_path_factory):
    return make_bundle(tmp_path_factory.mktemp("b"))[0]


@pytest.fixture(autouse=True)
def analyses(tmp_path, monkeypatch):
    monkeypatch.setenv(si.ENV, str(tmp_path / "analyses"))


def _load(path):
    return aseDatasetLoader(str(path))


def _rows(folder):
    [a] = list((folder / rf.ANALYSIS_DIR).iterdir())
    return rf.read_scores(a / rf.SCORES)


def test_a_file_is_scored_exactly_like_the_bundle_scores_it(tmp_path, bundle_dir):
    import detectana

    ds = _load(write_frames(tmp_path / "f.xyz", 5, seed=4))
    folder = si.score_dataset(ds, bundle_dir, si.make_settings(0.5))
    rows = _rows(folder)
    bundle = detectana.load(bundle_dir)
    want = bundle.score(np.stack([ds.getCoordinates(i) for i in range(5)]))
    assert [r["frame"] for r in rows] == [0, 1, 2, 3, 4]
    assert [r["time"] for r in rows] == [0.0, 0.5, 1.0, 1.5, 2.0]
    [track] = [t for t in want if want[t].get("T2") is not None][:1]
    assert [r["tracks"][track]["T2"] for r in rows] == pytest.approx(list(want[track]["T2"]))
    item = si.read_item(folder)
    assert item["state"] == "done" and item["parent"] == ds.getFingerprint() and not item["hidden"]


def test_the_same_key_twice_scores_once(tmp_path, bundle_dir):
    ds = _load(write_frames(tmp_path / "f.xyz", 4, seed=5))
    settings = si.make_settings(0.5)
    folder = si.score_dataset(ds, bundle_dir, settings)
    first = (next((folder / rf.ANALYSIS_DIR).iterdir()) / rf.SCORES).read_text()
    assert si.score_dataset(ds, bundle_dir, settings) == folder
    assert (next((folder / rf.ANALYSIS_DIR).iterdir()) / rf.SCORES).read_text() == first
    assert si.find_items(ds.getFingerprint()) == [folder]


def test_a_frame_the_reference_cannot_take_gets_its_error_text_and_the_others_are_scored(tmp_path, bundle_dir):
    frames = [initial_atoms().copy() for _ in range(4)]
    for k, a in enumerate(frames):
        a.positions += np.random.default_rng(k).normal(0, 0.02, a.positions.shape)
    frames[2] = Atoms("H2O", positions=[[0, 0, 0], [0.9, 0, 0], [0, 0.9, 0]])
    path = tmp_path / "mixed.xyz"
    write(str(path), frames, format="extxyz")
    folder = si.score_dataset(_load(path), bundle_dir, si.make_settings(0.5))
    rows = {r["frame"]: r for r in _rows(folder)}
    assert set(rows) == {0, 1, 2, 3}
    assert "atoms" in rows[2]["refused"] and "tracks" not in rows[2]
    assert all("tracks" in rows[i] for i in (0, 1, 3))
    assert si.read_item(folder)["state"] == "done"


def test_a_file_of_only_other_molecules_is_refused(tmp_path, bundle_dir):
    path = tmp_path / "water.xyz"
    write(str(path), [Atoms("H2O", positions=[[0, 0, 0], [0.9, 0, 0], [0, 0.9, 0]])] * 3, format="extxyz")
    folder = si.score_dataset(_load(path), bundle_dir, si.make_settings(0.5))
    item = si.read_item(folder)
    assert item["state"] == "refused" and "no frame" in item["reason"]


def test_a_file_with_a_cell_is_refused(tmp_path, bundle_dir):
    frames = [initial_atoms().copy() for _ in range(3)]
    for a in frames:
        a.set_cell([20, 20, 20])
        a.set_pbc(True)
    path = tmp_path / "cell.xyz"
    write(str(path), frames, format="extxyz")
    ds = _load(path)
    assert si.check_dataset(ds) == si.PERIODIC_REASON
    folder = si.score_dataset(ds, bundle_dir, si.make_settings(0.5))
    item = si.read_item(folder)
    assert item["state"] == "refused" and item["reason"] == si.PERIODIC_REASON
    assert not (folder / rf.ANALYSIS_DIR).exists() or not list((folder / rf.ANALYSIS_DIR).rglob("scores.jsonl"))


def test_stopping_leaves_cancelled_and_continuing_scores_only_the_missing_frames(tmp_path, bundle_dir, monkeypatch):
    monkeypatch.setattr(si, "CHUNK", 3)
    ds = _load(write_frames(tmp_path / "f.xyz", 7, seed=6))
    settings = si.make_settings(0.5)
    folder = si.score_dataset(ds, bundle_dir, settings, stopped=lambda: True)
    assert si.read_item(folder)["state"] == "cancelled"
    assert [r["frame"] for r in _rows(folder)] == [0, 1, 2]
    si.score_dataset(ds, bundle_dir, settings)
    assert si.read_item(folder)["state"] == "done"
    assert [r["frame"] for r in _rows(folder)] == list(range(7))


def test_without_a_time_frames_are_scored_and_no_alarm_is_raised(tmp_path, bundle_dir):
    base = initial_atoms().get_positions()
    frames = []
    for i in range(40):
        a = initial_atoms().copy()
        a.positions = base.copy()
        if i >= 10:
            u = a.positions[14] - a.positions[0]
            a.positions[14] += 0.6 * u / np.linalg.norm(u)
        frames.append(a)
    path = tmp_path / "s.xyz"
    write(str(path), frames, format="extxyz")
    folder = si.score_dataset(_load(path), bundle_dir, si.make_settings(None))
    rows = _rows(folder)
    assert len(rows) == 40 and all(r["time"] is None for r in rows) and any(r["marked"] for r in rows)
    events = rf.read_events(folder)
    assert any(e["kind"] == "warning" and "alarms are off" in e["text"] for e in events)
    assert not any(e["kind"] == "alarm" for e in events)


def test_hidden_items_are_not_found_and_scoring_again_unhides_them(tmp_path, bundle_dir):
    ds = _load(write_frames(tmp_path / "f.xyz", 3, seed=7))
    settings = si.make_settings(0.5)
    folder = si.score_dataset(ds, bundle_dir, settings)
    si.set_hidden(folder, True)
    assert si.find_items(ds.getFingerprint()) == []
    si.score_dataset(ds, bundle_dir, settings)
    assert si.find_items(ds.getFingerprint()) == [folder]


def test_the_time_field_gives_the_time_between_frames(tmp_path):
    frames = [initial_atoms().copy() for _ in range(4)]
    for i, a in enumerate(frames):
        a.info["time"] = 2.0 * i
    path = tmp_path / "t.xyz"
    write(str(path), frames, format="extxyz")
    assert si.suggest_frame_dt_fs(_load(path)) == 2.0
    frames[3].info["time"] = 9.0
    write(str(path), frames, format="extxyz")
    assert si.suggest_frame_dt_fs(_load(path)) is None

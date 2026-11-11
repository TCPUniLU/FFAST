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


# ── step 4: the scored item on the server ────────────────────────────────────

class _Registry(dict):
    def add(self, dataset):
        self[dataset.fingerprint] = dataset
        dataset.loaded = True


class _Env:
    def __init__(self):
        self.datasets = _Registry()
        self.scored_items = {}


def _open(tmp_path, bundle_dir, n=4, refuse_frame=None):
    """A scored item opened on a fake env; the server loop is not needed to read it."""
    import asyncio

    from ffast.live.scored_item import ScoredItem, ScoredItemDataset, item_name

    frames = [initial_atoms().copy() for _ in range(n)]
    for k, a in enumerate(frames):
        a.positions += np.random.default_rng(k).normal(0, 0.02, a.positions.shape)
    for a in frames:
        a.info["energy"] = 1.0
    if refuse_frame is not None:
        frames[refuse_frame] = Atoms("H2O", positions=[[0, 0, 0], [0.9, 0, 0], [0, 0.9, 0]])
        frames[refuse_frame].info["energy"] = 1.0
    path = tmp_path / "f.xyz"
    write(str(path), frames, format="extxyz")
    parent = _load(path)
    parent.initialise()
    settings = si.make_settings(0.5)
    folder = si.score_dataset(parent, bundle_dir, settings)
    item = si.read_item(folder)
    env = _Env()
    env.datasets.add(parent)
    ds = ScoredItemDataset(parent, item["item_id"], item_name(parent.getName(), bundle_dir, settings))
    ds.initialise()
    env.datasets.add(ds)
    scored = ScoredItem(env, folder, ds, lambda m: None)
    scored.registered = True
    env.scored_items[scored.run_id] = scored
    scored.apply(scored.read_files())
    return env, parent, ds, scored


def test_a_scored_items_values_are_its_rows_and_a_refused_frame_is_nan(tmp_path, bundle_dir):
    from ffast.metrics.input_resolver import InputResolver

    env, parent, ds, scored = _open(tmp_path, bundle_dir, refuse_frame=2)
    got = InputResolver(object()).resolve("run.score.worst", dataset=ds)
    want = [next((r["exceedance"] for r in scored.rows if r["frame"] == i and "exceedance" in r), np.nan)
            for i in range(4)]
    assert np.array_equal(got, want, equal_nan=True) and np.isnan(got[2]) and not np.isnan(got[0])
    assert ds.fingerprint.startswith("live:scored-") and not getattr(ds, "grows", False)
    assert ds.lineageMeta()["scored"] is True
    assert scored.refused_frames()[2].startswith("atom order") or "atoms" in scored.refused_frames()[2]


def test_every_value_but_the_scores_is_named_as_the_parents(tmp_path, bundle_dir):
    from ffast.metrics.input_resolver import _ResolverSource

    env, parent, ds, scored = _open(tmp_path, bundle_dir)
    for ref in ("reference.energies", "reference.positions", "reference.forces"):
        assert _ResolverSource(None, None, ds).identity(ref) == _ResolverSource(None, None, parent).identity(ref)
    assert _ResolverSource(None, None, parent).identity("reference.energies") is not None
    before = _ResolverSource(None, None, ds).identity("run.score.worst")
    assert before and ds.fingerprint in before
    scored.rows.append({"frame": 99, "step": 99, "refused": "x"})
    assert _ResolverSource(None, None, ds).identity("run.score.worst") != before


def test_energy_of_a_scored_item_reuses_the_parents_cached_result(tmp_path, bundle_dir):
    """The key the metric executor stores a result under is built from these names."""
    from ffast.metrics.input_resolver import _ResolverSource

    env, parent, ds, scored = _open(tmp_path, bundle_dir)
    a = _ResolverSource(None, None, parent)
    b = _ResolverSource(None, None, ds)
    assert [a.identity(r) for r in ("reference.energies", "offsets")] == \
        [b.identity(r) for r in ("reference.energies", "offsets")]


def test_a_frozen_subset_of_a_scored_item_gets_the_items_values_for_its_frames(tmp_path, bundle_dir):
    from ffast.loaders.dataset import DatasetView
    from ffast.metrics.input_resolver import InputResolver, run_value_problem

    env, parent, ds, scored = _open(tmp_path, bundle_dir)
    sub = DatasetView.frozenSubset(ds, None, [3, 1], "pick")
    sub.initialise()
    resolver = InputResolver(object())
    whole = resolver.resolve("run.score.worst", dataset=ds)
    assert resolver.resolve("run.score.worst", dataset=sub).tolist() == [whole[3], whole[1]]
    assert run_value_problem(None, "run.score.worst", sub) is None
    plain = DatasetView.frozenSubset(parent, None, [0], "p")
    plain.initialise()
    assert resolver.resolve("run.score.worst", dataset=plain) is None


def test_the_run_scores_metrics_appear_when_only_a_scored_item_is_open(tmp_path, bundle_dir):
    from ffast.metrics import run_values
    from ffast.metrics.registry import default_registry

    env, parent, ds, scored = _open(tmp_path, bundle_dir)
    try:
        # Opening listed them: the item's own follow loop syncs when its scores arrive.
        assert default_registry.has("ffast.run_score_worst") and default_registry.has("ffast.run_chem_broken")
    finally:
        env.scored_items.clear()
        run_values.sync(env)
    assert not default_registry.has("ffast.run_score_worst")


def test_the_message_says_it_is_scored_and_has_no_stop(tmp_path, bundle_dir):
    import msgpack

    env, parent, ds, scored = _open(tmp_path, bundle_dir, refuse_frame=1)
    m = msgpack.unpackb(scored.message(), raw=False, strict_map_key=False)
    kw = m.get("kwargs", m) if isinstance(m, dict) else m[-1]
    assert kw["kind"] == "scored" and kw["parent"] == parent.fingerprint and kw["state"] == "done"
    assert kw["n_frames"] == 4 and kw["stop"] is None and kw["stop_on_alarm"] is False and kw["producer"] is None
    assert kw["fingerprint"] == ds.fingerprint and kw["tab"].startswith("Scores ")
    assert list(kw["refused_frames"]) == [1]
    assert len(kw["series"]["frame"]) == 4


def test_an_item_scored_again_is_not_hidden_when_the_old_one_notices_it_was_closed(tmp_path, bundle_dir):
    from ffast.live.scored_item import ScoredItem

    env, parent, ds, old = _open(tmp_path, bundle_dir)
    # removed, then scored again with the same settings: a new object under the same id
    new = ScoredItem(env, old.folder, ds, lambda m: None)
    env.scored_items[old.run_id] = new
    old.closed()
    assert env.scored_items[old.run_id] is new
    assert not si.read_item(old.folder)["hidden"]
    # and closing the current one hides it, once
    new.closed()
    new.closed()
    assert old.run_id not in env.scored_items and si.read_item(old.folder)["hidden"]


def _water_then_aspirin(path, n_water, n_aspirin):
    water = Atoms("H2O", positions=[[0, 0, 0], [0.9, 0, 0], [0, 0.9, 0]])
    frames = [water.copy() for _ in range(n_water)]
    for k in range(n_aspirin):
        a = initial_atoms().copy()
        a.positions += np.random.default_rng(k).normal(0, 0.02, a.positions.shape)
        frames.append(a)
    write(str(path), frames, format="extxyz")
    return path


def test_cancelling_before_any_frame_is_accepted_is_a_cancel_not_a_refusal(tmp_path, bundle_dir, monkeypatch):
    # The first chunk holds only frames the reference refuses; the later frames could be
    # scored. Stopping there must leave the item incomplete, so Continue scoring works.
    monkeypatch.setattr(si, "CHUNK", 2)
    ds = _load(_water_then_aspirin(tmp_path / "w.xyz", 2, 3))
    settings = si.make_settings(0.5)
    folder = si.score_dataset(ds, bundle_dir, settings, stopped=lambda: True)
    assert si.read_item(folder)["state"] == "cancelled"
    si.score_dataset(ds, bundle_dir, settings)
    assert si.read_item(folder)["state"] == "done"
    assert sorted(r["frame"] for r in _rows(folder)) == list(range(5))


def test_without_a_time_the_alarms_off_warning_is_written_once(tmp_path, bundle_dir):
    folder = si.score_dataset(_load(write_frames(tmp_path / "f.xyz", 4, seed=8)), bundle_dir,
                              si.make_settings(None))
    warnings = [e for e in rf.read_events(folder) if e["kind"] == "warning"]
    assert [e["text"] for e in warnings] == [si.NO_TIME_EVENT]


def test_a_scoring_cut_off_by_a_restart_reads_as_cancelled(tmp_path, bundle_dir):
    ds = _load(write_frames(tmp_path / "f.xyz", 3, seed=9))
    folder = si.score_dataset(ds, bundle_dir, si.make_settings(0.5))
    si.write_item_json(folder, state="scoring")          # the server died mid-scoring
    si.mark_interrupted(folder)
    assert si.read_item(folder)["state"] == "cancelled"
    si.write_item_json(folder, state="done")
    si.mark_interrupted(folder)
    assert si.read_item(folder)["state"] == "done"


def test_two_scorings_of_one_item_never_run_at_once(tmp_path, bundle_dir, monkeypatch):
    # Cancel, then Continue scoring at once: the cancelled thread may still be scoring
    # its last chunk. The second must wait for it, not score the same frames again.
    import threading

    monkeypatch.setattr(si, "CHUNK", 2)
    ds = _load(write_frames(tmp_path / "f.xyz", 6, seed=10))
    settings = si.make_settings(0.5)
    first_in_chunk = threading.Event()
    release = threading.Event()

    def slow_progress(done, total):
        first_in_chunk.set()
        release.wait(10)

    a = threading.Thread(target=si.score_dataset, args=(ds, bundle_dir, settings),
                         kwargs={"stopped": lambda: True, "progress": slow_progress})
    a.start()
    assert first_in_chunk.wait(30)
    b = threading.Thread(target=si.score_dataset, args=(ds, bundle_dir, settings))
    b.start()
    b.join(0.5)
    release.set()
    a.join(30)
    b.join(30)
    frames = [r["frame"] for r in _rows(si.item_dir(si.item_id(ds.getFingerprint(), bundle_dir, settings)))]
    assert sorted(frames) == list(range(6)) and len(frames) == 6
    assert si.read_item(si.item_dir(si.item_id(ds.getFingerprint(), bundle_dir, settings)))["state"] == "done"

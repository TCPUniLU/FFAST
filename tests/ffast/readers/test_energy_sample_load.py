"""A dataset can be loaded as an energy sample, and its predictions follow it
(ADR 0096, step 3)."""
import json
import logging
import threading
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from ffast.core.dataset_registry import DatasetRegistry
from ffast.core.loading_coordinator import LoadingCoordinator
from ffast.io import frame_cache, frame_index as fi
from ffast.protocol.messages import LoadDatasetRequest
from ffast.readers import chunks, registry, sampling, text
from ffast.readers.dataset import ReaderDataset
from ffast.readers.sampling import sample_by_energy
from tests.ffast.readers.toy_reader import ToyEnergyReader, ToyTextReader, make_frames, write_toy
from tests.ffast.test_loading_coordinator import _file_env


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(fi, "CACHE_DIR", tmp_path / "index")
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "arrays")
    fi.clear_memory_cache()
    text.clear_memory_cache()
    registry.register(ToyEnergyReader)
    registry.register(ToyTextReader)
    yield
    fi.clear_memory_cache()
    text.clear_memory_cache()


def _skewed_frames(n=200, *, variable=False):
    """Frame i's energy: most low, a few high; positions say which frame it is."""
    frames = make_frames(n, variable=variable)
    rng = np.random.default_rng(3)
    for i, f in enumerate(frames):
        f["energy"] = float(rng.uniform(0, 1)) if i % 20 else float(rng.uniform(9, 10))
    return frames


class _Datasets:
    def __init__(self):
        self.added = []

    def add(self, dataset, slice_num, **kwargs):
        self.added.append((dataset, slice_num, kwargs))


def _coordinator():
    pushed = []
    coordinator = LoadingCoordinator.__new__(LoadingCoordinator)
    coordinator._env = SimpleNamespace(
        datasets=_Datasets(), mutation_lock=threading.Lock(), datasetTypes={},
        eventPush=lambda *a, **k: pushed.append((a, k)))
    coordinator.lookForGhosts = lambda: None
    return coordinator, pushed


def _energies(frames):
    return np.array([f.get("energy", np.nan) for f in frames], dtype=float)


# ── loading a sample ─────────────────────────────────────────────────────────

def test_a_sampled_load_reads_exactly_the_frames_the_sample_picks(tmp_path):
    frames = _skewed_frames()
    path = write_toy(tmp_path / "run.toys", frames)
    coordinator, pushed = _coordinator()
    coordinator.loadDataset(path, "toyenergy", taskID="t1",
                            sampling={"method": "even", "n": 20, "seed": 7})
    [(dataset, slice_num, kwargs)] = coordinator._env.datasets.added
    want = sample_by_energy(_energies(frames), 20, method="even", seed=7).frames.tolist()
    assert dataset.N == 20 and dataset.sourceFrames == want
    assert dataset.E.tolist() == _energies(frames)[want].tolist()
    assert slice_num == 0
    assert kwargs["kept_frames"].tolist() == want
    assert kwargs["sampling"] == {"method": "even", "n": 20, "bins": None, "seed": 7,
                                  "energy": "dft", "bins_used": dataset.sampling["bins_used"],
                                  "per_atom": False, "left_out": 0, "n_file_frames": 200}
    messages = [k["message"] for a, k in pushed if a[0] == "TASK_PROGRESS"]
    assert messages[-1] == "Reading run.toys: 20 of 20 frames…"


def test_the_defaults_are_even_and_seed_42():
    from ffast.core.loading_coordinator import _samplingSettings
    assert _samplingSettings({"n": 10}, "dft") == {
        "method": "even", "n": 10, "bins": None, "seed": 42, "energy": "dft"}


def test_a_sampled_xyz_load_bins_per_atom_when_atom_counts_differ(tmp_path):
    lines = []
    rng = np.random.default_rng(5)
    per_atom = rng.uniform(-5, -4, 120)
    counts = [2 + (i % 3) for i in range(120)]
    for i, (e, n) in enumerate(zip(per_atom, counts)):
        lines += [str(n), f"energy={float(e * n)!r} Properties=species:S:1:pos:R:3"]
        lines += [f"H {i} {a} 0.0" for a in range(n)]
    path = tmp_path / "run.xyz"
    path.write_text("\n".join(lines) + "\n")
    coordinator, _ = _coordinator()
    coordinator.loadDataset(str(path), "ase (auto)", sampling={"n": 30})
    [(dataset, _, kwargs)] = coordinator._env.datasets.added
    want = sample_by_energy(per_atom * np.array(counts), 30, n_atoms=np.array(counts))
    assert want.per_atom is True and kwargs["sampling"]["per_atom"] is True
    assert dataset.sourceFrames == want.frames.tolist()
    assert dataset.R_flat[dataset.molecule_offsets[0], 0] == want.frames[0]   # positions say the frame


def test_the_memory_check_counts_only_the_kept_frames(tmp_path, monkeypatch, caplog):
    path = write_toy(tmp_path / "run.toys", _skewed_frames())
    monkeypatch.setattr(text, "available_memory", lambda: 2000)
    coordinator, _ = _coordinator()
    with caplog.at_level(logging.ERROR):
        assert coordinator.loadDataset(path, "toyenergy", sampling={"n": 20}) is None
    [message] = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert "in 20 frames" in message


def test_frames_without_an_energy_are_left_out_with_a_warning(tmp_path, caplog):
    frames = _skewed_frames()
    for i in (3, 4, 5):
        frames[i]["energy"] = float("nan")
    path = write_toy(tmp_path / "run.toys", frames)
    coordinator, _ = _coordinator()
    with caplog.at_level(logging.WARNING):
        coordinator.loadDataset(path, "toyenergy", sampling={"n": 20})
    [(dataset, _, kwargs)] = coordinator._env.datasets.added
    assert not {3, 4, 5} & set(dataset.sourceFrames)
    assert kwargs["sampling"]["left_out"] == 3
    assert ("run.toys: 3 frames have no energy 'dft' and are left out of the sample."
            in [r.getMessage() for r in caplog.records])


def test_asking_for_more_frames_than_have_an_energy_keeps_them_all_and_says_so(tmp_path, caplog):
    path = write_toy(tmp_path / "run.toys", _skewed_frames(30))
    coordinator, _ = _coordinator()
    with caplog.at_level(logging.WARNING):
        coordinator.loadDataset(path, "toyenergy", sampling={"n": 100})
    [(dataset, _, _)] = coordinator._env.datasets.added
    assert dataset.N == 30
    assert ("run.toys: 100 frames asked for, but only 30 have an energy: all 30 are kept."
            in [r.getMessage() for r in caplog.records])


@pytest.mark.parametrize("kwargs, message", [
    (dict(reader="toytext", sampling={"n": 5}),
     "run.toys cannot be sampled by energy: the toytext reader does not list energies"),
    (dict(sampling={"n": 5, "energy": "mace"}),
     "run.toys has no energy named 'mace' to sample by; it has: dft"),
    (dict(sampling={"n": 1}), "Sampling by energy needs to keep at least 2 frames."),
    (dict(sampling={"n": 5, "method": "loudest"}), "Unknown sampling method 'loudest'"),
    (dict(sampling={"n": 5}, slice_num=3),
     "Load either a sample by energy or every n-th frame, not both."),
])
def test_a_sample_that_cannot_be_made_is_refused_in_plain_words(tmp_path, kwargs, message):
    path = write_toy(tmp_path / "run.toys", _skewed_frames(30))
    coordinator, _ = _coordinator()
    failures = []
    reader = kwargs.pop("reader", "toyenergy")
    coordinator.loadDataset(path, reader, on_error=failures.append, **kwargs)
    assert [str(f) for f in failures] and str(failures[0]).startswith(message)
    assert coordinator._env.datasets.added == []


def test_the_load_message_refuses_sampling_and_a_stride_together():
    LoadDatasetRequest(path="a.xyz", dataset_type="ase (auto)", sampling={"n": 5})
    LoadDatasetRequest(path="a.xyz", dataset_type="ase (auto)", sampling={"n": 5}, slice_num=0)
    with pytest.raises(ValidationError, match="not both"):
        LoadDatasetRequest(path="a.xyz", dataset_type="ase (auto)", sampling={"n": 5}, slice_num=3)


def test_given_frames_are_read_as_they_are_without_sampling_again(tmp_path, monkeypatch):
    path = write_toy(tmp_path / "run.toys", _skewed_frames())
    monkeypatch.setattr(sampling, "sample_by_energy", lambda *a, **k: pytest.fail("sampled again"))
    coordinator, _ = _coordinator()
    settings = {"method": "even", "n": 3, "bins": None, "seed": 1, "energy": "dft"}
    coordinator.loadDataset(path, "toyenergy", sampling=settings, frames=[5, 17, 150])
    [(dataset, _, kwargs)] = coordinator._env.datasets.added
    assert dataset.sourceFrames == [5, 17, 150]
    assert kwargs["kept_frames"].tolist() == [5, 17, 150]
    assert kwargs["sampling"]["seed"] == 1


def test_given_frames_the_file_no_longer_has_are_refused(tmp_path):
    path = write_toy(tmp_path / "run.toys", _skewed_frames(30))
    coordinator, _ = _coordinator()
    failures = []
    coordinator.loadDataset(path, "toyenergy", sampling={"n": 2}, frames=[5, 40],
                            on_error=failures.append)
    assert [str(f) for f in failures] == [
        "run.toys has changed since its sample was made: it has 30 frames, the sample "
        "has frame 40"]


# ── the registry ────────────────────────────────────────────────────────────

def _registry():
    events = SimpleNamespace(eventPush=lambda *a, **k: None,
                             objects=SimpleNamespace(prune=lambda key: None))
    return DatasetRegistry(cache=SimpleNamespace(invalidate=lambda f: []), events=events)


def test_the_registry_keeps_the_kept_frames_and_the_settings_and_delete_clears_them(tmp_path):
    path = write_toy(tmp_path / "run.toys", _skewed_frames(30))
    ds = ReaderDataset(path, chunks.read_arrays(ToyEnergyReader, path, select=lambda n: [1, 4, 9]),
                       "toyenergy")
    ds.initialise()
    reg = _registry()
    reg.add(ds, 0, kept_frames=np.array([1, 4, 9]), sampling={"n": 3})
    assert reg.kept_frames[ds.fingerprint].tolist() == [1, 4, 9]
    assert reg.kept_frames[ds.fingerprint].dtype == np.int64
    assert reg.sampling[ds.fingerprint] == {"n": 3}
    reg.delete(ds.fingerprint)
    assert ds.fingerprint not in reg.kept_frames and ds.fingerprint not in reg.sampling


# ── predictions follow the sample ───────────────────────────────────────────

def _sampled(tmp_path, frames, kept):
    path = write_toy(tmp_path / "ds.toys", frames)
    ds = ReaderDataset(path, chunks.read_arrays(ToyEnergyReader, path, select=lambda n: kept),
                       "toyenergy")
    ds.initialise()
    env = _file_env(ds, key=ds.fingerprint)
    env.datasets.kept_frames = {ds.fingerprint: np.array(kept)}
    return LoadingCoordinator(env), env, ds


def test_a_prediction_on_a_sampled_dataset_reads_the_same_frames(tmp_path):
    frames = _skewed_frames(40)
    kept = [2, 3, 11, 20, 39]
    coordinator, env, ds = _sampled(tmp_path, frames, kept)
    pred = make_frames(40)
    for i, f in enumerate(pred):
        f["energy"] = 1000.0 + i
    path = write_toy(tmp_path / "pred.toys", pred)
    errors = []
    coordinator.loadPrepredictedDataset(path, ds.fingerprint, reader="toyenergy", on_error=errors.append)
    assert errors == []
    stored = {kind: payload for kind, payload, _, _ in env.data.stored}
    assert stored["energy"]["energy"].tolist() == [1000.0 + i for i in kept]


def test_a_prediction_with_another_frame_count_than_the_source_file_is_refused(tmp_path):
    coordinator, env, ds = _sampled(tmp_path, _skewed_frames(40), [2, 3, 11])
    path = write_toy(tmp_path / "pred.toys", make_frames(3))       # as many as kept, not as the file
    errors = []
    coordinator.loadPrepredictedDataset(path, ds.fingerprint, reader="toyenergy", on_error=errors.append)
    assert errors == ["Prediction 'pred.toys' has 3 frames, but the dataset's file has 40."]
    assert env.data.stored == []


# ── sessions ────────────────────────────────────────────────────────────────

def test_a_session_saves_the_kept_frames_and_restores_them_without_sampling_again(tmp_path, monkeypatch):
    from ffast.session.persistence import SessionPersistence

    path = write_toy(tmp_path / "run.toys", _skewed_frames())
    coordinator, _ = _coordinator()
    coordinator.loadDataset(path, "toyenergy", sampling={"n": 12, "seed": 9})
    [(ds, _, kwargs)] = coordinator._env.datasets.added

    saver = SessionPersistence.__new__(SessionPersistence)
    saver._env = SimpleNamespace(
        cache={}, datasets=SimpleNamespace(all=lambda **k: [ds]),
        models=SimpleNamespace(all=lambda **k: []), objects=SimpleNamespace(snapshot=lambda: {}))
    folder = tmp_path / "session"
    saver.save(str(folder))
    info = json.loads((folder / "info.json").read_text())["objects"][ds.fingerprint]
    assert info["sampling"] == kwargs["sampling"]

    # Restore with other defaults in code: the frames come from the session, not a new sample.
    monkeypatch.setattr(sampling, "sample_by_energy", lambda *a, **k: pytest.fail("sampled again"))
    calls = []
    loader = SessionPersistence.__new__(SessionPersistence)
    loader._env = SimpleNamespace(
        datasetTypes={}, datasets=SimpleNamespace(get=lambda fp: None),
        getDatasetFromPath=lambda path: None,
        loadDataset=lambda *a, **k: calls.append((a, k)), lookForGhosts=lambda: None)
    loader.loadInfo = lambda info: None
    monkeypatch.setattr(SessionPersistence, "load_cache", lambda *a, **k: None, raising=False)
    loader.load(str(folder))
    [((p, reader), k)] = calls
    assert (p, reader) == (path, "toyenergy")
    assert list(k["frames"]) == ds.sourceFrames
    assert k["sampling"] == kwargs["sampling"]

    restored, _ = _coordinator()
    restored.loadDataset(p, reader, **{key: k[key] for key in ("sampling", "frames")})
    [(again, _, _)] = restored._env.datasets.added
    assert again.sourceFrames == ds.sourceFrames and again.fingerprint == ds.fingerprint

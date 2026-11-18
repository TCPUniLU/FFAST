"""A reader's file loads as a prediction for a loaded dataset, and every
prediction path runs the same checks (ADR 0095, step 2)."""
import logging
import threading
from types import SimpleNamespace

import ase.io
import numpy as np
import pytest
from ase import Atoms

from ffast.core.loading_coordinator import LoadingCoordinator
from ffast.loaders.ase import aseDatasetLoader
from ffast.readers import chunks, registry
from ffast.readers.dataset import ReaderDataset
from tests.ffast.readers.toy_reader import ToyReader, make_frames, write_toy
from tests.ffast.test_loading_coordinator import _file_env, _frame, _ingest_env, _write


def _setup(tmp_path, frames, stride=0, **dataset_kwargs):
    path = write_toy(tmp_path / "ds.toy", frames)
    ds = ReaderDataset(path, chunks.read_arrays(ToyReader, path, stride=stride), "toy",
                       **dataset_kwargs)
    ds.initialise()
    env = _file_env(ds, key=ds.fingerprint)
    if stride:
        env.datasets.slice_numbers = {ds.fingerprint: stride}
    return LoadingCoordinator(env), env, ds


def _load(coordinator, ds, frames, tmp_path, *, reader="toy", **kwargs):
    path = write_toy(tmp_path / "pred.toy", frames)
    errors = []
    ToyReader.asked = []
    coordinator.loadPrepredictedDataset(path, ds.fingerprint, reader=reader,
                                        on_error=errors.append, **kwargs)
    return errors


def _stored(env):
    return {kind: payload for kind, payload, _, _ in env.data.stored}


def test_a_toy_prediction_attaches_to_a_toy_dataset(toy, tmp_path):
    frames = make_frames(6)
    coordinator, env, ds = _setup(tmp_path, frames)
    assert _load(coordinator, ds, frames, tmp_path) == []
    stored = _stored(env)
    assert stored["energy"]["energy"].tolist() == [0.0, 10.0, 20.0, 30.0, 40.0, 50.0]
    assert np.asarray(stored["forces"]["forces"]).shape == (6, 3, 3)
    [ghost] = env.objects.registered.values()
    assert ghost["name"] == "pred.toy" and ghost["type"] == "ghost_model"


def test_a_variable_prediction_attaches_with_per_frame_forces(toy, tmp_path):
    frames = make_frames(5, variable=True)
    coordinator, env, ds = _setup(tmp_path, frames)
    assert _load(coordinator, ds, frames, tmp_path) == []
    forces = _stored(env)["forces"]["forces"]
    assert [len(f) for f in forces] == [3, 4, 5, 3, 4]


def test_a_different_frame_count_is_refused_with_its_message(toy, tmp_path):
    coordinator, env, ds = _setup(tmp_path, make_frames(6))
    errors = _load(coordinator, ds, make_frames(5), tmp_path)
    assert errors == ["Prediction 'pred.toy' has 5 frames, but the dataset's file has 6."]
    assert env.data.stored == [] and env.objects.registered == {}


def test_different_atoms_per_frame_are_refused_naming_the_first_frame(toy, tmp_path):
    frames = make_frames(5, variable=True)
    coordinator, env, ds = _setup(tmp_path, frames)
    other = make_frames(5, variable=True)
    other[2] = make_frames(1, atoms=4)[0]
    errors = _load(coordinator, ds, other, tmp_path)
    assert errors == ["Atom count mismatch for prediction 'pred.toy': frame 2 has 4 atoms, "
                      "the dataset's frame 2 has 5."]
    assert env.data.stored == []


def test_different_elements_are_refused_naming_frame_and_atom(toy, tmp_path):
    coordinator, env, ds = _setup(tmp_path, make_frames(3))
    errors = _load(coordinator, ds, make_frames(3, numbers=[8, 1, 6]), tmp_path)
    assert errors == ["Element mismatch for prediction 'pred.toy': in frame 0, atom 2 is C "
                      "but the dataset's is H."]


def test_a_file_without_atomic_numbers_is_not_checked_for_elements(toy, tmp_path):
    class NoNumbers(ToyReader):
        name = "nonumbers"

        def contents(self):
            return {**super().contents(), "numbers": False}

        def read(self, frames):
            out = super().read(frames)
            del out["numbers"]
            return out

    registry.register(NoNumbers)
    coordinator, env, ds = _setup(tmp_path, make_frames(3))
    assert _load(coordinator, ds, make_frames(3, numbers=[8, 1, 6]), tmp_path,
                 reader="nonumbers") == []
    assert "energy" in _stored(env)


def test_positions_and_units_that_differ_warn_and_the_load_goes_on(toy, tmp_path, caplog):
    class Hartree(ToyReader):
        name = "hartree"
        units = {"energy": "Hartree"}

    registry.register(Hartree)
    coordinator, env, ds = _setup(tmp_path, make_frames(3))
    ds.setUnits({"energy": "eV"})
    with caplog.at_level(logging.WARNING, logger="FFAST"):
        errors = _load(coordinator, ds, make_frames(3, shift=0.01), tmp_path, reader="hartree")
    assert errors == [] and "energy" in _stored(env)
    texts = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("positions differ from the dataset's by up to 0.0100" in t for t in texts)
    assert any("its energy unit is Hartree, the dataset's is eV" in t for t in texts)


def test_an_energy_only_file_and_a_forces_only_file_load(toy, tmp_path):
    coordinator, env, ds = _setup(tmp_path, make_frames(4))
    assert _load(coordinator, ds, make_frames(4, forces=False), tmp_path) == []
    assert set(_stored(env)) == {"energy"}
    coordinator, env, ds = _setup(tmp_path, make_frames(4))
    assert _load(coordinator, ds, make_frames(4, energy=False), tmp_path) == []
    assert set(_stored(env)) == {"forces"}


def test_a_file_with_neither_energies_nor_forces_is_refused(toy, tmp_path):
    coordinator, env, ds = _setup(tmp_path, make_frames(4))
    errors = _load(coordinator, ds, make_frames(4, energy=False, forces=False), tmp_path)
    assert errors == ["pred.toy has no energies or forces, so it cannot load as a prediction"]


def test_names_that_match_are_paired_and_the_users_pairing_overrides(toy, tmp_path):
    pred = make_frames(3, extra=["mace"])
    coordinator, env, ds = _setup(tmp_path, make_frames(3))
    _load(coordinator, ds, pred, tmp_path)
    assert _stored(env)["energy"]["energy"].tolist() == [0.0, 10.0, 20.0]          # dft with dft

    coordinator, env, ds = _setup(tmp_path, make_frames(3))
    _load(coordinator, ds, pred, tmp_path, selected_energy_key="mace", selected_force_key="mace")
    assert _stored(env)["energy"]["energy"].tolist() == [100.0, 101.0, 102.0]
    assert np.asarray(_stored(env)["forces"]["forces"])[0, 0].tolist() == [0.5, 0.0, 0.0]

    coordinator, env, ds = _setup(tmp_path, make_frames(3))          # an unmatched pairing
    _load(coordinator, ds, pred, tmp_path, selected_energy_key="dft", selected_force_key="mace")
    assert _stored(env)["energy"]["energy"].tolist() == [0.0, 10.0, 20.0]
    assert np.asarray(_stored(env)["forces"]["forces"])[0, 0].tolist() == [0.5, 0.0, 0.0]


def test_an_empty_key_means_none_and_an_unknown_key_is_refused(toy, tmp_path):
    pred = make_frames(3, extra=["mace"])
    coordinator, env, ds = _setup(tmp_path, make_frames(3))
    _load(coordinator, ds, pred, tmp_path, selected_energy_key="mace", selected_force_key="")
    assert set(_stored(env)) == {"energy"}
    errors = _load(coordinator, ds, pred, tmp_path, selected_energy_key="x")
    assert errors == ["pred.toy has no energy named 'x' (it has: dft, mace)"]


def test_a_prediction_reads_the_frames_the_dataset_kept_and_checks_the_files_length(toy, tmp_path):
    frames = make_frames(10)
    coordinator, env, ds = _setup(tmp_path, frames, stride=3)                 # file frames 0 3 6 9
    assert _load(coordinator, ds, frames, tmp_path) == []
    assert _stored(env)["energy"]["energy"].tolist() == [0.0, 30.0, 60.0, 90.0]
    assert sorted({f for chunk in ToyReader.asked for f in chunk}) == [0, 3, 6, 9]
    errors = _load(coordinator, ds, make_frames(9), tmp_path)                  # 3 kept too, 9 != 10
    assert errors == ["Prediction 'pred.toy' has 9 frames, but the dataset's file has 10."]


def test_a_reader_error_while_reading_the_prediction_reaches_on_error(toy, tmp_path):
    from ffast.readers import ReaderError

    class Strict(ToyReader):
        name = "strict"

        def read(self, frames):
            raise ReaderError("Frame 2 has no forces")

    registry.register(Strict)
    coordinator, env, ds = _setup(tmp_path, make_frames(3))
    assert _load(coordinator, ds, make_frames(3), tmp_path, reader="strict") == ["Frame 2 has no forces"]


def test_with_no_reader_named_the_readers_for_the_file_choose(toy, tmp_path):
    frames = make_frames(3)
    coordinator, env, ds = _setup(tmp_path, frames)
    assert _load(coordinator, ds, frames, tmp_path, reader=None) == []
    assert "energy" in _stored(env)


# ── predictions inside a reader's own file ──────────────────────────────────

class _Registry:
    def __init__(self):
        self.added, self.slice_numbers = [], {}

    def add(self, dataset, slice_num):
        self.added.append(dataset)


def _loading_env():
    env = _ingest_env()
    env.datasets = _Registry()
    env.datasetTypes = {}
    return env


def test_more_names_in_a_toy_file_load_as_predictions_in_place(toy, tmp_path):
    path = write_toy(tmp_path / "ds.toy", make_frames(4, extra=["mace", "orb"]))
    env = _loading_env()
    coordinator = LoadingCoordinator(env)
    coordinator.lookForGhosts = lambda: None
    coordinator.loadDataset(path, "toy", prediction_keys=[("mace", "mace", "MACE"), ("orb", "orb", "ORB")])
    [ds] = env.datasets.added
    assert ds.energy_key == "dft"                                   # the reference
    names = sorted(info["name"] for info in env.objects.registered.values())
    assert names == ["MACE", "ORB"]
    energies = sorted(tuple(p["energy"].tolist()) for k, p, _, _ in env.data.stored if k == "energy")
    assert energies == [(100.0, 101.0, 102.0, 103.0), (200.0, 201.0, 202.0, 203.0)]
    assert ds.arrays is None                         # the extra sets are not kept in memory


def test_in_file_predictions_of_a_variable_toy_dataset(toy, tmp_path):
    path = write_toy(tmp_path / "ds.toy", make_frames(5, variable=True, extra=["mace"]))
    env = _loading_env()
    coordinator = LoadingCoordinator(env)
    coordinator.lookForGhosts = lambda: None
    coordinator.loadDataset(path, "toy", prediction_keys=[("mace", "mace", "MACE")])
    assert [i["name"] for i in env.objects.registered.values()] == ["MACE"]
    forces = next(p["forces"] for k, p, _, _ in env.data.stored if k == "forces")
    assert [len(f) for f in forces] == [3, 4, 5, 3, 4]


# ── the old paths run the same checks ───────────────────────────────────────

def test_an_ase_prediction_with_the_wrong_atom_count_is_now_refused(tmp_path):
    ref = [_frame("CH4", energy=-1.0 - i, seed=i) for i in range(3)]
    dataset = aseDatasetLoader(_write(tmp_path, ref, "ref.extxyz"), atomsList=ref,
                               selected_energy_key="ref_energy", selected_force_key="ref_forces")
    dataset.initialise()
    dataset.sourceFrameCount = 3
    wrong = []
    for i in range(3):
        atoms = Atoms("C2H6", positions=np.zeros((8, 3)) + i)
        atoms.info["ref_energy"] = -5.0 - i                    # energies only: it attached before
        wrong.append(atoms)
    path = _write(tmp_path, wrong, "wrong.extxyz")
    env = _file_env(dataset, key=dataset.fingerprint)
    errors = []
    LoadingCoordinator(env).loadPrepredictedDataset(
        path, dataset.fingerprint, selected_energy_key="ref_energy", on_error=errors.append)
    assert len(errors) == 1 and errors[0].startswith("Atom count mismatch for prediction 'wrong.extxyz'")
    assert env.data.stored == []


def test_an_ase_prediction_of_another_length_is_refused_with_the_files_counts(tmp_path):
    ref = [_frame("CH4", energy=-1.0 - i, seed=i) for i in range(4)]
    dataset = aseDatasetLoader(_write(tmp_path, ref, "ref.extxyz"), atomsList=ref,
                               selected_energy_key="ref_energy", selected_force_key="ref_forces")
    dataset.initialise()
    dataset.sourceFrameCount = 4
    path = _write(tmp_path, ref[:3], "short.extxyz")
    env = _file_env(dataset, key=dataset.fingerprint)
    errors = []
    LoadingCoordinator(env).loadPrepredictedDataset(
        path, dataset.fingerprint, selected_energy_key="ref_energy",
        selected_force_key="ref_forces", on_error=errors.append)
    assert errors == ["Prediction 'short.extxyz' has 3 frames, but the dataset's file has 4."]


def _npz(tmp_path, n, atoms):
    path = str(tmp_path / "p.npz")
    np.savez(path, E=np.arange(n, dtype=float), F=np.zeros((n, atoms, 3)))
    return path


def test_an_npz_prediction_is_checked_for_file_length_and_atoms(toy, tmp_path):
    coordinator, env, ds = _setup(tmp_path, make_frames(6))
    errors = []
    coordinator.loadPrepredictedDataset(_npz(tmp_path, 5, 3), ds.fingerprint, on_error=errors.append)
    assert errors == ["Prediction 'p.npz' has 5 frames, but the dataset's file has 6."]
    coordinator.loadPrepredictedDataset(_npz(tmp_path, 6, 4), ds.fingerprint, on_error=errors.append)
    assert errors[1].startswith("Atom count mismatch for prediction 'p.npz': frame 0 has 4 atoms")
    assert env.data.stored == []
    coordinator.loadPrepredictedDataset(_npz(tmp_path, 6, 3), ds.fingerprint, on_error=errors.append)
    assert len(errors) == 2 and set(_stored(env)) == {"energy", "forces"}


# ── the old loaders record their file's frame count ─────────────────────────

def _recording_coordinator():
    env = _loading_env()
    coordinator = LoadingCoordinator(env)
    coordinator.lookForGhosts = lambda: None
    return coordinator, env


def test_an_ase_dataset_records_its_files_frame_count_with_and_without_a_stride(tmp_path):
    frames = [_frame("CH4", energy=-1.0 - i, seed=i) for i in range(10)]
    path = _write(tmp_path, frames)
    env = _loading_env()
    env.datasetTypes = {"ase (auto)": type("A", (), {})}
    from ffast.plugins.loaders.ase import loadData
    registered = {}
    loadData(SimpleNamespace(initialiseDatasetType=lambda t: registered.setdefault("ase", t)))
    env.datasetTypes = {"ase (auto)": registered["ase"]}
    for stride, kept in ((0, 10), (3, 4)):
        coordinator = LoadingCoordinator(env)
        coordinator.lookForGhosts = lambda: None
        env.datasets.added.clear()
        coordinator.loadDataset(path, "ase (auto)", slice_num=stride)
        [ds] = env.datasets.added
        assert (ds.N, ds.sourceFrameCount) == (kept, 10)


def test_an_sgdml_dataset_records_its_files_frame_count(tmp_path):
    from ffast.plugins.models.sgdml import sGDMLDatasetLoader
    path = str(tmp_path / "d.npz")
    np.savez(path, R=np.zeros((10, 3, 3)), E=np.arange(10.0), F=np.zeros((10, 3, 3)),
             z=np.array([6, 1, 8]))
    assert sGDMLDatasetLoader(path, slice_num=3).sourceFrameCount == 10
    assert sGDMLDatasetLoader(path).sourceFrameCount == 10


def _xyz(path, counts):
    """An xyz file with one frame per count: n, a comment line, n atom lines."""
    lines = []
    for n in counts:
        lines += [str(n), "Properties=species:S:1:pos:R:3:forces:R:3 energy=-1.0"]
        lines += ["H 0.0 0.0 0.0 0.0 0.0 0.0"] * n
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def test_same_total_atoms_split_differently_is_refused_with_file_lines(tmp_path):
    from ffast.readers.xyz_reader import XyzReader

    dataset_path = _xyz(tmp_path / "data.xyz", [3, 3, 4, 3])      # 13 atoms
    ds = ReaderDataset(dataset_path, chunks.read_arrays(XyzReader, dataset_path), "xyz")
    ds.initialise()
    env = _file_env(ds, key=ds.fingerprint)
    coordinator = LoadingCoordinator(env)
    prediction = _xyz(tmp_path / "pred.xyz", [3, 3, 3, 4])        # 13 atoms too
    errors = []
    coordinator.loadPrepredictedDataset(prediction, ds.fingerprint, reader="xyz",
                                        on_error=errors.append)
    assert errors == ["Atom count mismatch for prediction 'pred.xyz': frame 2 has 3 atoms, "
                      "the dataset's frame 2 has 4 (line 11 of data.xyz in the dataset, "
                      "line 11 of pred.xyz in the prediction)."]
    assert env.data.stored == []

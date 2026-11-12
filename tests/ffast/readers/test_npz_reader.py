"""sGDML ``.npz`` files load through a reader (ADR 0095, step 3)."""
import numpy as np
import pytest

from ffast.core.loading_coordinator import LoadingCoordinator
from ffast.plugins.models.sgdml import sGDMLDatasetLoader
from ffast.readers import check_reader, chunks, registry
from ffast.readers.dataset import ReaderDataset
from ffast.readers.npz_reader import NpzReader


def _npz(tmp_path, n=8, atoms=3, name="d.npz", lower=False, lattice=True, dtype=np.float64):
    rng = np.random.default_rng(3)
    keys = ("r", "e", "f", "z") if lower else ("R", "E", "F", "z")
    arrays = {
        keys[0]: rng.normal(size=(n, atoms, 3)).astype(dtype),
        keys[1]: np.arange(n, dtype=dtype).reshape(-1, 1),
        keys[2]: rng.normal(size=(n, atoms, 3)).astype(dtype),
        keys[3]: np.array([6, 1, 8][:atoms] + [1] * max(0, atoms - 3)),
    }
    if lattice:
        arrays["lattice"] = np.eye(3) * 12.0
    path = str(tmp_path / name)
    np.savez(path, **arrays)
    return path, arrays


def test_the_reader_follows_the_rules(tmp_path):
    check_reader(NpzReader, _npz(tmp_path)[0])


def test_a_prediction_file_with_only_E_and_F_follows_the_rules_and_has_no_atoms_of_its_own(tmp_path):
    path = str(tmp_path / "p.npz")
    np.savez(path, E=np.arange(6.0), F=np.zeros((6, 4, 3)))
    check_reader(NpzReader, path)
    contents = NpzReader(path).contents()
    assert (contents["positions"], contents["numbers"], contents["cell"]) == (False, False, False)
    assert (contents["energies"], contents["forces"]) == (["E"], ["F"])
    out = NpzReader(path).read([1, 3])
    assert out["n_atoms"].tolist() == [4, 4]


def test_keys_are_lower_cased_as_the_old_loader_did(tmp_path):
    path, arrays = _npz(tmp_path, lower=True)
    reader = NpzReader(path)
    assert reader.contents()["energies"] == ["E"] and reader.n_frames() == 8
    out = reader.read([0, 2])
    assert np.array_equal(out["positions"], arrays["r"][[0, 2]].reshape(-1, 3))


def test_the_cell_is_the_one_lattice_for_every_frame(tmp_path):
    path, _ = _npz(tmp_path)
    cell = NpzReader(path).read([0, 5, 7])["cell"]
    assert cell.shape == (3, 3, 3) and (cell == np.eye(3) * 12.0).all()
    assert "cell" not in NpzReader(_npz(tmp_path, lattice=False, name="n.npz")[0]).read([0])


def test_a_dataset_built_from_it_has_the_old_loaders_fingerprint_and_arrays(tmp_path):
    """Saved sessions and caches are keyed by the fingerprint: it must not change."""
    for dtype in (np.float64, np.float32):
        path, _ = _npz(tmp_path, dtype=dtype, name=f"d{np.dtype(dtype).itemsize}.npz")
        legacy = sGDMLDatasetLoader(path)
        new = ReaderDataset(path, chunks.read_arrays(NpzReader, path), "sGDML")
        assert new.getFingerprint() == legacy.getFingerprint()
        assert np.array_equal(new.R_flat, legacy.R_flat) and np.array_equal(new.F_flat, legacy.F_flat)
        assert np.array_equal(new.E, legacy.E)
        assert new.isVariable is False and new.chem == legacy.chem


def test_the_type_sGDML_is_a_reader_and_loadDataset_uses_it(tmp_path):
    import threading
    from types import SimpleNamespace
    path, _ = _npz(tmp_path, n=10)
    added = []
    env = SimpleNamespace(datasets=SimpleNamespace(add=lambda d, s: added.append((d, s))),
                          mutation_lock=threading.Lock(), datasetTypes={"sGDML": sGDMLDatasetLoader})
    coordinator = LoadingCoordinator(env)
    coordinator.lookForGhosts = lambda: None
    coordinator.loadDataset(path, "sGDML", slice_num=3)
    [(dataset, slice_num)] = added
    assert isinstance(dataset, ReaderDataset) and slice_num == 3
    assert (dataset.N, dataset.sourceFrameCount) == (4, 10)
    assert dataset.toMetaDict()["source_type"] == "sGDML"


def test_npz_files_go_to_the_sGDML_reader_first():
    names = [r.name for r in registry.readers_for("/x/pred.npz")]
    assert names[0] == "sGDML"

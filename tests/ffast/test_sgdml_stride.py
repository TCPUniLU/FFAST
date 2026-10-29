"""An sGDML .npz loads every Nth frame when the desktop's large-file dialog
asks for a stride, as ASE files already do. Only the per-frame arrays are
strided: the elements and the cell describe every frame and stay whole."""
import os
import tempfile

import numpy as np

from ffast.plugins.models.sgdml import sGDMLDatasetLoader


def _npz(n_frames=10, lattice=True):
    rng = np.random.default_rng(5)
    arrays = {
        "R": rng.normal(size=(n_frames, 3, 3)),
        "E": np.arange(n_frames, dtype=float).reshape(-1, 1),
        "F": rng.normal(size=(n_frames, 3, 3)),
        "z": np.array([6, 1, 8]),
    }
    if lattice:
        arrays["lattice"] = np.eye(3) * 12.0
    path = os.path.join(tempfile.mkdtemp(), "d.npz")
    np.savez(path, **arrays)
    return path, arrays


def test_a_stride_keeps_every_nth_frame_from_the_first():
    path, arrays = _npz()
    dataset = sGDMLDatasetLoader(path, slice_num=3)
    assert dataset.N == 4                                   # frames 0, 3, 6, 9
    assert dataset.E.tolist() == [0.0, 3.0, 6.0, 9.0]
    assert np.array_equal(dataset.R_flat, arrays["R"][::3].reshape(-1, 3))
    assert np.array_equal(dataset.F_flat, arrays["F"][::3].reshape(-1, 3))


def test_a_stride_leaves_the_elements_and_the_cell_whole():
    path, _ = _npz()
    dataset = sGDMLDatasetLoader(path, slice_num=3)
    assert dataset.z.tolist() == [6, 1, 8]
    assert dataset.getChemicalFormula() == sGDMLDatasetLoader(path).getChemicalFormula()
    assert np.array_equal(dataset.lattice, np.eye(3) * 12.0)


def test_no_stride_or_a_non_positive_one_loads_every_frame():
    path, _ = _npz()
    for slice_num in (None, 0, -1):
        kwargs = {} if slice_num is None else {"slice_num": slice_num}
        assert sGDMLDatasetLoader(path, **kwargs).N == 10


class _Datasets:
    def __init__(self):
        self.added = []
        self.slice_numbers = {}

    def add(self, dataset, slice_num):
        self.added.append((dataset, slice_num))


def test_the_coordinator_passes_the_stride_to_an_sgdml_load():
    from types import SimpleNamespace
    from ffast.core.loading_coordinator import LoadingCoordinator

    import threading

    path, _ = _npz()
    coordinator = LoadingCoordinator.__new__(LoadingCoordinator)
    coordinator._env = SimpleNamespace(datasets=_Datasets(), mutation_lock=threading.Lock())
    coordinator._resolveLoad = lambda kind, p, typ: sGDMLDatasetLoader
    coordinator.lookForGhosts = lambda: None
    coordinator.loadDataset(path, "sGDML", slice_num=5)
    (dataset, slice_num), = coordinator._env.datasets.added
    assert (dataset.N, slice_num) == (2, 5)


def test_an_npz_prediction_takes_its_datasets_stride():
    """A prediction is sampled exactly like the dataset it is attached to, as
    the ASE path already does (_readPredictionAtomsList)."""
    import threading
    from types import SimpleNamespace
    from ffast.core.loading_coordinator import LoadingCoordinator

    rng = np.random.default_rng(7)
    E, F = np.arange(10, dtype=float), rng.normal(size=(10, 3, 3))
    path = os.path.join(tempfile.mkdtemp(), "pred.npz")
    np.savez(path, E=E, F=F)

    datasets = _Datasets()
    datasets.slice_numbers = {"ds": 3}
    datasets.get = lambda key: "the dataset"
    coordinator = LoadingCoordinator.__new__(LoadingCoordinator)
    coordinator._env = SimpleNamespace(datasets=datasets, mutation_lock=threading.Lock())
    ingested = {}
    coordinator._ingestPrediction = lambda ds, e, f, **kw: ingested.update(E=e, F=f) or True
    coordinator.lookForGhosts = lambda: None
    coordinator.loadPrepredictedDataset(path, "ds")
    assert ingested["E"].tolist() == [0.0, 3.0, 6.0, 9.0]
    assert np.array_equal(ingested["F"], F[::3])

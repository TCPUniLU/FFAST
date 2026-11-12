"""A reader's arrays become FFAST's flat dataset, and ``loadDataset`` takes a
reader's type through the chunk reader with stride, progress and pop-ups."""
import logging
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from ffast.core.loading_coordinator import LoadingCoordinator
from ffast.io.extxyz_arrays import NotEnoughMemory
from ffast.readers import ReaderError, chunks, registry
from ffast.readers.dataset import ReaderDataset
from tests.ffast.readers.toy_reader import ToyReader, make_frames, write_toy


def _dataset(path, **kwargs):
    loaded = chunks.read_arrays(ToyReader, path, **{k: v for k, v in kwargs.items() if k == "stride"})
    keys = {k: v for k, v in kwargs.items() if k != "stride"}
    return ReaderDataset(path, loaded, "toy", **keys)


def test_a_uniform_file_is_a_uniform_dataset_with_the_right_arrays(toy, tmp_path):
    frames = make_frames(4, cell=True)
    path = write_toy(tmp_path / "u.toy", frames)
    ds = _dataset(path)
    assert ds.isVariable is False
    assert ds.N == 4
    assert ds.molecule_offsets.tolist() == [0, 3, 6, 9, 12]
    assert ds.R_flat.shape == (12, 3)
    assert np.array_equal(ds.R_flat[3:6, 0], [1, 2, 3])
    assert ds.z_flat.tolist() == [8, 1, 1] * 4
    assert ds.E.tolist() == [0.0, 10.0, 20.0, 30.0]
    assert ds.F_flat.shape == (12, 3) and ds.hasForces()
    assert ds.getLattice().shape == (4, 3, 3)
    assert ds.getLattice()[2, 0, 0] == 12.0
    assert ds.chem == "H2O"
    assert ds.getNAtoms() == 3


def test_frames_of_different_sizes_make_a_variable_dataset(toy, tmp_path):
    path = write_toy(tmp_path / "v.toy", make_frames(5, variable=True))
    ds = _dataset(path)
    assert ds.isVariable is True
    assert ds.molecule_offsets.tolist() == [0, 3, 7, 12, 15, 19]
    assert ds.getNAtoms().tolist() == [3, 4, 5, 3, 4]
    assert ds.getChemicalFormula() == "Variable (3-5 atoms)"


def test_the_same_count_with_other_atoms_in_a_frame_is_still_variable(toy, tmp_path):
    frames = make_frames(3)
    frames[1]["numbers"] = [1, 8, 1]            # H O H, not O H H
    path = write_toy(tmp_path / "o.toy", frames)
    assert _dataset(path).isVariable is True


def test_a_file_without_energies_or_forces_or_a_cell_leaves_them_absent_not_zero(toy, tmp_path):
    path = write_toy(tmp_path / "p.toy", make_frames(3, energy=False, forces=False))
    ds = _dataset(path)
    assert ds.E is None and ds.F_flat is None and ds.getLattice() is None
    assert not ds.hasForces()


def test_a_frame_without_a_value_is_nan_not_zero(toy, tmp_path):
    frames = make_frames(3)
    del frames[1]["energy"]
    ds = _dataset(write_toy(tmp_path / "n.toy", frames))
    assert np.isnan(ds.E[1]) and not np.isnan(ds.E[0])


def test_the_dataset_remembers_its_reader_and_the_file_frame_count(toy, tmp_path):
    path = write_toy(tmp_path / "a.toy", make_frames(9))
    ds = _dataset(path, stride=3)
    assert ds.N == 3
    assert ds.sourceFrameCount == 9
    assert ds.toMetaDict()["source_type"] == "toy"


def test_the_chosen_energy_and_force_names_are_read_and_an_unknown_one_is_refused(toy, tmp_path):
    path = write_toy(tmp_path / "a.toy", make_frames(3))
    assert _dataset(path, energy_key="dft", force_key="dft").E.tolist() == [0.0, 10.0, 20.0]
    with pytest.raises(ReaderError, match="no energy named 'mace'.*dft"):
        _dataset(path, energy_key="mace")


def test_a_file_without_positions_or_elements_cannot_load_as_a_dataset(toy, tmp_path):
    class Bare(ToyReader):
        name = "bare"

        def contents(self):
            return {**super().contents(), "positions": False}

        def read(self, frames):
            out = super().read(frames)
            del out["positions"]
            return out

    path = write_toy(tmp_path / "a.toy", make_frames(2))
    loaded = chunks.read_arrays(Bare, path)
    with pytest.raises(ReaderError, match="a.toy has no positions, so it cannot load as a dataset"):
        ReaderDataset(path, loaded, "bare")


# ── through the Loading Coordinator ─────────────────────────────────────────

class _Datasets:
    def __init__(self):
        self.added = []

    def add(self, dataset, slice_num):
        self.added.append((dataset, slice_num))


def _coordinator():
    pushed = []
    coordinator = LoadingCoordinator.__new__(LoadingCoordinator)
    coordinator._env = SimpleNamespace(
        datasets=_Datasets(), mutation_lock=threading.Lock(), datasetTypes={},
        eventPush=lambda *a, **k: pushed.append((a, k)))
    coordinator.lookForGhosts = lambda: None
    return coordinator, pushed


def test_loadDataset_loads_a_readers_type_with_stride_and_progress(toy, tmp_path):
    path = write_toy(tmp_path / "run.toy", make_frames(10))
    coordinator, pushed = _coordinator()
    coordinator.loadDataset(path, "toy", taskID="t1", slice_num=3)
    (dataset, slice_num), = coordinator._env.datasets.added
    assert (dataset.N, slice_num) == (4, 3)             # frames 0, 3, 6, 9
    assert dataset.E.tolist() == [0.0, 30.0, 60.0, 90.0]
    assert dataset.name == "run"                         # initialise() ran
    messages = [k["message"] for a, k in pushed if a[0] == "TASK_PROGRESS"]
    assert messages and messages[-1] == "Reading run.toy: 4 of 4 frames…"


def test_loadDataset_shows_a_reader_errors_text_and_no_traceback(toy, tmp_path, caplog):
    class Strict(ToyReader):
        name = "strict"

        def read(self, frames):
            raise ReaderError("Frame 12 has no forces")

    registry.register(Strict)
    path = write_toy(tmp_path / "run.toy", make_frames(2))
    coordinator, _ = _coordinator()
    with caplog.at_level(logging.DEBUG):
        assert coordinator.loadDataset(path, "strict") is None
    popups = [r for r in caplog.records if r.levelno >= logging.WARNING and getattr(r, "popup", True)]
    assert [r.getMessage() for r in popups] == ["Frame 12 has no forces"]
    assert popups[0].exc_info is None
    assert coordinator._env.datasets.added == []


def test_loadDataset_wraps_any_other_exception_and_calls_on_error(toy, tmp_path, caplog):
    class Careless(ToyReader):
        name = "careless"

        def read(self, frames):
            return {}["forces"]

    registry.register(Careless)
    path = write_toy(tmp_path / "run.toy", make_frames(2))
    coordinator, _ = _coordinator()
    failures = []
    with caplog.at_level(logging.DEBUG):
        coordinator.loadDataset(path, "careless", on_error=failures.append)
    assert [str(e) for e in failures] == ["careless reader could not read run.toy: 'forces'"]
    # with on_error the window is told itself: no second pop-up from the log
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING and getattr(r, "popup", True)]


def test_loadDataset_says_when_memory_is_short(toy, tmp_path, monkeypatch, caplog):
    path = write_toy(tmp_path / "big.toy", make_frames(50))
    monkeypatch.setattr(chunks, "available_memory", lambda: 2000)
    coordinator, _ = _coordinator()
    with caplog.at_level(logging.ERROR):
        assert coordinator.loadDataset(path, "toy") is None
    [record] = [r for r in caplog.records if r.levelno >= logging.ERROR and getattr(r, "popup", True)]
    assert record.getMessage().startswith("big.toy needs about ")


def test_the_old_branches_still_serve_their_own_types(tmp_path):
    """Until step 3, "ase (auto)" and "sGDML" are not readers."""
    coordinator, _ = _coordinator()
    coordinator._env.datasetTypes = {}
    assert coordinator.loadDataset(str(tmp_path / "x.xyz"), "ase (auto)") is None   # path missing: old message

"""Files ASE reads, other than .xyz and .extxyz, load through a reader (ADR 0095,
step 3). The two xyz formats keep today's fast path until step 4."""
import threading
from types import SimpleNamespace

import ase.io
import numpy as np
import pytest
from ase import Atoms
from ase.calculators.singlepoint import SinglePointCalculator

from ffast.core.loading_coordinator import LoadingCoordinator
from ffast.loaders.ase import aseDatasetLoader
from ffast.readers import check_reader, chunks, registry
from ffast.readers.ase_reader import AseReader
from ffast.readers.dataset import ReaderDataset


def _frames(n=5, variable=False, calc=True, info=True, seed=0.0, arrays=True):
    """``arrays=False`` for a .traj file, which keeps info and the calculator but not extra arrays."""
    out = []
    for i in range(n):
        atoms = Atoms("CH4" if not (variable and i % 2) else "C2H6")
        k = len(atoms)
        atoms.set_positions(np.arange(k * 3).reshape(k, 3) * 0.1 + i + seed)
        atoms.set_cell(np.eye(3) * 10.0)
        atoms.pbc = True
        if info:
            atoms.info["ref_energy"] = -1.0 - i
            atoms.info["score"] = float(i)
            if arrays:
                atoms.set_array("ref_forces", np.full((k, 3), i + 0.5))
                atoms.set_array("weight", np.arange(k, dtype=float))
        if calc:
            atoms.calc = SinglePointCalculator(
                atoms, energy=-10.0 - i, forces=np.full((k, 3), -(i + 0.25)))
        out.append(atoms)
    return out


def _write(tmp_path, frames, name="d.traj"):
    path = str(tmp_path / name)
    ase.io.write(path, frames)
    return path


def test_the_reader_follows_the_rules_on_a_trajectory_and_on_an_extxyz_file(tmp_path):
    frames = _frames(7)
    check_reader(AseReader, _write(tmp_path, frames, "a.traj"))
    check_reader(AseReader, _write(tmp_path, frames, "a.extxyz"))      # the class can read it; FFAST does not ask yet
    check_reader(AseReader, _write(tmp_path, _frames(6, variable=True), "v.traj"))


def test_contents_lists_keys_the_way_the_probe_does_with_calculator_values(tmp_path):
    reader = AseReader(_write(tmp_path, _frames(3), "c.extxyz"))
    c = reader.contents()
    assert c["energies"] == ["ref_energy", "energy"]        # info keys, then the calculator's
    assert c["forces"] == ["ref_forces", "forces"]
    assert (c["positions"], c["numbers"], c["cell"]) == (True, True, True)
    assert c["frame_fields"] == ["ref_energy", "score"] and c["atom_fields"] == ["weight"]
    bare = AseReader(_write(tmp_path, _frames(3, calc=False, info=False), "b.extxyz")).contents()
    assert bare["energies"] == [] and bare["forces"] == []


def test_read_gives_info_arrays_calculator_cell_and_fields(tmp_path):
    frames = _frames(4, variable=True)
    out = AseReader(_write(tmp_path, frames, "v.extxyz")).read([1, 3])
    assert out["n_atoms"].tolist() == [8, 8]
    assert out["energy:ref_energy"].tolist() == [-2.0, -4.0]
    assert out["energy:energy"].tolist() == [-11.0, -13.0]           # the calculator
    assert out["forces:ref_forces"].shape == (16, 3) and out["forces:ref_forces"][0, 0] == 1.5
    assert out["forces:forces"][0, 0] == -1.25
    assert out["cell"].shape == (2, 3, 3) and out["cell"][0, 0, 0] == 10.0
    assert out["frame:score"].tolist() == [1.0, 3.0]
    assert out["atom:weight"].shape == (16,)


def test_frames_asked_front_to_back_or_again_from_the_start_both_work(tmp_path):
    path = _write(tmp_path, _frames(6), "a.extxyz")
    reader = AseReader(path)
    assert reader.read([4])["energy:ref_energy"].tolist() == [-5.0]
    assert reader.read([1, 5])["energy:ref_energy"].tolist() == [-2.0, -6.0]     # behind: starts again
    assert reader.n_frames() == 6


def test_a_value_a_frame_lacks_is_nan(tmp_path):
    frames = _frames(3, calc=False)
    del frames[1].info["ref_energy"]
    out = AseReader(_write(tmp_path, frames)).read([0, 1, 2])
    assert np.isnan(out["energy:ref_energy"][1]) and not np.isnan(out["energy:ref_energy"][0])


def test_the_readers_decline_xyz_and_extxyz_and_take_everything_else(tmp_path):
    from ffast.readers.xyz_reader import XyzReader
    assert AseReader.can_read("/x/a.xyz") is False and AseReader.can_read("/x/a.EXTXYZ") is False
    assert AseReader.can_read("/x/a.traj") is True
    assert registry.readers_for("/x/a.xyz") == [XyzReader]      # ASE only when its parser cannot
    assert [r.name for r in registry.readers_for("/x/a.traj")] == ["ase (auto)"]
    assert [r.name for r in registry.readers_for("/x/a.npz")] == ["sGDML", "ase (auto)"]


def test_a_dataset_built_from_it_has_the_old_loaders_fingerprint(tmp_path):
    """Saved sessions and caches are keyed by the fingerprint: it must not change."""
    for variable in (False, True):
        frames = _frames(6, variable=variable, arrays=False)
        path = _write(tmp_path, frames, f"f{variable}.traj")
        legacy = aseDatasetLoader(path, atomsList=ase.io.read(path, ":"),
                                  selected_energy_key="ref_energy")
        new = ReaderDataset(path, chunks.read_arrays(AseReader, path), "ase (auto)",
                            energy_key="ref_energy")
        assert new.getFingerprint() == legacy.getFingerprint()
        assert new.isVariable is legacy.isVariable
        assert np.array_equal(new.E, legacy.E) and np.array_equal(new.F_flat, legacy.F_flat)


def test_the_calculator_values_are_the_default_when_a_file_has_no_named_keys(tmp_path):
    path = _write(tmp_path, _frames(4, info=False))
    ds = ReaderDataset(path, chunks.read_arrays(AseReader, path), "ase (auto)")
    assert (ds.energy_key, ds.force_key) == ("energy", "forces")
    assert ds.E.tolist() == [-10.0, -11.0, -12.0, -13.0]


# ── through the Loading Coordinator ─────────────────────────────────────────

def _loading_env():
    from tests.ffast.test_loading_coordinator import _ingest_env

    class Registry:
        def __init__(self):
            self.added, self.slice_numbers = [], {}

        def add(self, dataset, slice_num):
            self.added.append(dataset)

    env = _ingest_env()
    env.datasets = Registry()
    from ffast.plugins.loaders.ase import loadData
    registered = {}
    loadData(SimpleNamespace(initialiseDatasetType=lambda t: registered.setdefault("ase", t)))
    env.datasetTypes = {"ase (auto)": registered["ase"]}
    coordinator = LoadingCoordinator(env)
    coordinator.lookForGhosts = lambda: None
    return coordinator, env


def test_loadDataset_reads_a_trajectory_through_the_reader_with_a_stride(tmp_path):
    path = _write(tmp_path, _frames(10, arrays=False))
    coordinator, env = _loading_env()
    coordinator.loadDataset(path, "ase (auto)", slice_num=3, selected_energy_key="ref_energy")
    [ds] = env.datasets.added
    assert isinstance(ds, ReaderDataset)
    assert (ds.N, ds.sourceFrameCount) == (4, 10)
    assert ds.E.tolist() == [-1.0, -4.0, -7.0, -10.0]


def test_loadDataset_gives_xyz_and_extxyz_to_the_xyz_reader(tmp_path):
    path = _write(tmp_path, _frames(10), "d.extxyz")
    coordinator, env = _loading_env()
    coordinator.loadDataset(path, "ase (auto)", slice_num=3)
    [ds] = env.datasets.added
    assert isinstance(ds, ReaderDataset) and ds.reader_name == "xyz"
    assert (ds.N, ds.sourceFrameCount) == (4, 10)


def test_in_file_predictions_of_a_trajectory_use_the_names_the_probe_lists(tmp_path):
    path = _write(tmp_path, _frames(4, arrays=False))
    coordinator, env = _loading_env()
    coordinator.loadDataset(
        path, "ase (auto)", selected_energy_key="ref_energy",
        prediction_keys=[("energy", "forces", "Calc")])
    names = [i["name"] for i in env.objects.registered.values()]
    assert names == ["Calc"]
    energy = next(p["energy"] for k, p, _, _ in env.data.stored if k == "energy")
    assert energy.tolist() == [-10.0, -11.0, -12.0, -13.0]


def test_a_trajectory_prediction_loads_through_the_reader(tmp_path):
    from tests.ffast.test_loading_coordinator import _file_env
    frames = _frames(5, arrays=False)
    ds_path = _write(tmp_path, frames, "ds.traj")
    ds = ReaderDataset(ds_path, chunks.read_arrays(AseReader, ds_path), "ase (auto)",
                       energy_key="ref_energy")
    ds.initialise()
    pred_path = _write(tmp_path, _frames(5, arrays=False), "pred.traj")
    env = _file_env(ds, key=ds.fingerprint)
    errors = []
    LoadingCoordinator(env).loadPrepredictedDataset(
        pred_path, ds.fingerprint, selected_energy_key="energy", selected_force_key="forces",
        on_error=errors.append)
    assert errors == []
    stored = {k: p for k, p, _, _ in env.data.stored}
    assert stored["energy"]["energy"].tolist() == [-10.0, -11.0, -12.0, -13.0, -14.0]

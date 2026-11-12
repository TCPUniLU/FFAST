"""``.xyz`` and ``.extxyz`` files load through the xyz reader, a Level 2 reader
on today's fast parser (ADR 0095, step 4). Where that parser cannot read a
file, the ASE reader does, as before."""
import logging
import threading
from types import SimpleNamespace

import ase.io
import numpy as np
import pytest

from ffast.core.loading_coordinator import LoadingCoordinator
from ffast.io import extxyz_arrays as xa
from ffast.io import frame_cache, frame_index as fi
from ffast.loaders.ase import aseDatasetLoader
from ffast.readers import check_reader, chunks, registry, text
from ffast.readers.dataset import ReaderDataset
from ffast.readers.xyz_reader import XyzReader
from tests.ffast.read_once_fixtures import FILES, REPO_FILES
from tests.ffast.test_read_once import GOLDEN, REPO_GOLDEN


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(fi, "CACHE_DIR", tmp_path / "frame-index")
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "frame-arrays")
    fi.clear_memory_cache()
    text.clear_memory_cache()
    for name, body in FILES.items():
        (tmp_path / name).write_text(body)
    yield tmp_path
    fi.clear_memory_cache()
    text.clear_memory_cache()


def _env():
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


def _load(path, **kwargs):
    coordinator, env = _env()
    coordinator.loadDataset(str(path), "ase (auto)", **kwargs)
    [dataset] = env.datasets.added
    return dataset


@pytest.mark.parametrize("name", ["uniform.extxyz", "variable.extxyz", "plain.xyz", "columns_change.extxyz"])
def test_the_reader_follows_the_level_2_rules(files, name):
    check_reader(XyzReader, str(files / name))


def test_contents_name_things_the_way_the_ase_reader_and_the_old_probe_do(files):
    c = XyzReader(str(files / "variable.extxyz")).contents()
    assert c["energies"] == ["energy_mace", "energy"]       # info keys, then the calculator's
    assert c["forces"] == ["forces_mace", "forces"]
    assert c["frame_fields"] == ["charge", "energy_mace", "gap"]
    assert c["atom_fields"] == ["q"]
    assert (c["positions"], c["numbers"]) == (True, True)


@pytest.mark.parametrize("name, ek, fk", [k for k in GOLDEN if k[1] != "" and k[0] != "forces_change.extxyz"])
def test_the_dataset_has_the_fingerprint_the_old_fast_path_gave(files, name, ek, fk):
    """Saved sessions and caches are keyed by it. (``forces_change`` is read by
    ASE: see the test below.)"""
    dataset = _load(files / name, selected_energy_key=ek, selected_force_key=fk)
    assert isinstance(dataset, ReaderDataset)
    assert dataset.getFingerprint() == GOLDEN[(name, ek, fk)]


@pytest.mark.parametrize("path", REPO_FILES)
def test_the_example_files_have_their_golden_fingerprints(path):
    assert _load(path).getFingerprint() == REPO_GOLDEN[path.name]


@pytest.mark.parametrize("name", ["uniform.extxyz", "variable.extxyz", "plain.xyz"])
def test_the_arrays_are_the_old_loaders(files, name):
    path = str(files / name)
    legacy = aseDatasetLoader(path, atomsList=ase.io.read(path, index=":"))
    new = _load(path)
    assert new.reader_name == "xyz"
    assert new.isVariable is legacy.isVariable
    assert np.array_equal(new.molecule_offsets, legacy.molecule_offsets)
    assert np.array_equal(new.R_flat, legacy.R_flat) and np.array_equal(new.z_flat, legacy.z_flat)
    assert np.array_equal(new.getLattice(), legacy.getLattice())
    assert (new.E is None) == (legacy.E is None)
    if legacy.E is not None:
        assert np.array_equal(new.E, legacy.E)
    assert (new.F_flat is None) == (legacy.F_flat is None)
    if legacy.F_flat is not None:
        assert np.array_equal(new.F_flat, legacy.F_flat)


def test_a_file_the_fast_parser_cannot_read_loads_with_the_ase_reader_and_warns(files, caplog):
    with caplog.at_level(logging.WARNING, logger="FFAST"):
        dataset = _load(files / "forces_change.extxyz")
    assert dataset.reader_name == "ase (auto)" and dataset.N == 3
    assert any("Reading it with ASE" in r.getMessage() for r in caplog.records)
    # Frame 3 has no forces: its rows are NaN (ADR 0064), not "no forces at all"
    # as the old ASE path made of the whole file. Its fingerprint differs from
    # the old one for that reason.
    assert np.isnan(dataset.F_flat[4:]).all() and not np.isnan(dataset.F_flat[:4]).any()


def test_a_stride_keeps_every_nth_frame_and_records_the_files_count(files):
    path = files / "big.extxyz"
    ase.io.write(str(path), [_atoms(i) for i in range(10)], format="extxyz")
    dataset = _load(path, slice_num=3)
    assert dataset.N == 4 and dataset.sourceFrameCount == 10
    assert dataset.E.tolist() == [0.0, 30.0, 60.0, 90.0]


def _atoms(i):
    from ase import Atoms
    a = Atoms("H2O", positions=np.arange(9).reshape(3, 3) * 0.1 + i)
    a.info["ref_energy"] = float(i) * 10
    a.arrays["ref_forces"] = np.full((3, 3), float(i))
    return a


def test_a_second_load_parses_nothing_and_does_not_scan_again(files, monkeypatch):
    path = files / "uniform.extxyz"
    first = _load(path)
    calls = []
    real = text._parse_job
    monkeypatch.setattr(text, "_parse_job", lambda *a, **k: calls.append(1) or real(*a, **k))
    scans = []
    real_scan = fi.scan
    monkeypatch.setattr(fi, "scan", lambda *a, **k: scans.append(1) or real_scan(*a, **k))
    second = _load(path)
    assert calls == [] and scans == []
    assert second.getFingerprint() == first.getFingerprint()


def test_xyz_files_go_to_the_xyz_reader_and_the_type_is_not_listed(files):
    assert [r.name for r in registry.readers_for("/x/a.xyz")] == ["xyz"]
    assert [r.name for r in registry.readers_for("/x/a.EXTXYZ")] == ["xyz"]
    assert "xyz" not in [row["name"] for row in registry.type_rows()]
    assert LoadingCoordinator._datasetReader("ase (auto)", "/x/a.xyz") is XyzReader
    assert LoadingCoordinator._datasetReader("ase (auto)", "/x/a.traj").name == "ase (auto)"


def test_a_prediction_file_loads_through_the_reader_against_a_dataset(files):
    from tests.ffast.test_loading_coordinator import _file_env
    dataset_path, prediction_path = str(REPO_FILES[0]), str(REPO_FILES[1])
    dataset = _load(dataset_path)
    dataset.initialise()
    env = _file_env(dataset, key=dataset.fingerprint)
    errors = []
    LoadingCoordinator(env).loadPrepredictedDataset(
        prediction_path, dataset.fingerprint, on_error=errors.append)
    assert errors == []
    assert {k for k, _, _, _ in env.data.stored} == {"energy", "forces"}


def test_in_file_predictions_of_an_extxyz_file_use_the_names_the_probe_lists(files):
    coordinator, env = _env()
    coordinator.loadDataset(
        str(files / "variable.extxyz"), "ase (auto)",
        selected_energy_key="energy", selected_force_key="forces",
        prediction_keys=[("energy_mace", "forces_mace", "MACE")])
    assert [i["name"] for i in env.objects.registered.values()] == ["MACE"]
    energy = next(p["energy"] for k, p, _, _ in env.data.stored if k == "energy")
    assert energy.tolist() == [-5.5, -7.25, -1.5]


def test_the_names_follow_the_headers_order_so_the_default_is_the_first_listed(tmp_path):
    """A set's order is arbitrary: once the default energy was the wrong one of
    two. The first energy key of the header is the default, as for ASE."""
    path = tmp_path / "order.extxyz"
    path.write_text(
        "1\nProperties=species:S:1:pos:R:3 energy_b=1.0 energy_a=2.0 energy_c=3.0\nH 0 0 0\n"
        "1\nProperties=species:S:1:pos:R:3 energy_b=1.5 energy_a=2.5 energy_c=3.5\nH 0 0 1\n")
    assert XyzReader(str(path)).contents()["energies"] == ["energy_b", "energy_a", "energy_c"]
    out = XyzReader.parse(path.read_bytes(), 0)
    assert [k for k in out if k.startswith("energy:")] == ["energy:energy_b", "energy:energy_a",
                                                            "energy:energy_c"]


def test_a_key_only_some_frames_have_is_listed_and_nan_elsewhere(tmp_path):
    path = tmp_path / "gap.extxyz"
    path.write_text(
        "1\nProperties=species:S:1:pos:R:3 energy_x=1.0\nH 0 0 0\n"
        "1\nProperties=species:S:1:pos:R:3\nH 0 0 1\n"
        "1\nProperties=species:S:1:pos:R:3 energy_x=3.0\nH 0 0 2\n")
    assert XyzReader(str(path)).contents()["energies"] == ["energy_x"]
    out = XyzReader.parse(path.read_bytes(), 0)["energy:energy_x"]
    assert out[0] == 1.0 and np.isnan(out[1]) and out[2] == 3.0


def test_garbage_after_the_first_frame_does_not_stop_the_key_probe(tmp_path):
    path = tmp_path / "junk.extxyz"
    path.write_text("1\nProperties=species:S:1:pos:R:3 energy_x=1.0\nH 0 0 0\nnot a frame\n")
    assert XyzReader(str(path)).contents()["energies"] == ["energy_x"]

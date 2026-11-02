"""A load reads its file once (read-once plan 2026-11-02, ADR 0076).

Before: the ASE loader parsed the whole file 8 times (frame loop, uniformity
sample, cells, fingerprint, elements, positions, forces, energies), and
AtomsList parsed 300,000 frames when created. After: AtomsList reads nothing
until asked, the fast reader fills every array in one pass, and the
fingerprints are the ones today's code gives (GOLDEN, computed 2026-11-02 at
01be683 with the old loader).
"""
import logging
import types

import ase.io
import numpy as np
import pytest

from ffast.core.data_types import AtomsList
from ffast.core.loading_coordinator import LoadingCoordinator
from ffast.io import extxyz_arrays as xa
from ffast.io import frame_index as fi
from ffast.loaders.ase import aseDatasetLoader, read_atom_field, read_frame_field
from tests.ffast.read_once_fixtures import FILES, REPO_FILES

GOLDEN = {
    ("uniform.extxyz", None, None): "e49d3b353515c35ba73346342e8faa42",
    ("variable.extxyz", None, None): "2b3b68f068afa8abfe5ca002eb139b23",   # picks energy_mace/forces_mace
    ("variable.extxyz", "energy", "forces"): "0a8df1815fa50e7387f87abfb2f44cb6",  # int64 energies
    ("variable.extxyz", "", ""): "0a8df1815fa50e7387f87abfb2f44cb6",
    ("columns_change.extxyz", None, None): "037b94e52d74f636412b42677fb20101",
    ("plain.xyz", None, None): "94a08854db1569325cd84538a091a415",
}
REPO_GOLDEN = {
    "dataset.xyz": "b48660a99439405f8ad99bee9069dba9",
    "prediction.xyz": "7e87cb89e3e2d2851bdaa1eb81615591",
}


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(fi, "CACHE_DIR", tmp_path / "frame-index")
    fi.clear_memory_cache()
    for name, text in FILES.items():
        (tmp_path / name).write_text(text)
    yield tmp_path
    fi.clear_memory_cache()


@pytest.fixture
def chunk_loads(monkeypatch):
    """How many times AtomsList parsed a chunk of frames with ASE."""
    calls = []
    real = AtomsList.load_new_chunk

    def spy(self, start):
        calls.append(start)
        return real(self, start)

    monkeypatch.setattr(AtomsList, "load_new_chunk", spy)
    return calls


def _load(path, ek=None, fk=None, **kw):
    return aseDatasetLoader(str(path), atomsList=AtomsList(str(path)),
                            selected_energy_key=ek, selected_force_key=fk, **kw)


# ── AtomsList (step 2) ──────────────────────────────────────────────────────

def test_atoms_list_parses_nothing_when_made(files, chunk_loads):
    frames = AtomsList(str(files / "variable.extxyz"))
    assert len(frames) == 3
    assert chunk_loads == []
    assert frames.frame_index is not None and len(frames.frame_index) == 3
    assert frames.frame_arrays is None


def test_atoms_list_still_reads_on_request(files, chunk_loads):
    path = files / "variable.extxyz"
    expected = ase.io.read(path, index=":")
    frames = AtomsList(str(path))
    assert frames[1].positions.tolist() == expected[1].positions.tolist()
    fresh = AtomsList(str(path))
    assert [a.positions.tolist() for a in fresh[0:3:2]] == [expected[0].positions.tolist(),
                                                           expected[2].positions.tolist()]


# ── the loader (step 3) ─────────────────────────────────────────────────────

@pytest.mark.parametrize("name, ek, fk", list(GOLDEN))
def test_fingerprints_do_not_change(files, name, ek, fk):
    assert _load(files / name, ek, fk).getFingerprint() == GOLDEN[(name, ek, fk)]


@pytest.mark.parametrize("path", REPO_FILES)
def test_repo_example_fingerprints_do_not_change(path, tmp_path, monkeypatch):
    monkeypatch.setattr(fi, "CACHE_DIR", tmp_path)
    fi.clear_memory_cache()
    assert _load(path).getFingerprint() == REPO_GOLDEN[path.name]


@pytest.mark.parametrize("name", ["uniform.extxyz", "variable.extxyz"])
def test_a_fast_load_parses_no_chunk(files, chunk_loads, name):
    loader = _load(files / name)
    loader.getFingerprint()
    loader.getAllCoordinates()
    loader.getCoordinates(1)
    loader.getForces(2)
    loader.getEnergies(0)
    loader.getElements(1)
    loader.availableFieldKeys()
    assert chunk_loads == []


@pytest.mark.parametrize("name", ["columns_change.extxyz", "plain.xyz"])
def test_an_ase_load_parses_each_frame_once(files, chunk_loads, name):
    loader = _load(files / name)
    loader.getFingerprint()
    loader.getAllCoordinates()
    loader.getCoordinates(1)
    assert len(chunk_loads) == 1          # all 2-3 frames fit one chunk


def test_changed_columns_warn_and_load_with_ase(files, caplog):
    with caplog.at_level(logging.WARNING, logger="FFAST"):
        loader = _load(files / "columns_change.extxyz")
    warned = [r.getMessage() for r in caplog.records if "Reading it with ASE" in r.getMessage()]
    assert len(warned) == 1
    assert "Frame 2" in warned[0] and "magmoms" in warned[0]
    assert loader.getFingerprint() == GOLDEN[("columns_change.extxyz", None, None)]


def test_plain_xyz_loads_with_ase_without_warning(files, caplog):
    with caplog.at_level(logging.WARNING, logger="FFAST"):
        _load(files / "plain.xyz")
    assert not [r for r in caplog.records if "Reading it with ASE" in r.getMessage()]


def test_the_kind_follows_the_frames(files):
    assert not _load(files / "uniform.extxyz").isVariable
    assert _load(files / "variable.extxyz").isVariable


def test_fields_come_from_the_same_read(files):
    path = files / "variable.extxyz"
    loader = _load(path)
    frames = ase.io.read(path, index=":")
    assert loader.availableFieldKeys() == (["charge", "energy_mace", "gap"], ["q"])
    for key in ("charge", "gap", "energy_mace"):
        assert loader.getFrameField(key).tolist() == read_frame_field(frames, key).tolist()
    assert loader.getFrameField("gap", 1) == 2.5
    assert loader.getFrameField("gap", [0, 2]).tolist() == [1.25, 0.75]
    assert loader.getFrameField("energy") is None        # ASE moves it to the calculator
    assert loader.getFrameField("tag") is None           # text, not a number
    got, want = loader.getAtomField("q"), read_atom_field(frames, "q", variable=True)
    assert [g.tolist() for g in got] == [w.tolist() for w in want]
    assert loader.getAtomField("q", 1).tolist() == [0.1, 0.2, 0.3]


def test_the_lattice_is_one_array(files):
    loader = _load(files / "uniform.extxyz")
    assert loader.getLattice().shape == (3, 3, 3)
    assert np.asarray(loader.getLattice(0)).tolist() == [[5.0, 0.1, 0.0], [0.2, 6.0, 0.0], [0.3, 0.4, 7.0]]
    assert loader.getLattice([0, 2]).shape == (2, 3, 3)


def test_a_second_loader_on_the_same_frames_reads_nothing(files, monkeypatch):
    calls = []
    real = xa.read_frames
    monkeypatch.setattr(xa, "read_frames", lambda *a, **k: calls.append(1) or real(*a, **k))
    path = str(files / "variable.extxyz")
    frames = AtomsList(path)
    a = aseDatasetLoader(path, atomsList=frames)
    b = aseDatasetLoader(path, atomsList=frames, selected_energy_key="energy", selected_force_key="forces")
    assert len(calls) == 1
    assert a.getFingerprint() == GOLDEN[("variable.extxyz", None, None)]
    assert b.getFingerprint() == GOLDEN[("variable.extxyz", "energy", "forces")]


def test_a_plain_list_of_frames_loads_with_ase(files):
    path = files / "variable.extxyz"
    loader = aseDatasetLoader(str(path), atomsList=ase.io.read(path, index=":"))
    assert loader.getFingerprint() == GOLDEN[("variable.extxyz", None, None)]


# ── progress and workers (step 4) ───────────────────────────────────────────

def test_the_load_reports_frames_read(files):
    seen = []
    _load(files / "variable.extxyz", progress=lambda done, n: seen.append((done, n)))
    assert seen == [(3, 3)]


def test_workers_give_the_same_fingerprint(files, monkeypatch):
    monkeypatch.setattr(xa, "PARALLEL_MIN_FRAMES", 0)
    monkeypatch.setattr(xa, "MIN_BATCH", 1)
    monkeypatch.setattr(xa, "MAX_BATCH", 1)
    monkeypatch.setenv("FFAST_READ_WORKERS", "2")
    loader = _load(files / "variable.extxyz", "energy", "forces")
    assert loader.getFingerprint() == GOLDEN[("variable.extxyz", "energy", "forces")]


def _smart_loader():
    from ffast.plugins.loaders import ase as plugin
    holder = {}
    plugin.loadData(types.SimpleNamespace(initialiseDatasetType=lambda obj: holder.setdefault("x", obj)))
    return holder["x"]


def test_the_plugin_passes_progress(files):
    seen = []
    _smart_loader()(str(files / "variable.extxyz"), show_dialog=False, slice_num=0,
                    progress=lambda done, n: seen.append((done, n)))
    assert seen == [(3, 3)]


def test_the_coordinator_reports_reading_on_the_task():
    events = []
    env = types.SimpleNamespace(datasets=types.SimpleNamespace(slice_numbers={}),
                                eventPush=lambda *a, **k: events.append((a, k)))
    LoadingCoordinator(env)._readProgress("/x/shard_00.extxyz", 7)(900_000, 1_773_606)
    messages = [k["message"] for a, k in events if a[:2] == ("TASK_PROGRESS", 7)]
    assert messages == ["Reading shard_00.extxyz: 900,000 of 1,773,606 frames…"]

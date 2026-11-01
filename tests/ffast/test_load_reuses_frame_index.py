"""A load reuses the frame index (CONTEXT.md "Frame index").

Plan: docs/implementation/2026-11-01-frame-index.md, part 2.

Before: every ``ase.io.read(path, ...)`` on an .xyz file walked the whole file
to find its frames, and a load made several of them (count, count again, every
chunk). After: an .xyz/.extxyz file is opened by path only to scan it once; all
frames are read through the index. The spy below fails a test the moment
anything hands ASE the file's path.
"""
import types

import ase.io
import numpy as np
import pytest
from ase import Atoms

from ffast.core.data_types import AtomsList
from ffast.core.loading_coordinator import LoadingCoordinator
from ffast.io import frame_index as fi


def _frames(n):
    rng = np.random.default_rng(1)
    out = []
    for i in range(n):
        k = 3 + i % 4
        a = Atoms(["O"] + ["H"] * (k - 1), positions=rng.normal(size=(k, 3)))
        a.info["REF_energy"] = float(i)
        a.arrays["REF_forces"] = rng.normal(size=(k, 3))
        out.append(a)
    return out


@pytest.fixture
def xyz(tmp_path, monkeypatch):
    monkeypatch.setattr(fi, "CACHE_DIR", tmp_path / "frame-index")
    fi.clear_memory_cache()
    path = tmp_path / "d.extxyz"
    ase.io.write(path, _frames(23), format="extxyz")
    expected = ase.io.read(path, index=":")
    yield path, expected
    fi.clear_memory_cache()


@pytest.fixture
def no_path_reads(monkeypatch):
    """Fail if anything gives ASE a file path (str or Path) to read."""
    real = ase.io.read
    calls = []

    def spy(filename, *args, **kwargs):
        if isinstance(filename, (str, bytes)) or hasattr(filename, "__fspath__"):
            calls.append((str(filename), args, kwargs))
        return real(filename, *args, **kwargs)

    monkeypatch.setattr(ase.io, "read", spy)
    yield calls
    assert calls == [], f"ASE read a file by path: {calls}"


def _positions(frames):
    return [f.positions.tolist() for f in frames]


# ── AtomsList ───────────────────────────────────────────────────────────────

def test_atoms_list_counts_and_reads_through_the_index(xyz, no_path_reads):
    path, expected = xyz
    frames = AtomsList(str(path), atoms_chunk=5)
    assert len(frames) == 23
    assert _positions([frames[i] for i in range(23)]) == _positions(expected)
    assert _positions([frames[-1]]) == _positions(expected[-1:])
    assert _positions(frames[2:19:3]) == _positions(expected[2:19:3])
    assert _positions(list(frames)) == _positions(expected)


def test_atoms_list_jumps_back_and_forth_between_chunks(xyz, no_path_reads):
    path, expected = xyz
    frames = AtomsList(str(path), atoms_chunk=5)
    for i in (21, 0, 13, 4, 22, 5):
        assert frames[i].positions.tolist() == expected[i].positions.tolist()


def test_atoms_list_keeps_its_old_behaviour_for_other_formats(tmp_path):
    path = tmp_path / "d.traj"
    ase.io.write(path, _frames(7))
    frames = AtomsList(str(path), atoms_chunk=3)
    assert len(frames) == 7
    assert frames[6].positions.tolist() == ase.io.read(path, index=6).positions.tolist()


def test_the_static_count_comes_from_the_index_and_is_saved(xyz, no_path_reads):
    path, _ = xyz
    assert AtomsList.calc_dataset_length_static(str(path)) == 23
    assert fi.cache_file(path).exists()


def test_a_load_after_the_count_does_not_scan_again(xyz, monkeypatch):
    path, _ = xyz
    calls = []
    real = fi.scan
    monkeypatch.setattr(fi, "scan", lambda p, **kw: calls.append(p) or real(p, **kw))
    AtomsList.calc_dataset_length_static(str(path))
    AtomsList(str(path))
    assert len(calls) == 1


# ── the ASE loader plugin ───────────────────────────────────────────────────

def _smart_loader():
    from ffast.plugins.loaders import ase as plugin
    holder = {}
    plugin.loadData(types.SimpleNamespace(initialiseDatasetType=lambda obj: holder.setdefault("x", obj)))
    return holder["x"]


def test_the_ase_loader_reads_every_nth_frame_through_the_index(xyz, no_path_reads):
    path, expected = xyz
    loader, _ = _smart_loader()(str(path), show_dialog=False, slice_num=5)
    assert _positions(loader.atomsList) == _positions(expected[::5])


def test_the_ase_loader_reads_all_frames_lazily_through_the_index(xyz, no_path_reads):
    path, expected = xyz
    loader, _ = _smart_loader()(str(path), show_dialog=False, slice_num=0)
    assert len(loader.atomsList) == 23
    assert loader.atomsList[22].positions.tolist() == expected[22].positions.tolist()


# ── the Loading Coordinator ─────────────────────────────────────────────────

def _coordinator(slice_num):
    events = []
    env = types.SimpleNamespace(
        datasets=types.SimpleNamespace(slice_numbers={"ds": slice_num}),
        eventPush=lambda *a, **k: events.append((a, k)),
    )
    return LoadingCoordinator(env), events


def test_a_prediction_file_at_the_datasets_stride_reads_through_the_index(xyz, no_path_reads):
    path, expected = xyz
    coord, _ = _coordinator(4)
    frames = coord._readPredictionAtomsList(str(path), "ds")
    assert _positions(frames) == _positions(expected[::4])


def test_a_prediction_file_without_stride_is_lazy_through_the_index(xyz, no_path_reads):
    path, expected = xyz
    coord, _ = _coordinator(0)
    frames = coord._readPredictionAtomsList(str(path), "ds")
    assert len(frames) == 23
    assert frames[10].positions.tolist() == expected[10].positions.tolist()


def test_a_load_reports_indexing_progress_on_its_task(xyz, monkeypatch):
    path, _ = xyz
    monkeypatch.setattr(fi, "PROGRESS_EVERY", 10)
    coord, events = _coordinator(0)
    index = coord.prepareFrameIndex(str(path), taskID=7)
    assert len(index) == 23
    messages = [k["message"] for a, k in events if a[:2] == ("TASK_PROGRESS", 7)]
    assert messages == ["Indexing d.extxyz: 10 frames…", "Indexing d.extxyz: 20 frames…"]


def test_preparing_the_index_of_another_format_does_nothing(tmp_path):
    path = tmp_path / "d.traj"
    ase.io.write(path, _frames(3))
    coord, events = _coordinator(0)
    assert coord.prepareFrameIndex(str(path), taskID=7) is None
    assert events == []

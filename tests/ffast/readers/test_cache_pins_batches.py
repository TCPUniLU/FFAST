"""Batch caches are locked while open and share one eviction (ADR 0108)."""
import gc
import os
import subprocess
import sys

import pytest

from ffast.io import budget as resources
from ffast.io import cache_space, frame_cache, frame_index
from ffast.readers import chunks
from ffast.readers.xyz_reader import XyzReader
from tests.ffast.readers.test_memory_saving import write_xyz


@pytest.fixture
def roots(monkeypatch, tmp_path):
    owner = resources.Budget(ram_bytes=512 * 1024, parsed_bytes=2**30, result_bytes=8 * 2**20)
    owner.limit = 16 * 2**20
    monkeypatch.setattr(resources, "_budget", owner)
    monkeypatch.setenv("FFAST_RESULT_CACHE_DIR", str(tmp_path / "results"))
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "parsed")
    monkeypatch.setattr(frame_index, "CACHE_DIR", tmp_path / "index")
    yield tmp_path / "parsed"
    gc.collect()


def _scan(path):
    return chunks.read_arrays(XyzReader, path, memory_mode="saving", workers=1)


def _identity_of(path):
    return _scan(path).arrays.store.identity


def test_a_batch_store_open_in_another_process_is_not_evicted(roots, tmp_path):
    path = write_xyz(tmp_path / "held.xyz", n=300)
    other = write_xyz(tmp_path / "other.xyz", n=300)
    code = (
        "import sys\n"
        "from ffast.readers import chunks\n"
        "from ffast.readers.xyz_reader import XyzReader\n"
        "r = chunks.read_arrays(XyzReader, sys.argv[1], memory_mode='saving', workers=1)\n"
        "print('open', flush=True)\n"
        "sys.stdin.read()\n")
    env = dict(os.environ, FFAST_FRAME_ARRAYS_DIR=str(roots),
               FFAST_RESULT_CACHE_DIR=str(tmp_path / "results"),
               FFAST_FRAME_INDEX_DIR=str(tmp_path / "index"))
    child = subprocess.Popen([sys.executable, "-c", code, path], env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "open"
        _scan(other)
        gc.collect()
        held = {d.path: d for d in cache_space.datasets(roots)}[os.path.realpath(path)]
        assert held.open
        before = {p.name for p in (roots / "batches").iterdir()}
        # Nothing fits: the other dataset goes, the open one stays.
        cache_space.make_room(roots, 1, 1, saving=None)
        after = {p.name for p in (roots / "batches").iterdir()}
        assert {n for n in before if n.startswith(held.entries[0].key)} <= after
    finally:
        child.stdin.close()
        child.wait(timeout=60)


def test_reopening_a_dataset_moves_it_to_the_back_of_the_eviction_order(roots, tmp_path):
    a = write_xyz(tmp_path / "a.xyz", n=300)
    b = write_xyz(tmp_path / "b.xyz", n=300)
    ida, idb = _identity_of(a), _identity_of(b)
    gc.collect()
    folder = roots / "batches"
    os.utime(folder / f"{ida}.source.json", (1000, 1000))
    os.utime(folder / f"{idb}.source.json", (2000, 2000))   # a is the oldest
    _scan(a)                                                 # reopened: now the newest
    gc.collect()
    mine = lambda ident: {p.name for p in folder.iterdir() if p.name.startswith(ident)}
    before_a, before_b = mine(ida), mine(idb)
    cache_space.make_room(roots, 1, cache_space.used(roots), saving=None)
    assert mine(ida) == before_a
    assert mine(idb) < before_b


def test_a_save_does_not_trim_its_own_batches_but_the_manifest_may(roots, tmp_path):
    path = write_xyz(tmp_path / "own.xyz", n=300)
    store = _scan(path).arrays.store
    folder = store.folder
    atoms = sorted(folder.glob(f"{store.identity}-[0-9]*-[0-9]*.npz"))
    assert atoms
    records = {p.name for p in folder.iterdir() if p not in atoms}
    used = cache_space.used(roots)
    # Nothing but this dataset is cached and it does not fit: a window save
    # (the scan is not finished) leaves its own batches alone.
    assert not cache_space.make_room(roots, 10, used, saving=os.path.realpath(path))
    assert all(p.exists() for p in atoms)
    # The manifest of a finished scan may drop atom batches, never records.
    assert cache_space.make_room(roots, 10, used, saving=os.path.realpath(path),
                                 trim_own_atom_batches=True)
    assert not all(p.exists() for p in atoms)
    assert records <= {p.name for p in folder.iterdir()}

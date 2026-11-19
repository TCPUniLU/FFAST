"""One module finds, protects and evicts parsed cache copies (ADR 0108)."""
import json
import os
import subprocess
import sys

import pytest

from ffast.io import cache_space as cs

MB = 1000


def _array(root, key, path, size, age=0):
    d = root / key
    d.mkdir(parents=True)
    (d / "positions.npy").write_bytes(b"x" * size)
    meta = d / "meta.json"
    meta.write_text(json.dumps({"path": path}))
    t = 1_000_000 - age
    os.utime(meta, (t, t))
    return d


def _identity(n):
    return f"{n:064x}"


def _batch(root, n, path, atom=0, scalar=0, records=0, age=0):
    """A batch identity: per-atom batches, a scalar batch, a manifest."""
    folder = root / "batches"
    folder.mkdir(exist_ok=True)
    ident = _identity(n)
    (folder / f"{ident}.source.json").write_text(json.dumps({"path": path, "state": [1, 2, 3]}))
    t = 1_000_000 - age
    os.utime(folder / f"{ident}.source.json", (t, t))
    if atom:
        (folder / f"{ident}-0-10.npz").write_bytes(b"x" * atom)
    if scalar:
        (folder / f"{ident}--1--1.npz").write_bytes(b"x" * scalar)
    if records:
        (folder / f"{ident}.json").write_bytes(b"x" * records)
    return ident


def _names(root):
    return sorted(p.name for p in (root / "batches").iterdir()) if (root / "batches").exists() else []


def test_oldest_unprotected_dataset_goes_first(tmp_path):
    _array(tmp_path, "a", "/data/old.xyz", 4 * MB, age=300)
    _array(tmp_path, "b", "/data/new.xyz", 4 * MB, age=100)
    assert cs.make_room(tmp_path, 4 * MB, 9 * MB)
    assert not (tmp_path / "a").exists()
    assert (tmp_path / "b").exists()


def test_a_pinned_dataset_survives(tmp_path):
    _array(tmp_path, "a", "/data/old.xyz", 4 * MB, age=300)
    _array(tmp_path, "b", "/data/new.xyz", 4 * MB, age=100)
    cs.write_pins(tmp_path, ["/data/old.xyz"])
    assert cs.make_room(tmp_path, 4 * MB, 9 * MB)
    assert (tmp_path / "a").exists()
    assert not (tmp_path / "b").exists()


def test_an_open_dataset_survives(tmp_path):
    _array(tmp_path, "a", "/data/old.xyz", 4 * MB, age=300)
    _array(tmp_path, "b", "/data/new.xyz", 4 * MB, age=100)
    handle = cs.hold_open(tmp_path / "a" / ".open", needs=tmp_path / "a" / "meta.json")
    assert handle is not None
    assert cs.make_room(tmp_path, 4 * MB, 9 * MB)
    assert (tmp_path / "a").exists()
    handle.close()
    assert not cs.is_open(tmp_path / "a" / ".open")


def test_an_open_dataset_held_by_another_process_survives(tmp_path):
    _array(tmp_path, "a", "/data/old.xyz", 4 * MB, age=300)
    _array(tmp_path, "b", "/data/new.xyz", 4 * MB, age=100)
    code = ("import fcntl, sys\n"
            "h = open(sys.argv[1], 'a+b')\n"
            "fcntl.flock(h, fcntl.LOCK_SH)\n"
            "print('locked', flush=True)\n"
            "sys.stdin.read()\n")
    child = subprocess.Popen([sys.executable, "-c", code, str(tmp_path / "a" / ".open")],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "locked"
        assert cs.is_open(tmp_path / "a" / ".open")
        assert cs.make_room(tmp_path, 4 * MB, 9 * MB)
        assert (tmp_path / "a").exists()
        with pytest.raises(cs.InUse):
            cs.delete_dataset(tmp_path, "/data/old.xyz")
    finally:
        child.stdin.close()
        child.wait(timeout=30)
    assert not cs.is_open(tmp_path / "a" / ".open")


def test_big_files_go_before_small_records_inside_one_dataset(tmp_path):
    ident = _batch(tmp_path, 1, "/data/a.xyz", atom=5 * MB, scalar=1 * MB, records=1 * MB)
    cs.make_room(tmp_path, 3 * MB, 8 * MB)
    names = _names(tmp_path)
    assert f"{ident}-0-10.npz" not in names
    assert f"{ident}--1--1.npz" in names
    assert f"{ident}.json" in names


def test_small_records_go_last_with_the_source_and_lock_files(tmp_path):
    ident = _batch(tmp_path, 1, "/data/a.xyz", atom=2 * MB, scalar=1 * MB, records=1 * MB)
    (tmp_path / "batches" / f"{ident}.open").write_bytes(b"")
    assert cs.make_room(tmp_path, 4 * MB, 4 * MB)
    assert _names(tmp_path) == []


def test_the_dataset_being_saved_is_not_trimmed(tmp_path):
    _batch(tmp_path, 1, "/data/a.xyz", atom=5 * MB, age=300)
    _array(tmp_path, "b", "/data/b.xyz", 4 * MB, age=100)
    assert cs.make_room(tmp_path, 4 * MB, 10 * MB, saving="/data/a.xyz")
    assert (tmp_path / "batches" / f"{_identity(1)}-0-10.npz").exists()
    assert not (tmp_path / "b").exists()


def test_only_protected_data_left_returns_false(tmp_path):
    _array(tmp_path, "a", "/data/a.xyz", 4 * MB)
    cs.write_pins(tmp_path, ["/data/a.xyz"])
    assert not cs.make_room(tmp_path, 4 * MB, 5 * MB)
    assert (tmp_path / "a").exists()


def test_pins_survive_a_rewrite(tmp_path):
    cs.write_pins(tmp_path, ["/b", "/a"])
    cs.write_pins(tmp_path, cs.read_pins(tmp_path) + ["/c", "/a"])
    assert cs.read_pins(tmp_path) == ["/a", "/b", "/c"]
    assert cs.read_pins(tmp_path / "missing") == []


def test_a_dataset_is_grouped_across_both_stores_by_path(tmp_path):
    _array(tmp_path, "a", "/data/x.xyz", 2 * MB)
    _batch(tmp_path, 1, "/data/x.xyz", atom=3 * MB)
    _batch(tmp_path, 2, "/data/y.xyz", atom=1 * MB)
    by_path = {d.path: d for d in cs.datasets(tmp_path)}
    assert set(by_path) == {"/data/x.xyz", "/data/y.xyz"}
    assert len(by_path["/data/x.xyz"].entries) == 2
    assert by_path["/data/x.xyz"].bytes > 5 * MB


def test_an_entry_with_no_known_path_is_its_own_dataset_and_never_pinned(tmp_path):
    _array(tmp_path, "a", None, 2 * MB)
    _array(tmp_path, "b", None, 2 * MB)
    cs.write_pins(tmp_path, ["None"])
    found = cs.datasets(tmp_path)
    assert len(found) == 2 and not any(d.pinned for d in found)


def test_delete_removes_every_copy_of_a_path(tmp_path):
    _array(tmp_path, "a", "/data/x.xyz", 2 * MB)
    _batch(tmp_path, 1, "/data/x.xyz", atom=3 * MB)
    _array(tmp_path, "b", "/data/y.xyz", 2 * MB)
    assert cs.delete_dataset(tmp_path, "/data/x.xyz") > 5 * MB
    assert not (tmp_path / "a").exists()
    assert _names(tmp_path) == []
    assert (tmp_path / "b").exists()


def test_results_and_temporary_files_are_not_entries(tmp_path):
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "r.npy").write_bytes(b"x" * MB)
    (tmp_path / ".k.1.tmp").mkdir()
    (tmp_path / "batches").mkdir()
    (tmp_path / "batches" / f"{_identity(1)}-1.tmp").write_bytes(b"x" * MB)
    assert cs.used(tmp_path) == 0

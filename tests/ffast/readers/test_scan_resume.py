"""A stopped one-pass scan resumes from its last committed window (ADR 0107, step 5)."""
import json
import os

import numpy as np
import pytest

from ffast.cache import fingerprint
from ffast.metrics import stop
from ffast.readers import text
from tests.ffast.readers.test_scan_naming import (  # noqa: F401
    Progress, folder, forget_ram, scan, write_xyz)
from tests.ffast.readers.test_scan_record import cancel_after

pytestmark = pytest.mark.skipif(not text.fast_parser_usable(), reason="Optional fast parser unavailable")

N, ATOMS = 3000, 40          # about 4 one-megabyte windows


@pytest.fixture
def parsed(monkeypatch):
    """The first frame of every window the parser is asked for. (The one-frame
    reads at the end of a scan, which settle the formula, are not windows.)"""
    firsts = []
    real = text._read_fast_arrays

    def spy(reader, path, contents, frames, *args, **kwargs):
        if len(frames) > 1:
            firsts.append(int(frames[0]))
        return real(reader, path, contents, frames, *args, **kwargs)

    monkeypatch.setattr(text, "_read_fast_arrays", spy)
    return firsts


def same(a, b):
    """Two stores hold the same dataset: counts, every digest and every value."""
    assert a.counts.tolist() == b.counts.tolist() and a.starts.tolist() == b.starts.tolist()
    assert a.ends.tolist() == b.ends.tolist() and a.ranges[-1][1] == b.ranges[-1][1]
    assert a.info.keys() == b.info.keys()
    for key in a.info:
        assert a.info[key]["digest"] == b.info[key]["digest"], key
        assert a.info[key]["float_digest"] == b.info[key]["float_digest"], key
        assert a.info[key]["has_nan"] == b.info[key]["has_nan"]
        np.testing.assert_array_equal(np.asarray(a.column(key)), np.asarray(b.column(key)))
    assert a.same_formula == b.same_formula and a.uniform == b.uniform
    assert a.element_set == b.element_set
    assert a.first_numbers.tolist() == b.first_numbers.tolist()


def uninterrupted(tmp_path, path, monkeypatch):
    """The reference: a clean scan of the same file, in a cache of its own."""
    from ffast.io import frame_cache

    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "reference")
    store = scan(path)
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "parsed")
    return store


def first_window_after(store, windows):
    return store.ranges[windows][0]


def test_a_reload_after_a_cancel_gives_the_same_dataset_and_skips_the_committed_windows(
        folder, tmp_path, monkeypatch, parsed):
    path = write_xyz(tmp_path / "a.xyz", n=N, atoms=ATOMS)
    reference = uninterrupted(tmp_path, path, monkeypatch)
    assert len(reference.ranges) >= 4
    parsed.clear()
    cancel_after(path, 2)
    assert parsed == [0, reference.ranges[1][0]]
    parsed.clear()
    reports = []

    class Reports(Progress):
        def bytes_read(self, done, total, frames):
            reports.append(done)
            super().bytes_read(done, total, frames)

    resumed = scan(path, Reports())
    assert parsed[0] == reference.ranges[2][0]                      # window 3 first
    assert 0 not in parsed and reference.ranges[1][0] not in parsed
    assert reports[0] == int(reference.ends[reference.ranges[1][1] - 1])   # starts where it left off
    assert reports == sorted(reports)
    same(resumed, reference)
    # A finished resume leaves no record behind.
    assert not list(folder.glob("*.progress.json")) and not list(folder.glob("*.bin"))
    assert not list(folder.glob("*.tail.npz"))


def test_a_scan_cancelled_twice_resumes_twice(folder, tmp_path, monkeypatch, parsed):
    path = write_xyz(tmp_path / "b.xyz", n=N, atoms=ATOMS)
    reference = uninterrupted(tmp_path, path, monkeypatch)
    cancel_after(path, 1)
    parsed.clear()
    cancel_after(path, 2)       # the resume's own first report, one more window, then stop again
    assert parsed == [reference.ranges[1][0]]
    parsed.clear()
    resumed = scan(path)
    assert parsed[0] == reference.ranges[2][0] and reference.ranges[1][0] not in parsed
    same(resumed, reference)


def test_the_kept_level_and_the_allowance_come_from_the_record_not_recomputed(
        folder, tmp_path):
    path = write_xyz(tmp_path / "c.xyz", n=N, atoms=ATOMS)
    cancel_after(path, 2)
    [progress] = folder.glob("*.progress.json")
    record = json.loads(progress.read_text())
    assert record["kept_level"] == 3 and record["allowance"] > 0
    # The allowance now would be twice as big; the resumed scan must keep the
    # one its first windows were written under.
    from ffast.io import budget as resources

    resources.budget().parsed_limit = 2 * record["allowance"]
    resumed = scan(path)
    assert resumed._scan_allowance == record["allowance"] and resumed._kept_level == 3
    assert len(resumed.ranges) > 2


@pytest.mark.parametrize("how", ["touch", "append"])
def test_a_changed_source_scans_from_the_start_and_the_old_batches_are_gone(
        folder, tmp_path, monkeypatch, parsed, how):
    path = write_xyz(tmp_path / "d.xyz", n=N, atoms=ATOMS)
    cancel_after(path, 2)
    old = {p.name for p in folder.iterdir()}
    assert any(n.endswith(".progress.json") for n in old)
    if how == "touch":
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    else:
        with open(path, "a") as f:
            f.write("1\nProperties=species:S:1:pos:R:3:forces:R:3 energy=1\nC 0 0 0 0 0 0\n")
    parsed.clear()
    store = scan(path)
    assert parsed[0] == 0
    # Nothing of the old identity is left: its record, its batches, its tail.
    new = {p.name for p in folder.iterdir()}
    assert not [n for n in new if n.startswith(tuple(o.split(".")[0].split("-")[0] for o in old
                                                    if o.endswith(".progress.json")))]
    assert all(n.startswith(store.identity) or n == ".lock" for n in new)


def test_a_missing_batch_means_a_scan_from_the_start(folder, tmp_path, monkeypatch, parsed, caplog):
    import logging

    path = write_xyz(tmp_path / "e.xyz", n=N, atoms=ATOMS)
    reference = uninterrupted(tmp_path, path, monkeypatch)
    cancel_after(path, 2)
    next(iter(sorted(folder.glob("*-0-*.npz")))).unlink()
    parsed.clear()
    with caplog.at_level(logging.INFO, logger="FFAST"):
        resumed = scan(path)
    assert parsed[0] == 0
    assert any("cannot be continued" in r.getMessage() for r in caplog.records)
    same(resumed, reference)


@pytest.mark.parametrize("damage", ["short frames", "short chunks", "bad json", "no json", "empty json",
                                    "wrong scheme", "missing tail"])
def test_a_damaged_record_means_a_scan_from_the_start_and_no_error(
        folder, tmp_path, monkeypatch, parsed, damage):
    path = write_xyz(tmp_path / "f.xyz", n=N, atoms=ATOMS)
    reference = uninterrupted(tmp_path, path, monkeypatch)
    cancel_after(path, 2)
    [progress] = folder.glob("*.progress.json")
    identity = progress.name[:-len(".progress.json")]
    frames, chunks_bin = folder / f"{identity}.frames.bin", folder / f"{identity}.chunks.bin"
    if damage == "short frames":
        os.truncate(frames, frames.stat().st_size - 16)
    elif damage == "short chunks":
        os.truncate(chunks_bin, chunks_bin.stat().st_size - 10)
    elif damage == "bad json":
        progress.write_text(progress.read_text()[:-20])
    elif damage == "no json":
        progress.unlink()
    elif damage == "empty json":
        progress.write_text("{}")
    elif damage == "wrong scheme":
        record = json.loads(progress.read_text())
        record["scheme"] = "ffast-chunks-v0"
        progress.write_text(json.dumps(record))
    else:
        next(folder.glob("*.tail.npz")).unlink()
    parsed.clear()
    resumed = scan(path)
    if damage != "no json":                     # without a record, nothing says what is valid
        assert parsed[0] == 0
    same(resumed, reference)


def test_longer_append_only_files_than_the_record_say_are_cut_back(folder, tmp_path, monkeypatch):
    path = write_xyz(tmp_path / "g.xyz", n=N, atoms=ATOMS)
    reference = uninterrupted(tmp_path, path, monkeypatch)
    cancel_after(path, 2)
    [progress] = folder.glob("*.progress.json")
    identity = progress.name[:-len(".progress.json")]
    # A crash after the append but before the record: extra bytes at the end.
    with open(folder / f"{identity}.frames.bin", "ab") as f:
        f.write(b"\x01" * 32)
    with open(folder / f"{identity}.chunks.bin", "ab") as f:
        f.write(b'["positions", "<f8", 999, "00"]\n')
    same(scan(path), reference)


def test_with_the_disk_cache_off_a_cancelled_scan_leaves_nothing_and_the_reload_starts_over(
        folder, tmp_path, parsed):
    path = write_xyz(tmp_path / "h.xyz", n=N, atoms=ATOMS)

    def uncached(progress=None):
        from ffast.readers.indexed import IndexedStore
        from ffast.readers.xyz_reader import XyzReader
        forget_ram()
        return IndexedStore(XyzReader, path, XyzReader(path).contents(), [], [], [], [],
                            workers=1, scan_source=True, progress=progress, use_cache=False)

    cancelled = [False]
    token = stop.running_under(lambda: cancelled[0])
    try:
        with pytest.raises(stop.Stopped):
            uncached(Progress(lambda n: cancelled.__setitem__(0, n >= 2)))
    finally:
        stop.reset(token)
    assert not folder.exists() or not list(folder.iterdir())
    parsed.clear()
    uncached()
    assert parsed[0] == 0
    assert not folder.exists() or not list(folder.iterdir())


@pytest.mark.parametrize("fraction", [0.5, 1.0, 1.3], ids=["inside", "on the boundary", "past it"])
def test_a_chunk_boundary_inside_or_on_the_edge_of_the_last_committed_window(
        folder, tmp_path, monkeypatch, fraction):
    path = write_xyz(tmp_path / "i.xyz", n=N, atoms=ATOMS)
    window_atoms = int(scan(path).offsets[scan(path).ranges[0][1]])
    # Chunks of about a window's atoms: the committed windows end in the middle of a chunk,
    # exactly at the end of one, or after the end of one.
    monkeypatch.setattr(fingerprint, "CHUNK_ROWS", int(window_atoms * fraction))
    for p in folder.iterdir():
        p.unlink()
    reference = uninterrupted(tmp_path, path, monkeypatch)
    cancel_after(path, 2)
    assert list(folder.glob("*.progress.json"))
    same(scan(path), reference)


def test_a_reload_through_the_loader_resumes_too(folder, tmp_path, monkeypatch, parsed):
    from ffast.io import frame_index
    from ffast.readers import chunks
    from ffast.readers.xyz_reader import XyzReader

    path = write_xyz(tmp_path / "j.xyz", n=N, atoms=ATOMS)
    reference = uninterrupted(tmp_path, path, monkeypatch)
    monkeypatch.setattr(frame_index, "CACHE_DIR", tmp_path / "index")
    cancel_after(path, 2)
    parsed.clear()
    loaded = chunks.read_arrays(XyzReader, path, memory_mode="saving", workers=1)
    assert 0 not in parsed
    same(loaded.arrays.store, reference)
    # The finished scan saved a frame index of all frames, not only the resumed ones.
    index = frame_index.load_cached(path)
    assert len(index) == N and index.starts.tolist() == reference.starts.tolist() + [int(reference.ends[-1])]


def test_a_handover_to_the_python_scanner_deletes_the_record(folder, tmp_path, monkeypatch):
    from ffast.io import frame_index
    from ffast.io.extxyz_arrays import NotFast

    path = write_xyz(tmp_path / "k.xyz", n=N, atoms=ATOMS)
    cancel_after(path, 2)
    assert list(folder.glob("*.progress.json"))
    monkeypatch.setattr(frame_index, "_fast_finder", lambda: None)
    with pytest.raises(NotFast):
        scan(path)
    assert not list(folder.glob("*.progress.json")) and not list(folder.glob("*.bin"))

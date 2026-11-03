"""The frame index (CONTEXT.md "Frame index").

Plan: docs/implementation/2026-11-01-frame-index.md, part 1.

One pass over an .xyz/.extxyz file finds where every frame starts. These tests
pin that the index agrees with ASE frame for frame, says where a broken file
breaks, is saved once and reused, and is built once however many callers ask.
Every file here is written by the test, so nothing depends on local data.
"""
import os
import threading
import time

import ase.io
import numpy as np
import pytest
from ase import Atoms
from ase.calculators.singlepoint import SinglePointCalculator

from ffast.io import frame_index as fi


# ── helpers ─────────────────────────────────────────────────────────────────

def _frames(n, *, variable=True, periodic=False, calculator=False, seed=0):
    """n frames of 3..7 atoms (or always 3), with an energy key, a force
    column and a string key, as real extxyz files carry them."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        k = 3 + (i % 5 if variable else 0)
        a = Atoms(
            ["C"] + ["H"] * (k - 1),
            positions=rng.normal(size=(k, 3)),
            cell=np.eye(3) * 10 if periodic else None,
            pbc=periodic,
        )
        a.info["REF_energy"] = float(rng.normal())
        a.info["config_type"] = f"c{i}"
        a.arrays["REF_forces"] = rng.normal(size=(k, 3))
        if calculator:
            a.calc = SinglePointCalculator(
                a, energy=float(rng.normal()), forces=rng.normal(size=(k, 3)))
        out.append(a)
    return out


def _write(tmp_path, name="d.extxyz", n=17, **kw):
    path = tmp_path / name
    ase.io.write(path, _frames(n, **kw), format="extxyz")
    return path


def _same(x, y):
    """Two Atoms read from the same text are equal in everything ASE keeps."""
    assert x.get_chemical_symbols() == y.get_chemical_symbols()
    assert np.array_equal(x.positions, y.positions)
    assert np.array_equal(x.cell.array, y.cell.array)
    assert np.array_equal(x.pbc, y.pbc)
    assert sorted(x.arrays) == sorted(y.arrays)
    for key in x.arrays:
        assert np.array_equal(x.arrays[key], y.arrays[key])
    assert repr(sorted(x.info.items())) == repr(sorted(y.info.items()))
    assert (x.calc is None) == (y.calc is None)
    if x.calc is not None:
        assert repr(x.calc.results) == repr(y.calc.results)


def _same_all(xs, ys):
    assert len(xs) == len(ys)
    for x, y in zip(xs, ys):
        _same(x, y)


@pytest.fixture(autouse=True)
def _own_cache(tmp_path, monkeypatch):
    """Each test gets an empty cache folder and an empty memory cache."""
    monkeypatch.setattr(fi, "CACHE_DIR", tmp_path / "frame-index")
    fi.clear_memory_cache()
    yield
    fi.clear_memory_cache()


# ── which files ─────────────────────────────────────────────────────────────

def test_only_xyz_and_extxyz_files_are_indexed():
    assert fi.is_indexable("a.xyz")
    assert fi.is_indexable("/x/b.EXTXYZ")
    assert not fi.is_indexable("c.traj")
    assert not fi.is_indexable("d.db")
    assert not fi.is_indexable("e.npz")


# ── the scan agrees with ASE ────────────────────────────────────────────────

@pytest.mark.parametrize("kw", [
    {"variable": True},
    {"variable": False},
    {"variable": True, "periodic": True},
    {"variable": False, "calculator": True},
], ids=["variable", "uniform", "periodic", "calculator"])
def test_frame_count_is_the_one_ase_finds(tmp_path, kw):
    path = _write(tmp_path, **kw)
    index = fi.scan(path)
    assert len(index) == len(ase.io.read(path, index=":")) == 17


def test_the_index_remembers_the_file_it_was_made_from(tmp_path):
    path = _write(tmp_path)
    index = fi.scan(path)
    st = os.stat(path)
    assert index.path == str(path)
    assert index.size == st.st_size
    assert index.mtime_ns == st.st_mtime_ns


def test_frames_read_through_the_index_are_the_frames_ase_reads(tmp_path):
    path = _write(tmp_path, calculator=True, periodic=True)
    index = fi.scan(path)
    _same_all(index.read(3, 11), ase.io.read(path, index=slice(3, 11)))
    _same_all(index.read(0, None, 4), ase.io.read(path, index=slice(0, None, 4)))
    _same_all(index.read(), ase.io.read(path, index=":"))
    _same(index.read_one(16), ase.io.read(path, index=16))
    _same(index.read_one(-1), ase.io.read(path, index=-1))


def test_reading_past_the_end_is_clipped_like_a_python_slice(tmp_path):
    path = _write(tmp_path)
    index = fi.scan(path)
    assert len(index.read(15, 40)) == 2
    assert index.read(20, 30) == []
    with pytest.raises(IndexError):
        index.read_one(17)


def test_a_long_read_is_parsed_in_batches_with_the_same_result(tmp_path, monkeypatch):
    path = _write(tmp_path)
    monkeypatch.setattr(fi, "READ_BATCH", 3)
    index = fi.scan(path)
    _same_all(index.read(), ase.io.read(path, index=":"))
    _same_all(index.read(1, None, 2), ase.io.read(path, index=slice(1, None, 2)))


def test_windows_line_endings_trailing_blank_lines_and_no_final_newline(tmp_path):
    src = _write(tmp_path).read_bytes()
    cases = {
        "crlf.xyz": src.replace(b"\n", b"\r\n"),
        "trailing.xyz": src + b"\n\n  \n",
        "no_newline.xyz": src.rstrip(b"\n"),
    }
    for name, data in cases.items():
        path = tmp_path / name
        path.write_bytes(data)
        index = fi.scan(path)
        assert len(index) == len(ase.io.read(path, index=":")) == 17, name
        _same(index.read_one(-1), ase.io.read(path, index=-1))


# ── a broken file says where it breaks ──────────────────────────────────────

def _lines(path):
    return path.read_bytes().split(b"\n")


def test_a_bad_atom_count_names_the_frame_and_the_line(tmp_path):
    path = _write(tmp_path, variable=False)          # 3 atoms: 5 lines a frame
    lines = _lines(path)
    lines[10] = b"3l"                                # frame 3's atom-count line
    path.write_bytes(b"\n".join(lines))
    with pytest.raises(fi.FrameIndexError) as err:
        fi.scan(path)
    text = str(err.value)
    assert text.startswith("Frame 3 (line 11) is not a valid frame")
    assert "expected an atom count" in text
    assert "'3l'" in text


def test_a_last_frame_cut_short_says_so(tmp_path):
    path = _write(tmp_path, variable=False, n=5)     # 25 lines
    lines = _lines(path)
    path.write_bytes(b"\n".join(lines[:23]) + b"\n")  # frame 5 keeps 1 of 3 atoms
    with pytest.raises(fi.FrameIndexError) as err:
        fi.scan(path)
    text = str(err.value)
    assert text.startswith("Frame 5 (line 21) ends early")
    assert "3 atoms" in text


def test_a_blank_line_between_frames_is_a_broken_file(tmp_path):
    """ASE quietly stops at the blank line and drops every later frame; the
    index says where the file breaks instead (grilling 2026-11-01, Q11)."""
    path = _write(tmp_path, variable=False)
    lines = _lines(path)
    path.write_bytes(b"\n".join(lines[:15] + [b""] + lines[15:]))
    assert len(ase.io.read(path, index=":")) == 3
    with pytest.raises(fi.FrameIndexError) as err:
        fi.scan(path)
    assert str(err.value).startswith("Frame 4 (line 16) is not a valid frame")


def test_a_broken_file_saves_no_index(tmp_path):
    path = _write(tmp_path, variable=False)
    lines = _lines(path)
    lines[10] = b"x"
    path.write_bytes(b"\n".join(lines))
    with pytest.raises(fi.FrameIndexError):
        fi.get_frame_index(path)
    assert not fi.cache_file(path).exists()


# ── progress and stopping ───────────────────────────────────────────────────

def test_the_scan_reports_the_count_as_it_goes(tmp_path, monkeypatch):
    path = _write(tmp_path)
    monkeypatch.setattr(fi, "PROGRESS_EVERY", 4)
    seen = []
    fi.scan(path, progress=seen.append)
    assert seen == [4, 8, 12, 16]


def test_the_scan_stops_when_asked(tmp_path, monkeypatch):
    path = _write(tmp_path)
    monkeypatch.setattr(fi, "PROGRESS_EVERY", 4)
    seen = []
    with pytest.raises(fi.FrameIndexCancelled):
        fi.scan(path, progress=seen.append, should_stop=lambda: len(seen) >= 2)
    assert seen == [4, 8]


# ── the first frame alone ───────────────────────────────────────────────────

def test_the_first_frame_is_read_without_reading_the_rest(tmp_path):
    """Frame 0 followed by garbage: ASE's own read of frame 0 walks the whole
    file and fails; reading the first frame alone must not."""
    path = _write(tmp_path, n=1, calculator=True)
    good = ase.io.read(path, index=0)
    path.write_bytes(path.read_bytes() + b"not a frame\n")
    with pytest.raises(Exception):
        ase.io.read(path, index=0)
    _same(fi.read_first_frame(path), good)


def test_the_first_frame_of_another_format_comes_from_ase(tmp_path):
    path = tmp_path / "d.traj"
    ase.io.write(path, _frames(3))
    _same(fi.read_first_frame(path), ase.io.read(path, index=0))


# ── saved and reused ────────────────────────────────────────────────────────

class _CountScans:
    def __init__(self, monkeypatch, delay=0.0):
        self.calls = 0
        self._real = fi.scan
        self._delay = delay
        monkeypatch.setattr(fi, "scan", self)

    def __call__(self, path, **kw):
        self.calls += 1
        time.sleep(self._delay)
        return self._real(path, **kw)


def test_the_index_is_saved_and_read_back_instead_of_scanning(tmp_path, monkeypatch):
    path = _write(tmp_path)
    scans = _CountScans(monkeypatch)
    first = fi.get_frame_index(path)
    assert scans.calls == 1
    assert fi.cache_file(path).exists()
    assert fi.cache_file(path).parent == tmp_path / "frame-index"
    fi.clear_memory_cache()                          # as a new server would
    again = fi.get_frame_index(path)
    assert scans.calls == 1
    assert np.array_equal(again.starts, first.starts)
    assert len(again) == 17


def test_a_changed_file_is_scanned_again(tmp_path, monkeypatch):
    path = _write(tmp_path)
    scans = _CountScans(monkeypatch)
    fi.get_frame_index(path)
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    fi.get_frame_index(path)
    assert scans.calls == 2
    ase.io.write(path, _frames(5), format="extxyz")  # new size too
    assert len(fi.get_frame_index(path)) == 5
    assert scans.calls == 3


def test_an_index_that_cannot_be_saved_still_works(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    monkeypatch.setattr(fi, "CACHE_DIR", blocker)    # mkdir under a file fails
    path = _write(tmp_path)
    assert len(fi.get_frame_index(path)) == 17


def test_a_damaged_saved_index_is_ignored(tmp_path):
    path = _write(tmp_path)
    fi.get_frame_index(path)
    fi.cache_file(path).write_bytes(b"garbage")
    fi.clear_memory_cache()
    assert len(fi.get_frame_index(path)) == 17


# ── one scan, however many ask ──────────────────────────────────────────────

def test_two_callers_at_once_share_one_scan(tmp_path, monkeypatch):
    path = _write(tmp_path)
    scans = _CountScans(monkeypatch, delay=0.3)
    results = []
    threads = [threading.Thread(target=lambda: results.append(len(fi.get_frame_index(path))))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert results == [17, 17]
    assert scans.calls == 1


def _slow_scan(monkeypatch, *, seconds=1.0):
    """A scan that waits up to ``seconds``, asking should_stop as it goes.
    Records whether it was stopped."""
    real = fi.scan
    state = {"stopped": False, "started": threading.Event()}

    def slow(path, *, progress=None, should_stop=None):
        state["started"].set()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if should_stop and should_stop():
                state["stopped"] = True
                raise fi.FrameIndexCancelled(path)
            time.sleep(0.01)
        return real(path, progress=progress, should_stop=should_stop)

    monkeypatch.setattr(fi, "scan", slow)
    return state


def test_a_caller_that_cancels_stops_waiting_but_not_the_others_scan(tmp_path, monkeypatch):
    """The dialog's count (cancelled when you pick another file) and a load
    of the same file share the scan; the load still gets its index."""
    path = _write(tmp_path)
    state = _slow_scan(monkeypatch)
    probe_cancel = threading.Event()
    out = {}

    def probe():
        try:
            fi.get_frame_index(path, cancel=probe_cancel)
        except fi.FrameIndexCancelled:
            out["probe"] = "cancelled"

    def load():
        out["load"] = len(fi.get_frame_index(path))

    p = threading.Thread(target=probe)
    p.start()
    assert state["started"].wait(2)
    q = threading.Thread(target=load)
    q.start()
    time.sleep(0.1)
    probe_cancel.set()
    p.join(0.5)
    assert not p.is_alive(), "a cancelled caller returns at once"
    assert out["probe"] == "cancelled"
    q.join(5)
    assert out["load"] == 17
    assert state["stopped"] is False


def test_the_scan_stops_when_everyone_waiting_has_cancelled(tmp_path, monkeypatch):
    path = _write(tmp_path)
    state = _slow_scan(monkeypatch)
    cancel = threading.Event()
    out = {}

    def probe():
        try:
            fi.get_frame_index(path, cancel=cancel)
        except fi.FrameIndexCancelled:
            out["probe"] = "cancelled"

    t = threading.Thread(target=probe)
    t.start()
    assert state["started"].wait(2)
    cancel.set()
    t.join(0.5)
    assert out["probe"] == "cancelled"
    deadline = time.monotonic() + 2
    while not state["stopped"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert state["stopped"] is True
    assert not fi.cache_file(path).exists()


def test_progress_reaches_every_caller_still_waiting(tmp_path, monkeypatch):
    path = _write(tmp_path)
    monkeypatch.setattr(fi, "PROGRESS_EVERY", 4)
    real = fi.scan

    def slow(path, *, progress=None, should_stop=None):
        time.sleep(0.3)                              # lets the second caller join
        return real(path, progress=progress, should_stop=should_stop)

    monkeypatch.setattr(fi, "scan", slow)
    a, b = [], []
    threads = [threading.Thread(target=fi.get_frame_index, args=(path,), kwargs={"progress": seen.append})
               for seen in (a, b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert a == b == [4, 8, 12, 16]


# ── strided reads ───────────────────────────────────────────────────────────

def test_every_nth_frame_of_an_xyz_file_comes_from_the_index(tmp_path, monkeypatch):
    path = _write(tmp_path, calculator=True)
    expected = ase.io.read(path, index=slice(0, None, 5))
    scans = _CountScans(monkeypatch)
    _same_all(fi.read_every(path, 5), expected)
    assert scans.calls == 1


def test_every_nth_frame_of_another_format_comes_from_ase(tmp_path):
    path = tmp_path / "d.traj"
    ase.io.write(path, _frames(7))
    _same_all(fi.read_every(path, 3), ase.io.read(path, index=slice(0, None, 3)))


# ── atom counts (ADR 0083) ──────────────────────────────────────────────────

def test_the_scan_keeps_every_frames_atom_count(tmp_path):
    path = _write(tmp_path)
    assert fi.scan(path).atom_counts().tolist() == [len(a) for a in ase.io.read(path, index=":")]


def test_an_index_saved_without_counts_is_scanned_again(tmp_path, monkeypatch):
    path = _write(tmp_path)
    full = fi.scan(path)
    fi.save_cached(fi.FrameIndex(path, full.starts, full.size, full.mtime_ns))   # as saved before counts
    assert fi.load_cached(path) is None
    scans = _CountScans(monkeypatch)
    assert np.array_equal(fi.get_frame_index(path).counts, full.counts)
    assert scans.calls == 1
    assert np.array_equal(fi.load_cached(path).counts, full.counts)


def test_an_index_without_counts_reads_them_from_the_file(tmp_path):
    path = _write(tmp_path)
    full = fi.scan(path)
    old = fi.FrameIndex(path, full.starts, full.size, full.mtime_ns)
    assert np.array_equal(old.atom_counts(), full.counts)
    assert np.array_equal(old.every(3).atom_counts(), full.counts[::3])


def test_counts_of_a_file_with_windows_line_endings(tmp_path):
    path = _write(tmp_path)
    p = tmp_path / "crlf.extxyz"
    p.write_bytes(open(path, "rb").read().replace(b"\n", b"\r\n"))
    index = fi.scan(str(p))
    assert np.array_equal(fi.FrameIndex(str(p), index.starts, index.size, index.mtime_ns).atom_counts(),
                          index.counts)

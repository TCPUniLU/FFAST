"""A whole-file load of an .xyz file reads the text once (2026-11-21).

Before, a load in memory found every frame first (one read of the file) and
parsed them after (a second read): on MeluXina's Lustre the 90 GB file spent
45 s finding and 55 s parsing. Now frames are found and parsed window by
window while the window is still in memory; whether the arrays fit is
estimated from the first window."""
import gc

import numpy as np
import pytest

from ffast.io import frame_cache, frame_index
from ffast.readers import chunks, text
from ffast.readers.indexed import IndexedArrays
from ffast.readers.xyz_reader import XyzReader

pytestmark = pytest.mark.skipif(not text.fast_parser_usable(), reason="needs the fast parser")

EXAMPLES = [
    "examples/data/fixed-sized molecular/reference_plus_noise.xyz",
    "examples/data/variable-sized molecular/dataset.xyz",
    "examples/data/variable-sized periodic/mptrj_sampled_over_200.extxyz",
]


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(frame_index, "CACHE_DIR", tmp_path / "index")
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "parsed")
    _forget()
    yield
    _forget()


def _forget():
    text.clear_memory_cache()
    frame_index.clear_memory_cache()
    gc.collect()


def write_xyz(path, n=600, atoms=None):
    """Frames of 3..9 atoms (or ``atoms``), forces, an energy, and a per-atom
    charge only on even frames."""
    with open(path, "w") as f:
        for i in range(n):
            k = atoms or 3 + i % 7
            charge = ":charge:R:1" if i % 2 == 0 else ""
            f.write(f"{k}\nProperties=species:S:1:pos:R:3:forces:R:3{charge} energy={i * 0.5}\n")
            for j in range(k):
                extra = f" {j * 0.01}" if i % 2 == 0 else ""
                f.write(f"{'CHO'[j % 3]} {i}.125 {j}.25 -1.5 0.1 0.2 {i}.3{extra}\n")
    return str(path)


def two_pass(path):
    """The arrays as the earlier path makes them: frame index first."""
    _forget()
    frame_index.get_frame_index(path)
    assert not text.can_scan_and_read(XyzReader, path)
    loaded = chunks.read_arrays(XyzReader, path, use_cache=False)
    arrays = {k: np.array(v) for k, v in loaded.arrays.items()}
    _forget()
    for f in frame_index.CACHE_DIR.glob("*"):
        f.unlink()
    return arrays


def assert_same(want, got):
    assert sorted(want) == sorted(got)
    for key in want:
        np.testing.assert_array_equal(np.asarray(got[key]), want[key], err_msg=key)
        assert np.asarray(got[key]).dtype == want[key].dtype, key


@pytest.fixture
def small_windows(monkeypatch):
    """Many windows, and a first capacity guess far too small, so the arrays grow."""
    monkeypatch.setattr(text, "WHOLE_WINDOW_BYTES", 16 * 1024)
    monkeypatch.setattr(text, "WHOLE_FIRST_GUESS", 0.2)


@pytest.fixture
def no_index_pass(monkeypatch):
    def refuse(*a, **kw):
        raise AssertionError("a whole-file load must not run a separate frame-finding pass")
    monkeypatch.setattr(frame_index, "get_frame_index", refuse)


def test_a_load_without_an_index_goes_straight_to_one_read(tmp_path):
    path = write_xyz(tmp_path / "a.xyz")
    assert text.can_scan_and_read(XyzReader, path)                 # the coordinator skips its pass


def test_a_load_with_an_index_on_disk_keeps_the_earlier_path(tmp_path):
    path = write_xyz(tmp_path / "a.xyz")
    frame_index.get_frame_index(path)
    assert not text.can_scan_and_read(XyzReader, path)


def test_the_one_read_gives_the_arrays_of_the_two_pass_load(tmp_path, small_windows, monkeypatch):
    path = write_xyz(tmp_path / "a.xyz")
    want = two_pass(path)
    monkeypatch.setattr(frame_index, "get_frame_index",
                        lambda *a, **k: pytest.fail("separate frame-finding pass"))
    loaded = chunks.read_arrays(XyzReader, path, use_cache=False)
    assert not isinstance(loaded.arrays, IndexedArrays)
    assert loaded.n_file_frames == 600 and list(loaded.frames) == list(range(600))
    assert_same(want, loaded.arrays)


@pytest.mark.parametrize("path", EXAMPLES)
def test_the_example_files_read_alike(path, small_windows):
    want = two_pass(path)
    loaded = chunks.read_arrays(XyzReader, path, use_cache=False)
    assert_same(want, loaded.arrays)


def test_the_file_is_read_once(tmp_path, monkeypatch, no_index_pass):
    """Every window is found once and parsed once: the finder sees each byte once."""
    monkeypatch.setattr(text, "WHOLE_WINDOW_BYTES", 32 * 1024)
    path = write_xyz(tmp_path / "a.xyz")
    native = frame_index._fast_finder()
    seen = []
    from types import SimpleNamespace

    def find(p, pos, span, threads):
        out = native.find_frames(p, pos, span, threads)
        seen.append((pos, out[2]))
        return out
    monkeypatch.setattr(frame_index, "_fast_finder", lambda: SimpleNamespace(
        find_frames=find, FIND_MORE=native.FIND_MORE, FIND_END=native.FIND_END,
        FIND_PYTHON=native.FIND_PYTHON))
    chunks.read_arrays(XyzReader, path, use_cache=False)
    assert len(seen) > 3
    assert all(a[1] == b[0] for a, b in zip(seen, seen[1:]))     # windows follow each other


def test_the_frame_index_is_kept_for_the_next_load(tmp_path):
    path = write_xyz(tmp_path / "a.xyz")
    loaded = chunks.read_arrays(XyzReader, path, use_cache=False)
    _forget()
    assert frame_index.load_cached(path) is not None
    starts, counts = text.get_table(XyzReader, path)
    np.testing.assert_array_equal(counts, loaded.arrays["n_atoms"])


def test_a_reopen_opens_the_kept_copy(tmp_path, monkeypatch):
    path = write_xyz(tmp_path / "a.xyz")
    first = chunks.read_arrays(XyzReader, path)
    want = {k: np.array(v) for k, v in first.arrays.items()}
    del first
    _forget()
    monkeypatch.setattr(XyzReader, "parse_into",
                        classmethod(lambda *a, **k: pytest.fail("parsed again")))
    again = chunks.read_arrays(XyzReader, path)
    assert_same(want, again.arrays)


def test_a_file_whose_estimate_does_not_fit_goes_to_the_memory_saving_path(tmp_path, monkeypatch):
    monkeypatch.setattr(text, "WHOLE_MARGIN", 1e9)
    path = write_xyz(tmp_path / "a.xyz")
    loaded = chunks.read_arrays(XyzReader, path, use_cache=False)
    assert isinstance(loaded.arrays, IndexedArrays)
    assert len(loaded.frames) == 600


def test_saving_mode_still_takes_the_memory_saving_path(tmp_path):
    path = write_xyz(tmp_path / "a.xyz")
    loaded = chunks.read_arrays(XyzReader, path, use_cache=False, memory_mode="saving")
    assert isinstance(loaded.arrays, IndexedArrays)

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
    monkeypatch.setattr(text, "WHOLE_ROOM", 0.2)


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


def _is_view_with_spare_room(array):
    array = np.asarray(array)
    return array.base is not None and array.base.shape[0] > array.shape[0]


def test_the_arrays_keep_their_spare_room_instead_of_being_copied(tmp_path, monkeypatch):
    """With a parser that writes NaN itself, the room left over is never
    written: the arrays are views of it, not 45 GB copied to cut it off."""
    assert XyzReader.fills_missing()
    monkeypatch.setattr(text._Growing, "_grow",
                        lambda *a: pytest.fail("a file as dense as its first window must not grow"))
    monkeypatch.setattr(text, "WHOLE_WINDOW_BYTES", 16 * 1024)
    path = write_xyz(tmp_path / "a.xyz")
    want = two_pass(path)
    loaded = chunks.read_arrays(XyzReader, path, use_cache=False)
    assert _is_view_with_spare_room(loaded.arrays["positions"])
    assert_same(want, loaded.arrays)


def test_a_parser_without_the_flag_still_gets_trimmed_copies(tmp_path, monkeypatch):
    """An older ffast_xyz pre-fills the room with NaN, so the room holds
    memory and is cut off by a copy, as before."""
    monkeypatch.setattr(XyzReader, "fills_missing", staticmethod(lambda: False))
    monkeypatch.setattr(text, "WHOLE_FIRST_GUESS", 2.0)           # much room left over
    path = write_xyz(tmp_path / "a.xyz")
    want = two_pass(path)
    loaded = chunks.read_arrays(XyzReader, path, use_cache=False)
    assert not _is_view_with_spare_room(loaded.arrays["positions"])
    assert_same(want, loaded.arrays)


def test_arrays_that_cannot_be_made_go_to_the_memory_saving_path(tmp_path, monkeypatch):
    def refuse(self, atoms, frames):
        raise MemoryError("Unable to allocate")
    monkeypatch.setattr(text._Growing, "room", refuse)
    path = write_xyz(tmp_path / "a.xyz")
    loaded = chunks.read_arrays(XyzReader, path, use_cache=False)
    assert isinstance(loaded.arrays, IndexedArrays)
    assert len(loaded.frames) == 600


def test_the_window_digests_equal_the_digests_of_the_finished_arrays(tmp_path, small_windows, monkeypatch):
    """The kept copy's digests are taken window by window while parsing, also
    when the arrays grow; each must equal the digest of the whole array, and
    the write must not hash those arrays again."""
    import json

    from ffast.cache import fingerprint

    hashed = []
    real = fingerprint.array_digest
    monkeypatch.setattr(fingerprint, "array_digest", lambda a: hashed.append(np.asarray(a).shape) or real(a))
    path = write_xyz(tmp_path / "a.xyz")
    loaded = chunks.read_arrays(XyzReader, path)
    arrays = loaded.arrays
    [meta] = list(frame_cache.CACHE_DIR.rglob("meta.json"))          # the kept copy
    digests = json.loads(meta.read_text())["digests"]
    for key in ("numbers", "positions", "forces:forces", "cell"):
        assert digests[key] == fingerprint._hash_array(np.asarray(arrays[key])), key
    big = {np.asarray(arrays[k]).shape for k in ("positions", "forces:forces", "cell")}
    assert not big & set(hashed), "the write hashed an array the window digests covered"


def test_a_failure_while_hashing_windows_only_moves_the_hashing_to_the_write(tmp_path, monkeypatch):
    import json

    from ffast.cache import fingerprint

    def broken(self, rows):
        raise RuntimeError("hash failed")
    monkeypatch.setattr(fingerprint.ChunkHasher, "update", broken)
    path = write_xyz(tmp_path / "a.xyz")
    loaded = chunks.read_arrays(XyzReader, path)
    [meta] = list(frame_cache.CACHE_DIR.rglob("meta.json"))
    digests = json.loads(meta.read_text())["digests"]
    monkeypatch.undo()
    assert digests["positions"] == fingerprint._hash_array(np.asarray(loaded.arrays["positions"]))


@pytest.mark.parametrize("path", EXAMPLES)
def test_every_kept_digest_of_an_example_file_equals_its_array(path, small_windows):
    import json

    from ffast.cache import fingerprint

    loaded = chunks.read_arrays(XyzReader, path)
    [meta] = list(frame_cache.CACHE_DIR.rglob("meta.json"))
    for key, digest in json.loads(meta.read_text())["digests"].items():
        assert digest == fingerprint._hash_array(np.asarray(loaded.arrays[key])), key

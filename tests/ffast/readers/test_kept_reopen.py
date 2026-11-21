"""A kept copy reopens with plain reads and its stored digests (ADR 0112,
2026-11-21). On MeluXina's Lustre a memory-mapped 23 GB copy made the reopen
slower than parsing the text: every first touch (the fingerprint hash, the
predictions, Basic Errors) read pages one fault at a time."""
import numpy as np
import pytest

from ffast.cache import fingerprint
from ffast.io import frame_cache, frame_index
from ffast.readers import chunks, text
from tests.ffast.readers.toy_reader import ToyTextReader, make_frames, write_toy


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(frame_index, "CACHE_DIR", tmp_path / "index")
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "arrays")
    text.clear_memory_cache()
    ToyTextReader.found, ToyTextReader.parsed = [], []
    yield
    text.clear_memory_cache()


@pytest.fixture
def hashed(monkeypatch):
    """Every array the fingerprint code actually hashes."""
    calls = []
    real = fingerprint._hash_array
    monkeypatch.setattr(fingerprint, "_hash_array", lambda a: calls.append(a.shape) or real(a))
    return calls


def _file(tmp_path, n=12, **kw):
    return write_toy(tmp_path / "a.toyt", make_frames(n, **kw))


def _reopen(path):
    text.clear_memory_cache()
    return chunks.read_arrays(ToyTextReader, path)


def test_the_first_load_keeps_its_arrays_in_memory_after_saving_them(tmp_path):
    loaded = chunks.read_arrays(ToyTextReader, _file(tmp_path))
    assert not any(isinstance(a, np.memmap) for a in loaded.arrays.values())
    assert not any(a.flags.writeable for a in loaded.arrays.values())   # as the kept copy


def test_a_reopen_reads_the_kept_arrays_into_memory(tmp_path):
    path = _file(tmp_path, variable=True)
    first = {k: np.array(v) for k, v in chunks.read_arrays(ToyTextReader, path).arrays.items()}
    n = len(ToyTextReader.parsed)
    again = _reopen(path).arrays
    assert len(ToyTextReader.parsed) == n                                # not parsed again
    assert not any(isinstance(a, np.memmap) for a in again.values())
    assert not any(a.flags.writeable for a in again.values())
    assert first.keys() == again.keys()
    for key in first:
        np.testing.assert_array_equal(first[key], again[key])


def test_a_reopen_takes_the_digests_from_the_kept_copy(tmp_path, hashed):
    path = _file(tmp_path, variable=True)
    first = chunks.read_arrays(ToyTextReader, path).arrays
    want = {k: fingerprint.array_digest(np.array(v)) for k, v in first.items()}
    again = _reopen(path).arrays
    hashed.clear()
    assert {k: fingerprint.array_digest(v) for k, v in again.items()} == want
    assert {k: fingerprint.array_digest(v.reshape(-1) if v.ndim == 1 else v)
            for k, v in again.items()} == want                           # a 1-D view too
    assert hashed == []


def test_the_first_load_needs_no_second_hash_for_its_fingerprint(tmp_path, hashed):
    arrays = chunks.read_arrays(ToyTextReader, _file(tmp_path)).arrays
    hashed.clear()
    fingerprint.array_digest(arrays["positions"])
    assert hashed == []


def test_a_read_only_view_of_writable_memory_is_hashed_every_time(hashed):
    base = np.arange(12.0)
    view = base.reshape(4, 3)
    view.flags.writeable = False
    before = fingerprint.array_digest(view)
    base[0] = 99.0                                       # the memory changes under the view
    assert fingerprint.array_digest(view) != before
    assert len(hashed) == 2


def test_a_writable_array_is_hashed_every_time(hashed):
    a = np.arange(12.0).reshape(4, 3)
    before = fingerprint.array_digest(a)
    a[0, 0] = 99.0
    assert fingerprint.array_digest(a) != before
    assert len(hashed) == 2


def test_a_remembered_digest_goes_with_its_array():
    import gc
    a = np.array(np.arange(30.0).reshape(10, 3))          # owns its memory
    a.flags.writeable = False
    fingerprint.array_digest(a)
    owner = id(a)
    assert owner in fingerprint._known
    del a
    gc.collect()
    assert owner not in fingerprint._known


def test_a_part_of_a_kept_array_is_hashed_not_given_the_whole_digest(tmp_path, hashed):
    arrays = chunks.read_arrays(ToyTextReader, _file(tmp_path)).arrays
    positions = arrays["positions"]
    hashed.clear()
    part = fingerprint.array_digest(positions[:3])
    assert part == fingerprint.array_digest(np.array(positions[:3]))
    assert part != fingerprint.array_digest(positions)
    assert len(hashed) == 2                                              # the slice, its copy


def test_a_copy_without_digests_still_opens_and_is_hashed(tmp_path, hashed):
    import json
    path = _file(tmp_path)
    chunks.read_arrays(ToyTextReader, path)
    for meta in (tmp_path / "arrays").glob("*/meta.json"):          # written before digests
        data = json.loads(meta.read_text())
        data.pop("digests", None)
        meta.write_text(json.dumps(data))
    again = _reopen(path).arrays
    hashed.clear()
    fingerprint.array_digest(again["positions"])
    assert len(hashed) == 1

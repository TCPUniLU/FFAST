"""The chunked column digest (ADR 0107): the same for every way of feeding it."""
import hashlib

import numpy as np
import pytest

from ffast.cache import fingerprint as fp
from ffast.cache.fingerprint import (CHUNK_ROWS, SCHEME, ChunkHasher, array_digest,
                                     fingerprintFromArraysAndStrings, fingerprintFromParts,
                                     sgdml_md5)


def reference(array):
    """The recipe written out by hand, one chunk at a time."""
    array = np.ascontiguousarray(array)
    column = hashlib.sha256(SCHEME)
    for a in range(0, len(array), CHUNK_ROWS):
        column.update(hashlib.sha256(array[a:a + CHUNK_ROWS].tobytes()).digest())
    return column.hexdigest()


def fed(array, sizes):
    h = ChunkHasher(array.shape[1:], array.dtype)
    a = 0
    for size in sizes:
        h.update(array[a:a + size])
        a += size
    h.update(array[a:])
    return h.hexdigest()


def column(n, tail=(), dtype=np.float64):
    rng = np.random.default_rng(n)
    return (rng.random((n, *tail)) * 100).astype(dtype)


@pytest.mark.parametrize("n", [0, 1, CHUNK_ROWS - 1, CHUNK_ROWS, CHUNK_ROWS + 1, 3 * CHUNK_ROWS + 7])
@pytest.mark.parametrize("tail,dtype", [((), np.float64), ((3,), np.float64), ((), np.int64), ((3, 3), np.float32)])
def test_every_way_of_splitting_gives_the_same_digest(n, tail, dtype):
    x = column(n, tail, dtype)
    want = reference(x)
    assert fed(x, []) == want
    assert fed(x, [1] * min(n, 5)) == want
    for cut in (1, 7, CHUNK_ROWS - 1, CHUNK_ROWS, CHUNK_ROWS + 1):
        assert fed(x, [cut, cut]) == want
    assert array_digest(x) == want


def test_single_rows_cross_a_boundary():
    x = column(CHUNK_ROWS + 3)
    assert fed(x, [1] * (CHUNK_ROWS + 3)) == reference(x)


def test_an_empty_column_is_the_hash_of_the_scheme_tag():
    assert ChunkHasher().hexdigest() == hashlib.sha256(SCHEME).hexdigest()
    assert array_digest(np.empty(0)) == hashlib.sha256(SCHEME).hexdigest()


@pytest.mark.parametrize("stop", [0, 5, CHUNK_ROWS - 1, CHUNK_ROWS, CHUNK_ROWS + 9, 2 * CHUNK_ROWS + 1])
def test_restoring_digests_and_tail_continues_the_same_digest(stop):
    x = column(2 * CHUNK_ROWS + 100, (3,))
    h = ChunkHasher((3,), x.dtype)
    h.update(x[:stop])
    assert h.rows == stop
    assert len(h.tail()) == stop % CHUNK_ROWS
    again = ChunkHasher((3,), x.dtype)
    again.restore(h.digests(), h.tail())
    again.update(x[stop:])
    assert again.hexdigest() == reference(x)


def test_the_hasher_keeps_a_copy_not_a_view():
    x = column(10)
    h = ChunkHasher((), x.dtype)
    h.update(x)
    want = h.hexdigest()
    x[:] = 0
    assert h.hexdigest() == want


def test_hexdigest_does_not_change_the_hasher():
    x = column(CHUNK_ROWS + 5)
    h = ChunkHasher((), x.dtype)
    h.update(x)
    assert h.hexdigest() == h.hexdigest()
    h.update(x)
    assert h.hexdigest() == reference(np.concatenate([x, x]))


def test_threads_give_the_same_digest(monkeypatch):
    x = column(5 * CHUNK_ROWS + 11, (3,))
    assert x.nbytes >= fp._THREAD_BYTES
    used = []
    real = fp.ThreadPoolExecutor

    class Spy(real):
        def __init__(self, *a, **k):
            used.append(k.get("max_workers"))
            super().__init__(*a, **k)

    monkeypatch.setattr(fp, "ThreadPoolExecutor", Spy)
    assert array_digest(x) == reference(x) == fed(x, [CHUNK_ROWS + 1])
    assert used == [4]


def test_a_non_contiguous_array_hashes_its_values():
    x = column(100, (4,))
    assert array_digest(x[:, ::2]) == reference(np.ascontiguousarray(x[:, ::2]))


def test_the_same_bytes_in_another_row_shape_may_share_a_digest():
    # Dtype and shape are the caller's business (as with md5): rows are cut by
    # the first axis only, so a (n, 3) column of fewer than a chunk of rows
    # hashes the same bytes as its flat form.
    x = column(10, (3,))
    assert array_digest(x) == array_digest(x.reshape(-1))


def test_chunk_boundaries_follow_rows_not_values():
    x = column(CHUNK_ROWS + 1, (3,))
    assert array_digest(x) != array_digest(x.reshape(-1))


def test_a_list_of_arrays_hashes_as_their_concatenation():
    pieces = [column(CHUNK_ROWS - 2, (3,)), column(5, (3,)), column(CHUNK_ROWS, (3,))]
    joined = np.concatenate(pieces)
    assert fingerprintFromArraysAndStrings(pieces) == fingerprintFromArraysAndStrings(joined)


def test_strings_and_arrays_combine_in_order():
    a = column(7)
    parts = [hashlib.sha256(b"model").digest(), bytes.fromhex(array_digest(a))]
    assert fingerprintFromArraysAndStrings("model", a) == fingerprintFromParts(parts)
    assert fingerprintFromArraysAndStrings(a, "model") != fingerprintFromArraysAndStrings("model", a)
    assert len(fingerprintFromArraysAndStrings("x")) == 64


def test_sgdml_keeps_its_md5_recipe():
    a = np.arange(6.0).reshape(2, 3)
    want = hashlib.md5(hashlib.md5(a.ravel()).digest()).hexdigest()
    assert sgdml_md5(a) == want

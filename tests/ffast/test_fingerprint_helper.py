"""
fingerprintFromArraysAndStrings refuses what it cannot hash, instead of guessing.

It used to skip its "turn this into bytes" step for any input it did not
recognise (None, a number, a tuple) and hash the *previous* input again, so
``('abc', None)`` and ``('abc', 'abc')`` got the same fingerprint, and an
unrecognised first input crashed with UnboundLocalError. A fingerprint names a
dataset or model in saved sessions and caches, so a wrong one fails silently.

The values pinned below changed on purpose when fingerprints moved from md5 to
chunked SHA-256 (ADR 0107); from here on they must stay.
"""
import numpy as np
import pytest

from ffast.cache.fingerprint import array_digest, fingerprintFromArraysAndStrings

UNCHANGED = {
    "text": (("abc",), "672478796c631d3ad4f4748b0163492a02204e05a2dc4585b3f8c6cf6f4432f1"),
    "array": ((np.arange(6.0).reshape(2, 3),), "3beb0dac8b47e09c7d616a98487633db412844624f80fd64281ea8687ebd8b8a"),
    "list of arrays": (([np.arange(3.0), np.arange(2.0)],), "34e3136970b267d88a9984256cb32e023883f896a8ba06c34843b48c85597366"),
    "list of numbers": (([1, 2, 3],), "884454e06e93091b3a2c6f9b7b4c23a14c3ab5a1abecc29d34c58eb91cca0025"),
    "mixed": (("abc", np.arange(3), [np.ones(2)]), "05aa6a11a73eef0e77f7cf500584471e3e50e743c1aa1bb9095bd148a9204577"),
}


@pytest.mark.parametrize("name", list(UNCHANGED))
def test_fingerprint_of_understood_inputs_is_unchanged(name):
    args, expected = UNCHANGED[name]
    assert fingerprintFromArraysAndStrings(*args) == expected


@pytest.mark.parametrize("unknown", [None, 3, 2.5, (1, 2)], ids=["None", "int", "float", "tuple"])
def test_input_it_cannot_hash_is_refused(unknown):
    with pytest.raises(TypeError):
        fingerprintFromArraysAndStrings("abc", unknown)


def test_refused_input_first_gives_a_clear_error():
    """First position used to crash with UnboundLocalError instead."""
    with pytest.raises(TypeError):
        fingerprintFromArraysAndStrings(None, "abc")


def test_bytes_hash_like_the_same_text():
    """NequIP model metadata may arrive as bytes or as text; same content,
    same fingerprint either way."""
    assert fingerprintFromArraysAndStrings(b"abc") == fingerprintFromArraysAndStrings("abc")


def test_known_batched_fingerprints_survive_reshape_without_reading():
    from ffast.io.batched import BatchedArray

    values = np.arange(12.0).reshape(4, 3)
    def fail(*args):
        raise AssertionError("Fingerprint already established at load time")
    column = BatchedArray(values.shape, values.dtype, fail, digest=array_digest(values))
    assert (fingerprintFromArraysAndStrings(column.reshape(-1), "model")
            == fingerprintFromArraysAndStrings(values, "model"))


def test_a_batched_array_hashes_its_blocks_like_the_whole_array():
    from ffast.cache.fingerprint import CHUNK_ROWS
    from ffast.io.batched import BatchedArray

    x = np.random.default_rng(3).random((CHUNK_ROWS + 10, 3))
    b = BatchedArray(x.shape, x.dtype, lambda a, z: x[a:z], label="x")
    assert b.content_digest() == array_digest(x)
    assert fingerprintFromArraysAndStrings(b) == fingerprintFromArraysAndStrings(x)

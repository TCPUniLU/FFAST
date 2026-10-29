"""
md5FromArraysAndStrings refuses what it cannot hash, instead of guessing.

It used to skip its "turn this into bytes" step for any input it did not
recognise (None, a number, a tuple) and hash the *previous* input again, so
``('abc', None)`` and ``('abc', 'abc')`` got the same fingerprint, and an
unrecognised first input crashed with UnboundLocalError. A fingerprint names a
dataset or model in saved sessions and caches, so a wrong one fails silently.

The values pinned below were computed before the change: inputs it already
understood must keep their fingerprints.
"""
import numpy as np
import pytest

from ffast.cache.fingerprint import md5FromArraysAndStrings

UNCHANGED = {
    "text": (("abc",), "af5da9f45af7a300e3aded972f8ff687"),
    "array": ((np.arange(6.0).reshape(2, 3),), "2ea1cbf54fe6611a95c82c5c580d9ae9"),
    "list of arrays": (([np.arange(3.0), np.arange(2.0)],), "163a295360c11433faa58214e2b3c539"),
    "list of numbers": (([1, 2, 3],), "7d0e78b79a7061926226e3f7d8efd2c5"),
    "mixed": (("abc", np.arange(3), [np.ones(2)]), "94a1e5e20ec88a2dd676a166e76b784b"),
}


@pytest.mark.parametrize("name", list(UNCHANGED))
def test_fingerprint_of_understood_inputs_is_unchanged(name):
    args, expected = UNCHANGED[name]
    assert md5FromArraysAndStrings(*args) == expected


@pytest.mark.parametrize("unknown", [None, 3, 2.5, (1, 2)], ids=["None", "int", "float", "tuple"])
def test_input_it_cannot_hash_is_refused(unknown):
    with pytest.raises(TypeError):
        md5FromArraysAndStrings("abc", unknown)


def test_refused_input_first_gives_a_clear_error():
    """First position used to crash with UnboundLocalError instead."""
    with pytest.raises(TypeError):
        md5FromArraysAndStrings(None, "abc")


def test_bytes_hash_like_the_same_text():
    """NequIP model metadata may arrive as bytes or as text; same content,
    same fingerprint either way."""
    assert md5FromArraysAndStrings(b"abc") == md5FromArraysAndStrings("abc")

"""Content fingerprinting for cache/model identity (ADR 0047).

``md5FromArraysAndStrings`` hashes a mix of numpy arrays and strings into a
stable hex digest — the identity of prediction data (ghost-model fingerprints
keyed on E/F arrays) and other cached artefacts. Pure ``hashlib`` + ``numpy``,
Qt-free; relocated out of the flat ``utils.py`` so the server-closure loaders
and Loading Coordinator can reach it without importing the Desktop-Client spine.

The recipe: hash each input on its own, then hash those hashes in order
(``md5FromParts``). Data too big to hold at once can be fed into a part piece
by piece: an md5 fed frame by frame equals the md5 of the joined array, so
the fingerprint is the same (ADR 0058, decision B).
"""
import hashlib

import numpy as np


def md5FromArraysAndStrings(*args):
    return md5FromParts(hashlib.md5(_bytesOf(arg)) for arg in args)


def md5FromParts(parts):
    """Combine one md5 per input, in order, into the fingerprint.

    Each part is a ``hashlib.md5`` object. To fingerprint data without holding
    it whole, create the part with ``hashlib.md5()`` and ``update`` it piece by
    piece, in the order the joined array would hold the pieces.
    """
    fp = hashlib.md5()
    for part in parts:
        fp.update(part.digest())
    return fp.hexdigest()


def _bytesOf(arg):
    """The bytes md5FromArraysAndStrings hashes for one input."""
    if isinstance(arg, str):
        return arg.encode("utf8")
    if isinstance(arg, bytes):
        return arg
    if isinstance(arg, np.ndarray):
        return arg.ravel()
    if isinstance(arg, list):
        # Handle list of arrays (variable-sized datasets)
        if len(arg) > 0 and isinstance(arg[0], np.ndarray):
            return np.concatenate([a.ravel() for a in arg])
        # Regular list - try to convert to array
        return np.array(arg).ravel()
    raise TypeError(f"md5FromArraysAndStrings cannot hash {type(arg).__name__}: {arg!r}")

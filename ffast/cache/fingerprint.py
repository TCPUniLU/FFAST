"""Content fingerprinting for cache/model identity (ADR 0047).

``md5FromArraysAndStrings`` hashes a mix of numpy arrays and strings into a
stable hex digest — the identity of prediction data (ghost-model fingerprints
keyed on E/F arrays) and other cached artefacts. Pure ``hashlib`` + ``numpy``,
Qt-free; relocated out of the flat ``utils.py`` so the server-closure loaders
and Loading Coordinator can reach it without importing the Desktop-Client spine.
"""
import hashlib

import numpy as np


def md5FromArraysAndStrings(*args):
    fp = hashlib.md5()

    for arg in args:
        if isinstance(arg, str):
            d = arg.encode("utf8")
        elif isinstance(arg, np.ndarray):
            d = arg.ravel()
        elif isinstance(arg, list):
            # Handle list of arrays (variable-sized datasets)
            # Check if list contains numpy arrays
            if len(arg) > 0 and isinstance(arg[0], np.ndarray):
                # Flatten each array and concatenate
                d = np.concatenate([a.ravel() for a in arg])
            else:
                # Regular list - try to convert to array
                d = np.array(arg).ravel()

        fp.update(hashlib.md5(d).digest())

    return fp.hexdigest()

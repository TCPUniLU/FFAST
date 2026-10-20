"""Per-frame sums and means over flat per-atom arrays (ADR 0058, step 6).

A flat array holds one row per atom, frame after frame; ``offsets[i]`` is where
frame ``i``'s rows start, and ``offsets[-1]`` is the number of rows. A Python
loop over frames costs one numpy call per frame, which is slow on files of
100,000 frames; ``np.add.reduceat`` does all frames in one call.
"""
from __future__ import annotations

import numpy as np


def segmentSums(values, offsets) -> np.ndarray:
    """Sum of each frame's rows: ``(frames, ...)``. A frame with no rows sums
    to zero."""
    values = np.asarray(values, dtype=np.float64)
    offsets = np.asarray(offsets, dtype=np.intp)
    counts = np.diff(offsets)
    if counts.size and counts[0] > 0 and (counts == counts[0]).all():
        # Every frame the same size: a reshape, as fast as a 3D array.
        return values[:offsets[-1]].reshape(counts.size, int(counts[0]), *values.shape[1:]).sum(axis=1)
    out = np.zeros((counts.size,) + values.shape[1:], dtype=np.float64)
    filled = counts > 0
    if filled.any():
        # reduceat sums from each start to the next one given, so empty frames
        # (whose start equals the next start) are left out and stay zero.
        out[filled] = np.add.reduceat(values[:offsets[-1]], offsets[:-1][filled], axis=0)
    return out


def segmentMeans(values, offsets) -> np.ndarray:
    """Mean of each frame's rows: ``(frames, ...)``. A frame with no rows is
    NaN, as ``np.mean`` of nothing is."""
    counts = np.diff(np.asarray(offsets, dtype=np.intp)).astype(np.float64)
    sums = segmentSums(values, offsets)
    shape = (counts.size,) + (1,) * (sums.ndim - 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return sums / counts.reshape(shape)


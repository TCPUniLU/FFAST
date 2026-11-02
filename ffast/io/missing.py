"""A value a frame does not have, told apart from a bad value (ADR 0076).

When some frames of a file list a per-atom column and others do not, the
frames without it get MISSING: a NaN with its own bit pattern. It is still
NaN to every plot and summary (they skip NaN), but a formula metric can tell
it from a NaN written in the data, which stays a bad value (ADR 0064
section 8). The pattern survives copies, slices, float64 casts and pickling;
arithmetic does not keep it, so check inputs, not results.
"""
import numpy as np

_BITS = np.uint64(0x7FF8_0000_4D49_5353)   # quiet NaN, payload "MISS"
MISSING = float(np.array([_BITS], dtype=np.uint64).view(np.float64)[0])


def is_missing(values):
    """Where ``values`` (float64) hold MISSING."""
    arr = np.ascontiguousarray(values, dtype=np.float64)
    return arr.view(np.uint64) == _BITS

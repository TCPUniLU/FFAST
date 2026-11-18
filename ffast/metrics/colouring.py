"""Storage policy and validation for a bounded colour-range scan."""

from contextlib import contextmanager
from contextvars import ContextVar

import numpy as np

from ffast.io.batched import BatchedArray

_deferred = ContextVar("colouring_defers_result_arrays", default=False)


def deferring_results():
    return _deferred.get()


@contextmanager
def defer_results():
    token = _deferred.set(True)
    try:
        yield
    finally:
        _deferred.reset(token)


def checked_expression(values, gap, expr, warnings):
    """Validate each requested batch, preserving legitimate no-value rows."""

    def read(a, b):
        chunk = np.asarray(values[a:b])
        bad = ~np.isfinite(chunk)
        if gap is not None:
            mask = np.asarray(gap[a:b])
            bad &= ~mask.reshape(mask.shape + (1,) * (chunk.ndim - mask.ndim))
        if bad.any():
            raise ValueError(
                f"expr {expr!r} produced non-finite values (inf/nan) — check "
                "for division by zero or log of a non-positive value in the data"
            )
        return chunk

    result = BatchedArray(
        values.shape, values.dtype, read, batch_rows=values.batch_rows
    )
    result._colour_warnings = warnings
    return result

"""The streaming fallback works in float64 even when the source is float32,
as the eager code did: float32 overflows or rounds away what float64 keeps."""
import numpy as np

from ffast.io.batched import BatchedArray
from ffast.metrics import streaming
from ffast.metrics.transforms import _stream_summary


def _batched(values):
    return BatchedArray(values.shape, values.dtype, lambda a, b, v=values: v[a:b])


def test_worst_subtracts_in_float64():
    # In float32, 3e38 - (-3e38) overflows to inf and the component is dropped.
    true = np.array([[1.0, 2.0, 3.0], [3e38, 0.0, 0.0]], dtype=np.float32)
    pred = np.array([[1.0, 2.0, 3.0], [-3e38, 0.0, 0.0]], dtype=np.float32)
    got = streaming.worst(_batched(true), _batched(pred), offsets=[0, 1, 2], k=1)
    assert got.shape[0] == 1
    assert (got[0, 2], got[0, 3], got[0, 4]) == (1, 0, 0)       # frame, atom, component
    assert got[0, 0] == np.float64(true[1, 0]) and got[0, 1] == np.float64(pred[1, 0])


def test_parity_bins_in_float64():
    # In float32, x - lo overflows for values at both ends of the range.
    true = np.array([-3e38, 3e38, 0.0], dtype=np.float32)
    pred = true.copy()
    got = streaming.parity(_batched(true), _batched(pred), bins=4)
    t, p = true.astype(np.float64), pred.astype(np.float64)
    lo, hi = t.min(), t.max()
    width = (hi - lo) / 4
    want = np.zeros(16, dtype=np.int64)
    col = np.minimum(((t - lo) / width).astype(np.int64), 3)
    row = np.minimum(((p - lo) / width).astype(np.int64), 3)
    np.add.at(want, row * 4 + col, 1)
    np.testing.assert_array_equal(got[1:].ravel(), want)


def test_stream_summary_squares_and_sums_in_float64():
    # 2e19 ** 2 = 4e38 is above the float32 maximum (3.4e38).
    values = np.full(4, 2e19, dtype=np.float32)
    got = _stream_summary(_batched(values), square=True)
    assert got == np.float64(values[0]) ** 2
    assert _stream_summary(_batched(values), square=False) == np.float64(values[0])

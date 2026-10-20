"""Per-frame sums and means over flat per-atom arrays (ADR 0058, step 6)."""
import numpy as np

from ffast.metrics.segments import segmentMeans, segmentSums


def test_frames_of_different_sizes_sum_their_own_rows():
    values = np.arange(6, dtype=float)          # frames of 1, 3 and 2 rows
    assert segmentSums(values, [0, 1, 4, 6]).tolist() == [0, 6, 9]
    assert segmentMeans(values, [0, 1, 4, 6]).tolist() == [0, 2, 4.5]


def test_a_frame_without_rows_sums_to_zero_and_has_no_mean():
    values = np.array([[1.0, 2.0], [3.0, 4.0]])
    assert segmentSums(values, [0, 1, 1, 2]).tolist() == [[1, 2], [0, 0], [3, 4]]
    means = segmentMeans(values, [0, 1, 1, 2])
    assert np.isnan(means[1]).all() and means[2].tolist() == [3, 4]


def test_frames_of_one_size_give_what_a_3d_array_gives():
    rng = np.random.default_rng(0)
    stacked = rng.normal(size=(7, 4, 3))
    offsets = np.arange(8) * 4
    np.testing.assert_allclose(segmentSums(stacked.reshape(-1, 3), offsets), stacked.sum(axis=1))
    np.testing.assert_allclose(segmentMeans(stacked.reshape(-1, 3), offsets), stacked.mean(axis=1))

"""FlatFrames: per-atom values of many frames in one array (ADR 0080)."""
import numpy as np
import pytest

from ffast.core.flat_frames import FlatFrames


def _ragged():
    a = np.arange(12, dtype=float).reshape(4, 3)
    b = np.arange(100, 109, dtype=float).reshape(3, 3)
    c = np.arange(200, 206, dtype=float).reshape(2, 3)
    return [a, b, c]


def test_of_list_of_ragged_frames():
    a, b = _ragged()[:2]
    ff = FlatFrames.of([a, b])
    assert ff.values.shape == (7, 3)
    assert ff.offsets.tolist() == [0, 4, 7]
    assert len(ff) == 2
    np.testing.assert_array_equal(ff[1], b)
    assert [f.tolist() for f in ff] == [a.tolist(), b.tolist()]


def test_of_three_dimensional_array_is_a_view():
    arr = np.arange(30, dtype=float).reshape(5, 2, 3)
    ff = FlatFrames.of(arr)
    assert ff.values.shape == (10, 3)
    assert ff.offsets.tolist() == [0, 2, 4, 6, 8, 10]
    np.testing.assert_array_equal(ff[3], arr[3])
    assert np.shares_memory(ff.values, arr)


def test_of_two_dimensional_array_is_refused_and_flat_frames_pass_through():
    with pytest.raises(ValueError, match="pass FlatFrames"):
        FlatFrames.of(np.zeros((6, 3)))
    ff = FlatFrames.of([np.zeros((2, 3))])
    assert FlatFrames.of(ff) is ff


def test_offsets_must_end_at_the_length_of_values():
    with pytest.raises(ValueError):
        FlatFrames(np.zeros((5, 3)), [0, 2, 4])
    with pytest.raises(ValueError):
        FlatFrames(np.zeros((5, 3)), [1, 5])
    with pytest.raises(ValueError):
        FlatFrames(np.zeros((5, 3)), [0, 4, 2, 5])


def test_frames_picks_in_order_with_repeats():
    parts = _ragged()
    ff = FlatFrames.of(parts)
    sub = ff.frames([2, 0, 0])
    assert len(sub) == 3
    assert sub.offsets.tolist() == [0, 2, 6, 10]
    for got, want in zip(sub, [parts[2], parts[0], parts[0]]):
        np.testing.assert_array_equal(got, want)
    assert len(ff.frames([])) == 0


def test_integer_index_only():
    parts = _ragged()
    ff = FlatFrames.of(parts)
    np.testing.assert_array_equal(ff[-1], parts[-1])
    np.testing.assert_array_equal(ff[np.int64(1)], parts[1])
    with pytest.raises(TypeError):
        ff[1.0]
    with pytest.raises(TypeError):
        ff[[0, 1]]
    with pytest.raises(TypeError):
        ff[0:2]


def test_empty_list_is_zero_frames():
    ff = FlatFrames.of([])
    assert len(ff) == 0
    assert ff.values.shape == (0, 3)
    assert ff.offsets.tolist() == [0]


def test_atoms_gathers_values_with_new_offsets():
    ff = FlatFrames.of(_ragged()[:2])
    out = ff.atoms(np.array([0, 2, 4, 6]), [0, 2, 4])
    assert out.offsets.tolist() == [0, 2, 4]
    np.testing.assert_array_equal(out.values, ff.values[[0, 2, 4, 6]])

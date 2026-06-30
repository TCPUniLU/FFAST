import numpy as np
from ffast.metrics.builtin.force_metrics import (
    force_difference,
    force_mae,
    force_rmse,
    force_mae_per_structure,
)


def test_builtin_registry():
    from ffast.metrics.registry import _default_registry
    _, fn_diff = _default_registry.get("ffast.force_difference")
    _, fn_mae = _default_registry.get("ffast.force_mae")
    _, fn_rmse = _default_registry.get("ffast.force_rmse")
    assert fn_diff is force_difference
    assert fn_mae is force_mae
    assert fn_rmse is force_rmse


def test_force_difference_shape():
    reference = np.zeros((2, 3, 3))
    predicted = np.ones((2, 3, 3))
    diff = force_difference(reference, predicted)
    assert diff.shape == (2, 3, 3)
    assert np.all(diff == 1.0)


def test_force_mae_l2():
    diff = np.array([[[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]])  # shape (1, 2, 3)
    result = force_mae(diff, norm="l2")
    assert result.shape == (1, 2)
    assert np.isclose(result[0, 0], 0.0)
    assert np.isclose(result[0, 1], np.sqrt(3))


def test_force_mae_l1():
    diff = np.array([[[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]])  # shape (1, 2, 3)
    result = force_mae(diff, norm="l1")
    assert result.shape == (1, 2)
    assert np.isclose(result[0, 0], 0.0)
    assert np.isclose(result[0, 1], 1.0)  # mean([1,1,1]) = 1.0


def test_force_rmse_shape_and_value():
    # per-atom errors: shape (1, 2)
    per_atom = np.array([[0.0, np.sqrt(3)]])
    result = force_rmse(per_atom)
    assert result.shape == (1,)
    assert np.isclose(result[0], np.sqrt(np.mean([0.0, 3.0])))


def test_force_mae_per_structure_uniform():
    # uniform (N_frames, M) per-atom errors -> mean over atoms per frame
    per_atom = np.array([[0.0, 2.0], [1.0, 3.0]])
    result = force_mae_per_structure(per_atom)
    assert result.shape == (2,)
    assert np.allclose(result, [1.0, 2.0])


def test_force_mae_per_structure_offsets():
    # variable: flat per-atom errors split by offsets into 2 structures
    per_atom = np.array([0.0, 2.0, 4.0])
    result = force_mae_per_structure(per_atom, offsets=np.array([0, 2, 3]))
    assert result.shape == (2,)
    assert np.allclose(result, [1.0, 4.0])

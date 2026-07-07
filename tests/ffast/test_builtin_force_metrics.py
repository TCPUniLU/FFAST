import numpy as np
from ffast.metrics.builtin.force_metrics import (
    force_difference,
    force_mae,
    force_rmse,
    force_mae_per_structure,
)


def test_builtin_registered_under_expected_ids():
    from ffast.metrics.registry import _default_registry
    for metric_id in [
        "ffast.force_difference",
        "ffast.force_mae",
        "ffast.force_rmse",
    ]:
        decl, _ = _default_registry.get(metric_id)
        assert decl.id == metric_id


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


# ── Degenerate scientific inputs ──────────────────────────────────────────────

def test_force_mae_single_atom():
    # A single-atom force difference [3,4,0] -> l2 norm = 5.0, shape (1,).
    fd = np.array([[3.0, 4.0, 0.0]])
    result = force_mae(fd, norm="l2")
    assert result.shape == (1,)
    assert np.isclose(result[0], 5.0)


def test_force_difference_single_atom_single_frame():
    reference = np.array([[[1.0, 0.0, 0.0]]])   # (1 frame, 1 atom, 3)
    predicted = np.array([[[2.0, 0.0, 0.0]]])
    diff = force_difference(reference, predicted)
    assert diff.shape == (1, 1, 3)
    assert np.allclose(diff, [[[1.0, 0.0, 0.0]]])


def test_force_mae_propagates_nan():
    # A NaN component makes that atom's l2 norm NaN; the clean atom survives.
    fd = np.array([[np.nan, 4.0, 0.0], [3.0, 4.0, 0.0]])
    result = force_mae(fd, norm="l2")
    assert np.isnan(result[0])
    assert np.isclose(result[1], 5.0)


def test_force_rmse_propagates_nan():
    # force_rmse reduces the per-atom errors; a NaN taints the aggregate.
    per_atom = np.array([np.nan, 0.0])
    assert np.isnan(force_rmse(per_atom))

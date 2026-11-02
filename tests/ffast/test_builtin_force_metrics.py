import numpy as np
from ffast.metrics.builtin.force_metrics import force_difference

# Force MAE, RMSE and the rest are box settings of ffast.force_difference now
# (ADR 0065); tests/ffast/test_retired_metrics.py checks their numbers.


def test_builtin_registered_under_expected_ids():
    from ffast.metrics.registry import _default_registry
    for metric_id in [
        "ffast.force_difference",
        "ffast.force_net_residual",
    ]:
        decl, _ = _default_registry.get(metric_id)
        assert decl.id == metric_id


def test_force_difference_shape():
    reference = np.zeros((2, 3, 3))
    predicted = np.ones((2, 3, 3))
    diff = force_difference(reference, predicted)
    assert diff.shape == (2, 3, 3)
    assert np.all(diff == 1.0)


# ── Degenerate scientific inputs ──────────────────────────────────────────────

def test_force_difference_single_atom_single_frame():
    reference = np.array([[[1.0, 0.0, 0.0]]])   # (1 frame, 1 atom, 3)
    predicted = np.array([[[2.0, 0.0, 0.0]]])
    diff = force_difference(reference, predicted)
    assert diff.shape == (1, 1, 3)
    assert np.allclose(diff, [[[1.0, 0.0, 0.0]]])


# ── Force parity plot: density of every component + the worst as dots ───────
# 2026-11-02: plotting every component sent >4 GB for 1.77M structures.

from ffast.metrics.builtin.force_metrics import parity_density, worst_components  # noqa: E402


def test_parity_density_counts_every_finite_component_on_a_square_grid():
    true = np.array([[0.0, 1.0, 2.0], [3.0, np.nan, 4.0]])
    pred = np.array([[0.0, 1.0, 2.0], [3.0, 1.0, 0.0]])
    grid = parity_density(true, pred, bins=4)
    assert grid.shape == (5, 4)
    centres, counts = grid[0], grid[1:]
    np.testing.assert_allclose(centres, [0.5, 1.5, 2.5, 3.5])   # over 0..4, both axes
    assert counts.sum() == 5                                    # the NaN pair is left out
    # counts[row = predicted bin][column = true bin]: true 4, predicted 0
    assert counts[0, 3] == 1
    assert np.trace(counts) == 4                                # the rest lie on the diagonal


def test_worst_components_name_their_structure_and_atom_worst_first():
    # Structures of 1 and 2 atoms; one component of each atom is off.
    offsets = np.array([0, 1, 3])
    true = np.zeros((3, 3))
    pred = np.zeros((3, 3))
    pred[0, 2] = 0.5      # structure 0, atom 0, z
    pred[2, 0] = -3.0     # structure 1, atom 1, x
    pred[1, 1] = 1.0      # structure 1, atom 0, y
    rows = worst_components(true, pred, offsets, k=2, chunk=4)
    # columns: true, predicted, structure, atom, component
    np.testing.assert_array_equal(rows, [[0.0, -3.0, 1, 1, 0],
                                         [0.0, 1.0, 1, 0, 1]])


def test_worst_components_returns_all_when_fewer_than_asked():
    rows = worst_components(np.zeros((1, 3)), np.array([[1.0, np.nan, 2.0]]),
                            np.array([0, 1]), k=10)
    np.testing.assert_array_equal(rows[:, 4], [2, 0])           # the NaN is left out

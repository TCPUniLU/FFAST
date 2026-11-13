"""The energy parity plot: every structure counted on a grid, the worst as
dots (ADR 0078, amended 2026-11-13). Drawing all 1,773,605 energies of
shard_03 as dots froze the page."""
import numpy as np

import ffast.metrics.builtin  # noqa: F401
from ffast.metrics.builtin.energy_metrics import (
    energy_parity_density,
    energy_parity_outliers,
    worst_structures,
)
from ffast.metrics.registry import default_registry


def test_worst_structures_are_the_furthest_from_the_diagonal_worst_first():
    true = np.array([0.0, 1.0, 2.0, 3.0, np.nan])
    pred = np.array([0.0, 3.0, 2.5, 3.0, 9.0])
    rows = worst_structures(true, pred, k=2)
    # true, predicted, structure; a NaN on either side is left out.
    np.testing.assert_array_equal(rows, [[1.0, 3.0, 1.0], [2.0, 2.5, 2.0]])


def test_worst_structures_keep_every_finite_one_below_k():
    rows = worst_structures(np.array([1.0, np.nan]), np.array([2.0, 0.0]), k=10)
    np.testing.assert_array_equal(rows, [[1.0, 2.0, 0.0]])
    assert worst_structures(np.zeros(0), np.zeros(0), k=10).shape == (0, 3)


def test_the_shift_moves_the_predictions_by_the_mean_difference():
    true = np.array([0.0, 1.0, 2.0])
    pred = np.array([1.0, 2.0, 4.0])       # mean difference 4/3
    shift = np.float64(4.0 / 3.0)
    plain = energy_parity_density(true, pred, shift)
    moved = energy_parity_density(true, pred, shift, shifted=True)
    assert plain[1:].sum() == moved[1:].sum() == 3
    assert not np.array_equal(plain, moved)
    rows = energy_parity_outliers(true, pred, shift, shifted=True)
    np.testing.assert_allclose(rows[0], [2.0, 4.0 - 4.0 / 3.0, 2.0])


def test_both_energy_parity_metrics_take_the_tabs_shift_control():
    for mid in ("ffast.energy_parity_density", "ffast.energy_parity_outliers"):
        schema, _fn = default_registry.get(mid)
        assert "shifted" in schema.parameters, mid

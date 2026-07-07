import numpy as np
from ffast.metrics.builtin.energy_metrics import (
    energy_difference,
    energy_shift,
    energy_mae,
    energy_rmse,
    energy_mae_shifted,
    energy_rmse_shifted,
)


def test_builtin_registered_under_expected_ids():
    from ffast.metrics.registry import _default_registry
    for metric_id in [
        "ffast.energy_difference",
        "ffast.energy_shift",
        "ffast.energy_mae",
        "ffast.energy_rmse",
        "ffast.energy_mae_shifted",
        "ffast.energy_rmse_shifted",
    ]:
        decl, _ = _default_registry.get(metric_id)
        assert decl.id == metric_id


# reference=[1, 2, 3], predicted=[2, 3, 5] → diff=[1, 1, 2]
REFERENCE = np.array([1.0, 2.0, 3.0])
PREDICTED = np.array([2.0, 3.0, 5.0])
DIFF = np.array([1.0, 1.0, 2.0])
SHIFT = np.mean(DIFF)           # 4/3
SHIFTED_DIFF = DIFF - SHIFT     # [-1/3, -1/3, 2/3]


def test_energy_difference():
    result = energy_difference(REFERENCE, PREDICTED)
    assert result.shape == (3,)
    assert np.allclose(result, DIFF)


def test_energy_shift():
    result = energy_shift(DIFF)
    assert np.isclose(result, SHIFT)


def test_energy_mae():
    result = energy_mae(DIFF)
    assert np.isclose(result, np.mean(np.abs(DIFF)))


def test_energy_rmse():
    result = energy_rmse(DIFF)
    assert np.isclose(result, np.sqrt(np.mean(DIFF ** 2)))


def test_energy_mae_shifted():
    result = energy_mae_shifted(DIFF, SHIFT)
    assert np.isclose(result, np.mean(np.abs(SHIFTED_DIFF)))


def test_energy_rmse_shifted():
    result = energy_rmse_shifted(DIFF, SHIFT)
    assert np.isclose(result, np.sqrt(np.mean(SHIFTED_DIFF ** 2)))


def test_shifted_metrics_less_than_unshifted():
    result_mae = energy_mae(DIFF)
    result_mae_shifted = energy_mae_shifted(DIFF, SHIFT)
    assert result_mae_shifted < result_mae


# ── Degenerate scientific inputs ──────────────────────────────────────────────

def test_energy_difference_single_frame():
    # A one-frame trajectory still yields a one-element difference vector.
    result = energy_difference(np.array([2.0]), np.array([3.5]))
    assert result.shape == (1,)
    assert np.isclose(result[0], 1.5)


def test_energy_mae_single_frame():
    # mean(|[0.5]|) = 0.5 — the single value is the mean.
    assert np.isclose(energy_mae(np.array([0.5])), 0.5)


def test_energy_rmse_single_frame():
    # sqrt(mean([0.5]^2)) = 0.5
    assert np.isclose(energy_rmse(np.array([0.5])), 0.5)


def test_energy_difference_propagates_nan():
    # A NaN reference energy taints only its own frame; the clean frame survives.
    result = energy_difference(np.array([np.nan, 2.0]), np.array([1.0, 3.0]))
    assert np.isnan(result[0])
    assert np.isclose(result[1], 1.0)


def test_energy_mae_propagates_nan():
    # np.mean over any NaN element is NaN (no nan-aware reduction here).
    assert np.isnan(energy_mae(np.array([np.nan, 0.5])))


def test_energy_rmse_propagates_nan():
    assert np.isnan(energy_rmse(np.array([np.nan, 0.5])))

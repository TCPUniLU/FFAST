import numpy as np
from ffast.metrics.builtin.energy_metrics import (
    energy_difference,
    energy_shift,
    energy_mae,
    energy_rmse,
    energy_mae_shifted,
    energy_rmse_shifted,
)


def test_builtin_registry():
    from ffast.metrics.registry import _default_registry
    for metric_id, fn in [
        ("ffast.energy_difference", energy_difference),
        ("ffast.energy_shift", energy_shift),
        ("ffast.energy_mae", energy_mae),
        ("ffast.energy_rmse", energy_rmse),
        ("ffast.energy_mae_shifted", energy_mae_shifted),
        ("ffast.energy_rmse_shifted", energy_rmse_shifted),
    ]:
        _, registered_fn = _default_registry.get(metric_id)
        assert registered_fn is fn


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

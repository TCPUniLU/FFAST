import numpy as np
from ffast.metrics.builtin.energy_metrics import (
    energy_difference,
    energy_difference_shifted,
)

# Energy MAE, RMSE and the shift are box settings of ffast.energy_difference
# and ffast.energy_difference_shifted now (ADR 0065);
# tests/ffast/test_retired_metrics.py checks their numbers.


def test_builtin_registered_under_expected_ids():
    from ffast.metrics.registry import _default_registry
    for metric_id in [
        "ffast.energy_difference",
        "ffast.energy_difference_shifted",
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


def test_energy_difference_shifted():
    result = energy_difference_shifted(DIFF)
    assert np.allclose(result, SHIFTED_DIFF)


# ── Degenerate scientific inputs ──────────────────────────────────────────────

def test_energy_difference_single_frame():
    # A one-frame trajectory still yields a one-element difference vector.
    result = energy_difference(np.array([2.0]), np.array([3.5]))
    assert result.shape == (1,)
    assert np.isclose(result[0], 1.5)


def test_energy_difference_propagates_nan():
    # A NaN reference energy taints only its own frame; the clean frame survives.
    result = energy_difference(np.array([np.nan, 2.0]), np.array([1.0, 3.0]))
    assert np.isnan(result[0])
    assert np.isclose(result[1], 1.0)

"""Acceleration metrics on flat inputs: one row per atom, frame after frame,
every atom's mass and element, and offsets (ADR 0058 step 6)."""
import numpy as np
import pytest
import ffast.metrics.builtin.force_metrics  # noqa: F401 — register force_difference dependency
import ffast.metrics.builtin.accel_metrics  # noqa: F401 — register accel chain
from ffast.metrics.builtin.accel_metrics import accel_difference
from ffast.metrics.execution import FlatInputSource
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.registry import MetricRegistry


# reference inputs shared across tests: 1 structure, 2 atoms, 3 components
# force_diff = [[3,4,0],[0,0,0]], masses = [1, 2]
# accel_diff = [[3,4,0],[0,0,0]]  (mass 1 unchanged, mass 2 → 0/2=0)
_REF = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]])
_PRED = np.array([[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]])
_MASSES = np.array([1.0, 2.0])
_ELEMENTS = np.array([1, 6])


def _registry_with_builtins():
    from ffast.metrics.registry import _default_registry
    r = MetricRegistry()
    for mid in _default_registry.list_metrics():
        r._metrics[mid] = _default_registry.get(mid)
    return r


def test_accel_difference():
    fd = np.array([[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]])
    result = accel_difference(fd, _MASSES)
    assert result.shape == (2, 3)
    np.testing.assert_allclose(result[0], [3.0, 4.0, 0.0])
    np.testing.assert_allclose(result[1], [0.0, 0.0, 0.0])


def test_accel_difference_divides_each_atom_by_its_own_mass():
    fd = np.array([[2.0, 0.0, 0.0], [2.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
    result = accel_difference(fd, np.array([1.0, 2.0, 4.0]))
    np.testing.assert_allclose(result[:, 0], [2.0, 1.0, 0.5])


# Acceleration MAE, RMSE and the rest are box settings of
# ffast.accel_difference now (ADR 0065); tests/ffast/test_retired_metrics.py
# checks their numbers against the old metrics.


def test_executor_resolves_accel_chain():
    executor = InProcessExecutor(_registry_with_builtins())
    inputs = {"reference": _REF, "predicted": _PRED, "masses": _MASSES}
    result = executor.run("ffast.accel_difference[atom+structure;length]",
                          FlatInputSource(inputs), {})
    assert result.shape == "N_atoms"
    assert result.unit == "acceleration"
    np.testing.assert_allclose(result.values, [5.0, 0.0])


# ── Degenerate scientific inputs ──────────────────────────────────────────────

def test_accel_difference_zero_mass_raises():
    # A zero-mass atom makes acceleration undefined; accel_difference raises
    # rather than silently producing inf/nan.
    with pytest.raises(ValueError, match="zero-mass"):
        accel_difference(np.array([[3.0, 4.0, 0.0]]), np.array([0.0]))


def test_accel_difference_propagates_nan_force():
    # A NaN force-error component propagates through the mass division.
    result = accel_difference(np.array([[np.nan, 4.0, 0.0]]), np.array([1.0]))
    assert np.isnan(result[0, 0])
    np.testing.assert_allclose(result[0, 1:], [4.0, 0.0])


def test_executor_accel_mae_per_element():
    executor = InProcessExecutor(_registry_with_builtins())
    inputs = {"reference": _REF, "predicted": _PRED, "masses": _MASSES, "_elements": _ELEMENTS}
    result = executor.run("ffast.accel_difference[element;length;mae]",
                          FlatInputSource(inputs), {})
    assert result.shape == "N_elements"
    assert result.unit == "acceleration"
    np.testing.assert_allclose(result.values, [5.0, 0.0])

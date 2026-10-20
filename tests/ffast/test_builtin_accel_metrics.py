"""Acceleration metrics on flat inputs: one row per atom, frame after frame,
every atom's mass and element, and offsets (ADR 0058 step 6)."""
import numpy as np
import pytest
import ffast.metrics.builtin.force_metrics  # noqa: F401 — register force_difference dependency
import ffast.metrics.builtin.accel_metrics  # noqa: F401 — register accel chain
from ffast.metrics.builtin.accel_metrics import (
    accel_difference,
    accel_mae,
    accel_rmse,
    accel_mae_global,
    accel_rmse_global,
    accel_mae_per_atom,
    accel_mae_per_element,
)
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


def test_accel_mae_l2():
    ad = accel_difference(np.array([[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]]), _MASSES)
    result = accel_mae(ad, norm="l2")
    assert result.shape == (2,)
    np.testing.assert_allclose(result, [5.0, 0.0])


def test_accel_mae_l1():
    ad = accel_difference(np.array([[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]]), _MASSES)
    result = accel_mae(ad, norm="l1")
    np.testing.assert_allclose(result, [7.0 / 3.0, 0.0], rtol=1e-6)


def test_accel_rmse_of_one_structure():
    result = accel_rmse(np.array([5.0, 0.0]))
    assert result.shape == (1,)
    np.testing.assert_allclose(result[0], np.sqrt(12.5), rtol=1e-6)


def test_accel_rmse_gives_one_value_per_structure():
    result = accel_rmse(np.array([5.0, 0.0, 3.0, 4.0, 0.0]), offsets=np.array([0, 2, 5]))
    np.testing.assert_allclose(result, [np.sqrt(12.5), np.sqrt(25.0 / 3.0)])


def test_accel_mae_global():
    assert np.isclose(accel_mae_global(np.array([5.0, 0.0])), 2.5)


def test_accel_rmse_global():
    np.testing.assert_allclose(accel_rmse_global(np.array([5.0, 0.0])), np.sqrt(12.5), rtol=1e-6)


def test_accel_mae_per_atom_of_one_structure():
    result = accel_mae_per_atom(np.array([5.0, 0.0]))
    assert result.shape == (2,)
    np.testing.assert_allclose(result, [5.0, 0.0])


def test_accel_mae_per_atom_averages_structures_of_the_same_size():
    result = accel_mae_per_atom(np.array([5.0, 0.0, 3.0, 2.0]), offsets=np.array([0, 2, 4]))
    np.testing.assert_allclose(result, [4.0, 1.0])


def test_accel_mae_per_atom_keeps_every_atom_when_structures_differ_in_size():
    result = accel_mae_per_atom(np.array([5.0, 0.0, 3.0, 2.0, 1.0]), offsets=np.array([0, 2, 5]))
    np.testing.assert_allclose(result, [5.0, 0.0, 3.0, 2.0, 1.0])


def test_accel_mae_per_element():
    result = accel_mae_per_element(np.array([5.0, 0.0]), _ELEMENTS)
    assert result.shape == (2,)
    np.testing.assert_allclose(result, [5.0, 0.0])  # H, C


def test_accel_mae_per_element_over_several_structures():
    # Two structures, H C and H C: H errors 5 and 3, C errors 0 and 2.
    result = accel_mae_per_element(np.array([5.0, 0.0, 3.0, 2.0]), np.array([1, 6, 1, 6]))
    np.testing.assert_allclose(result, [4.0, 1.0])


def test_accel_mae_per_element_sorted_by_z():
    result = accel_mae_per_element(np.array([2.0, 5.0]), np.array([6, 1]))  # C first, then H
    # unique_z sorted = [1, 6]; index 0 = H (z=1, am=5.0), index 1 = C (z=6, am=2.0)
    np.testing.assert_allclose(result, [5.0, 2.0])


def test_executor_resolves_accel_chain():
    executor = InProcessExecutor(_registry_with_builtins())
    inputs = {"reference": _REF, "predicted": _PRED, "masses": _MASSES}
    result = executor.run("ffast.accel_mae", FlatInputSource(inputs), {"norm": "l2"})
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


def test_accel_mae_single_atom():
    result = accel_mae(np.array([[3.0, 4.0, 0.0]]), norm="l2")
    assert result.shape == (1,)
    np.testing.assert_allclose(result[0], 5.0)


def test_accel_mae_propagates_nan():
    result = accel_mae(np.array([[np.nan, 4.0, 0.0], [0.0, 0.0, 0.0]]), norm="l2")
    assert np.isnan(result[0])
    np.testing.assert_allclose(result[1], 0.0)


def test_executor_accel_mae_per_element():
    executor = InProcessExecutor(_registry_with_builtins())
    inputs = {"reference": _REF, "predicted": _PRED, "masses": _MASSES, "elements": _ELEMENTS}
    result = executor.run("ffast.accel_mae_per_element", FlatInputSource(inputs), {})
    assert result.shape == "N_elements"
    assert result.unit == "acceleration"
    np.testing.assert_allclose(result.values, [5.0, 0.0])

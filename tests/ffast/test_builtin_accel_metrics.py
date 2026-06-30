import numpy as np
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
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.registry import MetricRegistry


# reference inputs shared across tests: 1 structure, 2 atoms, 3 components
# force_diff = [[3,4,0],[0,0,0]], masses = [1, 2]
# accel_diff = [[3,4,0],[0,0,0]]  (mass 1 unchanged, mass 2 → 0/2=0)
_REF = np.array([[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]])
_PRED = np.array([[[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]]])
_MASSES = np.array([1.0, 2.0])
_ELEMENTS = np.array([1, 6])


def _registry_with_builtins():
    from ffast.metrics.registry import _default_registry
    r = MetricRegistry()
    for mid in _default_registry.list_metrics():
        r._metrics[mid] = _default_registry.get(mid)
    return r


def test_accel_difference():
    fd = np.array([[[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]]])
    result = accel_difference(fd, _MASSES)
    assert result.shape == (1, 2, 3)
    np.testing.assert_allclose(result[0, 0], [3.0, 4.0, 0.0])
    np.testing.assert_allclose(result[0, 1], [0.0, 0.0, 0.0])


def test_accel_difference_2d_input():
    fd = np.array([[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]])
    result = accel_difference(fd, _MASSES)
    assert result.shape == (1, 2, 3)


def test_accel_mae_l2():
    fd = np.array([[[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]]])
    ad = accel_difference(fd, _MASSES)
    result = accel_mae(ad, norm="l2")
    assert result.shape == (1, 2)
    np.testing.assert_allclose(result[0], [5.0, 0.0])


def test_accel_mae_l1():
    fd = np.array([[[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]]])
    ad = accel_difference(fd, _MASSES)
    result = accel_mae(ad, norm="l1")
    assert result.shape == (1, 2)
    np.testing.assert_allclose(result[0, 0], 7.0 / 3.0, rtol=1e-6)
    np.testing.assert_allclose(result[0, 1], 0.0)


def test_accel_rmse():
    am = np.array([[5.0, 0.0]])
    result = accel_rmse(am)
    assert result.shape == (1,)
    np.testing.assert_allclose(result[0], np.sqrt(12.5), rtol=1e-6)


def test_accel_mae_global():
    am = np.array([[5.0, 0.0]])
    assert np.isclose(accel_mae_global(am), 2.5)


def test_accel_rmse_global():
    am = np.array([[5.0, 0.0]])
    np.testing.assert_allclose(accel_rmse_global(am), np.sqrt(12.5), rtol=1e-6)


def test_accel_mae_per_atom():
    am = np.array([[5.0, 0.0]])
    result = accel_mae_per_atom(am)
    assert result.shape == (2,)
    np.testing.assert_allclose(result, [5.0, 0.0])


def test_accel_mae_per_atom_multi_structure():
    am = np.array([[5.0, 0.0], [3.0, 2.0]])
    result = accel_mae_per_atom(am)
    np.testing.assert_allclose(result, [4.0, 1.0])


def test_accel_mae_per_atom_1d_input():
    am = np.array([5.0, 0.0])
    result = accel_mae_per_atom(am)
    assert result.shape == (2,)
    np.testing.assert_allclose(result, [5.0, 0.0])


def test_accel_mae_per_element():
    am = np.array([[5.0, 0.0]])
    result = accel_mae_per_element(am, _ELEMENTS)
    assert result.shape == (2,)
    np.testing.assert_allclose(result[0], 5.0)  # H
    np.testing.assert_allclose(result[1], 0.0)  # C


def test_accel_mae_per_element_sorted_by_z():
    am = np.array([[2.0, 5.0]])
    el = np.array([6, 1])  # C first, then H
    result = accel_mae_per_element(am, el)
    # unique_z sorted = [1, 6]; index 0 = H (z=1, am=5.0), index 1 = C (z=6, am=2.0)
    np.testing.assert_allclose(result[0], 5.0)  # H
    np.testing.assert_allclose(result[1], 2.0)  # C


def test_executor_resolves_accel_chain():
    r = _registry_with_builtins()
    executor = InProcessExecutor(r)
    inputs = {
        "reference": _REF,
        "predicted": _PRED,
        "masses": _MASSES,
    }
    result = executor.run("ffast.accel_mae", inputs, {"norm": "l2"})
    assert result.shape == "(N_frames, N_atoms)"
    assert result.unit == "acceleration"
    np.testing.assert_allclose(result.values[0], [5.0, 0.0])


def test_executor_accel_mae_per_element():
    r = _registry_with_builtins()
    executor = InProcessExecutor(r)
    inputs = {
        "reference": _REF,
        "predicted": _PRED,
        "masses": _MASSES,
        "elements": _ELEMENTS,
    }
    result = executor.run("ffast.accel_mae_per_element", inputs, {})
    assert result.shape == "N_elements"
    assert result.unit == "acceleration"
    np.testing.assert_allclose(result.values[0], 5.0)
    np.testing.assert_allclose(result.values[1], 0.0)

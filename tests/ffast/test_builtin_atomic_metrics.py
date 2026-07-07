import numpy as np
import ffast.metrics.builtin.force_metrics  # noqa: F401 — register force_difference dependency
import ffast.metrics.builtin.transform_metrics  # noqa: F401 — registers the aggregate density
from ffast.metrics.builtin.atomic_metrics import (
    force_mae_per_element,
    force_mae_per_structure_per_element_kde,
    force_rmse_per_element,
)
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.registry import MetricRegistry


def _registry_with_builtins():
    from ffast.metrics.registry import _default_registry
    r = MetricRegistry()
    for mid in _default_registry.list_metrics():
        r._metrics[mid] = _default_registry.get(mid)
    return r


def test_builtin_registry():
    from ffast.metrics.registry import _default_registry
    _, fn_mae = _default_registry.get("ffast.force_mae_per_element")
    _, fn_rmse = _default_registry.get("ffast.force_rmse_per_element")
    assert fn_mae is force_mae_per_element
    assert fn_rmse is force_rmse_per_element


def test_force_mae_per_element_values():
    # 1 structure, 2 atoms (H=1, C=6), 3 force components
    # force_difference = [[1,0,0],[0,0,0]]
    fd = np.array([[[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]]])
    el = np.array([1, 6])
    result = force_mae_per_element(fd, el)
    assert result.shape == (2,)
    assert np.isclose(result[0], 1.0 / 3.0)  # H
    assert np.isclose(result[1], 0.0)          # C


def test_force_rmse_per_element_values():
    fd = np.array([[[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]]])
    el = np.array([1, 6])
    result = force_rmse_per_element(fd, el)
    assert result.shape == (2,)
    assert np.isclose(result[0], np.sqrt(1.0 / 3.0))  # H
    assert np.isclose(result[1], 0.0)                   # C


def test_force_mae_per_element_2d_input():
    # single structure without explicit N dimension
    fd = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]])
    el = np.array([1, 6])
    result = force_mae_per_element(fd, el)
    assert result.shape == (2,)
    assert np.isclose(result[0], 1.0 / 3.0)


def test_force_mae_per_element_multi_structure():
    # 3 structures, same 2 atoms, H always has diff [1,0,0]
    fd = np.array([
        [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
        [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
        [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
    ])
    el = np.array([1, 6])
    result = force_mae_per_element(fd, el)
    assert np.isclose(result[0], 1.0 / 3.0)
    assert np.isclose(result[1], 0.0)


def test_per_element_sorted_by_z():
    # elements in reverse order — result must still be sorted by Z
    fd = np.array([[[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]]])
    el = np.array([6, 1])  # C first, then H
    result_mae = force_mae_per_element(fd, el)
    result_rmse = force_rmse_per_element(fd, el)
    # unique_z sorted = [1, 6]; index 0 = H (z=1, fd=[2,0,0]), index 1 = C (z=6, fd=[1,0,0])
    assert np.isclose(result_mae[0], 2.0 / 3.0)   # H
    assert np.isclose(result_mae[1], 1.0 / 3.0)   # C
    assert np.isclose(result_rmse[0], np.sqrt(4.0 / 3.0))
    assert np.isclose(result_rmse[1], np.sqrt(1.0 / 3.0))


# ── Degenerate scientific inputs ──────────────────────────────────────────────

def test_force_mae_per_element_single_atom_single_element():
    # A one-atom structure with a single element: the mean over that element's
    # components is |1|+|0|+|0| averaged over 3 -> 1/3.
    fd = np.array([[[1.0, 0.0, 0.0]]])   # (1 frame, 1 atom, 3)
    el = np.array([1])
    result = force_mae_per_element(fd, el)
    assert result.shape == (1,)
    assert np.isclose(result[0], 1.0 / 3.0)


def test_force_mae_per_element_propagates_nan():
    # A NaN in the H atom's force diff makes H's mean NaN; C stays clean.
    fd = np.array([[[np.nan, 0.0, 0.0], [0.0, 0.0, 0.0]]])
    el = np.array([1, 6])
    result = force_mae_per_element(fd, el)
    assert np.isnan(result[0])   # H
    assert np.isclose(result[1], 0.0)  # C


def test_force_rmse_per_element_propagates_nan():
    fd = np.array([[[np.nan, 0.0, 0.0], [0.0, 0.0, 0.0]]])
    el = np.array([1, 6])
    result = force_rmse_per_element(fd, el)
    assert np.isnan(result[0])   # H
    assert np.isclose(result[1], 0.0)  # C


def test_executor_resolves_dependency():
    r = _registry_with_builtins()
    executor = InProcessExecutor(r)
    inputs = {
        "reference": np.array([[[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]]]),
        "predicted": np.array([[[2.0, 0.0, 0.0], [2.0, 0.0, 0.0]]]),
        "elements": np.array([1, 6]),
    }
    result = executor.run("ffast.force_mae_per_element", inputs, {})
    assert result.shape == "N_elements"
    assert np.isclose(result.values[0], 1.0 / 3.0)  # H diff=[1,0,0]
    assert np.isclose(result.values[1], 0.0)          # C diff=[0,0,0]


# --- per-element distribution (KDE) for the grouped_density Panel ------------- #

def test_per_element_kde_shape_uniform():
    # 3 structures, 4 atoms (z = H,H,C,O → 3 unique elements)
    rng = np.random.default_rng(0)
    fd = rng.normal(size=(3, 4, 3))
    el = np.array([1, 1, 6, 8])
    out = force_mae_per_structure_per_element_kde(fd, el)
    assert out.shape == (3, 2, 200)  # (N_elements, curve_xy, grid)


def test_per_element_kde_shape_variable():
    # 2 molecules, offsets [0,3,5] over 5 flat atoms (3 unique elements)
    rng = np.random.default_rng(1)
    fd = rng.normal(size=(5, 3))
    el = np.array([1, 6, 8, 1, 6])
    out = force_mae_per_structure_per_element_kde(fd, el, offsets=[0, 3, 5])
    assert out.shape == (3, 2, 200)


def test_per_element_kde_independent_ranges():
    # Each element gets its OWN x-range (no shared-grid padding): a low-error
    # element's curve must not stretch to a high-error element's max.
    # 4 structures, atom 0 = H (large errors), atom 1 = C (tiny errors).
    fd = np.zeros((4, 2, 3))
    fd[:, 0, 0] = [3.0, 4.0, 5.0, 6.0]    # H force-diff x-component
    fd[:, 1, 0] = [0.01, 0.02, 0.03, 0.04]  # C force-diff x-component
    el = np.array([1, 6])  # order = [1, 6] → row 0 = H, row 1 = C
    out = force_mae_per_structure_per_element_kde(fd, el)
    assert out[0, 0].max() > out[1, 0].max() * 10  # H range ≫ C range


def test_per_element_kde_through_executor():
    # End-to-end: resolves force_difference, computes the (N_elements,2,grid)
    # MetricResult — the same compute+result path the server/client use.
    r = _registry_with_builtins()
    executor = InProcessExecutor(r)
    inputs = {
        "reference": np.array([[[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]],
                               [[1.5, 0.0, 0.0], [2.0, 0.0, 0.0]]]),
        "predicted": np.array([[[2.0, 0.0, 0.0], [2.0, 0.0, 0.0]],
                               [[2.0, 0.0, 0.0], [2.0, 0.0, 0.0]]]),
        "elements": np.array([1, 6]),
    }
    result = executor.run("ffast.force_mae_per_structure_per_element_kde", inputs, {})
    assert result.shape == "(N_elements, curve_xy, grid)"
    assert np.asarray(result.values).shape == (2, 2, 200)

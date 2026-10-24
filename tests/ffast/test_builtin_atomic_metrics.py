"""Per-element force metrics on flat inputs: one row per atom, frame after
frame, every atom's element, and offsets (ADR 0058 step 6)."""
import numpy as np
import ffast.metrics.builtin.force_metrics  # noqa: F401 — register force_difference dependency
import ffast.metrics.builtin.transform_metrics  # noqa: F401 — registers the aggregate density
from ffast.metrics.builtin.atomic_metrics import force_mae_per_structure_per_element_kde
from ffast.metrics.execution import FlatInputSource
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.registry import MetricRegistry


def _registry_with_builtins():
    from ffast.metrics.registry import _default_registry
    r = MetricRegistry()
    for mid in _default_registry.list_metrics():
        r._metrics[mid] = _default_registry.get(mid)
    return r


# Force MAE and RMSE per element are box settings of ffast.force_difference
# now (ADR 0065); tests/ffast/test_retired_metrics.py checks their numbers.


def test_executor_resolves_dependency():
    r = _registry_with_builtins()
    executor = InProcessExecutor(r)
    inputs = {
        "reference": np.array([[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]]),
        "predicted": np.array([[2.0, 0.0, 0.0], [2.0, 0.0, 0.0]]),
        "_elements": np.array([1, 6]),
    }
    result = executor.run("ffast.force_difference[element;component;mae]",
                          FlatInputSource(inputs), {})
    assert result.shape == "N_elements"
    assert np.isclose(result.values[0], 1.0 / 3.0)  # H diff=[1,0,0]
    assert np.isclose(result.values[1], 0.0)          # C diff=[0,0,0]


# --- per-element distribution (KDE) for the grouped_density Panel ------------- #

def test_per_element_kde_shape_same_sized_structures():
    # 3 structures, 4 atoms (z = H,H,C,O → 3 unique elements)
    rng = np.random.default_rng(0)
    fd = rng.normal(size=(12, 3))
    el = np.tile([1, 1, 6, 8], 3)
    out = force_mae_per_structure_per_element_kde(fd, el, offsets=[0, 4, 8, 12])
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
    fd = np.zeros((8, 3))
    fd[0::2, 0] = [3.0, 4.0, 5.0, 6.0]    # H force-diff x-component
    fd[1::2, 0] = [0.01, 0.02, 0.03, 0.04]  # C force-diff x-component
    el = np.tile([1, 6], 4)  # order = [1, 6] → row 0 = H, row 1 = C
    out = force_mae_per_structure_per_element_kde(fd, el, offsets=[0, 2, 4, 6, 8])
    assert out[0, 0].max() > out[1, 0].max() * 10  # H range ≫ C range


def test_per_element_kde_through_executor():
    # End-to-end: resolves force_difference, computes the (N_elements,2,grid)
    # MetricResult — the same compute+result path the server/client use.
    r = _registry_with_builtins()
    executor = InProcessExecutor(r)
    inputs = {
        "reference": np.array([[1.0, 0.0, 0.0], [2.0, 0.0, 0.0],
                               [1.5, 0.0, 0.0], [2.0, 0.0, 0.0]]),
        "predicted": np.array([[2.0, 0.0, 0.0], [2.0, 0.0, 0.0],
                               [2.0, 0.0, 0.0], [2.0, 0.0, 0.0]]),
        "elements": np.array([1, 6, 1, 6]),
        "offsets": np.array([0, 2, 4]),
    }
    result = executor.run("ffast.force_mae_per_structure_per_element_kde", FlatInputSource(inputs), {})
    assert result.shape == "(N_elements, curve_xy, grid)"
    assert np.asarray(result.values).shape == (2, 2, 200)

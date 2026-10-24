"""Each formula in ffast/metrics/builtin/equivalents.py computes what its
built-in Python metric computes (ADR 0057 amendment 2026-10-23)."""
import numpy as np
import pytest

from ffast.metrics.builtin import (  # noqa: F401  (registers the built-ins)
    accel_metrics, atomic_metrics, energy_metrics, force_metrics, transform_metrics,
)
from ffast.metrics.builtin.equivalents import EQUIVALENTS, equivalent_of
from ffast.metrics.execution import FlatInputSource
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.expr import _ExprFn, analyze_expr
from ffast.metrics.registry import default_registry
from ffast.metrics.dims import shape_to_str

rng = np.random.default_rng(0)
N_ATOMS, N_FRAMES = 12, 4
# Structures of different sizes, so averaging by structure is tested properly.
OFFSETS = np.array([0, 2, 5, 9, 12])
RAW = {
    "offsets": OFFSETS,
    "reference.elements": np.array([8, 1, 6, 1, 1, 8, 1, 6, 6, 1, 7, 1]),
    "prediction.forces": rng.normal(size=(N_ATOMS, 3)),
    "reference.forces": rng.normal(size=(N_ATOMS, 3)),
    "prediction.energies": rng.normal(size=N_FRAMES),
    "reference.energies": rng.normal(size=N_FRAMES),
    "reference.masses": rng.uniform(1.0, 16.0, size=N_ATOMS),
}


def _python_inputs(metric_id):
    """The built-in's leaf inputs by local name, for FlatInputSource."""
    out, todo = {}, [metric_id]
    while todo:
        schema, _fn = default_registry.get(todo.pop())
        for local, ref in schema.inputs.items():
            if ref in RAW:
                out[local] = RAW[ref]
            elif default_registry.has(ref):
                todo.append(ref)
        for local in schema.optional_inputs:        # offsets
            out[local] = RAW[local]
    return out


@pytest.mark.parametrize("metric_id", sorted(EQUIVALENTS))
def test_the_formula_gives_what_the_python_metric_gives(metric_id):
    expr, vars_ = equivalent_of(metric_id)
    inputs, shape, var_parts = analyze_expr(expr, vars_, registry=default_registry)
    schema, _fn = default_registry.get(metric_id)
    assert shape_to_str(shape) == shape_to_str(schema.shape)

    formula = _ExprFn(expr, inputs, var_parts).evaluate(**{n: RAW[r] for n, r in inputs.items()})
    python = InProcessExecutor(default_registry).run(
        metric_id, FlatInputSource(_python_inputs(metric_id)), {})
    np.testing.assert_allclose(formula, python.values, rtol=1e-12)


def test_a_metric_that_smooths_or_measures_geometry_has_no_equivalent():
    for metric_id in ("ffast.force_rmse_smoothed", "ffast.gyradius"):
        assert equivalent_of(metric_id) is None


def test_most_built_ins_now_have_an_exact_formula():
    """ADR 0064 section 9: about 21 of the 37 that had none."""
    assert len(EQUIVALENTS) >= 8 + 21

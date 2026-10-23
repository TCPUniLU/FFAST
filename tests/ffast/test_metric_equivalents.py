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
RAW = {
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


def test_a_metric_that_averages_has_no_equivalent():
    assert equivalent_of("ffast.force_mae_per_structure") is None

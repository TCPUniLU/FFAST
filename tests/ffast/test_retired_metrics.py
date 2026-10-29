"""The fixed force, acceleration and energy metrics are gone (ADR 0065
section 7): naming one is refused with the box setting that replaces it, and
that setting gives the numbers the old metric gave."""
import numpy as np
import pytest

import ffast.metrics.builtin  # noqa: F401  (registers the built-ins)
from ffast.metrics.boxes import compile_box_metric, parse_box_id
from ffast.metrics.execution import FlatInputSource
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.expr import _ExprFn, analyze_expr
from ffast.metrics.registry import MetricRegistry, default_registry
from ffast.metrics.retired import REPLACEMENTS, retired_message
from tests.ffast.test_metric_boxes import FIXED

OLD_IDS = [
    "force_mae", "force_rmse", "force_mae_per_structure", "force_mae_global",
    "force_rmse_global", "force_component_mae", "force_component_rmse",
    "force_component_mae_per_structure", "force_component_rmse_per_structure",
    "force_mae_per_element", "force_rmse_per_element", "force_net_mae_per_structure",
    "force_net_mae", "force_net_rmse", "accel_mae", "accel_rmse", "accel_mae_global",
    "accel_rmse_global", "accel_component_mae", "accel_component_rmse",
    "accel_mae_per_atom", "accel_mae_per_element", "energy_shift", "energy_mae",
    "energy_rmse", "energy_mae_shifted", "energy_rmse_shifted",
    "energy_mae_p", "energy_rmse_p", "energy_prediction_shifted",
]


@pytest.mark.parametrize("name", OLD_IDS)
def test_an_old_id_is_gone_and_its_message_names_the_replacement(name):
    old = f"ffast.{name}"
    assert not default_registry.has(old)
    message = retired_message(old)
    assert message.startswith(f"{old} no longer exists; use ")
    with pytest.raises(KeyError, match="no longer exists"):
        default_registry.get(old)


@pytest.mark.parametrize("old,box", sorted(REPLACEMENTS.items()))
def test_the_replacement_is_a_setting_the_boxes_allow(old, box):
    assert parse_box_id(box) is not None
    assert box in retired_message(old)
    assert compile_box_metric(box, registry=default_registry) == box


def test_the_message_says_the_boxes_in_words():
    assert retired_message("ffast.force_rmse") == (
        "ffast.force_rmse no longer exists; use Force Difference with structure ✓, "
        "Length, RMSE (ffast.force_difference[structure;length;rmse])")


def test_a_formula_naming_an_old_id_is_refused_with_the_message():
    with pytest.raises(ValueError, match="ffast.force_rmse no longer exists; use Force Difference"):
        analyze_expr("2 * e", {"e": "ffast.force_rmse"}, registry=default_registry)


def test_a_tab_naming_an_old_id_is_refused_with_the_message():
    from ffast.config.models import PanelMetricRef
    from ffast.config.tabs import resolve_ref
    with pytest.raises(ValueError, match="no longer exists"):
        resolve_ref(PanelMetricRef(metric="ffast.energy_mae"))
    with pytest.raises(ValueError, match="no longer exists"):
        resolve_ref(PanelMetricRef(metric="ffast.force_component_mae_per_structure",
                                   transform="smooth"))


# ── the replacement gives the old numbers ────────────────────────────────────
rng = np.random.default_rng(1)
OFFSETS = np.array([0, 3, 6, 9])            # three structures of the same atoms
RAW = {
    "offsets": OFFSETS,
    "reference.elements": np.array([8, 1, 1] * 3),
    "prediction.forces": rng.normal(size=(9, 3)),
    "reference.forces": rng.normal(size=(9, 3)),
    "prediction.energies": rng.normal(size=3),
    "reference.energies": rng.normal(size=3),
    "reference.masses": np.array([16.0, 1.0, 1.0] * 3),
}


def _leaf_inputs(metric_id):
    """Every raw input of a metric and its dependencies, by local name."""
    out, todo = {}, [metric_id]
    while todo:
        schema, _fn = default_registry.get(todo.pop())
        for local, ref in schema.inputs.items():
            if ref in RAW:
                out[local] = RAW[ref]
            elif ref == "offsets":
                out[local] = OFFSETS
            elif default_registry.ensure(ref):
                todo.append(ref)
        for local in schema.optional_inputs:
            out[local] = RAW[local]
    return out


@pytest.mark.parametrize("expr,vars_,box", FIXED)
def test_the_replacement_gives_the_old_formulas_numbers(expr, vars_, box):
    inputs, _shape, parts = analyze_expr(expr, vars_, registry=MetricRegistry())
    old = _ExprFn(expr, inputs, parts).evaluate(**{n: RAW[r] for n, r in inputs.items()})
    compile_box_metric(box, registry=default_registry)
    new = InProcessExecutor(default_registry).run(box, FlatInputSource(_leaf_inputs(box)), {})
    np.testing.assert_allclose(new.values, old, rtol=1e-12)

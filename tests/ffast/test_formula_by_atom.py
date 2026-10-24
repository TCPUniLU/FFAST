"""ADR 0065 section 8: by=atom, a per-atom value with a per-atom vector, and
max_parts (the Max statistic over x, y and z)."""
import numpy as np
import pytest

from ffast.metrics import dims
from ffast.metrics.execution import FlatInputSource
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.expr import analyze_expr, compile_expr_metric
from ffast.metrics.models import MetricFailure
from ffast.metrics.registry import MetricRegistry

F = {"pF": "prediction.forces", "rF": "reference.forces", "m": "reference.masses"}

# Two structures of the same three atoms (O, H, H), in the same order.
UNIFORM = {
    "offsets": np.array([0, 3, 6]),
    "reference.elements": np.array([8, 1, 1, 8, 1, 1]),
    "reference.forces": np.zeros((6, 3)),
    "prediction.forces": np.array([
        [3.0, 0, 4], [1, 0, 0], [0, 2, 0],
        [0, 0, 0], [1, 0, 0], [0, 0, 6],
    ]),
    "reference.masses": np.array([16.0, 1, 1, 16, 1, 1]),
}


def _run(expr, raw, vars_=F):
    reg = MetricRegistry()
    mid = compile_expr_metric("t.f", expr, vars_, registry=reg)
    inputs = reg.get(mid)[0].inputs
    src = {name: raw[ref] for name, ref in inputs.items()}
    return InProcessExecutor(reg).run(mid, FlatInputSource(src), {})


def _shape(expr, vars_=F):
    return analyze_expr(expr, vars_, registry=MetricRegistry())[1]


def test_by_atom_gives_one_value_per_atom_of_a_structure():
    assert _shape("mean(norm(pF - rF), by=atom)") == (dims.N_atoms,)
    result = _run("mean(norm(pF - rF), by=atom)", UNIFORM)
    # atom 0: lengths 5 and 0; atom 1: 1 and 1; atom 2: 2 and 6
    np.testing.assert_allclose(result.values, [2.5, 1.0, 4.0])


def test_by_atom_keeps_the_parts():
    assert _shape("mean(pF - rF, by=atom)") == (dims.N_atoms, dims.xyz)
    result = _run("max(pF - rF, by=atom)", UNIFORM)
    np.testing.assert_allclose(result.values, [[3, 0, 4], [1, 0, 0], [0, 2, 6]])


@pytest.mark.parametrize("change", ["size", "order"])
def test_by_atom_is_refused_when_structures_differ_in_atoms(change):
    raw = dict(UNIFORM)
    if change == "size":
        raw["offsets"] = np.array([0, 2, 6])
    else:
        raw["reference.elements"] = np.array([8, 1, 1, 1, 8, 1])
    result = _run("mean(norm(pF - rF), by=atom)", raw)
    assert isinstance(result, MetricFailure)
    assert "by=atom needs structures with the same atoms in the same order" in result.traceback


def test_a_per_atom_value_applies_to_each_part_of_a_per_atom_vector():
    assert _shape("(pF - rF) / m") == (dims.N_atoms, dims.xyz)
    result = _run("(pF - rF) / m", UNIFORM)
    np.testing.assert_allclose(result.values[0], [3 / 16, 0, 4 / 16])
    np.testing.assert_allclose(result.values[5], [0, 0, 6])


def test_a_per_structure_value_does_not_combine_with_a_per_atom_vector():
    with pytest.raises(ValueError, match="different Metric Shapes"):
        _shape("pF / rE", {**F, "rE": "reference.energies"})


def test_max_parts_takes_the_largest_part():
    assert _shape("max_parts(abs(pF - rF))") == (dims.N_atoms,)
    result = _run("max(max_parts(abs(pF - rF)), by=structure)", UNIFORM)
    np.testing.assert_allclose(result.values, [4.0, 6.0])

"""Formula language growth (ADR 0057 rule 17): comparisons, ``norm``, named
parts ``x(F)``, and positions ``F[2]`` / ``S[0,1]`` over the part axes."""
import numpy as np
import pytest

from ffast.metrics import dims
from ffast.metrics.execution import FlatInputSource
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.expr import (
    _ExprFn,
    analyze_expr,
    compile_expr_metric,
    parse_and_validate,
)
from ffast.metrics.registry import MetricRegistry


def _registry():
    """Per-frame, per-atom and vector / tensor source metrics."""
    r = MetricRegistry()

    @r.metric(id="t.ref_e", inputs={"reference": "reference.energies"},
              shape=(dims.N_frames,), unit="energy")
    def ref_e(reference):
        return np.asarray(reference, dtype=float)

    @r.metric(id="t.atom_q", inputs={"value": "reference.atoms.charges"},
              shape=(dims.N_atoms,), unit="dimensionless")
    def atom_q(value):
        return np.asarray(value, dtype=float)

    @r.metric(id="t.ref_f", inputs={"reference": "reference.forces"},
              shape=(dims.N_atoms, dims.xyz), unit="force")
    def ref_f(reference):
        return np.asarray(reference, dtype=float)

    @r.metric(id="t.pred_f", inputs={"predicted": "prediction.forces"},
              shape=(dims.N_atoms, dims.xyz), unit="force")
    def pred_f(predicted):
        return np.asarray(predicted, dtype=float)

    @r.metric(id="t.stress", inputs={"v": "reference.energies"},
              shape=(dims.N_frames, dims.tensor_3x3), unit="pressure")
    def stress(v):
        return np.zeros((len(v), 3, 3))

    @r.metric(id="t.stress_voigt", inputs={"v": "reference.energies"},
              shape=(dims.N_frames, dims.voigt), unit="pressure")
    def stress_voigt(v):
        return np.zeros((len(v), 6))

    return r


def _run(r, mid, **inputs):
    r.freeze()
    out = InProcessExecutor(r).run(mid, FlatInputSource(inputs), {})
    assert not hasattr(out, "traceback"), getattr(out, "traceback", "")
    return out.values


def test_whitelist_version_bumped():
    from ffast.metrics import expr
    assert expr._WHITELIST_VERSION == "4"


def test_parse_accepts_new_forms():
    for e in ("where(a > b, a, b)", "a <= 1", "a == b", "norm(a - b)",
              "x(F) + y(F) - z(F)", "F[2]", "S[0,1] * 2"):
        parse_and_validate(e)


def test_comparison_in_where():
    r = _registry()
    mid = compile_expr_metric("lab.x", "where(ref > 2, ref, 0)",
                              {"ref": "t.ref_e"}, registry=r)
    np.testing.assert_allclose(_run(r, mid, reference=[1.0, 3.0, 2.0]), [0, 3, 0])


@pytest.mark.parametrize("op,expected", [
    (">", [0, 0, 1]), ("<", [1, 0, 0]), (">=", [0, 1, 1]),
    ("<=", [1, 1, 0]), ("==", [0, 1, 0]),
])
def test_comparison_operators(op, expected):
    r = _registry()
    mid = compile_expr_metric("lab.x", f"ref {op} 2", {"ref": "t.ref_e"}, registry=r)
    assert r.get(mid)[0].shape == (dims.N_frames,)
    np.testing.assert_allclose(_run(r, mid, reference=[1.0, 2.0, 3.0]), expected)


def test_norm_drops_xyz_axis():
    r = _registry()
    mid = compile_expr_metric("lab.fe", "norm(p - t)",
                              {"p": "t.pred_f", "t": "t.ref_f"}, registry=r)
    assert r.get(mid)[0].shape == (dims.N_atoms,)
    out = _run(r, mid, predicted=[[3.0, 4.0, 0.0], [1, 1, 1]],
               reference=[[0.0, 0.0, 0.0], [1, 1, 1]])
    np.testing.assert_allclose(out, [5.0, 0.0])


def test_named_parts():
    r = _registry()
    mid = compile_expr_metric("lab.x", "x(F) + 10 * y(F) + 100 * z(F)",
                              {"F": "t.ref_f"}, registry=r)
    assert r.get(mid)[0].shape == (dims.N_atoms,)
    np.testing.assert_allclose(_run(r, mid, reference=[[1.0, 2.0, 3.0]]), [321.0])


def test_position_on_vector():
    r = _registry()
    mid = compile_expr_metric("lab.x", "F[2]", {"F": "t.ref_f"}, registry=r)
    assert r.get(mid)[0].shape == (dims.N_atoms,)
    np.testing.assert_allclose(_run(r, mid, reference=[[1.0, 2.0, 3.0]]), [3.0])


def test_position_on_3x3_takes_two_numbers():
    r = _registry()
    mid = compile_expr_metric("lab.x", "S[0,1]", {"S": "t.stress"}, registry=r)
    assert r.get(mid)[0].shape == (dims.N_frames,)
    ef = _ExprFn("S[0,1]", {"S": "t.stress"}, {"S": 2})
    np.testing.assert_allclose(ef(S=np.arange(9.0).reshape(1, 3, 3)), [1.0])


def test_position_on_voigt_indexes_into_six():
    r = _registry()
    mid = compile_expr_metric("lab.x", "S[1]", {"S": "t.stress_voigt"}, registry=r)
    assert r.get(mid)[0].shape == (dims.N_frames,)
    ef = _ExprFn("S[1]", {"S": "t.stress_voigt"}, {"S": 1})
    np.testing.assert_allclose(ef(S=np.array([[10.0, 20, 30, 40, 50, 60]])), [20.0])


def test_norm_of_3x3_is_frobenius():
    r = _registry()
    mid = compile_expr_metric("lab.x", "norm(S)", {"S": "t.stress"}, registry=r)
    assert r.get(mid)[0].shape == (dims.N_frames,)
    ef = _ExprFn("norm(S)", {"S": "t.stress"}, {"S": 2})
    np.testing.assert_allclose(ef(S=np.full((1, 3, 3), 2.0)), [6.0])


@pytest.mark.parametrize("expr,name,var,frag", [
    ("F[3]", "F", "t.ref_f", "out of range"),
    ("F[0,1]", "F", "t.ref_f", "position number"),
    ("S[0]", "S", "t.stress", "position number"),
    ("S[0,3]", "S", "t.stress", "out of range"),
    ("R[0]", "R", "t.ref_e", "part axes"),
    ("norm(R)", "R", "t.ref_e", "part axes"),
    ("x(R)", "R", "t.ref_e", "vector"),
    ("x(S)", "S", "t.stress", "vector"),
])
def test_part_errors_are_check_errors(expr, name, var, frag):
    with pytest.raises(ValueError, match=frag):
        compile_expr_metric("lab.x", expr, {name: var}, registry=_registry())


def test_variable_named_x_is_still_a_value():
    r = _registry()
    mid = compile_expr_metric("lab.x", "x + 1", {"x": "t.ref_e"}, registry=r)
    np.testing.assert_allclose(_run(r, mid, reference=[1.0, 2.0]), [2.0, 3.0])


def test_vector_cannot_mix_with_per_atom_scalar_until_a_part_is_taken():
    r = _registry()
    with pytest.raises(ValueError, match="different Metric Shapes"):
        compile_expr_metric("lab.x", "F + q", {"F": "t.ref_f", "q": "t.atom_q"}, registry=r)
    mid = compile_expr_metric("lab.y", "x(F) * q", {"F": "t.ref_f", "q": "t.atom_q"}, registry=r)
    assert r.get(mid)[0].shape == (dims.N_atoms,)


def test_analyze_expr_checks_without_registering():
    r = _registry()
    inputs, shape, parts = analyze_expr("norm(F)", {"F": "t.ref_f"}, registry=r)
    assert shape == (dims.N_atoms,)
    assert parts == {"F": 1}
    assert inputs == {"F": "t.ref_f"}
    assert not r.has("<expr>")

"""Expression Metrics: element-wise algebra over refs, no Python (ADR 0042).

Isolated tests run against a throwaway registry so the global default_registry
isn't polluted; a few compile against the real built-ins for metric-id binding.
"""
import pickle

import numpy as np
import pytest

from ffast.metrics import dims
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.registry import MetricRegistry
from ffast.metrics.expr import (
    _ExprFn,
    compile_expr_metric,
    compile_expr_metrics,
    parse_and_validate,
)


def _base_registry():
    """Fresh registry with per-frame + per-atom + scalar source metrics."""
    r = MetricRegistry()

    @r.metric(id="t.pred_e", inputs={"predicted": "prediction.energies"},
              shape=(dims.N_frames,), unit="energy")
    def pred_e(predicted):
        return np.asarray(predicted, dtype=float)

    @r.metric(id="t.ref_e", inputs={"reference": "reference.energies"},
              shape=(dims.N_frames,), unit="energy")
    def ref_e(reference):
        return np.asarray(reference, dtype=float)

    @r.metric(id="t.shift", inputs={"d": "t.ref_e"}, shape=(dims.scalar,),
              unit="energy")
    def shift(d):
        return float(np.mean(d))

    @r.metric(id="t.atom_q", inputs={"value": "reference.atoms.charges"},
              shape=(dims.N_atoms,), unit="dimensionless")
    def atom_q(value):
        return np.asarray(value, dtype=float)

    return r


# --- AST validation --------------------------------------------------------- #
def test_parse_accepts_whitelist():
    for expr in ("a + b", "abs(a - b) / n_atoms", "sqrt(a**2) * pi",
                 "clip(a, 0, 1)", "where(a, b, c)", "minimum(a, b)",
                 "-a + maximum(a, sign(b))", "log(a) + log10(b) + exp(c)"):
        parse_and_validate(expr)  # no raise


@pytest.mark.parametrize("expr,frag", [
    ("a.foo", "attribute"),
    ("a[0]", "subscript"),
    ("[x for x in a]", "comprehension"),
    ("a and b", "operator"),
    ("a > b", "operator"),
    ("floor(a)", "function"),
    ("lambda x: x", "lambda"),
    ("+a", "unary"),  # Decision 6: only unary - is permitted, not unary +
    ("a if b else c", None),
])
def test_parse_rejects_disallowed(expr, frag):
    with pytest.raises(ValueError) as ei:
        parse_and_validate(expr)
    if frag:
        assert frag in str(ei.value).lower()


def test_parse_rejects_syntax_error():
    with pytest.raises(ValueError):
        parse_and_validate("a +")


# --- compiler: identity + idempotency --------------------------------------- #
def test_compile_returns_id_and_registers():
    r = _base_registry()
    mid = compile_expr_metric(
        "lab.epa", "abs(pred - ref) / n_atoms",
        {"pred": "t.pred_e", "ref": "t.ref_e"}, registry=r,
    )
    assert mid == "lab.epa"
    assert r.has("lab.epa")


def test_compile_idempotent():
    r = _base_registry()
    a = compile_expr_metric("lab.x", "pred - ref",
                            {"pred": "t.pred_e", "ref": "t.ref_e"}, registry=r)
    n = len(r.list_metrics())
    b = compile_expr_metric("lab.x", "pred - ref",
                            {"pred": "t.pred_e", "ref": "t.ref_e"}, registry=r)
    assert a == b
    assert len(r.list_metrics()) == n  # no re-register, no raise


def test_compile_rejects_unnamespaced_id():
    r = _base_registry()
    with pytest.raises(ValueError, match="namespaced|dot"):
        compile_expr_metric("noDot", "pred - ref",
                            {"pred": "t.pred_e", "ref": "t.ref_e"}, registry=r)


def test_compile_rejects_reserved_n_atoms_var():
    r = _base_registry()
    with pytest.raises(ValueError, match="n_atoms"):
        compile_expr_metric("lab.x", "pred / n_atoms",
                            {"pred": "t.pred_e", "n_atoms": "t.ref_e"}, registry=r)


def test_compile_rejects_unknown_identifier():
    r = _base_registry()
    with pytest.raises(ValueError, match="unknown|identifier|nope"):
        compile_expr_metric("lab.x", "pred - nope",
                            {"pred": "t.pred_e"}, registry=r)


def test_compile_rejects_bad_ref():
    r = _base_registry()
    with pytest.raises(ValueError):
        compile_expr_metric("lab.x", "a + b",
                            {"a": "t.pred_e", "b": "reference.stress"}, registry=r)


def test_compile_rejects_elements_ref():
    # reference.elements is categorical (atomic numbers), not a continuous
    # quantity — rejected at config-load rather than crashing at compute.
    r = _base_registry()
    with pytest.raises(ValueError):
        compile_expr_metric("lab.x", "abs(z)",
                            {"z": "reference.elements"}, registry=r)


def test_var_named_self_does_not_collide():
    # A variable legitimately named `self` must not clash with _ExprFn.__call__'s
    # positional-only self (regression: kwargs collision → TypeError at compute).
    fn = _ExprFn("self * 2", {"self": "reference.energies"})
    np.testing.assert_allclose(fn(self=np.array([1.0, 2.0])), [2.0, 4.0])


# --- shapes ----------------------------------------------------------------- #
def test_output_shape_per_frame():
    r = _base_registry()
    mid = compile_expr_metric("lab.x", "pred - ref",
                             {"pred": "t.pred_e", "ref": "t.ref_e"}, registry=r)
    assert r.get(mid)[0].shape == (dims.N_frames,)


def test_output_shape_per_atom():
    r = _base_registry()
    mid = compile_expr_metric("lab.x", "abs(q)", {"q": "t.atom_q"}, registry=r)
    assert r.get(mid)[0].shape == (dims.N_atoms,)


def test_n_atoms_makes_output_per_frame():
    r = _base_registry()
    mid = compile_expr_metric("lab.x", "pred / n_atoms",
                             {"pred": "t.pred_e"}, registry=r)
    assert r.get(mid)[0].shape == (dims.N_frames,)
    # n_atoms is wired as an input ref
    assert r.get(mid)[0].inputs.get("n_atoms") == "n_atoms"


def test_scalar_broadcasts_against_array():
    r = _base_registry()
    # per-frame minus scalar → per-frame (scalar broadcasts, same-shape passes)
    mid = compile_expr_metric("lab.x", "ref - s",
                             {"ref": "t.ref_e", "s": "t.shift"}, registry=r)
    assert r.get(mid)[0].shape == (dims.N_frames,)


def test_all_scalar_output_scalar():
    r = _base_registry()
    mid = compile_expr_metric("lab.x", "s * 2 + pi", {"s": "t.shift"}, registry=r)
    assert r.get(mid)[0].shape == (dims.scalar,)


def test_mixed_shape_rejected():
    r = _base_registry()
    with pytest.raises(ValueError, match="shape|per-atom|per-structure|N_atoms|N_frames"):
        compile_expr_metric("lab.x", "e + q",
                            {"e": "t.pred_e", "q": "t.atom_q"}, registry=r)


# --- graph wiring ----------------------------------------------------------- #
def test_metric_id_vars_become_deps_and_freeze_ok():
    r = _base_registry()
    mid = compile_expr_metric("lab.x", "pred - ref",
                             {"pred": "t.pred_e", "ref": "t.ref_e"}, registry=r)
    assert r.freeze() == []
    assert {"t.pred_e", "t.ref_e"} <= r.dependencies_of(mid)


def test_freeze_ok_with_n_atoms():
    r = _base_registry()
    compile_expr_metric("lab.x", "pred / n_atoms", {"pred": "t.pred_e"}, registry=r)
    assert r.freeze() == []


# --- numeric evaluation ----------------------------------------------------- #
def test_eval_energy_per_atom():
    r = _base_registry()
    mid = compile_expr_metric("lab.epa", "abs(pred - ref) / n_atoms",
                             {"pred": "t.pred_e", "ref": "t.ref_e"}, registry=r)
    r.freeze()
    ex = InProcessExecutor(r)
    out = ex.run(mid, {
        "predicted": [10.0, 20.0], "reference": [8.0, 26.0], "n_atoms": [2, 3],
    }, {})
    assert not hasattr(out, "traceback"), getattr(out, "traceback", "")
    np.testing.assert_allclose(out.values, [1.0, 2.0])


def test_eval_offset_correction():
    r = _base_registry()
    mid = compile_expr_metric("lab.x", "ref - s",
                             {"ref": "t.ref_e", "s": "t.shift"}, registry=r)
    r.freeze()
    ex = InProcessExecutor(r)
    # t.shift = mean(reference) = 2.0; ref - s = [-1, 0, 1]
    out = ex.run(mid, {"reference": [1.0, 2.0, 3.0]}, {})
    np.testing.assert_allclose(out.values, [-1.0, 0.0, 1.0])


def test_eval_whitelist_functions():
    r = _base_registry()
    mid = compile_expr_metric("lab.x", "sqrt(clip(ref, 0, 100)) + pi",
                             {"ref": "t.ref_e"}, registry=r)
    r.freeze()
    out = InProcessExecutor(r).run(mid, {"reference": [4.0, 9.0]}, {})
    np.testing.assert_allclose(out.values, [2.0 + np.pi, 3.0 + np.pi])


# --- non-finite policy (Decision 7) ----------------------------------------- #
def test_non_finite_raises_metric_failure():
    r = _base_registry()
    mid = compile_expr_metric("lab.x", "ref / n_atoms",
                             {"ref": "t.ref_e"}, registry=r)
    r.freeze()
    out = InProcessExecutor(r).run(mid, {"reference": [1.0, 2.0], "n_atoms": [1, 0]}, {})
    assert hasattr(out, "traceback")
    assert mid in out.metric_id


# --- cache identity (Decision 8) -------------------------------------------- #
def test_impl_source_busts_on_expr_edit():
    a = _ExprFn("pred - ref", {"pred": "t.pred_e", "ref": "t.ref_e"})
    b = _ExprFn("pred + ref", {"pred": "t.pred_e", "ref": "t.ref_e"})
    from ffast.metrics.cache import function_hash
    assert function_hash(a) != function_hash(b)


def test_impl_source_busts_on_var_ref_swap():
    a = _ExprFn("pred - ref", {"pred": "t.pred_e", "ref": "t.ref_e"})
    b = _ExprFn("pred - ref", {"pred": "prediction.energies", "ref": "t.ref_e"})
    from ffast.metrics.cache import function_hash
    assert function_hash(a) != function_hash(b)


def test_expr_fn_picklable():
    fn = _ExprFn("abs(a - b)", {"a": "reference.energies", "b": "prediction.energies"})
    out = pickle.loads(pickle.dumps(fn))(a=np.array([3.0]), b=np.array([1.0]))
    np.testing.assert_allclose(out, [2.0])


# --- config-model + batch compile ------------------------------------------- #
def test_expr_metric_config_validation():
    from ffast.config.models import ExprMetricConfig
    ExprMetricConfig(id="lab.x", expr="a - b", vars={"a": "reference.energies",
                                                     "b": "prediction.energies"})
    with pytest.raises(ValueError):
        ExprMetricConfig(id="noDot", expr="a", vars={"a": "reference.energies"})
    with pytest.raises(ValueError):
        ExprMetricConfig(id="lab.x", expr="a", vars={"n_atoms": "reference.energies"})


def test_compile_expr_metrics_batch():
    r = _base_registry()
    from ffast.config.models import ExprMetricConfig
    cfgs = [
        ExprMetricConfig(id="lab.a", expr="pred - ref",
                         vars={"pred": "t.pred_e", "ref": "t.ref_e"}),
        ExprMetricConfig(id="lab.b", expr="abs(q)", vars={"q": "t.atom_q"}),
    ]
    ids = compile_expr_metrics(cfgs, registry=r)
    assert ids == ["lab.a", "lab.b"]
    assert r.freeze() == []


# --- reserved n_atoms resolution (Decision 4) ------------------------------- #
def test_resolver_n_atoms_uniform():
    from ffast.metrics.input_resolver import InputResolver

    class _Uniform:
        isVariable = False
        def getN(self):
            return 4
        def getNAtoms(self):
            return 3

    out = InputResolver(env=None).resolve("n_atoms", dataset=_Uniform())
    np.testing.assert_array_equal(out, [3, 3, 3, 3])


def test_resolver_n_atoms_variable():
    from ffast.metrics.input_resolver import InputResolver

    class _Variable:
        isVariable = True
        molecule_offsets = np.array([0, 2, 5, 6])

    out = InputResolver(env=None).resolve("n_atoms", dataset=_Variable())
    np.testing.assert_array_equal(out, [2, 3, 1])  # np.diff(offsets)

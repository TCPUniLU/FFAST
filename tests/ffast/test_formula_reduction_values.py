"""What a reduction computes, and "no value" next to bad values (ADR 0064
step 4, sections 1-3, 7 and 8)."""
import numpy as np
import pytest

from ffast.metrics import dims
from ffast.metrics.execution import FlatInputSource
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.expr import _ExprFn, analyze_expr, compile_expr_metric
from ffast.metrics.models import MetricFailure
from ffast.metrics.registry import MetricRegistry
from ffast.metrics.try_formula import try_formula

# Three structures of 2, 3 and 1 atoms; elements O H | C H H | O.
OFFSETS = np.array([0, 2, 5, 6])
Z = np.array([8, 1, 6, 1, 1, 8])
ERR = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
F = np.arange(18, dtype=float).reshape(6, 3)
E = np.array([1.0, -2.0, 4.0])

VARS = {"err": "reference.atoms.err", "F": "reference.forces", "E": "reference.energies"}
RAW = {"err": ERR, "F": F, "E": E, "_offsets": OFFSETS, "_elements": Z}


def _values(expr, vars_=VARS, raw=RAW, registry=None):
    r = registry or MetricRegistry()
    inputs, _shape, parts = analyze_expr(expr, vars_, registry=r)
    return _ExprFn(expr, inputs, parts)(**{n: raw[n] for n in inputs})


# ── section 1: by=structure, by=element, by=all ──────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("mean", [1.5, 4.0, 6.0]),
    ("sum", [3.0, 12.0, 6.0]),
    ("min", [1.0, 3.0, 6.0]),
    ("max", [2.0, 5.0, 6.0]),
    ("rms", [np.sqrt(2.5), np.sqrt(50 / 3), 6.0]),
    ("std", [0.5, np.std([3.0, 4.0, 5.0]), 0.0]),     # a group of one has std 0
])
def test_each_reduction_by_structure_of_unequal_sizes(name, expected):
    np.testing.assert_allclose(_values(f"{name}(err, by=structure)"), expected)


def test_structures_of_equal_size():
    raw = {**RAW, "_offsets": np.array([0, 3, 6])}
    np.testing.assert_allclose(_values("mean(err, by=structure)", raw=raw), [2.0, 5.0])


def test_by_element_lists_elements_in_order_of_atomic_number():
    # H (1): 2, 4, 5   C (6): 3   O (8): 1, 6
    np.testing.assert_allclose(_values("mean(err, by=element)"), [11 / 3, 3.0, 3.5])


def test_by_all_counts_every_atom_once():
    assert _values("mean(err, by=all)") == pytest.approx(3.5)
    # The mean of the per-structure means is something else, written out.
    assert _values("mean(mean(err, by=structure), by=all)") == pytest.approx((1.5 + 4 + 6) / 3)


def test_a_reduction_keeps_the_vector_parts():
    np.testing.assert_allclose(_values("sum(F, by=structure)"),
                               [F[0:2].sum(0), F[2:5].sum(0), F[5:6].sum(0)])
    np.testing.assert_allclose(_values("sum(F, by=all)"), F.sum(0))


def test_mean_parts_and_sum_parts():
    np.testing.assert_allclose(_values("mean_parts(F)"), F.mean(1))
    np.testing.assert_allclose(_values("sum_parts(F)"), F.sum(1))


def test_a_per_structure_value_by_all():
    assert _values("mean(abs(E), by=all)") == pytest.approx(7 / 3)
    np.testing.assert_allclose(_values("abs(E - mean(E, by=all))"), np.abs(E - 1.0))


def test_a_one_number_spreads_onto_atoms():
    np.testing.assert_allclose(_values("err / max(err, by=all)"), ERR / 6.0)


# ── section 7: element(...) and where= ───────────────────────────────────────

def test_element_is_one_for_those_atoms():
    np.testing.assert_allclose(_values('element("H")'), [0, 1, 0, 1, 1, 0])
    np.testing.assert_allclose(_values('1 - element("C", "O")'), [0, 1, 0, 1, 1, 0])
    np.testing.assert_allclose(_values('where(element("H"), err, 0)'), [0, 2, 0, 4, 5, 0])


def test_where_leaves_rows_out():
    np.testing.assert_allclose(_values('mean(err, by=structure, where=element("O", "C"))'),
                               [1.0, 3.0, 6.0])
    assert _values("mean(err, by=all, where=err > 2)") == pytest.approx(4.5)
    raw = {**RAW, "n_atoms": np.diff(OFFSETS)}
    assert _values("sum(E, by=all, where=n_atoms > 1)", raw=raw) == pytest.approx(-1.0)


# ── section 8: no value ──────────────────────────────────────────────────────

def test_a_structure_with_nothing_left_has_no_value_and_the_metric_succeeds():
    out = _values('mean(err, by=structure, where=element("H"))')
    np.testing.assert_allclose(out[:2], [2.0, 4.5])
    assert np.isnan(out[2])                          # the last structure has no H


def test_by_element_with_a_filter_lists_every_element():
    out = _values('mean(err, by=element, where=element("H"))')
    assert out[0] == pytest.approx(11 / 3) and np.isnan(out[1:]).all()


def test_a_structure_with_no_atoms_has_no_value():
    raw = {**RAW, "_offsets": np.array([0, 2, 2, 6])}
    out = _values("sum(err, by=structure)", raw=raw)
    np.testing.assert_allclose(out[[0, 2]], [3.0, 18.0])
    assert np.isnan(out[1])


def test_a_later_reduction_leaves_no_value_rows_out():
    assert _values('mean(mean(err, by=structure, where=element("H")), by=all)') == pytest.approx(3.25)


def test_when_everything_is_left_out_the_result_has_no_value():
    assert np.isnan(_values('mean(err, by=all, where=element("N"))'))


def test_arithmetic_on_a_gap_is_a_gap():
    out = _values('mean(err, by=structure, where=element("H")) * 2 + 1')
    assert np.isnan(out[2]) and out[0] == pytest.approx(5.0)


def test_a_bad_value_still_fails_next_to_a_no_value_row():
    raw = {**RAW, "err": np.array([1.0, np.inf, 3.0, 4.0, 5.0, 6.0])}
    with pytest.raises(ValueError, match="non-finite"):
        _values('mean(err, by=structure, where=element("H"))', raw=raw)


def test_evaluate_tells_a_bad_value_from_no_value():
    raw = {**RAW, "err": np.array([1.0, np.inf, 3.0, 4.0, 5.0, 6.0])}
    expr = 'mean(err, by=structure, where=element("H"))'
    inputs, _s, parts = analyze_expr(expr, VARS, registry=MetricRegistry())
    out, gap, _ignored = _ExprFn(expr, inputs, parts).evaluate_split(**{n: raw[n] for n in inputs})
    assert gap.tolist() == [False, False, True]
    assert np.isinf(out[0]) and np.isnan(out[2])


def _with_gaps_registry():
    """A formula metric whose last structure has no value, and a Python metric
    that gives NaN."""
    r = MetricRegistry()

    @r.metric(id="t.py_nan", inputs={"v": "reference.energies"}, shape=(dims.N_frames,), unit="x")
    def py_nan(v):
        return np.array([1.0, np.nan, 3.0])

    compile_expr_metric("t.h_err", 'mean(err, by=structure, where=element("H"))',
                        {"err": "reference.atoms.err"}, registry=r)
    return r


def _run(r, expr, vars_):
    mid = compile_expr_metric("t.out", expr, vars_, registry=r)
    return InProcessExecutor(r).run(mid, FlatInputSource({"err": ERR, "v": E, "_offsets": OFFSETS,
                                                           "_elements": Z}), {})


def test_a_nan_from_a_formula_input_is_no_value():
    r = _with_gaps_registry()
    out = _run(r, "h * 2", {"h": "t.h_err"})
    assert not isinstance(out, MetricFailure), getattr(out, "traceback", "")
    np.testing.assert_allclose(out.values[:2], [4.0, 9.0])
    assert np.isnan(out.values[2])


def test_a_nan_from_a_python_input_fails_the_metric():
    r = _with_gaps_registry()
    out = _run(r, "p * 2", {"p": "t.py_nan"})
    assert isinstance(out, MetricFailure) and "non-finite" in out.traceback


def test_the_hidden_inputs_are_the_offsets_and_the_elements():
    inputs, _s, _p = analyze_expr('mean(err, by=structure, where=element("H"))', VARS,
                                  registry=MetricRegistry())
    assert inputs == {"err": "reference.atoms.err", "_offsets": "offsets",
                      "_elements": "reference.elements"}
    inputs, _s, _p = analyze_expr("mean(err, by=all)", VARS, registry=MetricRegistry())
    assert inputs == {"err": "reference.atoms.err"}


def test_a_hidden_name_cannot_be_a_variable():
    with pytest.raises(ValueError, match="_offsets"):
        analyze_expr("_offsets * 2", {"_offsets": "reference.energies"}, registry=MetricRegistry())


# ── Try counts no value apart from bad values ────────────────────────────────

def _resolve(ref):
    return {"reference.atoms.err": ERR, "offsets": OFFSETS, "reference.elements": Z,
            "reference.forces": F}.get(ref)


def test_try_counts_the_structures_with_no_value():
    r = try_formula('mean(err, by=structure, where=element("H"))', {"err": "reference.atoms.err"},
                    MetricRegistry(), _resolve, np.diff(OFFSETS))
    assert (r.no_value_count, r.no_value_rows, r.bad_count) == (1, "structures", 0)
    assert (r.min, r.max, r.count) == (2.0, 4.5, 3)


def test_try_by_structure_in_chunks_gives_the_one_pass_answer():
    expr = 'mean(err, by=structure, where=element("H"))'
    one = try_formula(expr, {"err": "reference.atoms.err"}, MetricRegistry(), _resolve,
                      np.diff(OFFSETS), chunk_frames=100)
    many = try_formula(expr, {"err": "reference.atoms.err"}, MetricRegistry(), _resolve,
                       np.diff(OFFSETS), chunk_frames=1)
    assert one.to_dict() == many.to_dict()


def test_try_of_a_dataset_wide_word_is_one_pass_over_everything():
    r = try_formula("err / max(err, by=all)", {"err": "reference.atoms.err"},
                    MetricRegistry(), _resolve, np.diff(OFFSETS), chunk_frames=1)
    assert (r.min, r.max) == (pytest.approx(1 / 6), 1.0)


def test_try_of_a_result_with_no_value_says_so():
    r = try_formula('mean(err, by=all, where=element("N"))', {"err": "reference.atoms.err"},
                    MetricRegistry(), _resolve, np.diff(OFFSETS))
    assert (r.no_value_count, r.no_value_rows, r.mean) == (1, "result", None)


def test_the_catalog_shows_only_the_variables_the_user_named():
    from ffast.metrics.catalog import build_metric_catalog
    r = MetricRegistry()
    compile_expr_metric("t.h", 'mean(err, by=structure, where=element("H")) / n_atoms',
                        {"err": "reference.atoms.err"}, registry=r)
    [entry] = [e for e in build_metric_catalog(r) if e["id"] == "t.h"]
    assert entry["vars"] == {"err": "reference.atoms.err"}


def test_a_registry_holding_a_grouping_formula_freezes():
    r = MetricRegistry()
    compile_expr_metric("t.h", 'mean(norm(p - q), by=structure, where=element("H"))',
                        {"p": "prediction.forces", "q": "reference.forces"}, registry=r)
    assert r.freeze() == []


# ── review fixes: a bad value is never skipped ───────────────────────────────

@pytest.mark.parametrize("name,bad", [("min", np.inf), ("max", -np.inf), ("min", np.nan)])
def test_min_and_max_never_skip_a_bad_value(name, bad):
    raw = {**RAW, "err": np.array([1.0, bad, 3.0, 4.0, 5.0, 6.0])}
    with pytest.raises(ValueError, match="non-finite"):
        _values(f"{name}(err, by=structure)", raw=raw)


@pytest.mark.parametrize("w", [np.nan, np.inf])
def test_a_bad_value_in_where_fails_the_metric(w):
    vars_ = {**VARS, "w": "reference.atoms.w"}
    raw = {**RAW, "w": np.array([1.0, w, 1.0, 0.0, 1.0, 1.0])}
    with pytest.raises(ValueError, match="non-finite"):
        _values("mean(err, by=all, where=w)", vars_=vars_, raw=raw)


# ── amendment: a bad value where= leaves out is ignored, with a warning ──────

def _with_broken_carbon():
    # The carbon atom (row 2, structure 1) has a broken value.
    return {**RAW, "err": np.array([1.0, 2.0, np.nan, 4.0, 5.0, 6.0])}


def test_a_bad_value_left_out_by_where_does_not_fail_but_warns():
    from ffast.metrics.models import WithWarnings
    out = _values('mean(err, by=structure, where=element("H"))', raw=_with_broken_carbon())
    assert isinstance(out, WithWarnings)
    np.testing.assert_allclose(out.values[:2], [2.0, 4.5])
    assert out.warnings == ["1 bad value in rows left out by where= was ignored (frame 1)"]


def test_without_where_the_same_bad_value_fails():
    with pytest.raises(ValueError, match="non-finite"):
        _values("mean(err, by=structure)", raw=_with_broken_carbon())


def test_a_clean_result_carries_no_warning():
    out = _values('mean(err, by=structure, where=element("H"))')
    assert isinstance(out, np.ndarray)


def test_the_warning_counts_and_lists_frames():
    from ffast.metrics.expr import ignored_warning
    assert ignored_warning(3, [4, 9, 12]) == (
        "3 bad values in rows left out by where= were ignored (frames 4, 9, 12)")
    assert ignored_warning(7, list(range(7))) == (
        "7 bad values in rows left out by where= were ignored (frames 0, 1, 2, 3, 4, …)")
    assert ignored_warning(2, []) == "2 bad values in rows left out by where= were ignored"


def test_a_metric_result_carries_the_warning_and_passes_it_on():
    r = MetricRegistry()
    compile_expr_metric("t.h", 'mean(err, by=structure, where=element("H"))',
                        {"err": "reference.atoms.err"}, registry=r)
    compile_expr_metric("t.h2", "2 * h", {"h": "t.h"}, registry=r)
    source = FlatInputSource({"err": _with_broken_carbon()["err"], "_offsets": OFFSETS, "_elements": Z})
    out = InProcessExecutor(r).run("t.h2", source, {})
    assert not isinstance(out, MetricFailure), getattr(out, "traceback", "")
    assert out.warnings == ["1 bad value in rows left out by where= was ignored (frame 1)"]
    np.testing.assert_allclose(out.values[:2], [4.0, 9.0])


def test_the_warning_travels_in_the_metric_result_message():
    from ffast.metrics.models import MetricResult
    from ffast.protocol.rpc import pack_metric_result, unpack, unpack_metric_result
    result = MetricResult(metric_id="t.h", shape="N_frames", dtype="float64", unit="x",
                          compute_parameters={}, implementation_hash="h", checksum="c",
                          values=np.array([1.0, np.nan]), warnings=["careful"])
    _event, _args, kwargs = unpack(pack_metric_result("r1", "t.h", True, result))
    assert kwargs["warnings"] == ["careful"]
    assert unpack_metric_result(kwargs).warnings == ["careful"]
    plain = result.model_copy(update={"warnings": []})
    _e, _a, kwargs = unpack(pack_metric_result("r1", "t.h", True, plain))
    assert "warnings" not in kwargs                  # an old client never sees the field


def test_try_reports_the_ignored_bad_values():
    def resolve(ref):
        return {"reference.atoms.err": _with_broken_carbon()["err"], "offsets": OFFSETS,
                "reference.elements": Z}.get(ref)
    for chunk in (100, 1):
        r = try_formula('mean(err, by=structure, where=element("H"))', {"err": "reference.atoms.err"},
                        MetricRegistry(), resolve, np.diff(OFFSETS), chunk_frames=chunk)
        assert r.bad_count == 0
        assert r.ignored_warning == "1 bad value in rows left out by where= was ignored (frame 1)"

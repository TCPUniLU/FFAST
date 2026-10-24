"""Per structure and per element at once (ADR 0064 amendment 2026-10-24):
``by=(structure, element)`` gives a table, a row per structure and a column
per element. Step 1: the parser and the shape check."""
import pytest

from ffast.metrics import dims
from ffast.metrics.expr import analyze_expr, colour_problem, parse_and_validate, shape_words
from ffast.metrics.registry import MetricRegistry

VARS = {"pF": "prediction.forces", "rF": "reference.forces",
        "pE": "prediction.energies", "rE": "reference.energies"}
TABLE = (dims.N_frames, dims.N_elements)
PAIR_ERR = "mean(mean_parts(abs(pF - rF)), by=(structure, element))"


def _registry():
    r = MetricRegistry()

    @r.metric(id="t.per_element", inputs={"v": "reference.elements"},
              shape=(dims.N_elements,), unit="force")
    def per_element(v):
        return v

    @r.metric(id="t.table", inputs={"v": "reference.elements"}, shape=TABLE, unit="force")
    def table(v):
        return v

    return r


def _shape(expr):
    return analyze_expr(expr, {**VARS, "pe": "t.per_element", "tb": "t.table"},
                        registry=_registry())[1]


def _error(expr):
    with pytest.raises(ValueError) as info:
        _shape(expr)
    return str(info.value)


# ── point 1: the pair after by= ──────────────────────────────────────────────

@pytest.mark.parametrize("name", ["mean", "sum", "min", "max", "rms", "std"])
def test_every_reduction_takes_the_pair(name):
    assert _shape(f"{name}(norm(pF - rF), by=(structure, element))") == TABLE


def test_the_pair_takes_where():
    assert _shape('mean(norm(pF - rF), by=(structure, element), where=element("H"))') == TABLE


def test_the_pair_keeps_the_vector_parts():
    assert _shape("sum(pF - rF, by=(structure, element))") == (*TABLE, dims.xyz)


def test_the_pair_needs_a_per_atom_value():
    msg = _error("mean(rE, by=(structure, element))")
    assert "by=(structure, element) needs a per-atom value; rE is per structure" in msg


def test_the_other_order_names_the_right_one():
    assert "write by=(structure, element), structure first" in _error(
        "mean(norm(pF), by=(element, structure))")


@pytest.mark.parametrize("by", ["(structure, all)", "(structure,)", "(structure, element, all)"])
def test_no_other_pair_is_a_by_word(by):
    assert "by= takes structure, element, all or (structure, element)" in _error(
        f"mean(norm(pF), by={by})")


def test_the_pair_words_are_not_inputs():
    # analyze_expr would name them as unknown identifiers otherwise.
    inputs = analyze_expr(PAIR_ERR, VARS, registry=MetricRegistry())[0]
    assert set(inputs) == {"pF", "rF", "_offsets", "_elements"}


def test_the_pair_is_parsed():
    parse_and_validate("mean(a, by=(structure, element), where=b > 1)")


# ── point 2: its name in words ───────────────────────────────────────────────

def test_the_shape_check_names_the_table():
    assert shape_words(TABLE) == "per structure and element"
    assert shape_words((*TABLE, dims.xyz)) == "per structure and element, a vector"


# ── point 3: what a table combines with ──────────────────────────────────────

@pytest.mark.parametrize("expr", [
    f"{PAIR_ERR} * 1000",
    f"{PAIR_ERR} / mean(norm(pF - rF), by=all)",
    f"{PAIR_ERR} - tb",
    f"abs(tb)",
])
def test_a_table_combines_with_one_number_and_with_a_table(expr):
    assert _shape(expr) == TABLE


@pytest.mark.parametrize("other", ["rE", "pe", "norm(pF)"])
def test_a_table_does_not_mix_with_another_shape(other):
    assert "different Metric Shapes" in _error(f"{PAIR_ERR} - {other}")


@pytest.mark.parametrize("by", ["structure", "element", "all"])
def test_a_table_cannot_be_reduced_again(by):
    assert "tb is per structure and element, which can't be reduced again yet" in _error(
        f"mean(tb, by={by})")


# ── point 4: the pair colours ────────────────────────────────────────────────

def test_the_pair_is_not_refused_for_colouring():
    assert colour_problem(PAIR_ERR) is None


def test_by_element_on_its_own_is_still_refused():
    assert colour_problem(f"{PAIR_ERR} + mean(norm(pF), by=element) * 0") is not None


# ── step 2: what the pair computes ───────────────────────────────────────────

import numpy as np  # noqa: E402

from ffast.metrics.execution import FlatInputSource  # noqa: E402
from ffast.metrics.executor import InProcessExecutor  # noqa: E402
from ffast.metrics.expr import _ExprFn, compile_expr_metric  # noqa: E402
from ffast.metrics.models import MetricFailure, WithWarnings  # noqa: E402

# Three structures of 2, 3 and 1 atoms; elements O H | C H H | O, so the
# columns are H, C, O.
OFFSETS = np.array([0, 2, 5, 6])
Z = np.array([8, 1, 6, 1, 1, 8])
ERR = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
F = np.arange(18, dtype=float).reshape(6, 3)
EVARS = {"err": "reference.atoms.err", "F": "reference.forces"}
RAW = {"err": ERR, "F": F, "_offsets": OFFSETS, "_elements": Z}
NAN = np.nan


def _values(expr, raw=RAW):
    inputs, _shape, parts = analyze_expr(expr, EVARS, registry=MetricRegistry())
    return _ExprFn(expr, inputs, parts)(**{n: raw[n] for n in inputs})


@pytest.mark.parametrize("name,expected", [
    ("mean", [[2.0, NAN, 1.0], [4.5, 3.0, NAN], [NAN, NAN, 6.0]]),
    ("sum", [[2.0, NAN, 1.0], [9.0, 3.0, NAN], [NAN, NAN, 6.0]]),
    ("max", [[2.0, NAN, 1.0], [5.0, 3.0, NAN], [NAN, NAN, 6.0]]),
    ("std", [[0.0, NAN, 0.0], [0.5, 0.0, NAN], [NAN, NAN, 0.0]]),
])
def test_a_row_per_structure_and_a_column_per_element(name, expected):
    np.testing.assert_allclose(_values(f"{name}(err, by=(structure, element))"), expected)


def test_structures_of_equal_size():
    raw = {**RAW, "_offsets": np.array([0, 3, 6])}
    # O H C | H H O
    np.testing.assert_allclose(_values("mean(err, by=(structure, element))", raw),
                               [[2.0, 3.0, 1.0], [4.5, NAN, 6.0]])


def test_the_columns_are_every_element_of_the_dataset():
    # Structure 3 holds only O, yet its row has the H and C columns, empty.
    out = _values("mean(err, by=(structure, element))")
    assert out.shape == (3, 3) and np.isnan(out[2, :2]).all()


def test_where_empties_the_cells_it_leaves_out():
    np.testing.assert_allclose(_values('mean(err, by=(structure, element), where=element("H", "O"))'),
                               [[2.0, NAN, 1.0], [4.5, NAN, NAN], [NAN, NAN, 6.0]])
    np.testing.assert_allclose(_values("mean(err, by=(structure, element), where=err > 1.5)"),
                               [[2.0, NAN, NAN], [4.5, 3.0, NAN], [NAN, NAN, 6.0]])


def test_the_vector_parts_are_kept():
    out = _values("sum(F, by=(structure, element))")
    assert out.shape == (3, 3, 3)
    np.testing.assert_allclose(out[1, 0], F[3] + F[4])      # structure 2's two H


def test_a_table_combines_cell_by_cell():
    np.testing.assert_allclose(_values("mean(err, by=(structure, element)) * 2 + 1")[1], [10.0, 7.0, NAN])


def test_a_bad_value_in_a_counted_cell_fails_the_metric():
    raw = {**RAW, "err": np.array([1.0, np.inf, 3.0, 4.0, 5.0, 6.0])}
    with pytest.raises(ValueError, match="non-finite"):
        _values("mean(err, by=(structure, element))", raw)


def test_a_bad_value_where_leaves_out_is_ignored_with_a_warning():
    raw = {**RAW, "err": np.array([1.0, 2.0, np.inf, 4.0, 5.0, 6.0])}
    out = _values('mean(err, by=(structure, element), where=element("H"))', raw)
    assert isinstance(out, WithWarnings) and "frame 1" in out.warnings[0]


def test_a_table_from_a_formula_input_keeps_its_empty_cells():
    r = MetricRegistry()
    compile_expr_metric("t.table", "mean(err, by=(structure, element))",
                        {"err": "reference.atoms.err"}, registry=r)
    mid = compile_expr_metric("t.twice", "t * 2", {"t": "t.table"}, registry=r)
    out = InProcessExecutor(r).run(mid, FlatInputSource(
        {"err": ERR, "_offsets": OFFSETS, "_elements": Z}), {})
    assert not isinstance(out, MetricFailure), getattr(out, "traceback", "")
    np.testing.assert_allclose(out.values, [[4.0, NAN, 2.0], [9.0, 6.0, NAN], [NAN, NAN, 12.0]])


def test_one_structure_without_offsets_is_one_row():
    # The colouring path runs a metric on the frame on screen, without offsets.
    raw = {"err": ERR[2:5], "_elements": Z[2:5], "_offsets": None}
    np.testing.assert_allclose(_values("mean(err, by=(structure, element))", raw), [[4.5, 3.0]])

"""Formulas average, total and take extremes by structure, element or all,
over chosen atoms (ADR 0064): the parser and the shape check (step 2)."""
import pytest

from ffast.metrics import dims
from ffast.metrics.expr import analyze_expr, parse_and_validate
from ffast.metrics.registry import MetricRegistry

F = {"pF": "prediction.forces", "rF": "reference.forces"}
E = {"pE": "prediction.energies", "rE": "reference.energies"}
ALL = {**F, **E, "m": "reference.masses"}


def _registry():
    r = MetricRegistry()

    @r.metric(id="t.per_element", inputs={"v": "reference.elements"},
              shape=(dims.N_elements,), unit="force")
    def per_element(v):
        return v

    return r


def _shape(expr, vars_=ALL):
    return analyze_expr(expr, {**vars_, "pe": "t.per_element"}, registry=_registry())[1]


def _error(expr, vars_=ALL):
    with pytest.raises(ValueError) as info:
        _shape(expr, vars_)
    return str(info.value)


# ── section 1: the table ─────────────────────────────────────────────────────

@pytest.mark.parametrize("expr,shape", [
    ("mean(norm(pF - rF), by=structure)", (dims.N_frames,)),
    ("mean(norm(pF - rF), by=element)", (dims.N_elements,)),
    ("mean(norm(pF - rF), by=all)", (dims.scalar,)),
    ("mean(pE - rE, by=all)", (dims.scalar,)),
    ("mean(pe, by=all)", (dims.scalar,)),
])
def test_by_decides_the_result_shape(expr, shape):
    assert _shape(expr) == shape


@pytest.mark.parametrize("word", ["structure", "element"])
def test_a_per_structure_input_cannot_be_grouped_by_structure_or_element(word):
    msg = _error(f"mean(rE, by={word})")
    assert f"by={word} needs a per-atom value; rE is per structure" in msg


def test_a_one_number_input_has_nothing_to_combine():
    msg = _error("mean(mean(rE, by=all), by=all)")
    assert "by=all needs a per-atom, per-structure or per-element value; mean(rE, by=all) is one number" in msg


def test_a_per_element_input_cannot_be_grouped_by_structure():
    assert "by=structure needs a per-atom value; pe is per element" in _error("sum(pe, by=structure)")


@pytest.mark.parametrize("name", ["mean", "sum", "min", "max", "rms", "std"])
def test_the_six_reductions(name):
    assert _shape(f"{name}(norm(pF - rF), by=structure)") == (dims.N_frames,)


# ── by= is required, and min/max are not minimum/maximum ─────────────────────

def test_a_forgotten_by_is_an_error():
    assert "mean(...) needs by=structure, by=element, by=atom or by=all" in _error("mean(norm(pF - rF))")


def test_by_takes_only_its_words():
    assert "by= takes structure, element, atom, all or (structure, element)" in _error("mean(pE, by=frame)")
    assert "by= takes structure, element, atom, all or (structure, element)" in _error("mean(pE, by=2)")


@pytest.mark.parametrize("name,pair", [("min", "minimum"), ("max", "maximum")])
def test_min_of_two_points_to_minimum(name, pair):
    msg = _error(f"{name}(pE, rE)")
    assert f"use {pair}(a, b)" in msg


def test_a_reduction_takes_one_value():
    assert "mean(...) takes one value" in _error("mean(pE, rE, by=all)")


def test_only_by_and_where_are_keywords():
    assert "keyword" in _error("mean(pE, by=all, weights=m)")
    assert "keyword" in _error("abs(pE, by=all)")


def test_the_by_words_are_not_inputs():
    # structure / element / all after by= are words, not variables.
    inputs, _shape_, _parts = analyze_expr("mean(norm(pF - rF), by=all)", F, registry=_registry())
    assert set(inputs) <= {"pF", "rF", "_offsets", "_elements"}


# ── section 2: reductions keep parts; mean_parts / sum_parts drop them ───────

def test_a_reduction_keeps_the_vector_parts():
    assert _shape("sum(pF - rF, by=structure)") == (dims.N_frames, dims.xyz)
    assert _shape("sum(pF - rF, by=element)") == (dims.N_elements, dims.xyz)
    assert _shape("sum(pF - rF, by=all)") == (dims.xyz,)


def test_mean_parts_and_sum_parts_drop_the_parts():
    assert _shape("mean_parts(abs(pF - rF))") == (dims.N_atoms,)
    assert _shape("sum_parts(pF)") == (dims.N_atoms,)
    assert _shape("mean(mean_parts(abs(pF - rF)), by=all)") == (dims.scalar,)
    assert _shape("norm(sum(pF - rF, by=all))") == (dims.scalar,)


def test_mean_parts_needs_parts():
    assert "part axes" in _error("mean_parts(pE)")


# ── section 4: reductions nest and mix ───────────────────────────────────────

@pytest.mark.parametrize("expr,shape", [
    ("mean(mean(norm(pF - rF), by=structure), by=all)", (dims.scalar,)),
    ("abs(pE - rE - mean(pE - rE, by=all))", (dims.N_frames,)),
    ("norm(pF - rF) / max(norm(pF - rF), by=all)", (dims.N_atoms,)),
    ("mean(norm(pF - rF), by=structure) / n_atoms", (dims.N_frames,)),
])
def test_reductions_nest_and_mix(expr, shape):
    assert _shape(expr) == shape


def test_a_per_structure_value_does_not_spread_onto_atoms():
    assert "different Metric Shapes" in _error("norm(pF - rF) - mean(norm(pF - rF), by=structure)")


# ── section 7: element(...) and where= ───────────────────────────────────────

def test_element_is_a_per_atom_value():
    assert _shape('element("H")') == (dims.N_atoms,)
    assert _shape('1 - element("C", "N", "O")') == (dims.N_atoms,)
    assert _shape('where(element("H"), norm(pF - rF), 0)') == (dims.N_atoms,)


def test_an_unknown_symbol_is_named():
    assert '"Hx" is not an element symbol' in _error('mean(norm(pF - rF), by=all, where=element("Hx"))')


@pytest.mark.parametrize("expr", ['element()', 'element(1)', 'element(H)'])
def test_element_takes_symbols_in_quotes(expr):
    assert "element(...) takes element symbols in quotes" in _error(expr)


def test_text_is_allowed_only_inside_element():
    assert "only numeric literals" in _error('pE + "H"')
    assert "by= takes structure, element, atom, all or (structure, element)" in _error('mean(pE, by="all")')


@pytest.mark.parametrize("expr,shape", [
    ('mean(norm(pF - rF), by=structure, where=element("H"))', (dims.N_frames,)),
    ('rms(norm(pF - rF), by=all, where=element("C", "O"))', (dims.scalar,)),
    ("mean(norm(pF - rF), by=all, where=norm(rF) > 1)", (dims.scalar,)),
    ("mean(abs(pE - rE), by=all, where=n_atoms > 20)", (dims.scalar,)),
    ('sum(pF - rF, by=structure, where=element("H"))', (dims.N_frames, dims.xyz)),
    ('mean(norm(pF - rF), by=element, where=element("H"))', (dims.N_elements,)),
])
def test_where_never_changes_the_shape(expr, shape):
    assert _shape(expr) == shape


def test_where_must_have_the_inputs_shape():
    msg = _error("mean(norm(pF - rF), by=structure, where=n_atoms > 20)")
    assert "where= must have the input's shape: per atom here, n_atoms > 20 is per structure" in msg


def test_the_language_version_went_up():
    from ffast.metrics import expr
    assert expr._WHITELIST_VERSION == "5"


def test_the_parser_accepts_every_new_form():
    for e in ('mean(a, by=all)', 'sum(a, by=structure, where=b)', 'mean_parts(a)',
              'element("H", "C")', 'std(a, where=b > 1, by=element)'):
        parse_and_validate(e)


# ── section 5: colouring from one frame refuses dataset-wide words ───────────

@pytest.mark.parametrize("expr,problem", [
    ("norm(pF - rF) / max(norm(pF - rF), by=all)", "by=all"),
    ("mean(norm(pF - rF), by=element)", "by=element"),
    ('where(element("H"), norm(pF - rF), 0)', None),
    ("mean(norm(pF - rF), by=structure)", None),
])
def test_colouring_from_one_frame_refuses_by_all_and_by_element(expr, problem):
    from ffast.metrics.expr import colour_problem
    got = colour_problem(expr)
    if problem is None:
        assert got is None
    else:
        assert got == (f"{problem} colours atoms only when the prediction covers every "
                       f"frame; this one covers the frame on screen")

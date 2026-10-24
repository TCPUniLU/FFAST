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

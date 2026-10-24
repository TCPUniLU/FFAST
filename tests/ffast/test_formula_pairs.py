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


# ── step 3: Try ──────────────────────────────────────────────────────────────

from ffast.metrics.try_formula import try_formula  # noqa: E402


def _resolve(ref):
    return {"reference.atoms.err": ERR, "offsets": OFFSETS, "reference.elements": Z}.get(ref)


def test_try_summarises_a_table_over_the_cells_that_have_a_value():
    for chunk in (100, 1):    # always one pass: the columns are the dataset's
        r = try_formula("mean(err, by=(structure, element))", {"err": "reference.atoms.err"},
                        MetricRegistry(), _resolve, np.diff(OFFSETS), chunk_frames=chunk)
        assert r.table == [3, 3] and r.count == 9
        assert (r.no_value_count, r.no_value_rows, r.bad_count) == (4, "cells", 0)
        assert (r.min, r.max) == (1.0, 6.0)
        assert r.mean == pytest.approx((2 + 1 + 4.5 + 3 + 6) / 5)
        assert sum(r.histogram["counts"]) == 5


def test_try_of_a_table_names_the_frame_of_a_bad_cell():
    def resolve(ref):
        return {**{"reference.elements": Z, "offsets": OFFSETS},
                "reference.atoms.err": np.array([1.0, 2.0, np.inf, 4.0, 5.0, 6.0])}.get(ref)
    r = try_formula("mean(err, by=(structure, element))", {"err": "reference.atoms.err"},
                    MetricRegistry(), resolve, np.diff(OFFSETS))
    assert (r.bad_count, r.bad_frames) == (1, [1])


# ── step 4: colouring ────────────────────────────────────────────────────────

class _TwoFrames:
    """Two frames of different make-up: C H H, then H O."""
    isVariable = True
    _z = [np.array([6, 1, 1]), np.array([1, 8])]
    _F = [np.zeros((3, 3)), np.zeros((2, 3))]

    def getN(self): return 2
    def getCoordinates(self, idx): return np.zeros((len(self._z[idx]), 3))
    def getElements(self, idx=None): return self._z[idx]
    def getForces(self, indices=None): return self._F[indices]
    def getBondIndices(self, idx): return np.empty((0, 2), dtype=np.int64)


class _TwoFramesPrediction:
    forces = [np.array([[1.0, 0, 0], [3, 0, 0], [6, 0, 0]]), np.array([[2.0, 0, 0], [9, 0, 0]])]


@pytest.mark.parametrize("frame", [0, 1])
def test_an_atoms_colour_is_its_cell_of_the_table(frame):
    from ffast.metrics.executor import InProcessExecutor
    from ffast.metrics.registry import _default_registry as reg
    from ffast.visualization.models import VisualizationState
    from ffast.visualization.scene_builder import build_scene

    expr = "mean(norm(pF - rF), by=(structure, element))"
    vars_ = {"pF": "prediction.forces", "rF": "reference.forces"}
    mid = compile_expr_metric("test.pair_colour", expr, vars_, registry=reg)
    try:
        state = VisualizationState(
            view_id="v", dataset_ref="fp", prediction_ref="m", structure_index=frame,
            parameters={"ffast.atom_color": {"source": f"metric:{mid}"}})
        scene = build_scene(state, lambda fp: _TwoFrames(), lambda d, m: _TwoFramesPrediction(),
                            executor=InProcessExecutor(reg))
        colours = scene.atoms.color_by.values
    finally:
        reg._metrics.pop(mid, None)

    # The table Try gives over the whole dataset: columns H, C, O.
    flat = {"prediction.forces": np.concatenate(_TwoFramesPrediction.forces),
            "reference.forces": np.zeros((5, 3)),
            "reference.elements": np.concatenate(_TwoFrames._z),
            "offsets": np.array([0, 3, 5])}
    r = MetricRegistry()
    inputs, _s, parts = analyze_expr(expr, vars_, registry=r)
    table = _ExprFn(expr, inputs, parts).evaluate(**{n: flat[ref] for n, ref in inputs.items()})
    np.testing.assert_allclose(table, [[4.5, 1.0, NAN], [2.0, NAN, 9.0]])
    column = {1: 0, 6: 1, 8: 2}
    expected = [table[frame, column[int(z)]] for z in _TwoFrames._z[frame]]
    np.testing.assert_allclose(colours, expected)


def test_the_desktop_offers_a_table_for_colouring():
    # The browser's list is COLORABLE_SHAPES in start_settings.js, checked by
    # the web tests.
    from tests.ffast.test_atom_coloring import _load_loupe_atoms
    assert _load_loupe_atoms()._atom_coloring_metric_ids(_registry()) == ["t.per_element", "t.table"]

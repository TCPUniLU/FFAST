"""Keep boxes (ADR 0065 sections 1-3): a box setting writes a formula over a
base metric, and a formula is read back into its box setting."""
import numpy as np
import pytest

from ffast.metrics import dims
from ffast.metrics.boxes import (
    Setting, box_id, box_label, classify, compile_box_metric, formula, parse_box_id,
    setting_problem,
)
from ffast.metrics.registry import MetricRegistry

ATOM_XYZ = (dims.N_atoms, dims.xyz)
FRAME = (dims.N_frames,)
FRAME_XYZ = (dims.N_frames, dims.xyz)


def S(keep, combine=None, statistic=None):
    return Setting(frozenset(keep.split("+")) if keep else frozenset(), combine, statistic)


# ── section 2: the table, setting → formula ──────────────────────────────────

SECTION_2 = [
    (S("structure", "component", "mae"), "mean(mean_parts(abs(D)), by=structure)"),
    (S("structure", "length", "rmse"), "rms(norm(D), by=structure)"),
    (S("element", "component", "rmse"), "sqrt(mean(mean_parts(D ** 2), by=element))"),
    (S("atom", "length", "mae"), "mean(norm(D), by=atom)"),
    (S("xyz", None, "mean"), "mean(D, by=all)"),
]


@pytest.mark.parametrize("setting,expr", SECTION_2)
def test_a_setting_writes_its_formula(setting, expr):
    assert formula(setting, ATOM_XYZ) == expr


@pytest.mark.parametrize("setting,expr", SECTION_2)
def test_the_formula_reads_back_as_its_setting(setting, expr):
    got = classify(expr, {"D": "ffast.force_difference"}, _registry())
    assert (got.base, got.setting, got.custom) == ("ffast.force_difference", setting, False)


# ── section 1: what the boxes allow ──────────────────────────────────────────

@pytest.mark.parametrize("setting,shape,words", [
    (S("atom+element", "length", "mae"), ATOM_XYZ, "element is greyed when atom is ticked"),
    (S("element", "length", "mae"), FRAME, "already averaged per frame"),
    (S("xyz", None, "mae"), FRAME, "no x/y/z"),
    (S("structure", None, "mae"), ATOM_XYZ, "Combine"),
    (S("structure+atom", "length", "mae"), ATOM_XYZ, "nothing is averaged"),
    (S("structure", "length", None), ATOM_XYZ, "Statistic"),
    (S("structure", "length", "mean"), ATOM_XYZ, "a length has no sign"),
])
def test_a_setting_the_boxes_grey_is_refused(setting, shape, words):
    assert words in setting_problem(setting, shape)


@pytest.mark.parametrize("setting,shape,expr", [
    (S("atom+structure", "length"), ATOM_XYZ, "norm(D)"),
    (S("atom+structure", "component", "rmse"), ATOM_XYZ, "sqrt(mean_parts(D ** 2))"),
    (S("atom+structure+xyz"), ATOM_XYZ, "D"),
    (S("structure", None, "mae"), FRAME, None),        # nothing to average
    (S("", None, "max"), FRAME, "max(abs(D), by=all)"),
    (S("structure", "component", "mae"), FRAME_XYZ, "mean_parts(abs(D))"),
    (S("structure+element", "component", "max"), ATOM_XYZ,
     "max(max_parts(abs(D)), by=(structure, element))"),
])
def test_more_settings(setting, shape, expr):
    if expr is None:
        assert "nothing is averaged" in setting_problem(setting, shape)
    else:
        assert setting_problem(setting, shape) is None
        assert formula(setting, shape) == expr


# ── ids and names ────────────────────────────────────────────────────────────

def test_the_id_is_the_base_and_the_setting():
    mid = box_id("ffast.force_difference", S("structure", "component", "rmse"))
    assert mid == "ffast.force_difference[structure;component;rmse]"
    assert parse_box_id(mid) == ("ffast.force_difference", S("structure", "component", "rmse"))
    assert box_id("ffast.energy_difference", S("", None, "mae")) == "ffast.energy_difference[dataset;mae]"
    assert box_id("ffast.force_difference", S("atom+structure", "length")) == \
        "ffast.force_difference[atom+structure;length]"
    assert parse_box_id("ffast.force_difference") is None


def test_a_box_id_written_out_of_order_is_not_one():
    assert parse_box_id("ffast.force_difference[rmse;structure;component]") is None
    assert parse_box_id("ffast.force_difference[element+structure;component;rmse]") is None


def test_the_name_says_the_setting():
    assert box_label("Force Difference", S("structure", "component", "mae")) == \
        "Force Difference · MAE · per frame · per component"
    assert box_label("Energy Difference", S("", None, "mean")) == \
        "Energy Difference · Mean (signed) · dataset"
    assert box_label("Force Difference", S("atom", "length", "rmse")) == \
        "Force Difference · RMSE · per atom, over frames · length"


# ── a box id compiles to a formula metric ────────────────────────────────────

def _registry():
    r = MetricRegistry()

    @r.metric(id="ffast.force_difference", label="Force Difference", unit="force",
              inputs={"p": "prediction.forces", "r": "reference.forces"}, shape=ATOM_XYZ)
    def force_difference(p, r):
        return p - r

    @r.metric(id="ffast.energy_difference", label="Energy Difference", unit="energy",
              inputs={"p": "prediction.energies", "r": "reference.energies"}, shape=FRAME)
    def energy_difference(p, r):
        return p - r

    return r


def test_a_box_id_compiles_to_its_formula_over_the_base():
    r = _registry()
    mid = compile_box_metric("ffast.force_difference[structure;length;rmse]", registry=r)
    schema, fn = r.get(mid)
    assert fn.expr == "rms(norm(D), by=structure)"
    assert fn.user_vars == {"D": "ffast.force_difference"}
    assert schema.shape == (dims.N_frames,)
    assert schema.unit == "force"
    assert schema.label == "Force Difference · RMSE · per frame · length"
    assert compile_box_metric(mid, registry=r) == mid      # idempotent


def test_a_box_id_the_boxes_grey_names_why():
    with pytest.raises(ValueError, match="already averaged per frame"):
        compile_box_metric("ffast.energy_difference[element;mae]", registry=_registry())
    with pytest.raises(ValueError, match="not a metric"):
        compile_box_metric("ffast.nothing[structure;mae]", registry=_registry())


# ── section 3: reading an existing metric ────────────────────────────────────

def test_a_formula_over_raw_inputs_finds_its_base_through_the_bases_twin():
    # D = pred_F - ref_F is the formula twin of Force Difference.
    got = classify("rms(norm(pred_F - ref_F), by=structure)",
                   {"pred_F": "prediction.forces", "ref_F": "reference.forces"}, _registry())
    assert (got.base, got.setting) == ("ffast.force_difference", S("structure", "length", "rmse"))


def test_a_formula_over_raw_inputs_finds_a_formula_base():
    r = _registry()
    from ffast.metrics.expr import compile_expr_metric
    e = {"pE": "prediction.energies", "rE": "reference.energies"}
    compile_expr_metric("t.shifted", "pE - rE - mean(pE - rE, by=all)", e, registry=r)
    got = classify("rms(a - b - mean(a - b, by=all), by=all)",
                   {"a": "prediction.energies", "b": "reference.energies"}, r)
    assert (got.base, got.setting) == ("t.shifted", S("", None, "rmse"))


def test_a_formula_that_fits_only_partly_is_custom_with_boxes_from_its_shape():
    got = classify("abs(D - mean(D, by=all))", {"D": "ffast.energy_difference"}, _registry())
    assert got.custom is True
    assert got.setting is None
    assert got.keep == frozenset({"structure"})


def test_a_box_id_reads_as_its_setting():
    r = _registry()
    mid = compile_box_metric("ffast.force_difference[element;component;max]", registry=r)
    got = classify(r.get(mid)[1].expr, r.get(mid)[1].user_vars, r)
    assert (got.base, got.setting) == ("ffast.force_difference", S("element", "component", "max"))


def test_every_setting_of_a_base_survives_the_round_trip():
    from ffast.metrics.boxes import settings_for
    r = _registry()
    count = 0
    for shape, base in ((ATOM_XYZ, "ffast.force_difference"), (FRAME, "ffast.energy_difference")):
        for setting in settings_for(shape):
            expr = formula(setting, shape)
            got = classify(expr, {"D": base}, r)
            assert got.setting == setting, expr
            count += 1
    assert count > 40


def test_the_box_formula_gives_the_numbers():
    from ffast.metrics.execution import FlatInputSource
    from ffast.metrics.executor import InProcessExecutor
    r = _registry()
    mid = compile_box_metric("ffast.force_difference[structure;component;max]", registry=r)
    raw = {"p": np.array([[3.0, 0, -4], [1, 0, 0], [0, 2, 0]]), "r": np.zeros((3, 3)),
           "_offsets": np.array([0, 2, 3])}
    out = InProcessExecutor(r).run(mid, FlatInputSource(raw), {})
    np.testing.assert_allclose(out.values, [4.0, 2.0])


# ── each fixed metric's formula reads as its setting ─────────────────────────
# The formulas of the fixed force and energy metrics ADR 0065 retires, each
# with the box id that replaces it (section 7).
_FV = {"pred_F": "prediction.forces", "ref_F": "reference.forces"}
_AV = {**_FV, "m": "reference.masses"}
_EV = {"pred_E": "prediction.energies", "ref_E": "reference.energies"}
_A = "(pred_F - ref_F) / m"
_N = "sum(pred_F - ref_F, by=structure)"
FIXED = [
    ("norm(pred_F - ref_F)", _FV, "ffast.force_difference[atom+structure;length]"),
    ("mean(norm(pred_F - ref_F), by=structure)", _FV, "ffast.force_difference[structure;length;mae]"),
    ("rms(norm(pred_F - ref_F), by=structure)", _FV, "ffast.force_difference[structure;length;rmse]"),
    ("mean(norm(pred_F - ref_F), by=all)", _FV, "ffast.force_difference[dataset;length;mae]"),
    ("rms(norm(pred_F - ref_F), by=all)", _FV, "ffast.force_difference[dataset;length;rmse]"),
    ("mean(mean_parts(abs(pred_F - ref_F)), by=all)", _FV, "ffast.force_difference[dataset;component;mae]"),
    ("sqrt(mean(mean_parts((pred_F - ref_F) ** 2), by=all))", _FV,
     "ffast.force_difference[dataset;component;rmse]"),
    ("mean(mean_parts(abs(pred_F - ref_F)), by=structure)", _FV,
     "ffast.force_difference[structure;component;mae]"),
    ("sqrt(mean(mean_parts((pred_F - ref_F) ** 2), by=structure))", _FV,
     "ffast.force_difference[structure;component;rmse]"),
    ("mean(mean_parts(abs(pred_F - ref_F)), by=element)", _FV,
     "ffast.force_difference[element;component;mae]"),
    ("sqrt(mean(mean_parts((pred_F - ref_F) ** 2), by=element))", _FV,
     "ffast.force_difference[element;component;rmse]"),
    (f"mean_parts(abs({_N}))", _FV, "ffast.force_net_residual[structure;component;mae]"),
    (f"mean(mean_parts(abs({_N})), by=all)", _FV, "ffast.force_net_residual[dataset;component;mae]"),
    (f"sqrt(mean(mean_parts({_N} ** 2), by=all))", _FV, "ffast.force_net_residual[dataset;component;rmse]"),
    (f"norm({_A})", _AV, "ffast.accel_difference[atom+structure;length]"),
    (f"rms(norm({_A}), by=structure)", _AV, "ffast.accel_difference[structure;length;rmse]"),
    (f"mean(norm({_A}), by=all)", _AV, "ffast.accel_difference[dataset;length;mae]"),
    (f"rms(norm({_A}), by=all)", _AV, "ffast.accel_difference[dataset;length;rmse]"),
    (f"mean(mean_parts(abs({_A})), by=all)", _AV, "ffast.accel_difference[dataset;component;mae]"),
    (f"sqrt(mean(mean_parts(({_A}) ** 2), by=all))", _AV, "ffast.accel_difference[dataset;component;rmse]"),
    (f"mean(norm({_A}), by=atom)", _AV, "ffast.accel_difference[atom;length;mae]"),
    (f"mean(norm({_A}), by=element)", _AV, "ffast.accel_difference[element;length;mae]"),
    ("mean(pred_E - ref_E, by=all)", _EV, "ffast.energy_difference[dataset;mean]"),
    ("mean(abs(pred_E - ref_E), by=all)", _EV, "ffast.energy_difference[dataset;mae]"),
    ("rms(pred_E - ref_E, by=all)", _EV, "ffast.energy_difference[dataset;rmse]"),
    ("mean(abs(pred_E - ref_E - mean(pred_E - ref_E, by=all)), by=all)", _EV,
     "ffast.energy_difference_shifted[dataset;mae]"),
    ("rms(pred_E - ref_E - mean(pred_E - ref_E, by=all), by=all)", _EV,
     "ffast.energy_difference_shifted[dataset;rmse]"),
]


@pytest.mark.parametrize("expr,vars_,replacement", FIXED)
def test_each_fixed_metrics_formula_reads_as_its_setting(expr, vars_, replacement):
    from ffast.metrics.builtin import accel_metrics, force_metrics, energy_metrics  # noqa: F401
    from ffast.metrics.registry import default_registry
    got = classify(expr, vars_, default_registry)
    assert box_id(got.base, got.setting) == replacement


# ── what the Metrics pane is told (section 4) ────────────────────────────────

def test_the_pane_reads_a_box_metric_as_its_base_and_setting():
    from ffast.metrics.boxes import box_state
    r = _registry()
    state = box_state(r, metric_id="ffast.force_difference[structure;length;rmse]")
    assert state["base"] == "ffast.force_difference"
    assert state["setting"] == {"keep": ["structure"], "combine": "length", "statistic": "rmse"}
    assert state["custom"] is False
    assert state["id"] == "ffast.force_difference[structure;length;rmse]"
    assert state["expr"] == "rms(norm(D), by=structure)"
    assert state["vars"] == {"D": "ffast.force_difference"}
    assert state["label"] == "Force Difference · RMSE · per frame · length"
    assert state["shape"] == "N_frames" and state["unit"] == "force"
    assert state["exists"] is True          # opening a box id registers it
    assert {"keep": ["structure"], "combine": "component", "statistic": "mae"} in state["allowed"]


def test_a_base_opens_with_everything_kept_and_nothing_to_create():
    from ffast.metrics.boxes import box_state
    state = box_state(_registry(), metric_id="ffast.force_difference")
    assert state["setting"] == {"keep": ["atom", "structure", "xyz"], "combine": None, "statistic": None}
    assert state["id"] == "ffast.force_difference"
    assert state["exists"] is True


def test_a_change_of_boxes_gives_the_new_formula_and_name():
    from ffast.metrics.boxes import box_state
    state = box_state(_registry(), base="ffast.force_difference",
                      setting={"keep": ["element"], "combine": "component", "statistic": "mae"})
    assert state["id"] == "ffast.force_difference[element;component;mae]"
    assert state["expr"] == "mean(mean_parts(abs(D)), by=element)"
    assert state["shape"] == "N_elements"
    assert state["problem"] is None


def test_a_per_frame_metric_greys_the_finer_boxes_with_a_hint():
    from ffast.metrics.boxes import box_state
    state = box_state(_registry(), metric_id="ffast.energy_difference")
    assert state["hints"]["atom"] == "this metric is already averaged per frame"
    assert state["hints"]["element"] == "this metric is already averaged per frame"
    assert state["hints"]["xyz"] == "this metric has no x/y/z"
    assert "structure" not in state["hints"]


def test_atom_without_structure_is_greyed_when_structures_differ():
    from ffast.metrics.boxes import box_state
    state = box_state(_registry(), metric_id="ffast.force_difference", uniform=False)
    assert all(s["keep"] != ["atom"] for s in state["allowed"])
    assert state["hints"]["atom_only"] == "structures have different atoms"


def test_a_custom_formula_is_its_own_base():
    from ffast.metrics.boxes import box_state
    from ffast.metrics.expr import compile_expr_metric
    r = _registry()
    compile_expr_metric("user.odd", "abs(D - mean(D, by=all))", {"D": "ffast.energy_difference"},
                        label="Odd", unit="energy", registry=r)
    state = box_state(r, metric_id="user.odd")
    assert state["custom"] is True
    assert state["base"] == "user.odd"
    state = box_state(r, base="user.odd", setting={"keep": [], "combine": None, "statistic": "max"})
    assert state["expr"] == "max(abs(D), by=all)"
    assert state["id"] == "user.odd[dataset;max]"

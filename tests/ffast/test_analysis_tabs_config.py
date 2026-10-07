"""Phase 5b: TOML Analysis-Tab / Panel schema + compile pass (ADR 0021)."""
import tomllib

import pytest
from pydantic import ValidationError

import ffast.config.tabs as tabs_mod
from ffast.config.models import AnalysisTabConfig, ProjectConfig
from ffast.config.tabs import (
    build_tab_layout,
    compile_tabs_metrics,
    load_builtin_tabs,
    merge_tabs,
    resolve_ref,
)


def test_builtin_tabs_validate():
    tabs = load_builtin_tabs()
    assert tabs, "expected at least the bundled Subsystem tab"
    names = {t.name for t in tabs}
    assert "Subsystem Errors" in names
    for tab in tabs:
        assert tab.panels  # every tab has panels


def test_builtin_tabs_are_one_file_per_tab():
    """Maintenance convention: each builtin tab is its own NN_*.toml in the
    builtin_tabs/ dir, and load order follows the numeric filename prefix."""
    import tomllib

    from ffast.config.tabs import _BUILTIN_TABS_DIR

    files = sorted(_BUILTIN_TABS_DIR.glob("*.toml"))
    assert len(files) >= 4
    for path in files:
        with open(path, "rb") as f:
            assert len(tomllib.load(f).get("tabs", [])) == 1, path.name

    # First file (00_*) is the 3D tab, then 01_* Basic Errors.
    assert [t.name for t in load_builtin_tabs()[:2]] == ["3D", "Basic Errors"]


def test_builtin_3d_tab_holds_one_linked_3d_panel():
    """The fixed 3D tab of the browser is an ordinary built-in tab now
    (ADR 0056 rule 6): one 3D panel filling it, showing the main view."""
    tab = load_builtin_tabs()[0]
    assert tab.name == "3D"
    assert [(p.kind, p.row, p.col, p.rowspan, p.colspan) for p in tab.panels] == [
        ("3d", 0, 0, 1, 1)]
    assert tab.panels[0].metrics == {}


def test_3d_panel_takes_no_metrics():
    """A 3D panel shows a visualization view; it binds no metric roles."""
    with pytest.raises(ValidationError, match="3D panel"):
        AnalysisTabConfig.model_validate({"name": "T", "panels": [{
            "kind": "3d", "row": 0, "col": 0,
            "metrics": {"y": {"metric": "ffast.gyradius"}},
        }]})


def test_3d_panels_compile_no_metrics_and_reach_the_layout():
    """compile and layout skip over 3D panels' empty metrics; the panel still
    travels to the browser with its place."""
    import ffast.metrics.builtin  # noqa: F401

    tab = AnalysisTabConfig.model_validate({"name": "Mixed", "panels": [
        {"kind": "3d", "row": 0, "col": 0},
        {"kind": "timeline", "row": 0, "col": 1,
         "metrics": {"y": {"metric": "ffast.gyradius", "transform": "smooth"}}},
    ]})
    assert compile_tabs_metrics([tab]) == ["ffast.gyradius__smooth"]
    panels = build_tab_layout([tab])[0]["panels"]
    assert panels[0]["kind"] == "3d" and panels[0]["metrics"] == {}


def test_basic_errors_tab_is_declarative():
    """Basic Errors is the fully-config end state (no imperative module): a tab
    with the energy-shift tab control, a `tables` scroll strip, and scatter x/y."""
    tabs = {t.name: t for t in load_builtin_tabs()}
    be = tabs["Basic Errors"]
    assert be.controls == ["energy_shift"]

    # The four scalar tables share one horizontal scroll strip.
    grouped = [p for p in be.panels if p.scroll_group == "tables"]
    assert len(grouped) == 4
    assert all(p.kind == "table" for p in grouped)

    # Energy panels hide the per-panel `shifted` control (driven by the tab toggle):
    # energy distribution + timeline + scatter + the two energy tables.
    energy = [p for p in be.panels if "shifted" in p.hidden_params]
    assert len(energy) == 5

    scatters = [p for p in be.panels if p.kind == "scatter"]
    assert scatters and all(set(p.metrics) == {"x", "y"} for p in scatters)


def test_basic_errors_metrics_registered():
    """Every metric the Basic Errors panels bind exists in the registry (the
    compile pass + builtin import leave the tab fully computable server-side)."""
    import ffast.metrics.builtin  # noqa: F401
    from ffast.metrics.registry import default_registry

    compile_tabs_metrics(load_builtin_tabs())
    be = {t.name: t for t in load_builtin_tabs()}["Basic Errors"]
    for panel in be.panels:
        for role in panel.metrics.values():
            for ref in (role if isinstance(role, list) else [role]):
                assert default_registry.has(resolve_ref(ref)), ref.metric


def test_scroll_group_and_tab_controls_parse():
    tab = AnalysisTabConfig.model_validate({
        "name": "T",
        "controls": ["energy_shift"],
        "panels": [
            {"kind": "table", "row": 0, "col": 0, "scroll_group": "g"},
            {"kind": "table", "row": 0, "col": 0, "scroll_group": "g"},
        ],
    })
    assert tab.controls == ["energy_shift"]
    assert [p.scroll_group for p in tab.panels] == ["g", "g"]


def test_unknown_key_rejected():
    with pytest.raises(ValidationError):
        AnalysisTabConfig.model_validate(
            {"name": "Bad", "panels": [{"kind": "table", "row": 0, "col": 0,
                                        "bogus_key": 1}]}
        )


def test_unparseable_tab_toml_raises_decode_error(tmp_path, monkeypatch):
    # Broken TOML in the builtin-tabs dir surfaces as a tomllib decode error
    # out of the tab loader (unterminated table header).
    monkeypatch.setattr(tabs_mod, "_BUILTIN_TABS_DIR", tmp_path)
    (tmp_path / "01_bad.toml").write_text("[[tabs\n")
    with pytest.raises(tomllib.TOMLDecodeError):
        load_builtin_tabs()


def test_wrong_typed_panel_row_raises_validation_error(tmp_path, monkeypatch):
    # Structurally valid TOML but a panel `row` (pydantic int) given a
    # non-numeric string fails validation inside the tab loader.
    monkeypatch.setattr(tabs_mod, "_BUILTIN_TABS_DIR", tmp_path)
    (tmp_path / "01_bad.toml").write_text("""
[[tabs]]
name = "T"
[[tabs.panels]]
kind = "table"
row = "notanumber"
col = 0
""")
    with pytest.raises(ValidationError):
        load_builtin_tabs()


def test_metric_ref_forms():
    tab = AnalysisTabConfig.model_validate({
        "name": "T",
        "panels": [{
            "kind": "density", "row": 0, "col": 0,
            "metrics": {"value": {"metric": "ffast.force_rmse", "transform": "mirror_kde"}},
        }],
    })
    ref = tab.panels[0].metrics["value"]
    assert ref.metric == "ffast.force_rmse" and ref.transform == "mirror_kde"


def test_compile_tabs_registers_transform_metrics():
    """The bundled tabs' transform refs compile + register against the real
    built-ins (idempotent, so safe on the shared default_registry)."""
    import ffast.metrics.builtin  # noqa: F401
    from ffast.metrics.registry import default_registry

    ids = compile_tabs_metrics(load_builtin_tabs())
    assert "ffast.force_net_mae_per_structure__mirror_kde" in ids
    for mid in ids:
        assert default_registry.has(mid)


def test_resolve_ref_raw_vs_transform():
    import ffast.metrics.builtin  # noqa: F401
    from ffast.config.models import PanelMetricRef

    raw = PanelMetricRef(metric="ffast.force_net_mae")
    assert resolve_ref(raw) == "ffast.force_net_mae"  # no transform → raw id

    smoothed = PanelMetricRef(metric="ffast.gyradius", transform="smooth")
    assert resolve_ref(smoothed) == "ffast.gyradius__smooth"


# --- build_tab_layout: the ADR 0045 Phase 3 TAB_LAYOUT wire payload --------- #
def test_build_tab_layout_shape_and_resolved_ids():
    """The layout carries every renderer-side field and replaces each metric
    role with its resolved concrete id (so the browser skips the compiler)."""
    import ffast.metrics.builtin  # noqa: F401

    layout = build_tab_layout(load_builtin_tabs())
    names = [t["name"] for t in layout]
    assert names[:2] == ["3D", "Basic Errors"] and "Gyration" in names

    by_name = {t["name"]: t for t in layout}
    basic = by_name["Basic Errors"]
    assert basic["controls"] == ["energy_shift"]

    # A raw (no-transform) role resolves to the bare id.
    energy_scatter = next(
        p for p in basic["panels"] if p["kind"] == "scatter" and p["title"] == "Energy Scatter"
    )
    assert energy_scatter["diagonal"] is True
    assert energy_scatter["metrics"]["x"] == "ffast.energy_reference"

    # A transform role resolves to the compiled `{source}__{transform}` id.
    subsystem = by_name["Subsystem Errors"]
    density = next(p for p in subsystem["panels"] if p["kind"] == "density")
    assert density["metrics"]["value"] == "ffast.force_net_mae_per_structure__mirror_kde"

    # An overlay `series` list role resolves to a list of ids.
    gyration = by_name["Gyration"]
    overlay = next(p for p in gyration["panels"] if p["kind"] == "overlay_timeline")
    assert isinstance(overlay["metrics"]["series"], list)
    assert overlay["metrics"]["series"][0] == "ffast.gyradius__smooth"
    assert overlay["options"]["series_labels"][0].startswith("Gyradius")


def test_build_tab_layout_appends_custom_tab_identically():
    """A project [[visualization.tabs]] entry appears after the built-ins with
    the same dict shape — the parity the browser custom-tab test relies on."""
    import ffast.metrics.builtin  # noqa: F401

    project = ProjectConfig.model_validate({
        "visualization": {"tabs": [{
            "name": "Custom",
            "panels": [{
                "kind": "scatter", "row": 0, "col": 0, "diagonal": True,
                "metrics": {"x": {"metric": "ffast.energy_reference"},
                            "y": {"metric": "ffast.energy_prediction"}},
            }],
        }]},
    })
    layout = build_tab_layout(merge_tabs(project))
    custom = layout[-1]
    assert custom["name"] == "Custom"
    # Same keys as a built-in tab and its panels.
    assert set(custom) == set(layout[1])
    assert set(custom["panels"][0]) == set(layout[1]["panels"][0])
    assert custom["panels"][0]["metrics"]["y"] == "ffast.energy_prediction"


def test_project_tabs_append_to_builtin():
    project = ProjectConfig.model_validate({
        "visualization": {"tabs": [{"name": "Custom", "panels": []}]},
    })
    merged = merge_tabs(project)
    names = [t.name for t in merged]
    assert "Subsystem Errors" in names and names[-1] == "Custom"


# --- compile_project_metrics: fail-soft error collection (ADR 0042 Gap 1) --- #
def test_compile_project_metrics_none_compiles_bundled_tabs():
    from ffast.config.tabs import compile_project_metrics

    result = compile_project_metrics(None)
    assert result.errors == []
    assert result.ids  # the bundled tabs still compile without a project config


def test_compile_project_metrics_collects_per_entry_errors():
    # A good expr registers; a shape-mismatched expr is collected as an error
    # (naming the offending entry) instead of aborting the whole compile pass.
    from ffast.config.tabs import compile_project_metrics

    project = ProjectConfig.model_validate({
        "metrics": {"expr": [
            {"id": "projtest.good", "expr": "a - b",
             "vars": {"a": "reference.energies", "b": "prediction.energies"}},
            {"id": "projtest.badshape", "expr": "e + f",
             "vars": {"e": "reference.energies", "f": "reference.forces"}},
        ]},
    })
    result = compile_project_metrics(project)
    assert "projtest.good" in result.ids
    assert any("projtest.badshape" in ctx for ctx, _ in result.errors)
    assert not any("projtest.good" in ctx for ctx, _ in result.errors)


def test_compile_project_metrics_fields_before_expr():
    # An Expression Variable binding a Dataset Field id resolves in the same pass
    # only because fields compile before exprs.
    from ffast.config.tabs import compile_project_metrics

    project = ProjectConfig.model_validate({
        "metrics": {
            "fields": [{"id": "projtest.q", "ref": "reference.atoms.charges"}],
            "expr": [{"id": "projtest.absq", "expr": "abs(q)",
                      "vars": {"q": "projtest.q"}}],
        },
    })
    result = compile_project_metrics(project)
    assert "projtest.q" in result.ids and "projtest.absq" in result.ids
    assert result.errors == []


# --- column_widths / row_heights (ADR 0056 rule 12) -------------------------- #
def _sized_tab(**sizes):
    return AnalysisTabConfig.model_validate({"name": "Sized", **sizes, "panels": [
        {"kind": "3d", "row": 0, "col": 0, "rowspan": 2},
        {"kind": "table", "row": 0, "col": 1},
        {"kind": "table", "row": 1, "col": 1},
    ]})


def test_tab_sizes_are_relative_and_optional():
    assert _sized_tab().column_widths is None and _sized_tab().row_heights is None
    tab = _sized_tab(column_widths=[2, 1], row_heights=[3, 1.5])
    assert tab.column_widths == [2, 1] and tab.row_heights == [3, 1.5]


def test_tab_sizes_cover_every_column_and_row():
    """A list too short for the grid is a config error naming the count it
    needs, like any other config mistake; a longer one leaves columns or rows
    empty (a column count set in the browser survives a save)."""
    with pytest.raises(ValidationError, match="column_widths has 1 entry; the tab has 2 columns"):
        _sized_tab(column_widths=[1])
    with pytest.raises(ValidationError, match="row_heights has 1 entry; the tab has 2 rows"):
        _sized_tab(row_heights=[1])
    assert _sized_tab(column_widths=[1, 1, 1], row_heights=[1, 1, 1]).column_widths == [1, 1, 1]


def test_tab_sizes_are_positive():
    with pytest.raises(ValidationError, match="column_widths must be positive"):
        _sized_tab(column_widths=[1, 0])
    with pytest.raises(ValidationError, match="row_heights must be positive"):
        _sized_tab(row_heights=[-1, 1])


def test_tab_sizes_reach_the_browser():
    layout = build_tab_layout([_sized_tab(column_widths=[2, 1])])[0]
    assert layout["column_widths"] == [2, 1] and layout["row_heights"] is None

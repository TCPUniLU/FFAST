"""Phase 5b: TOML Analysis-Tab / Panel schema + compile pass (ADR 0021)."""
import pytest
from pydantic import ValidationError

from ffast.config.models import AnalysisTabConfig, ProjectConfig
from ffast.config.tabs import (
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

    # First file (01_*) is Basic Errors → first tab returned.
    assert load_builtin_tabs()[0].name == "Basic Errors"


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


def test_project_tabs_append_to_builtin():
    project = ProjectConfig.model_validate({
        "visualization": {"tabs": [{"name": "Custom", "panels": []}]},
    })
    merged = merge_tabs(project)
    names = [t.name for t in merged]
    assert "Subsystem Errors" in names and names[-1] == "Custom"

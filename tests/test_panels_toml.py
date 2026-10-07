"""Which configured tabs the Qt desktop builds (ADR 0056 point 17).

The desktop has its own 3D Loupe window, so a tab made only of 3D panels —
the built-in "3D" tab among them — would be nothing but grey "shown in the
browser only" boxes there. It skips such tabs. ``UI.panels_toml`` imports no
Qt at module level, so this runs without a display.
"""
from ffast.config.models import AnalysisTabConfig
from ffast.config.tabs import load_builtin_tabs
from UI.panels_toml import desktop_tabs


def _tab(name, *kinds):
    return AnalysisTabConfig.model_validate({"name": name, "panels": [
        {"kind": k, "row": 0, "col": i} for i, k in enumerate(kinds)]})


def test_desktop_skips_tabs_made_only_of_3d_panels():
    tabs = [_tab("3D", "3d"), _tab("Two views", "3d", "3d"), _tab("Plots", "table")]
    assert [t.name for t in desktop_tabs(tabs)] == ["Plots"]


def test_desktop_keeps_mixed_and_empty_tabs():
    tabs = [_tab("Mixed", "3d", "table"), _tab("Empty")]
    assert [t.name for t in desktop_tabs(tabs)] == ["Mixed", "Empty"]


def test_desktop_does_not_show_the_builtin_3d_tab():
    names = [t.name for t in desktop_tabs(load_builtin_tabs())]
    assert "3D" not in names and names[0] == "Basic Errors"

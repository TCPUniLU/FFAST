"""Panel Display Override identity wiring through make_panel (ADR 0029).

The identity is content-based -- (Analysis Tab name, Panel Kind, bound Metric
IDs) -- not grid position, so it survives TOML reordering. The tab name comes
from ContentTab.tabName, stashed by UIHandler.addContentTab.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

import ffast.core.events as events  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import client.display_overrides as display_overrides  # noqa: E402
from UI.panels import make_panel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _redirect_overrides_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        display_overrides, "OVERRIDES_FILE", str(tmp_path / "display_overrides.json")
    )


class _FakeEnv(events.EventClass):
    def getColorMix(self, dataset, model):
        return "#ff0000"


class _FakeHandler(events.EventClass):
    def __init__(self):
        super().__init__()
        self.env = _FakeEnv()
        self.config = {"envs": {"TextColor1": "#ffffff", "BGColor3": "#222222"}}


class _FakeTab(QWidget):
    """Stands in for a ContentTab -- only .tabName matters here (set by
    UIHandler.addContentTab in the real app)."""


def _make_density_panel(tab_name, metric_id="ffast.fake_metric", **spec):
    tab = _FakeTab()
    tab.tabName = tab_name
    return make_panel(_FakeHandler(), "density", parent=tab, value=metric_id, **spec)


def test_identity_is_tab_kind_and_bound_metrics(qapp):
    panel = _make_density_panel("Basic Errors", name="p1")
    assert panel._displayOverrideKey == ("Basic Errors", "density", ["ffast.fake_metric"])


def test_missing_tab_name_falls_back_to_empty_string(qapp):
    panel = make_panel(_FakeHandler(), "density", parent=None,
                        value="ffast.fake_metric", name="p2")
    assert panel._displayOverrideKey == ("", "density", ["ffast.fake_metric"])


def test_saved_override_is_picked_up_at_construction(qapp):
    key = ("Basic Errors", "density", ["ffast.fake_metric"])
    display_overrides.set_panel_override(*key, ("y_label", "text"), "Custom Density")

    panel = _make_density_panel("Basic Errors", name="p3")
    assert panel._currentAxisText("left") == "Custom Density"


def test_different_tab_same_metric_is_a_different_panel(qapp):
    key_a = ("Basic Errors", "density", ["ffast.fake_metric"])
    display_overrides.set_panel_override(*key_a, ("y_label", "text"), "Only In Basic Errors")

    panel = _make_density_panel("Subsystem Errors", name="p4")
    assert panel._currentAxisText("left") == "Density"  # untouched Panel Kind default

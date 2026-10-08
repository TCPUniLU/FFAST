"""How the Qt desktop builds a project tab that holds 3D panels (ADR 0056
point 17).

3D panels are shown in the browser only. In a tab that mixes them with 2D
panels the desktop draws the 2D panels and puts a grey "3D panel — shown in
the browser only" box in each 3D panel's place; `column_widths` and
`row_heights` are the browser's, and a tab with them builds the same. The
tab, the plots and the tab controls are stand-ins, so only the box is a real
widget (offscreen).
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel, QSizePolicy  # noqa: E402

import UI.ContentTab  # noqa: E402
import UI.controls  # noqa: E402
import UI.panels  # noqa: E402
from ffast.config.models import AnalysisTabConfig  # noqa: E402
from UI.panels_toml import build_analysis_tab  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeLayout:
    def __init__(self):
        self.widgets = []

    def addWidget(self, widget):
        self.widgets.append(widget)


class _FakeTab:
    """Stands in for ContentTab: records where widgets are placed."""

    def __init__(self, handler, hasDataSelector):
        self.handler = handler
        self.dataSelector = object() if hasDataSelector else None
        self.topLayout = _FakeLayout()
        self.placed = []
        self.callbacks = []

    def addWidget(self, widget, *place):
        self.placed.append((widget, place))

    def addDataSelectionCallback(self, func):
        self.callbacks.append(func)


class _FakePanel:
    def __init__(self, kind, title):
        self.kind, self.title = kind, title

    def setModelDatasetDependencies(self, *args):
        pass


class _FakeHandler:
    def addContentTab(self, tab, name):
        pass


@pytest.fixture
def build_tab(qapp, monkeypatch):
    """Build a tab with the stand-ins; returns (tab, kinds made, controls)."""
    made, controls = [], []

    def make_panel(handler, kind, parent=None, **spec):
        made.append(kind)
        return _FakePanel(kind, spec.get("title"))

    def make_tab_control(name, tab, panels):
        controls.append((name, [p.title for p in panels]))
        return object()

    monkeypatch.setattr(UI.ContentTab, "ContentTab", _FakeTab)
    monkeypatch.setattr(UI.panels, "make_panel", make_panel)
    monkeypatch.setattr(UI.controls, "make_tab_control", make_tab_control)

    def build(tab):
        config = AnalysisTabConfig.model_validate(tab)
        return build_analysis_tab(_FakeHandler(), None, config), made, controls
    return build


_MIXED = {
    "name": "Compare",
    "controls": ["energy_shift"],
    "column_widths": [2, 1],
    "row_heights": [1.5, 1],
    "panels": [
        {"kind": "3d", "row": 0, "col": 0, "rowspan": 2},
        {"kind": "3d", "row": 1, "col": 1, "view": "independent",
         "link_camera": False},
        {"kind": "table", "row": 0, "col": 1, "title": "Force MAE",
         "metrics": {"value": {"metric": "ffast.force_component_mae"}}},
    ],
}


def test_a_3d_panel_is_a_grey_box_in_its_place(build_tab):
    tab, made, _ = build_tab(_MIXED)
    boxes = [(w, at) for w, at in tab.placed if isinstance(w, QLabel)]
    assert [at for _, at in boxes] == [(0, 0, 2, 1), (1, 1, 1, 1)]
    grow = QSizePolicy.Policy.MinimumExpanding
    for box, _ in boxes:
        assert box.text() == "3D panel — shown in the browser only"
        assert box.objectName() == "browserOnlyPanel"
        # Sized as a desktop plot is, so its place never shrinks to a strip.
        assert (box.minimumWidth(), box.minimumHeight()) == (400, 400)
        policy = box.sizePolicy()
        assert policy.horizontalPolicy() == policy.verticalPolicy() == grow
    assert made == ["table"]  # no desktop panel is made for a 3D one


def test_the_2d_panels_are_drawn_as_usual(build_tab):
    tab, _, controls = build_tab(_MIXED)
    plots = [(w.title, at) for w, at in tab.placed
             if isinstance(w, _FakePanel)]
    assert plots == [("Force MAE", (0, 1, 1, 1))]
    # Tab controls and the data selector drive the plots only.
    assert controls == [("energy_shift", ["Force MAE"])]
    assert len(tab.callbacks) == 1

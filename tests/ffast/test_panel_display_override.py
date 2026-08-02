"""Panel Display Override interactive mechanics (ADR 0029).

Exercises the editable-axis-label and editable-legend code in UI/Plots.py
against a real pyqtgraph PlotWidget (offscreen), duck-typing only the handler/
env surface BasicPlotWidget actually touches (no real Environment needed --
see [[project_test_venv]]-style offscreen smoke pattern in test_tab_controls.py).

Double-click/scroll are simulated with directly-constructed QMouseEvent/
QWheelEvent sent via QApplication.sendEvent rather than QTest.mouseDClick:
QTest's multi-event double-click synthesis was found to steal focus from the
freshly-created inline editor mid-sequence in the offscreen platform, which is
a test-harness artifact (a real double-click doesn't retarget mid-click),
not a bug in the eventFilter/coordinate-mapping code under test.
"""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

import ffast.core.events as events  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent  # noqa: E402
from PySide6 import QtWidgets  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import client.display_overrides as display_overrides  # noqa: E402
from UI.Plots import BasicPlotWidget, _MAX_LABEL_FONT_SIZE, _MIN_LABEL_FONT_SIZE  # noqa: E402


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


class _FakeObj:
    def __init__(self, fingerprint, name):
        self.fingerprint = fingerprint
        self._name = name

    def getDisplayName(self):
        return self._name


def _make_widget(qapp, key=("TestTab", "density", ["ffast.x"])):
    w = BasicPlotWidget(_FakeHandler(), name="test", title="Test Plot")
    w._displayOverrideKey = key
    w.resize(500, 500)
    w.show()
    qapp.processEvents()
    return w


def _dblclick(qapp, widget, viewPos):
    ev = QMouseEvent(
        QEvent.Type.MouseButtonDblClick, QPointF(viewPos),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, ev)
    qapp.processEvents()


def _wheel(qapp, widget, viewPos, steps=1):
    ev = QWheelEvent(
        QPointF(viewPos), QPointF(viewPos), QPoint(0, 0), QPoint(0, 120 * steps),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )
    QApplication.sendEvent(widget, ev)
    qapp.processEvents()


def _axis_view_center(w, axisName):
    rect = w._axisLabelSceneRect(axisName)
    topLeft = w.plotWidget.mapFromScene(rect.topLeft())
    bottomRight = w.plotWidget.mapFromScene(rect.bottomRight())
    return QPointF((topLeft.x() + bottomRight.x()) / 2, (topLeft.y() + bottomRight.y()) / 2)


def test_default_label_shows_until_edited(qapp):
    w = _make_widget(qapp)
    w.setXLabel("Energy MAE")
    assert w._currentAxisText("bottom") == "Energy MAE"


def test_double_click_opens_editor_prefilled_with_current_text(qapp):
    w = _make_widget(qapp)
    w.setXLabel("Energy MAE")
    _dblclick(qapp, w.plotWidget, _axis_view_center(w, "bottom"))
    assert w._activeEditBox is not None
    assert w._activeEditBox.text() == "Energy MAE"


def test_committing_edit_updates_label_and_persists(qapp):
    w = _make_widget(qapp)
    w.setXLabel("Energy MAE")
    _dblclick(qapp, w.plotWidget, _axis_view_center(w, "bottom"))
    w._activeEditBox.setText("Custom X Label")
    w._activeEditBox.editingFinished.emit()
    qapp.processEvents()

    assert w._currentAxisText("bottom") == "Custom X Label"
    override = display_overrides.get_panel_override(*w._displayOverrideKey)
    assert override["x_label"]["text"] == "Custom X Label"


def test_clearing_edit_reverts_to_default_and_clears_override(qapp):
    w = _make_widget(qapp)
    w.setXLabel("Energy MAE")
    w._commitAxisLabelEdit("bottom", "Custom X Label")
    w._commitAxisLabelEdit("bottom", "")  # empty commit -> Q9: revert to default

    assert w._currentAxisText("bottom") == "Energy MAE"
    override = display_overrides.get_panel_override(*w._displayOverrideKey)
    assert "x_label" not in override


def test_scroll_over_label_resizes_font_and_persists(qapp):
    w = _make_widget(qapp)
    w.setXLabel("Energy MAE")
    _wheel(qapp, w.plotWidget, _axis_view_center(w, "bottom"), steps=1)

    assert w._labelState["bottom"]["font_size"] == 21
    override = display_overrides.get_panel_override(*w._displayOverrideKey)
    assert override["x_label"]["font_size"] == 21


def test_scroll_elsewhere_on_plot_does_not_resize_label(qapp):
    w = _make_widget(qapp)
    w.setXLabel("Energy MAE")
    _wheel(qapp, w.plotWidget, QPointF(250, 250), steps=1)  # plot center, not the label
    assert w._labelState["bottom"]["font_size"] is None


def test_override_reapplied_on_a_fresh_widget_instance(qapp):
    key = ("TestTab", "density", ["ffast.energy_difference_density"])
    first = _make_widget(qapp, key=key)
    first.setXLabel("Energy MAE")
    first._commitAxisLabelEdit("bottom", "Renamed Axis")
    first._resizeAxisLabel("bottom", 1)

    # A brand new widget with the same content-based identity -- simulating
    # a restart -- must pick the saved override back up (ADR 0029: identity
    # is (tab, kind, metric_ids), not the widget instance).
    second = _make_widget(qapp, key=None)
    second.setXLabel("Energy MAE")  # Panel Kind/TOML default, same as before
    second._displayOverrideKey = key
    second._loadDisplayOverride()

    assert second._currentAxisText("bottom") == "Renamed Axis"
    assert second._labelState["bottom"]["font_size"] == 21


def test_legend_drag_and_wheel_persist(qapp):
    w = _make_widget(qapp)
    w._onLegendMoved(42.0, 17.0)
    w._onLegendWheel(1)

    override = display_overrides.get_panel_override(*w._displayOverrideKey)
    assert override["legend"]["position"] == [42.0, 17.0]
    assert override["legend"]["font_size"] == 13


def test_legend_entry_rename_reapplies_after_refresh(qapp):
    w = _make_widget(qapp)
    w.dataWatcher.datasetDependencies = ["ds1"]
    w.dataWatcher.modelDependencies = ["m1"]

    ds, model = _FakeObj("ds1", "Aspirin"), _FakeObj("m1", "MACE-small")
    autoLabel = {"dataset": ds, "model": model}
    w.plot(np.array([0, 1, 2]), np.array([0, 1, 2]), autoColor=autoLabel, autoLabel=autoLabel)
    w.plotItems = [w._series[k]["item"] for k in w._visitOrder]
    w.labelsList = [w._series[k]["label"] for k in w._visitOrder]
    w.legend.show()
    w.refreshLegend()

    entryKey = w._legendEntryKey(autoLabel)
    w._commitLegendEntryEdit(entryKey, "My Custom Model")

    # refreshLegend() rebuilds legend rows from scratch on every data update
    # (ADR 0022 incremental refresh); the rename must survive that rebuild.
    w.refreshLegend()
    _, rebuilt_label = w.legend.items[0]
    assert rebuilt_label.text == "My Custom Model"

    override = display_overrides.get_panel_override(*w._displayOverrideKey)
    assert override["legend"]["entries"][entryKey] == "My Custom Model"


def test_legend_position_survives_rebuild(qapp):
    """pyqtgraph's LegendItem.updateSize() -- called on every clear()/addItem(),
    i.e. every legend rebuild -- ends in setGeometry(0, 0, w, h), which resets
    a QGraphicsWidget's position to (0, 0) as a side effect of resizing. A
    dragged-then-renamed (or dragged-then-just-refreshed) legend must not
    silently snap back."""
    w = _make_widget(qapp)
    w.dataWatcher.datasetDependencies = ["ds1"]
    w.dataWatcher.modelDependencies = ["m1"]

    ds, model = _FakeObj("ds1", "Aspirin"), _FakeObj("m1", "MACE-small")
    autoLabel = {"dataset": ds, "model": model}
    w.plot(np.array([0, 1, 2]), np.array([0, 1, 2]), autoColor=autoLabel, autoLabel=autoLabel)
    w.plotItems = [w._series[k]["item"] for k in w._visitOrder]
    w.labelsList = [w._series[k]["label"] for k in w._visitOrder]
    w.legend.show()
    w.refreshLegend()

    w.legend.setPos(180, 220)
    entryKey = w._legendEntryKey(autoLabel)
    w._commitLegendEntryEdit(entryKey, "My Custom Model")  # rebuilds via refreshLegend()
    assert w.legend.pos() == QPointF(180, 220)

    w.refreshLegend()  # a plain data refresh, not a rename, rebuilds too
    assert w.legend.pos() == QPointF(180, 220)


def test_display_override_key_none_does_not_persist(qapp):
    """A Panel built outside the config-driven path (no identity) still edits
    live but never touches disk -- get_panel_override with an arbitrary key
    must stay empty."""
    w = _make_widget(qapp, key=None)
    w.setXLabel("Energy MAE")
    w._commitAxisLabelEdit("bottom", "Should Not Persist")
    assert w._currentAxisText("bottom") == "Should Not Persist"
    assert display_overrides.get_panel_override("any", "any", []) == {}


# --------------------------------------------------------------------- #
# Right-click menu on an axis label -- a discoverable alternative to
# scroll-to-resize/double-click-to-edit (not tested via a real right-click +
# QMenu.exec(), which blocks waiting for interactive dismissal; exercised at
# the eventFilter-consumption level and the built-menu level instead).
# --------------------------------------------------------------------- #
def _rightclick(qapp, widget, viewPos):
    ev = QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(viewPos),
        Qt.MouseButton.RightButton, Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier,
    )
    return QApplication.sendEvent(widget, ev)


def test_right_click_on_label_is_consumed_and_opens_menu_for_that_axis(qapp):
    w = _make_widget(qapp)
    w.setXLabel("Energy MAE")
    calls = []
    w._showAxisLabelMenu = lambda axisName, pos: calls.append(axisName)

    consumed = _rightclick(qapp, w.plotWidget, _axis_view_center(w, "bottom"))

    assert consumed is True
    assert calls == ["bottom"]


def test_right_click_elsewhere_on_plot_is_not_consumed(qapp):
    """Away from any axis label, our menu must not fire, so pyqtgraph's own
    ViewBox context menu still gets the press (sendEvent's return reflects
    Qt's own accept() bookkeeping, not specifically our filter, so the only
    reliable check here is that our handler never ran)."""
    w = _make_widget(qapp)
    w.setXLabel("Energy MAE")
    calls = []
    w._showAxisLabelMenu = lambda axisName, pos: calls.append(axisName)

    _rightclick(qapp, w.plotWidget, QPointF(250, 250))

    assert calls == []


def test_label_menu_actions_and_effects(qapp):
    w = _make_widget(qapp)
    w.setXLabel("Energy MAE")
    menu = w._buildAxisLabelMenu("bottom")
    actions = {a.text(): a for a in menu.actions() if a.text()}
    assert set(actions) == {
        "Set Font Size…", "Increase Font Size", "Decrease Font Size",
        "Edit Label…", "Reset Label",
    }

    actions["Increase Font Size"].trigger()
    assert w._labelState["bottom"]["font_size"] == 21

    actions["Decrease Font Size"].trigger()
    actions["Decrease Font Size"].trigger()
    assert w._labelState["bottom"]["font_size"] == 19

    actions["Edit Label…"].trigger()
    assert w._activeEditBox is not None
    assert w._activeEditBox.text() == "Energy MAE"
    w._activeEditBox.editingFinished.emit()  # close it before Reset


def test_label_menu_set_font_size_prompts_with_current_value_and_applies(qapp):
    """The dialog itself is never invoked in tests -- _askFontSizeDialog is the
    seam that isolates it (mirrors the _buildAxisLabelMenu/_showAxisLabelMenu
    split for QMenu). It's stubbed to call onResult synchronously, standing in
    for the dialog's intValueSelected/rejected signal firing later."""
    w = _make_widget(qapp)
    w.setXLabel("Energy MAE")
    w._resizeAxisLabel("bottom", 1)  # font_size now 21, so the dialog should offer 21

    seen = []
    w._askFontSizeDialog = lambda current, mn, mx, onResult: (
        seen.append((current, mn, mx)) or onResult(32, True)
    )

    actions = {a.text(): a for a in w._buildAxisLabelMenu("bottom").actions() if a.text()}
    actions["Set Font Size…"].trigger()

    assert seen == [(21, _MIN_LABEL_FONT_SIZE, _MAX_LABEL_FONT_SIZE)]
    assert w._labelState["bottom"]["font_size"] == 32
    override = display_overrides.get_panel_override(*w._displayOverrideKey)
    assert override["x_label"]["font_size"] == 32


def test_label_menu_set_font_size_cancelled_leaves_size_unchanged(qapp):
    w = _make_widget(qapp)
    w.setXLabel("Energy MAE")
    w._askFontSizeDialog = lambda current, mn, mx, onResult: onResult(current, False)  # Cancel

    actions = {a.text(): a for a in w._buildAxisLabelMenu("bottom").actions() if a.text()}
    actions["Set Font Size…"].trigger()

    assert w._labelState["bottom"]["font_size"] is None


def test_label_menu_set_font_size_clamps_out_of_range_value(qapp):
    w = _make_widget(qapp)
    w.setXLabel("Energy MAE")
    w._askFontSizeDialog = lambda current, mn, mx, onResult: onResult(999, True)

    actions = {a.text(): a for a in w._buildAxisLabelMenu("bottom").actions() if a.text()}
    actions["Set Font Size…"].trigger()

    assert w._labelState["bottom"]["font_size"] == _MAX_LABEL_FONT_SIZE


def test_label_menu_reset_clears_text_and_font_size(qapp):
    w = _make_widget(qapp)
    w.setXLabel("Energy MAE")
    w._commitAxisLabelEdit("bottom", "Custom Text")
    w._resizeAxisLabel("bottom", 1)

    menu = w._buildAxisLabelMenu("bottom")
    dict(zip((a.text() for a in menu.actions()), menu.actions()))["Reset Label"].trigger()

    assert w._currentAxisText("bottom") == "Energy MAE"
    assert w._labelState["bottom"]["font_size"] is None
    assert display_overrides.get_panel_override(*w._displayOverrideKey) == {}


# --------------------------------------------------------------------- #
# Right-click menu on the legend (mirrors the axis label menu). pyqtgraph
# dispatches right-clicks to scene items via its own ``mouseClickEvent``
# convention (GraphicsScene.sendClickEvent), not Qt's mousePressEvent -- a
# lightweight fake event stands in for pyqtgraph's wrapped click event
# (button()/accept()/screenPos()), same level of testing already used for
# _onLegendMoved/_onLegendWheel elsewhere in this file (the scene's own
# hit-dispatch is pyqtgraph's, not new code under test here).
# --------------------------------------------------------------------- #
class _FakeClickEvent:
    def __init__(self, button):
        self._button = button
        self.accepted = False

    def button(self):
        return self._button

    def accept(self):
        self.accepted = True

    def screenPos(self):
        return QPointF(10, 10)  # real pyqtgraph click events return QPointF here


def test_legend_right_click_opens_menu(qapp):
    w = _make_widget(qapp)
    calls = []
    w._showLegendMenu = lambda pos: calls.append(pos)

    ev = _FakeClickEvent(Qt.MouseButton.RightButton)
    w.legend.mouseClickEvent(ev)

    assert ev.accepted is True
    assert len(calls) == 1


def test_legend_left_click_does_not_open_menu(qapp):
    """Left click stays reserved for drag (mouseDragEvent handles that
    separately); mouseClickEvent must not also react to it."""
    w = _make_widget(qapp)
    calls = []
    w._showLegendMenu = lambda pos: calls.append(pos)

    ev = _FakeClickEvent(Qt.MouseButton.LeftButton)
    w.legend.mouseClickEvent(ev)

    assert ev.accepted is False
    assert calls == []


def test_legend_menu_actions_and_effects(qapp):
    w = _make_widget(qapp)
    actions = {a.text(): a for a in w._buildLegendMenu().actions() if a.text()}
    assert set(actions) == {
        "Set Font Size…", "Increase Font Size", "Decrease Font Size", "Reset Legend",
    }

    actions["Increase Font Size"].trigger()
    assert w._legendFontSize == 13

    actions["Decrease Font Size"].trigger()
    actions["Decrease Font Size"].trigger()
    assert w._legendFontSize == 11


def test_legend_menu_set_font_size_prompts_with_current_value_and_applies(qapp):
    w = _make_widget(qapp)
    w._resizeLegendFontSize(1)  # 13pt, so the dialog should offer 13
    seen = []
    w._askFontSizeDialog = lambda current, mn, mx, onResult: (
        seen.append(current) or onResult(18, True)
    )

    w._buildLegendMenu().actions()[0].trigger()  # "Set Font Size…"

    assert seen == [13]
    assert w._legendFontSize == 18
    override = display_overrides.get_panel_override(*w._displayOverrideKey)
    assert override["legend"]["font_size"] == 18


def test_legend_menu_set_font_size_cancelled_leaves_size_unchanged(qapp):
    w = _make_widget(qapp)
    w._askFontSizeDialog = lambda current, mn, mx, onResult: onResult(current, False)

    w._buildLegendMenu().actions()[0].trigger()

    assert w._legendFontSize is None


def test_legend_menu_reset_clears_position_and_font_size(qapp):
    w = _make_widget(qapp)
    w._onLegendMoved(99.0, 88.0)
    w._resizeLegendFontSize(2)
    w.legend.setPos(99, 88)

    actions = {a.text(): a for a in w._buildLegendMenu().actions() if a.text()}
    actions["Reset Legend"].trigger()

    assert w._legendFontSize is None
    assert w.legend.pos() == QPointF(30, 10)
    assert display_overrides.get_panel_override(*w._displayOverrideKey) == {}


# --------------------------------------------------------------------- #
# _askFontSizeDialog's *real* implementation must not block. Qt and asyncio
# share one event loop in this app (qasync), so a static, blocking
# QInputDialog.getInt()-style call left open by the user could starve the
# WebSocket keepalive ping/pong and get the connection dropped by the server
# (1011 ping timeout) -- exactly what motivated switching to QDialog.open().
# --------------------------------------------------------------------- #
def test_ask_font_size_dialog_does_not_block_and_prefills_current_value(qapp):
    w = _make_widget(qapp)
    results = []

    w._askFontSizeDialog(21, 8, 48, lambda size, ok: results.append((size, ok)))

    # If this were exec()/getInt(), the call above would never return without
    # a real user interacting with a modal dialog -- reaching this line at all
    # is the non-blocking assertion.
    dialog = w.plotWidget.findChild(QtWidgets.QInputDialog)
    assert dialog is not None
    assert dialog.intValue() == 21
    assert dialog.intMinimum() == 8 and dialog.intMaximum() == 48
    assert results == []  # no signal fired yet -- nothing forced synchronously

    dialog.intValueSelected.emit(30)
    assert results == [(30, True)]


def test_ask_font_size_dialog_rejected_reports_original_current_value(qapp):
    w = _make_widget(qapp)
    results = []

    w._askFontSizeDialog(21, 8, 48, lambda size, ok: results.append((size, ok)))
    dialog = w.plotWidget.findChild(QtWidgets.QInputDialog)
    dialog.rejected.emit()

    assert results == [(21, False)]

"""Colorbar Display Override interactive mechanics (ADR 0029).

ColorbarOverlay is the Qt-native replacement for vispy's grid-cell
ColorBarWidget (UI/loupe/colorbar_overlay.py) -- built so drag-to-reposition,
double-click-to-edit, and scroll-to-resize work at all, which vispy's grid
widget cannot support. Identity is the client-tracked Metric ID driving
atom-coloring, injected via a callable so this test never needs a real Loupe
window or server connection.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import client.display_overrides as display_overrides  # noqa: E402
from UI.loupe.colorbar_overlay import ColorbarOverlay  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _redirect_overrides_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        display_overrides, "OVERRIDES_FILE", str(tmp_path / "display_overrides.json")
    )


def _make_overlay(qapp, metric_id):
    parent = QWidget()
    parent.resize(400, 400)
    overlay = ColorbarOverlay(parent, lambda: metric_id[0])
    overlay._test_parent_keepalive = parent  # Qt would delete overlay's C++
    # object too if the parent QWidget is garbage-collected on the Python side
    return overlay


def _drag(overlay, start, end):
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(*start),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
    overlay.mousePressEvent(press)
    move = QMouseEvent(QEvent.Type.MouseMove, QPointF(*end),
                        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    overlay.mouseMoveEvent(move)
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(*end),
                           Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                           Qt.KeyboardModifier.NoModifier)
    overlay.mouseReleaseEvent(release)


def _dblclick_label(overlay):
    center = overlay._labelRect().center()
    ev = QMouseEvent(QEvent.Type.MouseButtonDblClick, QPointF(center),
                      Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                      Qt.KeyboardModifier.NoModifier)
    overlay.mouseDoubleClickEvent(ev)


def _wheel(overlay, steps=1):
    ev = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, 120 * steps),
                      Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                      Qt.ScrollPhase.NoScrollPhase, False)
    overlay.wheelEvent(ev)


def test_default_label_shown_with_no_override(qapp):
    overlay = _make_overlay(qapp, ["ffast.force_error"])
    overlay.update_descriptor(None, 0.0, 1.0, "Force Error")
    assert overlay._currentLabelText() == "Force Error"


def test_drag_moves_and_persists_position(qapp):
    overlay = _make_overlay(qapp, ["ffast.force_error"])
    overlay.update_descriptor(None, 0.0, 1.0, "Force Error")
    _drag(overlay, (50, 50), (80, 90))

    assert overlay.pos() == QPoint(50, 60)
    override = display_overrides.get_colorbar_override("ffast.force_error")
    assert override["position"] == {"x": 50, "y": 60}


def test_double_click_label_opens_editor_and_commit_persists(qapp):
    overlay = _make_overlay(qapp, ["ffast.force_error"])
    overlay.update_descriptor(None, 0.0, 1.0, "Force Error")
    _dblclick_label(overlay)

    assert overlay._activeEditBox is not None
    assert overlay._activeEditBox.text() == "Force Error"

    overlay._activeEditBox.setText("Custom Force Label")
    overlay._activeEditBox.editingFinished.emit()

    assert overlay._currentLabelText() == "Custom Force Label"
    override = display_overrides.get_colorbar_override("ffast.force_error")
    assert override["label"]["text"] == "Custom Force Label"


def test_clearing_label_edit_reverts_to_default(qapp):
    overlay = _make_overlay(qapp, ["ffast.force_error"])
    overlay.update_descriptor(None, 0.0, 1.0, "Force Error")
    _dblclick_label(overlay)
    overlay._activeEditBox.setText("")
    overlay._activeEditBox.editingFinished.emit()

    assert overlay._currentLabelText() == "Force Error"
    override = display_overrides.get_colorbar_override("ffast.force_error")
    assert "text" not in override.get("label", {})


def test_scroll_resizes_and_persists_font(qapp):
    overlay = _make_overlay(qapp, ["ffast.force_error"])
    overlay.update_descriptor(None, 0.0, 1.0, "Force Error")
    _wheel(overlay, steps=1)

    assert overlay._font_size == 11
    override = display_overrides.get_colorbar_override("ffast.force_error")
    assert override["label"]["font_size"] == 11


def test_switching_metric_resets_then_reapplies_its_own_override(qapp):
    metric_id = ["ffast.force_error"]
    overlay = _make_overlay(qapp, metric_id)
    overlay.update_descriptor(None, 0.0, 1.0, "Force Error")
    _drag(overlay, (50, 50), (80, 90))
    _dblclick_label(overlay)
    overlay._activeEditBox.setText("Custom Force Label")
    overlay._activeEditBox.editingFinished.emit()

    # Selecting a different coloring metric shows ITS default, at the
    # default position -- not the previous metric's override leaking over.
    metric_id[0] = "ffast.energy_error"
    overlay.update_descriptor(None, 0.0, 1.0, "Energy Error")
    assert overlay._currentLabelText() == "Energy Error"
    assert overlay.pos() == QPoint(20, 20)

    # Switching back re-picks up the force_error override from disk.
    metric_id[0] = "ffast.force_error"
    overlay.update_descriptor(None, 0.0, 1.0, "Force Error")
    assert overlay._currentLabelText() == "Custom Force Label"
    assert overlay.pos() == QPoint(50, 60)


def test_redraw_with_same_metric_does_not_fight_in_progress_state(qapp):
    """A data refresh (new frame/model, same coloring metric) must not reset a
    font size the user just set -- override reload is gated on metric change,
    not on every update_descriptor call."""
    overlay = _make_overlay(qapp, ["ffast.force_error"])
    overlay.update_descriptor(None, 0.0, 1.0, "Force Error")
    _wheel(overlay, steps=2)
    size_after_scroll = overlay._font_size

    overlay.update_descriptor(None, 0.1, 0.9, "Force Error")  # same metric, new range
    assert overlay._font_size == size_after_scroll


def test_no_metric_selected_edits_live_but_does_not_persist(qapp):
    overlay = _make_overlay(qapp, [None])
    overlay.update_descriptor(None, 0.0, 1.0, "Force Error")
    _dblclick_label(overlay)
    overlay._activeEditBox.setText("Unsaved Edit")
    overlay._activeEditBox.editingFinished.emit()

    assert overlay._currentLabelText() == "Unsaved Edit"
    assert display_overrides.get_colorbar_override("ffast.force_error") == {}

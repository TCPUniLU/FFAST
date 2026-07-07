"""Colorbar Display Override interactive mechanics (ADR 0029).

The Loupe colorbar is four independent floating pieces -- the gradient **bar**
(overlay._bar), the **name** label (overlay._label), and the **vmax** / **vmin**
values (overlay._vmax / overlay._vmin) -- each draggable and 90°-rotatable on
its own, each persisting its own state under a sub-key of the per-metric
override. "Rotate all 90°" spins the whole group as one rigid block. Identity is
the client-tracked Metric ID driving atom-coloring, injected via a callable so
this test never needs a real Loupe window or server connection.
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
    overlay._test_parent_keepalive = parent  # Qt would delete the pieces' C++
    # objects too if the parent QWidget is garbage-collected on the Python side
    # Alignment-guide snapping is a separate concern from drag mechanics, and
    # on this small 400x400 test canvas the default positions sit close
    # enough together that snapping would interfere incidentally; tests that
    # actually exercise snapping re-enable it explicitly.
    overlay._snap_enabled = False
    return overlay


def _drag(item, start, end):
    item.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(*start), Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    item.mouseMoveEvent(QMouseEvent(
        QEvent.Type.MouseMove, QPointF(*end), Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    item.mouseReleaseEvent(QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(*end), Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))


def _dblclick(item):
    item.mouseDoubleClickEvent(QMouseEvent(
        QEvent.Type.MouseButtonDblClick, QPointF(5, 5), Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def _wheel(item, steps=1):
    item.wheelEvent(QWheelEvent(
        QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120 * steps),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False))


def test_each_piece_drags_and_persists_under_its_own_key(qapp):
    overlay = _make_overlay(qapp, ["ffast.force_error"])
    overlay.update_descriptor(None, 0.0, 1.0, "Force Error")

    bar0 = overlay._bar.pos()
    _drag(overlay._bar, (5, 5), (35, 45))  # +30, +40
    assert overlay._bar.pos() == QPoint(bar0.x() + 30, bar0.y() + 40)

    # Positions persist as a FRACTION of the parent canvas (400x400 here), not
    # raw pixels -- reproducible across any canvas size.
    override = display_overrides.get_colorbar_override("ffast.force_error")
    assert override["bar"]["pos"] == pytest.approx(
        [(bar0.x() + 30) / 400, (bar0.y() + 40) / 400])
    # Moving the bar doesn't touch the name or values entries.
    assert "label" not in override
    assert "values" not in override


def test_drag_snaps_to_another_pieces_edge(qapp):
    overlay = _make_overlay(qapp, ["ffast.force_error"])
    overlay._snap_enabled = True  # off by default in _make_overlay
    overlay.update_descriptor(None, 0.0, 2.0, "Force Error")

    # Move vmin/label far out of the way so only the bar-vs-vmax pair is in
    # play -- otherwise their default positions can put a competing line
    # (e.g. vmax's own center vs. another piece's center) closer than the
    # edge match this test is isolating, and the nearest pair always wins.
    # label is tall when rotated (58x170), so y=10 would put its own center
    # within the snap threshold of vmax's center -- push it further down.
    overlay._vmin.move(350, 350)
    overlay._label.move(350, 500)

    bar_right = overlay._bar.pos().x() + overlay._bar.width()
    vmax_left0 = overlay._vmax.pos().x()
    vmax_y0 = overlay._vmax.pos().y()

    # Drag vmax so its left edge lands 3px from the bar's right edge -- within
    # the snap threshold -- and confirm it snaps flush instead of stopping 3px
    # short (PowerPoint/Figma-style alignment guide).
    target_left = bar_right + 3
    overlay.begin_drag(overlay._vmax)
    overlay.do_drag(QPoint(target_left - vmax_left0, 0))
    assert overlay._vmax.pos().x() == bar_right          # snapped flush
    assert overlay._vmax.pos().y() == vmax_y0            # y untouched, no cross-axis snap
    assert overlay._guide._vx == bar_right               # guide line drawn at the snap point
    # isHidden(), not isVisible() -- the test's top-level parent is never
    # shown, so isVisible() (which depends on the whole ancestor chain) would
    # always read False regardless of the guide's own show()/hide() state.
    assert not overlay._guide.isHidden()

    overlay.end_drag()
    assert overlay._guide.isHidden()
    assert overlay._guide._vx is None                    # guide cleared after drop
    override = display_overrides.get_colorbar_override("ffast.force_error")
    assert override["vmax"]["pos"][0] == pytest.approx(bar_right / 400)


def test_rotate_bar_is_independent_of_the_text_pieces(qapp):
    overlay = _make_overlay(qapp, ["ffast.force_error"])
    overlay.update_descriptor(None, 0.0, 2.0, "Force Error")

    # Vertical preset default: bar upright, name pre-rotated -90°
    assert overlay._bar._rotation == 0
    assert overlay._label._rotation == -90

    bw, bh = overlay._bar.width(), overlay._bar.height()
    overlay._bar._toggleRotation()
    assert overlay._bar._rotation == 90
    assert (overlay._bar.width(), overlay._bar.height()) == (bh, bw)  # swapped
    assert overlay._label._rotation == -90  # untouched by the bar's own toggle
    assert overlay._vmax._rotation == 0 and overlay._vmin._rotation == 0

    override = display_overrides.get_colorbar_override("ffast.force_error")
    assert override["bar"]["rotation"] == 90
    assert "label" not in override and "vmax" not in override and "vmin" not in override


def test_vmin_vmax_are_separate_pieces(qapp):
    overlay = _make_overlay(qapp, ["ffast.force_error"])
    overlay.update_descriptor(None, 0.0, 2.0, "Force Error")
    assert overlay._vmax._lines() == ["2.00"]
    assert overlay._vmin._lines() == ["0.00"]

    _drag(overlay._vmax, (3, 3), (3, 33))       # move vmax down 30
    overlay._vmin._toggleRotation()             # rotate vmin only
    assert overlay._vmin._rotation == 90 and overlay._vmax._rotation == 0

    override = display_overrides.get_colorbar_override("ffast.force_error")
    assert "pos" in override["vmax"]
    assert override["vmin"]["rotation"] == 90
    assert "rotation" not in override.get("vmax", {})  # vmax orientation untouched


def test_scroll_resizes_bar_and_persists(qapp):
    overlay = _make_overlay(qapp, ["ffast.force_error"])
    overlay.update_descriptor(None, 0.0, 2.0, "Force Error")
    w0, h0 = overlay._bar.width(), overlay._bar.height()

    # A few notches, not one -- on a small test canvas the fraction-derived
    # base size is small enough that a single 5% nudge can round-trip to the
    # same pixel value.
    _wheel(overlay._bar, steps=5)      # scroll up -> bigger
    assert overlay._bar.width() > w0 and overlay._bar.height() > h0
    assert display_overrides.get_colorbar_override(
        "ffast.force_error")["bar"]["scale"] > 1.0

    overlay._bar._resetSize()
    _wheel(overlay._bar, steps=-5)     # scroll down from default -> smaller
    assert overlay._bar.height() < h0
    assert display_overrides.get_colorbar_override(
        "ffast.force_error")["bar"]["scale"] < 1.0

    overlay._bar._resetSize()
    assert (overlay._bar.width(), overlay._bar.height()) == (w0, h0)
    assert "scale" not in display_overrides.get_colorbar_override(
        "ffast.force_error").get("bar", {})


def test_rotate_name_and_values_independently(qapp):
    overlay = _make_overlay(qapp, ["ffast.force_error"])
    overlay.update_descriptor(None, 0.0, 2.0, "Force Error")

    # Vertical preset default: name starts pre-rotated -90°, vmax starts upright
    assert overlay._label._rotation == -90 and overlay._vmax._rotation == 0

    overlay._label._toggleRotation()  # -90 -> 0 (label only cycles 0/-90)
    overlay._vmax._toggleRotation()   # 0 -> 90
    assert overlay._label._rotation == 0 and overlay._vmax._rotation == 90
    assert overlay._bar._rotation == 0 and overlay._vmin._rotation == 0  # untouched

    override = display_overrides.get_colorbar_override("ffast.force_error")
    # rotation=0 clears the field (falls back to the preset default) rather
    # than storing an explicit 0
    assert "rotation" not in override["label"]
    assert override["vmax"]["rotation"] == 90
    assert "bar" not in override and "vmin" not in override


def test_set_font_size_numeric_persists(qapp):
    overlay = _make_overlay(qapp, ["ffast.force_error"])
    overlay.update_descriptor(None, 0.0, 1.0, "Force Error")
    overlay._label._setFontSize(18)
    assert overlay._label._font_size == 18
    assert display_overrides.get_colorbar_override(
        "ffast.force_error")["label"]["font_size"] == 18
    # out-of-range clamps
    overlay._vmax._setFontSize(999)
    assert overlay._vmax._font_size == 32  # _MAX_FONT_PT


def test_rotate_all_snaps_between_curated_presets(qapp):
    overlay = _make_overlay(qapp, ["ffast.force_error"])
    overlay.update_descriptor(None, 0.0, 2.0, "Force Error")

    overlay.rotate_all()  # vertical -> horizontal preset
    assert overlay._orientation == "horizontal"
    # horizontal: wide bar (rotated), upright text, vmin left of vmax, name below
    assert overlay._bar._rotation == 90
    assert overlay._vmax._rotation == 0 and overlay._vmin._rotation == 0
    assert overlay._label._rotation == 0
    assert overlay._vmin.pos().x() < overlay._vmax.pos().x()
    assert overlay._label.pos().y() > overlay._bar.pos().y()
    assert display_overrides.get_colorbar_override("ffast.force_error")["_layout"] == "horizontal"

    overlay.rotate_all()  # back to vertical preset
    assert overlay._orientation == "vertical"
    # vertical: tall bar upright, name rotated -90° on the right
    assert overlay._bar._rotation == 0 and overlay._label._rotation == -90
    assert overlay._label.pos().x() > overlay._bar.pos().x()
    # cleared to the default orientation, not stored as "vertical"
    assert "_layout" not in display_overrides.get_colorbar_override("ffast.force_error")


def test_select_shift_click_then_group_drags_together(qapp):
    overlay = _make_overlay(qapp, ["ffast.force_error"])
    overlay.update_descriptor(None, 0.0, 2.0, "Force Error")

    overlay.on_press(overlay._bar, additive=False)   # select bar
    overlay.on_press(overlay._label, additive=True)  # add name
    assert overlay._selected == {overlay._bar, overlay._label}
    assert overlay.can_group()

    overlay.group_selected()
    assert overlay.group_of(overlay._bar) is overlay.group_of(overlay._label)
    assert overlay.group_of(overlay._vmax) is None  # not in the group

    # dragging the grouped bar moves the name too, by the same delta
    bar0, name0 = overlay._bar.pos(), overlay._label.pos()
    overlay.begin_drag(overlay._bar)
    overlay.do_drag(QPoint(25, 40))
    overlay.end_drag()
    assert overlay._bar.pos() == QPoint(bar0.x() + 25, bar0.y() + 40)
    assert overlay._label.pos() == QPoint(name0.x() + 25, name0.y() + 40)
    # vmax (outside the group) stayed put
    ov = display_overrides.get_colorbar_override("ffast.force_error")
    assert "pos" in ov["bar"] and "pos" in ov["label"]


def test_ungroup_restores_independent_drag(qapp):
    overlay = _make_overlay(qapp, ["ffast.force_error"])
    overlay.update_descriptor(None, 0.0, 2.0, "Force Error")
    overlay.on_press(overlay._bar, additive=False)
    overlay.on_press(overlay._vmax, additive=True)
    overlay.group_selected()
    assert overlay.group_of(overlay._bar) is not None

    overlay.ungroup(overlay._bar)
    assert overlay.group_of(overlay._bar) is None
    assert overlay.group_of(overlay._vmax) is None

    # after ungroup, dragging the bar (now selecting only it) moves only it
    overlay.on_press(overlay._bar, additive=False)
    vmax0 = overlay._vmax.pos()
    overlay.begin_drag(overlay._bar)
    overlay.do_drag(QPoint(30, 0))
    overlay.end_drag()
    assert overlay._vmax.pos() == vmax0  # untouched


def test_double_click_name_edits_and_persists(qapp):
    overlay = _make_overlay(qapp, ["ffast.force_error"])
    overlay.update_descriptor(None, 0.0, 1.0, "Force Error")
    _dblclick(overlay._label)
    assert overlay._label._activeEditBox is not None
    assert overlay._label._activeEditBox.text() == "Force Error"

    overlay._label._activeEditBox.setText("Custom")
    overlay._label._activeEditBox.editingFinished.emit()
    assert overlay._label.current_text() == "Custom"
    assert display_overrides.get_colorbar_override("ffast.force_error")["label"]["text"] == "Custom"


def test_clearing_name_reverts_to_default(qapp):
    overlay = _make_overlay(qapp, ["ffast.force_error"])
    overlay.update_descriptor(None, 0.0, 1.0, "Force Error")
    _dblclick(overlay._label)
    overlay._label._activeEditBox.setText("")
    overlay._label._activeEditBox.editingFinished.emit()
    assert overlay._label.current_text() == "Force Error"
    assert "text" not in display_overrides.get_colorbar_override(
        "ffast.force_error").get("label", {})


def test_scroll_resizes_value_font_and_persists(qapp):
    overlay = _make_overlay(qapp, ["ffast.force_error"])
    overlay.update_descriptor(None, 0.0, 1.0, "Force Error")
    _wheel(overlay._vmax, steps=1)
    assert overlay._vmax._font_size == 25
    assert display_overrides.get_colorbar_override(
        "ffast.force_error")["vmax"]["font_size"] == 25
    # name and vmin fonts untouched
    ov = display_overrides.get_colorbar_override("ffast.force_error")
    assert "label" not in ov and "vmin" not in ov


def test_switching_metric_reloads_each_piece(qapp):
    metric_id = ["ffast.force_error"]
    overlay = _make_overlay(qapp, metric_id)
    overlay.update_descriptor(None, 0.0, 2.0, "Force Error")
    _drag(overlay._bar, (5, 5), (55, 5))  # +50 x
    overlay._label._toggleRotation()
    _dblclick(overlay._label)
    overlay._label._activeEditBox.setText("Custom")
    overlay._label._activeEditBox.editingFinished.emit()
    bar_x = overlay._bar.pos().x()

    metric_id[0] = "ffast.energy_error"
    overlay.update_descriptor(None, 0.0, 1.0, "Energy Error")
    assert overlay._label.current_text() == "Energy Error"
    # unrelated metric, no override -> vertical preset default (pre-rotated -90°)
    assert overlay._label._rotation == -90

    metric_id[0] = "ffast.force_error"
    overlay.update_descriptor(None, 0.0, 2.0, "Force Error")
    assert overlay._label.current_text() == "Custom"
    # toggled -90->0 earlier, which cleared the field back to the preset default (-90)
    assert overlay._label._rotation == -90
    assert overlay._bar.pos().x() == bar_x


def test_redraw_same_metric_keeps_in_progress_state(qapp):
    overlay = _make_overlay(qapp, ["ffast.force_error"])
    overlay.update_descriptor(None, 0.0, 1.0, "Force Error")
    _wheel(overlay._vmin, steps=2)
    size = overlay._vmin._font_size
    overlay.update_descriptor(None, 0.1, 0.9, "Force Error")  # same metric, new range
    assert overlay._vmin._font_size == size


def test_no_metric_selected_edits_live_but_does_not_persist(qapp):
    overlay = _make_overlay(qapp, [None])
    overlay.update_descriptor(None, 0.0, 1.0, "Force Error")
    _dblclick(overlay._label)
    overlay._label._activeEditBox.setText("Unsaved")
    overlay._label._activeEditBox.editingFinished.emit()
    assert overlay._label.current_text() == "Unsaved"
    assert display_overrides.get_colorbar_override("ffast.force_error") == {}

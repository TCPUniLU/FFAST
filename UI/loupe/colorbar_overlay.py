"""Qt-native colorbar overlay for Loupe atom-coloring (ADR 0029, ADR 0016).

Replaces vispy's grid-cell ``ColorBarWidget``: vispy's grid layout can't float,
be dragged, or accept text-edit events, so drag-to-reposition (explicitly in
scope for the Colorbar Display Override) could not be bolted onto the existing
widget. This instead draws its own gradient bar + tick labels with QPainter,
floated over ``canvas.native`` the same way ``atomSelectBar`` is (UI/loupe/
canvas.py) -- a plain child QWidget positioned with move()/setGeometry(), not
added to any layout.

Excludes vmin/vmax editing: the color range is a Presentation Parameter
concern (schema-driven, server-resolved), not cosmetic UI chrome.
"""
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from client import display_overrides

_DEFAULT_FONT_PT = 10
_MIN_FONT_PT = 6
_MAX_FONT_PT = 24
_BAR_WIDTH = 24
_BAR_HEIGHT = 160
_LABEL_HEIGHT = 24
_DEFAULT_POS = (20, 20)


class ColorbarOverlay(QtWidgets.QWidget):
    """Floating, draggable, editable colorbar for value-driven atom coloring.

    ``get_metric_id`` is a zero-arg callable returning the client-tracked
    Metric ID currently driving atom-coloring (or None), the identity a
    Colorbar Display Override is keyed on -- the wire-level ``AtomColorBy``
    descriptor carries no identity by design (ADR 0016).
    """

    def __init__(self, parent, get_metric_id):
        super().__init__(parent)
        self._get_metric_id = get_metric_id
        self._colormap = None
        self._vmin, self._vmax = 0.0, 1.0
        self._default_label = ""
        self._label_override = None
        self._font_size = None
        self._current_metric_id = None
        self._dragOffset = None
        self._activeEditBox = None
        self._activeEditBoxCancel = None
        self.setFixedSize(140, _BAR_HEIGHT + _LABEL_HEIGHT + 20)
        self.move(*_DEFAULT_POS)
        self.hide()

    # ------------------------------------------------------------------ #
    def _currentLabelText(self):
        return self._label_override if self._label_override is not None else self._default_label

    def update_descriptor(self, colormap, vmin, vmax, label):
        """Refresh from a new AtomColorBy descriptor. Only reloads the saved
        override when the active metric actually changes -- a redraw with the
        same metric (new frame/model) must not fight an in-progress drag or
        reset a font-size the user just set."""
        self._colormap = colormap
        self._vmin, self._vmax = vmin, vmax
        self._default_label = label

        metric_id = self._get_metric_id()
        if metric_id != self._current_metric_id:
            self._current_metric_id = metric_id
            self._label_override = None
            self._font_size = None
            self.move(*_DEFAULT_POS)
            if metric_id is not None:
                self._applyOverride(metric_id)

        self.show()
        self.raise_()
        self.update()

    def hide_colorbar(self):
        self.hide()
        self._current_metric_id = None

    # ------------------------------------------------------------------ #
    def _applyOverride(self, metric_id):
        override = display_overrides.get_colorbar_override(metric_id)
        label_ov = override.get("label") or {}
        if "text" in label_ov:
            self._label_override = label_ov["text"]
        if "font_size" in label_ov:
            self._font_size = label_ov["font_size"]
        position = override.get("position") or {}
        self.move(
            int(position.get("x", _DEFAULT_POS[0])),
            int(position.get("y", _DEFAULT_POS[1])),
        )

    def _persist(self, path, value):
        if self._current_metric_id is None:
            return
        display_overrides.set_colorbar_override(self._current_metric_id, path, value)

    # ------------------------------------------------------------------ #
    # Paint
    def _barRect(self):
        return QtCore.QRect(10, 10, _BAR_WIDTH, _BAR_HEIGHT)

    def _labelRect(self):
        bar = self._barRect()
        return QtCore.QRect(0, bar.bottom() + 8, self.width(), _LABEL_HEIGHT)

    def _sampleColor(self, frac):
        if self._colormap is None:
            gray = int(max(0.0, min(1.0, frac)) * 255)
            return QtGui.QColor(gray, gray, gray)
        rgba = np.asarray(self._colormap[float(frac)].rgba).reshape(-1)
        r, g, b = rgba[0], rgba[1], rgba[2]
        a = rgba[3] if rgba.size > 3 else 1.0
        return QtGui.QColor.fromRgbF(float(r), float(g), float(b), float(a))

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        bar = self._barRect()
        gradient = QtGui.QLinearGradient(0, bar.top(), 0, bar.bottom())
        for frac in np.linspace(0, 1, 16):
            gradient.setColorAt(float(frac), self._sampleColor(1.0 - frac))
        painter.fillRect(bar, gradient)
        painter.setPen(QtGui.QColor("lightgray"))
        painter.drawRect(bar)

        size = self._font_size or _DEFAULT_FONT_PT
        font = painter.font()
        font.setPointSize(size)
        painter.setFont(font)
        painter.drawText(bar.right() + 6, bar.top() + size, f"{self._vmax:.2f}")
        painter.drawText(bar.right() + 6, bar.bottom(), f"{self._vmin:.2f}")
        painter.drawText(
            self._labelRect(), QtCore.Qt.AlignmentFlag.AlignHCenter, self._currentLabelText()
        )

    # ------------------------------------------------------------------ #
    # Drag to reposition (ADR 0029, Q1-C / Q3)
    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._dragOffset = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragOffset is not None:
            self.move(self.mapToParent(event.position().toPoint() - self._dragOffset))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragOffset is not None:
            self._dragOffset = None
            pos = self.pos()
            self._persist(("position", "x"), pos.x())
            self._persist(("position", "y"), pos.y())
        super().mouseReleaseEvent(event)

    # Double-click the label to edit its text (ADR 0029, Q7: direct
    # manipulation, no dialogs/menus).
    def mouseDoubleClickEvent(self, event):
        if self._labelRect().contains(event.position().toPoint()):
            self._startLabelEdit()
            return
        super().mouseDoubleClickEvent(event)

    def _startLabelEdit(self):
        if self._activeEditBox is not None:
            self._activeEditBoxCancel()

        box = QtWidgets.QLineEdit(self)
        box.setText(self._currentLabelText())
        box.setGeometry(self._labelRect())
        state = {"committed": False}

        def commit():
            if state["committed"]:
                return
            state["committed"] = True
            text = box.text().strip()
            box.deleteLater()
            self._activeEditBox = None
            self._activeEditBoxCancel = None
            self._label_override = text or None
            self._persist(("label", "text"), text or None)
            self.update()

        def cancel():
            if state["committed"]:
                return
            state["committed"] = True
            box.deleteLater()
            self._activeEditBox = None
            self._activeEditBoxCancel = None

        box.editingFinished.connect(commit)
        box.installEventFilter(self)
        self._activeEditBox = box
        self._activeEditBoxCancel = cancel
        box.show()
        box.setFocus()
        box.selectAll()

    def eventFilter(self, obj, event):
        if (obj is self._activeEditBox
                and event.type() == QtCore.QEvent.Type.KeyPress
                and event.key() == QtCore.Qt.Key.Key_Escape):
            self._activeEditBoxCancel()
            return True
        return super().eventFilter(obj, event)

    # Scroll to resize the label/tick font (ADR 0029, Q8: scroll-to-resize,
    # no drag handles or +/- buttons).
    def wheelEvent(self, event):
        size = self._font_size or _DEFAULT_FONT_PT
        delta = event.angleDelta().y()
        size = min(_MAX_FONT_PT, max(_MIN_FONT_PT, size + (1 if delta > 0 else -1)))
        self._font_size = size
        self._persist(("label", "font_size"), size)
        self.update()

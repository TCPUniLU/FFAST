"""Qt-native colorbar overlay for Loupe atom-coloring (ADR 0029, ADR 0016).

Replaces vispy's grid-cell ``ColorBarWidget`` (which can't float, drag, or take
text edits) with the colorbar drawn as four *independent* floating pieces over
``canvas.native`` -- the gradient **bar**, the **name**, and the **vmax** /
**vmin** values -- each its own small child QWidget so it can be moved, rotated
90°, and resized on its own without a canvas-spanning widget swallowing mouse
events. Pieces can be **selected** (click / shift-click) and **grouped** so they
drag and rotate together. ``Rotate all 90°`` snaps the whole colorbar between two
curated layouts (vertical / horizontal). Every piece persists its own state per
Metric ID via the Colorbar Display Override.

Excludes vmin/vmax *editing*: the color range is a Presentation Parameter
concern (schema-driven, server-resolved), not cosmetic UI chrome.
"""
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from client import display_overrides

_DEFAULT_FONT_PT = 24
_MIN_FONT_PT = 10
_MAX_FONT_PT = 32
_PAD = 10
_GAP = 6  # spacing between pieces in the curated presets

# The bar's base size, as a FRACTION of the parent canvas -- same reasoning as
# the positions below: a fixed pixel size only looks right on the one canvas
# size it was tuned against (matplotlib's colorbar fraction=/shrink= do the
# same). Font size and _PAD/_GAP stay fixed points/pixels on purpose: text
# legibility and small margins are about physical size, not "% of window" --
# nobody wants their label shrinking because the window got narrower.
_BAR_WIDTH_FRAC = 0.06
_BAR_HEIGHT_FRAC = 0.6
# Fallback pixel size used only before the parent has real geometry (e.g. the
# constructor's initial, hidden placement); corrected on the first apply_state.
_BAR_FALLBACK_SIZE = (84, 400)
_MIN_BAR_SCALE = 0.4
_MAX_BAR_SCALE = 5.0
_BAR_SCALE_PER_NOTCH = 1.05  # gentle; scaled by actual scroll amount below

# Default positions, as a FRACTION of the parent canvas's current size (a
# vertical-ish starting layout) -- not raw pixels, which mean nothing without
# knowing the canvas size they were picked against. (x/parent.width(),
# y/parent.height()) reproduces the same relative placement on any window
# size / OS / DPI, the same way matplotlib's bbox_to_anchor or CSS % do.
_BAR_POS_FRAC = (0.02, 0.2)
_VMAX_POS_FRAC = (0.09, 0.18)
_VMIN_POS_FRAC = (0.09, 0.78)
_LABEL_POS_FRAC = (0.10, 0.28)

# Per-preset starting rotation for a piece with no saved override -- mirrors
# the rotations _apply_preset() snaps to, so a fresh metric already reads
# right instead of flat-then-fixed-on-first-rotate-all.
_VERTICAL_DEFAULT_ROTATIONS = {"bar": 0, "vmax": 0, "vmin": 0, "label": -90}
_HORIZONTAL_DEFAULT_ROTATIONS = {"bar": 90, "vmax": 0, "vmin": 0, "label": 0}


class _ColorItem(QtWidgets.QWidget):
    """One floating, draggable, selectable, 90°-rotatable piece of the colorbar.

    Content is authored unrotated; a rigid 90° rotation is a QTransform applied
    on top, so paint code stays orientation-agnostic and the widget's own size
    swaps W<->H. Drag/selection/rotation are routed through the coordinator so a
    group (or multi-selection) transforms together."""

    def __init__(self, parent, coordinator, key, default_pos_frac):
        super().__init__(parent)
        self._coord = coordinator
        self._key = key
        self._default_pos_frac = default_pos_frac
        self._rotation = 0
        self._selected = False
        self._dragging = False
        self.move(self._defaultPos())
        self.hide()

    # -- state / persistence ------------------------------------------------ #
    def _persist(self, subpath, value):
        self._coord.persist((self._key,) + tuple(subpath), value)

    # -- fractional positioning ---------------------------------------------- #
    # Positions are stored/persisted as a FRACTION of the parent canvas's
    # current size, not raw pixels -- a raw pixel position is meaningless
    # without knowing the canvas size it was captured against, so it wouldn't
    # reproduce the same placement on a different window size or OS.
    def _posToFrac(self, point):
        parent = self.parentWidget()
        if parent is None or parent.width() <= 0 or parent.height() <= 0:
            return [0.0, 0.0]
        return [point.x() / parent.width(), point.y() / parent.height()]

    def _fracToPos(self, frac):
        parent = self.parentWidget()
        if parent is None or parent.width() <= 0 or parent.height() <= 0:
            return QtCore.QPoint(0, 0)
        # round(), not int(): a fraction that doesn't represent exactly in
        # binary float (e.g. 58/400) can convert back a hair under the
        # original pixel value, and truncation would lose that pixel.
        return QtCore.QPoint(round(frac[0] * parent.width()), round(frac[1] * parent.height()))

    def _defaultPos(self):
        return self._fracToPos(self._default_pos_frac)

    def apply_state(self, state, default_rotation=0):
        """Load this piece's saved override (pos + rotation); subclasses extend
        for text/font/scale. Missing fields fall back to defaults -- rotation
        falls back to default_rotation so a piece can start pre-rotated under
        the active preset (e.g. the name starts turned in Vertical). Rotation
        is signed (-90/0/90): +90 and -90 are distinct, opposite-reading
        orientations, not just "turned vs not"."""
        saved = state.get("rotation")
        self._rotation = saved if saved is not None else default_rotation
        pos = state.get("pos")
        self._relayout()
        if pos:
            self.move(self._fracToPos(pos))
        else:
            self.move(self._defaultPos())

    def set_selected(self, flag):
        if self._selected != flag:
            self._selected = flag
            self.update()

    # -- rotation ----------------------------------------------------------- #
    def _content_size(self):
        raise NotImplementedError

    def _relayout(self):
        w, h = self._content_size()
        self.setFixedSize(QtCore.QSize(h, w) if self._rotation
                          else QtCore.QSize(w, h))
        self.update()

    def _transform(self):
        t = QtGui.QTransform()
        if self._rotation == 90:
            t.translate(self.width(), 0)
            t.rotate(90)
        elif self._rotation == -90:
            t.translate(0, self.height())
            t.rotate(-90)
        return t

    # Manual "Rotate 90°" cycles through the three supported orientations;
    # +90 and -90 read in opposite directions, so this is how a piece that
    # started in one turned orientation reaches the other.
    _ROTATION_CYCLE = (0, 90, -90)

    def set_rotation(self, rotation):
        """Set rotation, resizing but NOT moving or persisting -- callers that
        reposition (group rotate / presets) handle move + persist themselves."""
        self._rotation = rotation
        self._relayout()

    def _nextRotation(self):
        cycle = self._ROTATION_CYCLE
        idx = cycle.index(self._rotation) if self._rotation in cycle else 0
        return cycle[(idx + 1) % len(cycle)]

    def _toggleRotation(self):
        pivot = self.geometry().center()  # keep the piece visually centred
        self.set_rotation(self._nextRotation())
        self.move(pivot.x() - self.width() // 2, pivot.y() - self.height() // 2)
        self._persist(("rotation",), self._rotation or None)
        self._persist(("pos",), self._posToFrac(self.pos()))

    # -- paint -------------------------------------------------------------- #
    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setTransform(self._transform())
        self._paint_content(painter)
        if self._selected:
            painter.resetTransform()
            pen = QtGui.QPen(QtGui.QColor("deepskyblue"))
            pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

    def _paint_content(self, painter):
        raise NotImplementedError

    # -- drag + selection --------------------------------------------------- #
    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            self._coord.on_press(self, additive=False)
            self._showMenu(event.globalPosition().toPoint())
            return
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            mods = event.modifiers()
            additive = bool(mods & (QtCore.Qt.KeyboardModifier.ShiftModifier
                                    | QtCore.Qt.KeyboardModifier.MetaModifier
                                    | QtCore.Qt.KeyboardModifier.ControlModifier))
            self._coord.on_press(self, additive=additive)
            self._dragging = not additive
            if self._dragging:
                self._dragOffset = event.position().toPoint()
                self._coord.begin_drag(self)

    def mouseMoveEvent(self, event):
        if self._dragging:
            target = self.mapToParent(event.position().toPoint() - self._dragOffset)
            self._coord.do_drag(target - self.pos())

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            self._coord.end_drag()

    # -- menu --------------------------------------------------------------- #
    def _extendMenu(self, menu):
        pass  # subclasses add font/rename/size entries

    def _buildMenu(self):
        menu = QtWidgets.QMenu(self)
        grouped = self._coord.group_of(self) is not None
        menu.addAction("Rotate 90°", self._coord.rotate_selection_or(self))
        menu.addAction("Rotate all 90°", self._coord.rotate_all)
        if self._coord.can_group():
            menu.addAction("Group", self._coord.group_selected)
        if grouped:
            menu.addAction("Ungroup", lambda: self._coord.ungroup(self))
        self._extendMenu(menu)
        return menu

    def _showMenu(self, globalPos):
        # popup(), not exec() -- a nested modal loop would starve the shared
        # qasync event loop and drop the socket (same as the 2D plot menus).
        menu = self._buildMenu()
        menu.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.popup(globalPos)


class _BarItem(_ColorItem):
    """The gradient bar itself; scroll to grow/shrink."""

    def __init__(self, parent, coordinator):
        self._colormap = None
        self._vmin, self._vmax = 0.0, 1.0
        self._scale = 1.0
        super().__init__(parent, coordinator, "bar", _BAR_POS_FRAC)
        self._relayout()

    def apply_state(self, state, default_rotation=0):
        self._scale = float(state.get("scale") or 1.0)
        super().apply_state(state, default_rotation)

    def _baseSize(self):
        parent = self.parentWidget()
        if parent is None or parent.width() <= 0 or parent.height() <= 0:
            return _BAR_FALLBACK_SIZE
        return parent.width() * _BAR_WIDTH_FRAC, parent.height() * _BAR_HEIGHT_FRAC

    def _bar_dims(self):
        base_w, base_h = self._baseSize()
        # round(), not int(): truncation swallows a scale nudge entirely when
        # the fraction-derived base size is small (e.g. a narrow canvas).
        return round(base_w * self._scale), round(base_h * self._scale)

    def _content_size(self):
        bw, bh = self._bar_dims()
        return (bw + 2 * _PAD, bh + 2 * _PAD)

    def set_data(self, colormap, vmin, vmax):
        self._colormap = colormap
        self._vmin, self._vmax = vmin, vmax
        self.update()

    def _sampleColor(self, frac):
        if self._colormap is None:
            gray = int(max(0.0, min(1.0, frac)) * 255)
            return QtGui.QColor(gray, gray, gray)
        rgba = np.asarray(self._colormap[float(frac)].rgba).reshape(-1)
        r, g, b = rgba[0], rgba[1], rgba[2]
        a = rgba[3] if rgba.size > 3 else 1.0
        return QtGui.QColor.fromRgbF(float(r), float(g), float(b), float(a))

    def _paint_content(self, painter):
        bw, bh = self._bar_dims()
        bar = QtCore.QRect(_PAD, _PAD, bw, bh)
        gradient = QtGui.QLinearGradient(0, bar.top(), 0, bar.bottom())
        for frac in np.linspace(0, 1, 16):
            gradient.setColorAt(float(frac), self._sampleColor(1.0 - frac))
        painter.fillRect(bar, gradient)
        painter.setPen(QtGui.QColor("lightgray"))
        painter.drawRect(bar)

    # Scroll to resize; scale by ACTUAL scroll amount (normalised to a notch) so
    # a trackpad's many small events don't compound into a huge jump.
    def wheelEvent(self, event):
        notches = event.angleDelta().y() / 120.0
        factor = _BAR_SCALE_PER_NOTCH ** notches
        self._scale = min(_MAX_BAR_SCALE, max(_MIN_BAR_SCALE, self._scale * factor))
        self._relayout()
        self._persist(("scale",), self._scale if abs(self._scale - 1.0) > 1e-6 else None)

    def _extendMenu(self, menu):
        menu.addAction("Reset size", self._resetSize)

    def _resetSize(self):
        self._scale = 1.0
        self._relayout()
        self._persist(("scale",), None)


class _TextItem(_ColorItem):
    """Base for the text pieces: scroll- or menu-resizable font, drawn through
    the rotation transform so the glyphs turn with the piece."""

    def __init__(self, parent, coordinator, key, default_pos_frac):
        self._font_size = None
        super().__init__(parent, coordinator, key, default_pos_frac)

    def apply_state(self, state, default_rotation=0):
        self._font_size = state.get("font_size")
        super().apply_state(state, default_rotation)

    def _font(self):
        f = QtGui.QFont()
        f.setPointSize(self._font_size or _DEFAULT_FONT_PT)
        return f

    def _lines(self):
        raise NotImplementedError

    def _content_size(self):
        fm = QtGui.QFontMetrics(self._font())
        lines = self._lines() or [""]
        w = max(fm.horizontalAdvance(t) for t in lines) + 2 * _PAD
        h = fm.height() * len(lines) + 2 * _PAD
        return (max(w, 16), max(h, 16))

    def _paint_content(self, painter):
        painter.setFont(self._font())
        painter.setPen(QtGui.QColor("lightgray"))
        fm = painter.fontMetrics()
        y = _PAD + fm.ascent()
        for line in self._lines():
            painter.drawText(_PAD, y, line)
            y += fm.height()

    def _setFontSize(self, size):
        self._font_size = min(_MAX_FONT_PT, max(_MIN_FONT_PT, int(size)))
        self._relayout()
        self._persist(("font_size",), self._font_size)

    def wheelEvent(self, event):
        self._setFontSize((self._font_size or _DEFAULT_FONT_PT)
                          + (1 if event.angleDelta().y() > 0 else -1))

    def _extendMenu(self, menu):
        menu.addAction("Set font size…", self._promptFontSize)
        menu.addAction("Reset font size", self._resetFont)

    def _promptFontSize(self):
        # Non-blocking QInputDialog: a modal .exec()/getInt() would spin a nested
        # Qt loop and starve the shared qasync event loop (dropping the socket).
        dialog = QtWidgets.QInputDialog(self)
        dialog.setWindowTitle("Font size")
        dialog.setLabelText("Font size (pt):")
        dialog.setInputMode(QtWidgets.QInputDialog.InputMode.IntInput)
        dialog.setIntRange(_MIN_FONT_PT, _MAX_FONT_PT)
        dialog.setIntValue(self._font_size or _DEFAULT_FONT_PT)
        dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.intValueSelected.connect(self._setFontSize)
        dialog.open()

    def _resetFont(self):
        self._font_size = None
        self._relayout()
        self._persist(("font_size",), None)


class _ScalarItem(_TextItem):
    """One value number (vmin or vmax) -- its own movable/rotatable piece."""

    def __init__(self, parent, coordinator, key, default_pos_frac):
        self._value = 0.0
        super().__init__(parent, coordinator, key, default_pos_frac)
        self._relayout()

    def set_value(self, value):
        self._value = value
        self._relayout()

    def _lines(self):
        return [f"{self._value:.2f}"]


class _LabelItem(_TextItem):
    """The colorbar name; double-click to rename."""

    # The name only ever reads upright or turned -90° -- never +90 -- so its
    # "Rotate 90°" action toggles just those two, unlike the other pieces'
    # three-way (0/90/-90) cycle.
    _ROTATION_CYCLE = (0, -90)

    def __init__(self, parent, coordinator):
        self._default_text = ""
        self._text_override = None
        self._activeEditBox = None
        self._activeEditBoxCancel = None
        super().__init__(parent, coordinator, "label", _LABEL_POS_FRAC)
        self._relayout()

    def set_default_text(self, text):
        self._default_text = text
        self._relayout()

    def current_text(self):
        return self._text_override if self._text_override is not None else self._default_text

    def apply_state(self, state, default_rotation=0):
        self._text_override = state.get("text")
        super().apply_state(state, default_rotation)

    def _lines(self):
        return [self.current_text()]

    def mouseDoubleClickEvent(self, event):
        if self._activeEditBox is not None:
            self._activeEditBoxCancel()
        box = QtWidgets.QLineEdit(self)
        box.setText(self.current_text())
        box.setGeometry(0, 0, self.width(), self.height())
        state = {"committed": False}

        def commit():
            if state["committed"]:
                return
            state["committed"] = True
            text = box.text().strip()
            box.deleteLater()
            self._activeEditBox = None
            self._activeEditBoxCancel = None
            self._text_override = text or None
            self._relayout()
            self._persist(("text",), text or None)

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


class _GuideOverlay(QtWidgets.QWidget):
    """Dashed alignment-guide line(s) shown while dragging a piece near
    another piece's edge/center -- PowerPoint/Keynote/Figma-style snap
    feedback. Qt has no built-in widget for this; it's hand-rolled the same
    way those apps do it. Spans the full canvas and is mouse-transparent so
    it can sit above the pieces (to be visible over them) without blocking
    their drag handling."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self._vx = None
        self._hy = None
        self.hide()

    def setGuides(self, vx, hy):
        if (vx, hy) != (self._vx, self._hy):
            self._vx, self._hy = vx, hy
            self.update()

    def paintEvent(self, event):
        if self._vx is None and self._hy is None:
            return
        painter = QtGui.QPainter(self)
        pen = QtGui.QPen(QtGui.QColor("deepskyblue"))
        pen.setStyle(QtCore.Qt.PenStyle.DashLine)
        painter.setPen(pen)
        if self._vx is not None:
            painter.drawLine(self._vx, 0, self._vx, self.height())
        if self._hy is not None:
            painter.drawLine(0, self._hy, self.width(), self._hy)


class ColorbarOverlay:
    """Coordinates the four floating colorbar pieces (bar / vmax / vmin / name):
    selection, grouping, group/preset transforms, and per-metric persistence.

    ``get_metric_id`` returns the client-tracked Metric ID driving atom-coloring
    (or None) -- the identity a Colorbar Display Override is keyed on (ADR 0016).
    """

    def __init__(self, parent, get_metric_id):
        self._get_metric_id = get_metric_id
        self._current_metric_id = None
        self._orientation = "vertical"
        self._bar = _BarItem(parent, self)
        self._vmax = _ScalarItem(parent, self, "vmax", _VMAX_POS_FRAC)
        self._vmin = _ScalarItem(parent, self, "vmin", _VMIN_POS_FRAC)
        self._label = _LabelItem(parent, self)
        self._items = (self._bar, self._vmax, self._vmin, self._label)
        self._selected = set()
        self._groups = []          # list[set[_ColorItem]]
        self._drag = None          # list[_ColorItem] currently being dragged together
        self._drag_lead = None     # the item under the cursor -- drives snapping
        self._snap_enabled = True  # alignment-guide snapping while dragging
        self._guide = _GuideOverlay(parent)

    # -- persistence -------------------------------------------------------- #
    def persist(self, path, value):
        if self._current_metric_id is None:
            return
        display_overrides.set_colorbar_override(self._current_metric_id, path, value)

    # -- selection ---------------------------------------------------------- #
    def on_press(self, item, additive):
        if additive:
            (self._selected.discard if item in self._selected else self._selected.add)(item)
        elif item not in self._selected:
            grp = self.group_of(item)
            self._selected = set(grp) if grp is not None else {item}
        # else: item already selected -> keep the selection so a drag moves it all
        self._sync_highlight()

    def _sync_highlight(self):
        for it in self._items:
            it.set_selected(it in self._selected)

    def can_group(self):
        return len(self._selected) >= 2

    def group_of(self, item):
        for g in self._groups:
            if item in g:
                return g
        return None

    def group_selected(self):
        if len(self._selected) < 2:
            return
        members = set(self._selected)
        # a piece belongs to one group: drop these from any existing group first
        self._groups = [g - members for g in self._groups]
        self._groups = [g for g in self._groups if len(g) >= 2]
        self._groups.append(members)

    def ungroup(self, item):
        self._groups = [g for g in self._groups if item not in g]
        self._selected.clear()  # drop the stale multi-selection so the next
        self._sync_highlight()  # plain click selects just one piece again

    # -- drag (group / multi-selection aware) ------------------------------- #
    def _peers(self, item):
        grp = self.group_of(item)
        if grp is not None:
            return list(grp)
        if item in self._selected and len(self._selected) > 1:
            return list(self._selected)
        return [item]

    # Snap the dragged piece's edges/center to another piece's edges/center
    # within this many pixels -- PowerPoint/Figma-style alignment guides.
    _SNAP_THRESHOLD_PX = 6

    def begin_drag(self, item):
        # The dragged item computes its own target via mapToParent and reports a
        # delta (robust for synthetic test events, whose globalPosition is 0,0);
        # every peer moves by that same delta.
        self._drag = list(self._peers(item))
        self._drag_lead = item
        parent = self._guide.parentWidget()
        if parent is not None:
            self._guide.setGeometry(parent.rect())
        self._guide.show()
        self._guide.raise_()

    def _snapDelta(self, lead, delta):
        """Adjust delta so the lead item's edges/center align with another
        piece's edges/center, if within the snap threshold. Returns the
        adjusted (dx, dy) plus the guide line coordinates to draw (or None)."""
        targets = [it for it in self._items if it not in self._drag]
        x0, y0 = lead.pos().x() + delta.x(), lead.pos().y() + delta.y()
        w, h = lead.width(), lead.height()
        lead_x = (x0, x0 + w / 2, x0 + w)
        lead_y = (y0, y0 + h / 2, y0 + h)

        best_dx, vx = 0, None
        best_dy, hy = 0, None
        # Track the true global minimum diff (no threshold folded in here) --
        # filtering happens once at the end, against the real threshold.
        best_dx_abs = best_dy_abs = float("inf")

        for target in targets:
            tx, ty, tw, th = target.pos().x(), target.pos().y(), target.width(), target.height()
            for lval in lead_x:
                for tval in (tx, tx + tw / 2, tx + tw):
                    diff = tval - lval
                    if abs(diff) < best_dx_abs:
                        best_dx_abs, best_dx, vx = abs(diff), diff, tval
            for lval in lead_y:
                for tval in (ty, ty + th / 2, ty + th):
                    diff = tval - lval
                    if abs(diff) < best_dy_abs:
                        best_dy_abs, best_dy, hy = abs(diff), diff, tval

        if best_dx_abs > self._SNAP_THRESHOLD_PX:
            best_dx, vx = 0, None
        if best_dy_abs > self._SNAP_THRESHOLD_PX:
            best_dy, hy = 0, None
        return delta.x() + best_dx, delta.y() + best_dy, vx, hy

    def do_drag(self, delta):
        if self._drag is None:
            return
        vx, hy = None, None
        if self._snap_enabled:
            dx, dy, vx, hy = self._snapDelta(self._drag_lead, delta)
            delta = QtCore.QPoint(dx, dy)
        for it in self._drag:
            it.move(it.pos() + delta)
        self._guide.setGuides(vx, hy)

    def end_drag(self):
        if self._drag is None:
            return
        self._guide.hide()
        self._guide.setGuides(None, None)
        self._drag_lead = None
        for it in self._drag:
            it._persist(("pos",), it._posToFrac(it.pos()))
        self._drag = None

    # -- rotation ----------------------------------------------------------- #
    def rotate_selection_or(self, item):
        """Menu callback factory target: rotate the item's group as a block if
        it's grouped, else just the piece."""
        def _do():
            grp = self.group_of(item)
            if grp is not None and len(grp) > 1:
                self._rotate_block(list(grp))
            else:
                item._toggleRotation()
        return _do

    def _rotate_block(self, items):
        """Rigid 90° rotation of a set of pieces: each orbits their common
        centre AND turns, so they spin as one block."""
        geoms = [it.geometry() for it in items]
        cx = (min(g.left() for g in geoms) + max(g.right() for g in geoms)) / 2.0
        cy = (min(g.top() for g in geoms) + max(g.bottom() for g in geoms)) / 2.0
        for it in items:
            c = it.geometry().center()
            dx, dy = c.x() - cx, c.y() - cy
            ncx, ncy = cx - dy, cy + dx  # 90° (top -> right), matches content turn
            it.set_rotation(it._nextRotation())
            it.move(int(ncx - it.width() / 2), int(ncy - it.height() / 2))
            it._persist(("rotation",), it._rotation or None)
            it._persist(("pos",), it._posToFrac(it.pos()))

    def rotate_all(self):
        """Snap the whole colorbar between two curated layouts."""
        self._apply_preset("horizontal" if self._orientation == "vertical" else "vertical")

    def _apply_preset(self, orientation):
        self._orientation = orientation
        bar, vmax, vmin, name = self._bar, self._vmax, self._vmin, self._label
        ox, oy = bar.pos().x(), bar.pos().y()  # anchor at the bar's current corner
        if orientation == "vertical":
            # tall bar; vmax top / vmin bottom (horizontal text); name to the
            # right of the bar, rotated -90°.
            bar.set_rotation(0)
            vmax.set_rotation(0)
            vmin.set_rotation(0)
            name.set_rotation(-90)
            bar.move(ox, oy)
            bw, bh = bar.width(), bar.height()
            vmax.move(ox + bw + _GAP, oy + _PAD)
            vmin.move(ox + bw + _GAP, oy + bh - vmin.height() - _PAD)
            values_w = max(vmax.width(), vmin.width())
            name.move(ox + bw + _GAP + values_w + _GAP, oy)
        else:
            # wide bar; vmin left / vmax right; name centred below (all upright).
            bar.set_rotation(90)
            vmax.set_rotation(0)
            vmin.set_rotation(0)
            name.set_rotation(0)
            bar.move(ox, oy)
            bw, bh = bar.width(), bar.height()
            vy = oy + bh + _GAP
            vmin.move(ox, vy)
            vmax.move(ox + bw - vmax.width(), vy)
            values_h = max(vmin.height(), vmax.height())
            name.move(ox + (bw - name.width()) // 2, vy + values_h + _GAP)
        for it in self._items:
            it._persist(("rotation",), it._rotation or None)
            it._persist(("pos",), it._posToFrac(it.pos()))
        self.persist(("_layout",), orientation if orientation != "vertical" else None)

    # -- descriptor / lifecycle -------------------------------------------- #
    def update_descriptor(self, colormap, vmin, vmax, label):
        """Refresh from a new AtomColorBy. Reloads each piece's saved override
        only when the active metric actually changes."""
        self._bar.set_data(colormap, vmin, vmax)
        self._vmax.set_value(vmax)
        self._vmin.set_value(vmin)
        self._label.set_default_text(label)

        metric_id = self._get_metric_id()
        if metric_id != self._current_metric_id:
            self._current_metric_id = metric_id
            self._selected.clear()
            self._groups = []
            override = (display_overrides.get_colorbar_override(metric_id)
                        if metric_id is not None else {})
            self._orientation = override.get("_layout") or "vertical"
            # Un-overridden pieces start pre-rotated to match the active
            # preset (name reads bottom-to-top in Vertical; upright text and
            # a sideways bar in Horizontal) instead of always flat.
            defaults = (_VERTICAL_DEFAULT_ROTATIONS if self._orientation == "vertical"
                       else _HORIZONTAL_DEFAULT_ROTATIONS)
            self._bar.apply_state(override.get("bar") or {}, defaults["bar"])
            self._vmax.apply_state(override.get("vmax") or {}, defaults["vmax"])
            self._vmin.apply_state(override.get("vmin") or {}, defaults["vmin"])
            self._label.apply_state(override.get("label") or {}, defaults["label"])
            self._sync_highlight()

        for item in self._items:
            item.show()
            item.raise_()
            item.update()

    def hide_colorbar(self):
        for item in self._items:
            item.hide()
        self._current_metric_id = None

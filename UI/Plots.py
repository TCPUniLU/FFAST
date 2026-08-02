from UI.Templates import Widget, ToolButton, ToolCheckButton, PushButton, TableView, InfoToolButton
from PySide6 import QtCore, QtGui, QtWidgets
from config.uiConfig import config, configStyleSheet
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QWidget, QTabWidget
from config.uiConfig import config, getIcon
from PySide6.QtWidgets import QSizePolicy
from ffast.core.events import EventChildClass
import pyqtgraph
import logging
from client.dataWatcher import DataWatcher
from client import display_overrides
import numpy as np
from ffast.loaders.dataset import SubDataset
from ffast.config.user import getConfig
from UI import plot_profiler

# No-op unless FFAST_PLOT_PROFILE is set; times the scatter/curve paint paths
# to locate the real pan/zoom repaint cost (see UI/plot_profiler.py).
plot_profiler.maybe_enable()

_DEFAULT_LABEL_FONT_SIZE = 20
_MIN_LABEL_FONT_SIZE = 8
_MAX_LABEL_FONT_SIZE = 48
_DEFAULT_LEGEND_FONT_PT = 12
_MIN_LEGEND_FONT_PT = 6
_MAX_LEGEND_FONT_PT = 32

# PyQtGraph's built-in "X axis"/"Y axis" right-click submenu (ViewBoxMenu)
# embeds a compact form -- radio buttons, checkboxes, a combo, two line
# edits -- in a zero-margin, zero-spacing QGridLayout sized for native Qt
# control heights. Our app-wide QSS (style.qss) inflates those same widget
# types for our own toolbars/forms (QCheckBox/QRadioButton to 36px with 28px
# indicators, QComboBox to 32px, QLineEdit/QSpinBox to 24px) -- under this
# zero-spacing template that crowded the rows into each other (measured
# 356x250, overlapping). This resets sizing to compact but readable: a
# slightly larger font than the inherited default, a couple pixels of row
# margin so it doesn't look cramped (the grid has zero spacing of its own),
# and a small inset around the embedded form. Measured 378x189 -- still well
# under the original's overlapping 250px tall, with breathing room restored.
# Nothing else in the app uses this stylesheet.
_AXIS_CONTROL_MENU_QSS = """
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
  height: 22px;
  padding: 0px 6px;
  margin: 2px 0px;
  font-size: 11pt;
}
QCheckBox, QRadioButton {
  height: 22px;
  spacing: 8px;
  margin: 2px 0px;
  font-size: 11pt;
}
QCheckBox::indicator, QRadioButton::indicator {
  width: 15px;
  height: 15px;
}
QLabel {
  font-size: 11pt;
}
QMenu {
  padding: 6px;
}
"""


class _EditableLegendItem(pyqtgraph.LegendItem):
    """LegendItem with drag-to-reposition, scroll-to-resize, and double-click-to-
    rename entries (Panel Display Override, ADR 0029). Dragging replaces the base
    corner-anchored ``autoAnchor`` with a plain absolute ``setPos`` -- simpler to
    persist/restore, at the cost of no longer re-anchoring on window resize once a
    user has moved it, which is an acceptable trade for a user-placed position."""

    def __init__(self, owner, **kwargs):
        super().__init__(**kwargs)
        self._owner = owner

    def updateSize(self):
        # pyqtgraph's base updateSize() (called on every clear()/addItem(),
        # i.e. every legend rebuild -- a rename, a data refresh, anything)
        # ends in self.setGeometry(0, 0, w, h), which for a QGraphicsWidget
        # resets position to (0, 0) as a side effect of resizing. That silently
        # undid every drag the moment the legend's contents next changed --
        # preserve position across the resize (ADR 0029 bug fix).
        pos = self.pos()
        super().updateSize()
        self.setPos(pos)

    def mouseDragEvent(self, ev):
        if ev.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        ev.accept()
        dpos = ev.pos() - ev.lastPos()
        self.setPos(self.pos() + dpos)
        if ev.isFinish():
            pos = self.pos()
            self._owner._onLegendMoved(pos.x(), pos.y())

    def wheelEvent(self, ev):
        ev.accept()
        self._owner._onLegendWheel(ev.delta())

    def mouseClickEvent(self, ev):
        # pyqtgraph's GraphicsScene convention (distinct from Qt's own
        # mousePressEvent): sendClickEvent() walks items front-to-back and
        # stops at the first one whose mouseClickEvent() accepts, so accepting
        # here keeps the ViewBox's own right-click menu (ViewBox.mouseClickEvent,
        # unconditional -- it doesn't check isAccepted() itself) from also firing.
        if ev.button() == QtCore.Qt.MouseButton.RightButton:
            ev.accept()
            self._owner._showLegendMenu(ev.screenPos().toPoint())

    def mouseDoubleClickEvent(self, ev):
        for sample, label in self.items:
            if label.geometry().contains(ev.pos()):
                ev.accept()
                self._owner._startLegendEntryEdit(sample, label)
                return
        super().mouseDoubleClickEvent(ev)


class DataDependentObject:

    dataWatcher = None

    def __init__(self):
        self.initialiseWatcher()

    def setDataDependencies(self, *args):
        self.dataWatcher.setDataDependencies(*args)

    def setMetricDependencies(self, metric_deps: dict):
        self.dataWatcher.setMetricDependencies(metric_deps)

    def setDatasetDependencies(self, *args):
        self.dataWatcher.setDatasetDependencies(*args)

    def setModelDependencies(self, *args):
        self.dataWatcher.setModelDependencies(*args)

    def setModelDatasetDependencies(self, *args):
        self.dataWatcher.setModelDatasetDependencies(*args)

    def getDatasetDependencies(self):
        return self.dataWatcher.getDatasetDependencies()

    def getModelDependencies(self):
        return self.dataWatcher.getModelDependencies()

    def initialiseWatcher(self):
        dw = DataWatcher(self.handler.env)
        dw.addRefreshWidget(self)

        self.dataWatcher = dw
        self.dataWatcher.parentName = self.name


class DataloaderButton(PushButton, EventChildClass):

    styleSheet = """
    @OBJECT{
        padding-left: 10px;
        padding-right: 10px;
    }
    @OBJECT:enabled{
        border: 1px solid @HLColor2;
        color: @HLColor2;
    }
    @OBJECT:disabled{
        border: 1px solid @BGColor4;
        color: @BGColor4;
    }
    """
    lastUpdatedStamp = -1

    def __init__(self, handler, watcher, **kwargs):
        super().__init__("Load", styleSheet=self.styleSheet, **kwargs)
        self.handler = handler
        EventChildClass.__init__(self)
        self.dataWatcher = watcher

        # self.setIcon(QtGui.QIcon(getIcon("load")))
        self.dataWatcher.addRefreshWidget(self)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        self.eventSubscribe("WIDGET_REFRESH", self.onWidgetRefresh)
        self.eventSubscribe("AUTO_COMPUTE_TRIGGERED", self.onAutoCompute)
        self.clicked.connect(self.dataWatcher.loadContent)

        self.onWidgetRefresh(self)

    def onWidgetRefresh(self, widget):
        if self is not widget:
            return

        self.refresh()

    def onAutoCompute(self, all_plots):
        """Fire loadContent automatically when selection changes.

        all_plots=True  → small dataset, compute everything.
        all_plots=False → large dataset, only priority watchers.
        """
        if not self.isEnabled():
            return
        if all_plots or self.dataWatcher.autocomputePriority:
            self.dataWatcher.loadContent()

    def refresh(self):
        missing = self.dataWatcher.currentlyMissingKeys
        if len(missing) == 0:
            self.setEnabled(False)
        else:
            self.setEnabled(True)


class BasicPlotWidget(Widget, EventChildClass, DataDependentObject):

    lastUpdatedStamp = -1
    styleSheet = """
    @OBJECT{
        border-radius:10px;
    }
    """

    def __init__(
        self,
        handler,
        name="N/A",
        title="N/A",
        hasLegend=True,
        isSubbable=True,
        color="@BGColor3",
        **kwargs,
    ):
        self.handler = handler
        self.env = handler.env
        self.name = name
        self.isSubbable = isSubbable

        super().__init__(
            layout="vertical",
            color="@BGColor3",
            styleSheet=self.styleSheet,
            **kwargs,
        )
        EventChildClass.__init__(self)
        DataDependentObject.__init__(self)

        self.plotItems = []
        self.labelsList = []
        self.hasLegend = hasLegend
        self.colorCount = 0
        self.symbolCount = 0

        # Panel Display Override state (ADR 0029). ``_displayOverrideKey`` is
        # None until a caller (e.g. MetricPlotPanel) sets it to (tab, kind,
        # metric_ids); until then edits still work live but don't persist.
        # ``_labelState[axis]["text"]`` is the user override (None = show the
        # Panel Kind/TOML default in "default_text"); "font_size" likewise.
        self._displayOverrideKey = None
        self._labelState = {
            "bottom": {"text": None, "font_size": None, "default_text": ""},
            "left": {"text": None, "font_size": None, "default_text": ""},
        }
        self._legendEntryOverrides = {}       # "<dataset_fp>|<model_fp>" -> text
        self._legendItemAutoLabels = {}       # id(plotDataItem) -> autoLabel dict
        self._legendFontSize = None           # None = _DEFAULT_LEGEND_FONT_PT
        self._activeEditBox = None
        self._activeEditBoxCancel = None

        # Incremental refresh (ADR 0022): persistent Series keyed by
        # (dataset_fp, model_fp, kind, sub-index). A refresh reconciles against
        # these — skip unchanged, setData changed, create new, drop unvisited —
        # instead of clear()+rebuild, so a streamed metric result no longer
        # tears down and rebuilds every Series on the GUI thread. plotItems /
        # labelsList are rebuilt in draw order each refresh for the legend.
        self._series = {}
        self._visitOrder = []
        self._visitedKeys = set()
        self._keyCounter = {}
        self._changedThisRefresh = False

        # Trailing-debounce timer that coalesces a burst of refresh requests
        # into a single rebuild (see visualRefresh). 200ms comfortably spans the
        # ~100ms inter-arrival gap of streamed metric results, so a whole batch
        # collapses to one rebuild per plot.
        self._pendingForce = False
        self._pendingNoAutoRange = True
        self._refreshTimer = QtCore.QTimer(self)
        self._refreshTimer.setSingleShot(True)
        self._refreshTimer.setInterval(200)
        self._refreshTimer.timeout.connect(self._performVisualRefresh)

        self.colorString = color
        self.layout.setContentsMargins(13, 13, 13, 13)
        self.layout.setSpacing(8)

        # TOOLBAR
        self.toolbar = Widget(parent=self, layout="horizontal")
        self.toolbar.setFixedHeight(30)
        self.toolbar.setObjectName("plotToolbar")
        self.layout.addWidget(self.toolbar)

        # DIVIDER
        divider = Widget(parent=self, color="@TextColor3")
        divider.setFixedHeight(1)
        self.layout.addWidget(divider)

        # OPTIONS
        self.optionsToolbar = Widget(parent=self, layout="horizontal")
        self.optionsToolbar.setObjectName("plotoptionsToolbar")
        self.optionsToolbar.layout.addStretch()
        self.layout.addWidget(self.optionsToolbar)

        # PLOTWIDGET
        self.plotWidget = pyqtgraph.PlotWidget(name=f"{name}PlotWidget")
        self.plotItem = self.plotWidget.getPlotItem()
        self.layout.addWidget(self.plotWidget)
        self.applyPlotWidget()
        self.applyToolbar(title=title)  # needs the plotwidget to exist
        # Double-click-to-edit / scroll-to-resize axis labels (ADR 0029, Q7/Q8):
        # only consumed when the event actually lands on a label's bounding rect,
        # so normal double-click-to-autorange and wheel-to-zoom pass through
        # everywhere else on the plot.
        self.plotWidget.installEventFilter(self)

        # REFRESH
        self.eventSubscribe("WIDGET_REFRESH", self.onWidgetRefresh)
        self.eventSubscribe("WIDGET_VISUAL_REFRESH", self.visualRefresh)
        self.eventSubscribe("QUIT_READY", self.onQuit)

        # EVENTS
        self.eventSubscribe(
            "OBJECT_NAME_CHANGED", self.onModelDatasetNameChanged
        )
        self.eventSubscribe(
            "OBJECT_COLOR_CHANGED", self.onModelDatasetColorChanged
        )
        # LEGEND
        self.applyLegend()
        self.applyStyle()

    def applyToolbar(self, title="N/A"):
        layout = self.toolbar.layout
        layout.setContentsMargins(20, 0, 0, 0)
        layout.setSpacing(8)

        self.titleLabel = QtWidgets.QLabel(title)
        self.titleLabel.setObjectName("plotTitleLabel")
        layout.addWidget(self.titleLabel)

        layout.addStretch()

        self.infoButton = InfoToolButton()
        layout.addWidget(self.infoButton)

        if self.hasLegend:
            self.legendCheckBox = ToolCheckButton(
                self.handler, self.updateLegend, icon="legend"
            )
            layout.addWidget(self.legendCheckBox)
            self.legendCheckBox.setToolTip("Toggle legend")

        if self.isSubbable:
            self.subCheckBox = ToolCheckButton(
                self.handler, self.onSubStateChanged, icon="subbing"
            )
            layout.addWidget(self.subCheckBox)
            self.plotWidget.sigRangeChanged.connect(self.updateSub)
            self.subCheckBox.setToolTip(
                "Toggle subbing: create dataset of selected subsection"
            )

        self.loadButton = DataloaderButton(self.handler, self.dataWatcher)
        layout.addWidget(self.loadButton)

    def applyPlotWidget(self):
        pi = self.plotItem
        pw = self.plotWidget

        pi.setContentsMargins(7, 7, 7, 7)  # fixes axis cutting
        self._compactifyAxisControlMenu(pi)

    def _compactifyAxisControlMenu(self, plotItem):
        """See _AXIS_CONTROL_MENU_QSS: rescope the built-in X/Y-axis submenu's
        controls back to compact sizing so its zero-margin grid doesn't
        overflow under our app-wide QSS."""
        viewBoxMenu = plotItem.getViewBox().menu
        if viewBoxMenu is not None:
            viewBoxMenu.setStyleSheet(_AXIS_CONTROL_MENU_QSS)

    def applyStyle(self):
        self.setMinimumSize(400, 400)
        self.setSizePolicy(
            QSizePolicy.MinimumExpanding, QSizePolicy.MinimumExpanding
        )

        color = config["envs"].get(self.colorString.replace("@", ""))
        self.plotWidget.setBackground(color)

    def applyLegend(self):
        if not self.hasLegend:
            return

        self.legend = _EditableLegendItem(
            self,
            offset=None,
            labelTextSize=f"{_DEFAULT_LEGEND_FONT_PT}pt",
            labelTextColor=self.handler.config["envs"].get("TextColor1"),
        )
        self.legend.setParentItem(self.plotWidget.graphicsItem())
        # offset=None deliberately skips pyqtgraph's corner-anchor: passing an
        # offset here would connect the legend to the parent's geometryChanged
        # signal and re-snap it to that anchor on every resize/redraw, fighting
        # every drag update's plain setPos() (ADR 0029 -- the bug this fixes).
        self.legend.setPos(30, 10)
        self.updateLegend()

    def updateLegend(self):
        if not self.hasLegend:
            return

        if self.legendCheckBox.checked:
            self.legend.show()
            self.refreshLegend()
        else:
            self.legend.hide()

    def isSubbing(self):
        return self.isSubbable and self.subCheckBox.isChecked()

    def addWidgetToToolbar(self, widget):
        n = self.toolbarLayout.count()
        self.toolbarLayout.insertWidget(n - 1, widget)

    def setXLabel(self, label, unit=None):
        self._setAxisDefaultLabel("bottom", label, unit)

    def setYLabel(self, label, unit=None):
        self._setAxisDefaultLabel("left", label, unit)

    # ----------------------------------------------------------------- #
    # Panel Display Override: editable axis labels (ADR 0029)
    # ----------------------------------------------------------------- #
    def _setAxisDefaultLabel(self, axisName, label, unit=None):
        """Set the Panel Kind/TOML default for one axis. A live text override
        (from a prior edit or a loaded Panel Display Override) still wins;
        this only changes what's shown once that override is cleared."""
        if unit is not None:
            label = f"{label} [{unit}]"
        self._labelState[axisName]["default_text"] = label
        self._renderAxisLabel(axisName)

    def _currentAxisText(self, axisName):
        state = self._labelState[axisName]
        return state["text"] if state["text"] is not None else state["default_text"]

    def _renderAxisLabel(self, axisName):
        state = self._labelState[axisName]
        size = state["font_size"] or _DEFAULT_LABEL_FONT_SIZE
        fontOptions = {"font-size": f"{size}px", "color": "lightgray"}
        self.plotWidget.setLabel(axisName, self._currentAxisText(axisName), **fontOptions)

    def _axisLabelSceneRect(self, axisName):
        axis = self.plotWidget.getAxis(axisName)
        item = getattr(axis, "label", None)
        if item is None or not item.isVisible():
            return None
        return item.mapRectToScene(item.boundingRect())

    _LABEL_HIT_PAD = 12  # generous tolerance so a near-miss doesn't fall through
    # to the ViewBox's own wheel-zoom / double-click-to-autorange -- the label's
    # tight boundingRect is easy to miss by a few pixels, which read as "scroll
    # sometimes zooms the plot instead of resizing the label" (ADR 0029 fix).

    def _labelAxisAt(self, viewPos):
        scenePos = self.plotWidget.mapToScene(viewPos)
        for axisName in ("bottom", "left"):
            rect = self._axisLabelSceneRect(axisName)
            if rect is None:
                continue
            pad = self._LABEL_HIT_PAD
            if rect.adjusted(-pad, -pad, pad, pad).contains(scenePos):
                return axisName
        return None

    def _startAxisLabelEdit(self, axisName):
        axis = self.plotWidget.getAxis(axisName)
        self._openInlineEditor(
            axis.label, self._currentAxisText(axisName),
            lambda text: self._commitAxisLabelEdit(axisName, text),
        )

    def _commitAxisLabelEdit(self, axisName, text):
        self._labelState[axisName]["text"] = text or None
        self._renderAxisLabel(axisName)
        role = "x_label" if axisName == "bottom" else "y_label"
        self._persistOverride((role, "text"), text or None)

    def _setAxisLabelFontSize(self, axisName, size):
        size = min(_MAX_LABEL_FONT_SIZE, max(_MIN_LABEL_FONT_SIZE, size))
        self._labelState[axisName]["font_size"] = size
        self._renderAxisLabel(axisName)
        role = "x_label" if axisName == "bottom" else "y_label"
        self._persistOverride((role, "font_size"), size)

    def _resizeAxisLabel(self, axisName, delta):
        state = self._labelState[axisName]
        size = state["font_size"] or _DEFAULT_LABEL_FONT_SIZE
        self._setAxisLabelFontSize(axisName, size + (1 if delta > 0 else -1))

    def _resetAxisLabel(self, axisName):
        self._labelState[axisName]["text"] = None
        self._labelState[axisName]["font_size"] = None
        self._renderAxisLabel(axisName)
        role = "x_label" if axisName == "bottom" else "y_label"
        self._persistOverride((role, "text"), None)
        self._persistOverride((role, "font_size"), None)

    def _promptAxisLabelFontSize(self, axisName):
        current = self._labelState[axisName]["font_size"] or _DEFAULT_LABEL_FONT_SIZE

        def _apply(size, ok):
            if ok:
                self._setAxisLabelFontSize(axisName, size)

        self._askFontSizeDialog(current, _MIN_LABEL_FONT_SIZE, _MAX_LABEL_FONT_SIZE, _apply)

    def _buildAxisLabelMenu(self, axisName):
        """Right-click menu on an axis label -- a discoverable alternative to
        scroll-to-resize/double-click-to-edit, not a replacement for them.
        Split from showing it so tests can inspect the built menu without
        invoking the (now non-blocking, see _showAxisLabelMenu) popup."""
        menu = QtWidgets.QMenu(self.plotWidget)
        # Flat action list, no checkboxes/submenus -- the "compactContextMenu"
        # QSS rule (style.qss) sizes the popup to the text instead of leaving
        # dead space for an indicator column it never uses.
        menu.setObjectName("compactContextMenu")
        menu.addAction("Set Font Size…", lambda: self._promptAxisLabelFontSize(axisName))
        menu.addAction("Increase Font Size", lambda: self._resizeAxisLabel(axisName, 1))
        menu.addAction("Decrease Font Size", lambda: self._resizeAxisLabel(axisName, -1))
        menu.addSeparator()
        menu.addAction("Edit Label…", lambda: self._startAxisLabelEdit(axisName))
        menu.addAction("Reset Label", lambda: self._resetAxisLabel(axisName))
        return menu

    def _showAxisLabelMenu(self, axisName, globalPos):
        # popup(), not exec(): exec() spins a nested Qt event loop for as long
        # as the menu stays open. Qt and asyncio share one event loop here
        # (qasync, main.py), so a menu left open too long can starve the
        # WebSocket keepalive ping/pong and get the connection dropped by the
        # server (1011 ping timeout). popup() is also what ViewBox itself uses
        # for its own right-click menu (ViewBox.raiseContextMenu).
        menu = self._buildAxisLabelMenu(axisName)
        menu.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.popup(globalPos)

    # ----------------------------------------------------------------- #
    # Panel Display Override: shared inline-edit widget (ADR 0029, Q7)
    # ----------------------------------------------------------------- #
    def _openInlineEditor(self, graphicsItem, currentText, onCommit):
        """Float a QLineEdit over ``graphicsItem`` pre-filled with ``currentText``;
        Enter/focus-out commits via ``onCommit(text)``, Escape cancels. No dialogs
        or context menus -- direct manipulation, consistent with legend dragging."""
        if self._activeEditBox is not None:
            self._activeEditBoxCancel()

        rect = graphicsItem.mapRectToScene(graphicsItem.boundingRect())
        topLeft = self.plotWidget.mapFromScene(rect.topLeft())
        bottomRight = self.plotWidget.mapFromScene(rect.bottomRight())
        pad = 4
        box = QtWidgets.QLineEdit(self.plotWidget)
        box.setText(currentText or "")
        box.setGeometry(
            topLeft.x() - pad, topLeft.y() - pad,
            max(80, bottomRight.x() - topLeft.x() + 2 * pad),
            max(20, bottomRight.y() - topLeft.y() + 2 * pad),
        )

        state = {"committed": False}

        def commit():
            if state["committed"]:
                return
            state["committed"] = True
            text = box.text().strip()
            box.deleteLater()
            if self._activeEditBox is box:
                self._activeEditBox = None
                self._activeEditBoxCancel = None
            onCommit(text)

        def cancel():
            if state["committed"]:
                return
            state["committed"] = True
            box.deleteLater()
            if self._activeEditBox is box:
                self._activeEditBox = None
                self._activeEditBoxCancel = None

        box.editingFinished.connect(commit)
        box.installEventFilter(self)
        self._activeEditBox = box
        self._activeEditBoxCancel = cancel
        box.show()
        box.setFocus()
        box.selectAll()

    def _persistOverride(self, path, value):
        if self._displayOverrideKey is None:
            return
        tab, kind, mids = self._displayOverrideKey
        display_overrides.set_panel_override(tab, kind, mids, path, value)

    def _askFontSizeDialog(self, current, minSize, maxSize, onResult):
        """Numeric font-size prompt shared by the axis-label and legend
        context menus. Isolated as its own method so tests can stub it
        without ever constructing a real dialog.

        Uses QInputDialog.open() (non-blocking), not the static getInt()
        convenience (blocking): Qt and asyncio share one event loop in this
        app (qasync, main.py), so a dialog left open blocks that loop in a
        nested Qt event loop for as long as it stays open, which can starve
        the WebSocket keepalive ping/pong long enough for the server to drop
        the connection (1011 ping timeout). open() shows the dialog without a
        nested loop; onResult(size, ok) fires later via signal instead.
        """
        dialog = QtWidgets.QInputDialog(self.plotWidget)
        dialog.setWindowTitle("Font Size")
        dialog.setLabelText("Font size (pt):")
        dialog.setInputMode(QtWidgets.QInputDialog.InputMode.IntInput)
        dialog.setIntRange(minSize, maxSize)
        dialog.setIntValue(current)
        dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.intValueSelected.connect(lambda size: onResult(size, True))
        dialog.rejected.connect(lambda: onResult(current, False))
        dialog.open()

    def setXTicks(self, x, labels):
        ax = self.plotWidget.getAxis("bottom")
        n = len(x)
        ax.setTicks([[(x[i], str(labels[i])) for i in range(n)]])

    def setYTicks(self, y, labels):
        ay = self.plotWidget.getAxis("left")
        n = len(y)
        ay.setTicks([[(y[i], str(labels[i])) for i in range(n)]])

    def getRanges(self):
        (x1, y1, x2, y2) = self.plotWidget.visibleRange().getCoords()
        return (x1, x2), (y1, y2)

    def onSubStateChanged(self):
        existingSubs = self.getExistingSubDatasets()

        subbing = self.isSubbing()
        for sub in existingSubs:
            sub.setActive(subbing)

        if subbing:
            self.updateSub()

        self.refresh()

    def getValidSubDatasets(self, asFingerprint=False):
        subs = []
        env = self.env

        # datasetKeys = self.dataWatcher.getDatasetDependencies()
        # modelKeys = self.dataWatcher.getModelDependencies()

        # for datasetKey in datasetKeys:
        #     dataset = env.datasets.get(datasetKey)

        #     # dont do subdatasets of subdatasets
        #     if dataset.isSubDataset and not dataset.isAtomFiltered:
        #         continue

        #     for modelKey in modelKeys:
        #         model = env.models.get(modelKey)
        for de in self.getWatchedData():
            dataset, model = de["dataset"], de["model"]
            idx = self.getDatasetSubIndices(dataset, model)
            if (idx is None) or (len(idx) == 0):
                idx = None

            if asFingerprint:
                fp = SubDataset.getFingerprint(
                    SubDataset, dataset, model, self.name
                )
                subs.append(fp)
            else:
                subs.append((dataset, model, idx))

        return subs

    def getExistingSubDatasets(self):
        fps = self.getValidSubDatasets(asFingerprint=True)
        ds = []
        for fp in fps:
            sub = self.env.datasets.get(fp)
            if sub is not None:
                ds.append(sub)

        return ds

    def updateSub(self):
        if not self.isSubbing():
            return

        subDatasets = self.getValidSubDatasets()
        for dataset, model, idx in subDatasets:
            self.declareSubDataset(dataset, model, idx)

    def declareSubDataset(self, dataset, model, idx):
        self.env.declareSubDataset(dataset, model, idx, self.name)

    def getDatasetSubIndices(self, dataset, model):
        raise NotImplementedError

    def onWidgetRefresh(self, widget):
        if self is not widget:
            return
        self.refresh()

    def onQuit(self):
        self._refreshTimer.stop()
        self.plotWidget.close()

    def eventFilter(self, obj, event):
        if obj is self._activeEditBox:
            if (event.type() == QEvent.Type.KeyPress
                    and event.key() == Qt.Key.Key_Escape):
                self._activeEditBoxCancel()
                return True
            return False
        if obj is self.plotWidget:
            if event.type() == QEvent.Type.MouseButtonDblClick:
                axisName = self._labelAxisAt(event.position().toPoint())
                if axisName is not None:
                    self._startAxisLabelEdit(axisName)
                    return True
            elif (event.type() == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.MouseButton.RightButton):
                # Consumed here (before pyqtgraph's own right-click handling
                # sees the press) so this doesn't also raise the ViewBox's
                # menu -- AxisItem has no context menu of its own to conflict
                # with; this is a clean, separate menu over the label only.
                axisName = self._labelAxisAt(event.position().toPoint())
                if axisName is not None:
                    self._showAxisLabelMenu(axisName, event.globalPosition().toPoint())
                    return True
            elif event.type() == QEvent.Type.Wheel:
                axisName = self._labelAxisAt(event.position().toPoint())
                if axisName is not None:
                    self._resizeAxisLabel(axisName, event.angleDelta().y())
                    return True
        return super().eventFilter(obj, event)

    def getWatchedData(self):
        return self.dataWatcher.getWatchedData()

    def refresh(self):
        self.visualRefresh()

    def _addPlots(self, **kwargs):
        # Reset per-refresh reconcile state. plot() repopulates _visitOrder /
        # _visitedKeys / _changedThisRefresh; plotItems/labelsList are rebuilt
        # from _visitOrder afterwards (in _performVisualRefresh).
        self.colorCount = 0
        self.symbolCount = 0
        self._visitOrder = []
        self._visitedKeys = set()
        self._keyCounter = {}
        self._changedThisRefresh = False
        self.addPlots()

    def addPlots(self, **kwargs):
        # placeholder, should be implemented by user
        return NotImplementedError

    def visualRefresh(self, force=False, noAutoRange=False):
        # Coalesce refresh requests. Server-side metric results arrive one at a
        # time (~10/sec), each in its own event-loop tick, so the per-tick
        # eventStamp gate cannot dedupe them: with N models every plot would
        # rebuild N times per batch, and toggling the energy shift fires a fresh
        # batch of recomputes — a rebuild storm that saturates the GUI thread
        # (felt as scroll lag). Instead, restart a short single-shot timer on
        # each request and rebuild ONCE after results stop arriving. Flags are
        # merged so the coalesced rebuild is at least as eager as any request:
        # force if any asked to force; autoRange unless every request opted out.
        plot_profiler.note_refresh("request")
        self._pendingForce = self._pendingForce or force
        self._pendingNoAutoRange = self._pendingNoAutoRange and noAutoRange
        self._refreshTimer.start()

    def _performVisualRefresh(self):
        force = self._pendingForce
        noAutoRange = self._pendingNoAutoRange
        self._pendingForce = False
        self._pendingNoAutoRange = True
        # when many refresh events happen in a single loop, no need to
        # refresh every time since information won't change
        if (not force) and self.eventStamp <= self.lastUpdatedStamp:
            return

        plot_profiler.note_refresh("rebuild")
        self.lastUpdatedStamp = self.eventStamp

        # Reconcile rather than clear()+rebuild. _addPlots() calls plot() per
        # Series, which skips/updates/creates against self._series in place.
        self._addPlots()
        # Drop Series not redrawn this refresh (a model/dataset was removed).
        for key in [k for k in self._series if k not in self._visitedKeys]:
            self.plotWidget.removeItem(self._series[key]["item"])
            del self._series[key]
            self._changedThisRefresh = True
        # Rebuild ordered item/label lists in draw order (the legend reads
        # plotItems[i] and labelsList[i] in parallel).
        self.plotItems = [self._series[k]["item"] for k in self._visitOrder]
        self.labelsList = [self._series[k]["label"] for k in self._visitOrder]
        # autoRange only when something actually changed: an unchanged plot must
        # not re-fit (and yank a manually-zoomed view) on a streamed update.
        if self._changedThisRefresh and (not noAutoRange) and (not self.isSubbing()):
            self.plotItem.autoRange()
        if self.hasLegend:
            self.updateLegend()

    def getLabelFromData(self, data):
        s = ""
        if data["dataset"] is not None:
            s = data["dataset"].getDisplayName()

        if data["model"] is not None:
            s += " & "
            s += data["model"].getDisplayName()

        return s

    def _legendEntryKey(self, autoLabel):
        """Stable identity for a legend entry -- a Series' (dataset, model)
        fingerprints, same identity ``_seriesKey`` uses (ADR 0029: a Colorbar/
        Panel Display Override rename is keyed on content, not a display name
        the user might also rename)."""
        if not autoLabel:
            return None
        dsFp = getattr(autoLabel.get("dataset"), "fingerprint", None)
        mFp = getattr(autoLabel.get("model"), "fingerprint", None)
        if dsFp is None and mFp is None:
            return None
        return f"{dsFp}|{mFp}"

    def refreshLegend(self):
        dw = self.dataWatcher
        hasDataset = len(dw.getDatasetDependencies()) >= 1
        hasModel = len(dw.getModelDependencies()) >= 1

        self.legend.clear()
        self._legendItemAutoLabels = {}

        if not (hasDataset or hasModel):
            return

        for i in range(len(self.plotItems)):
            item = self.plotItems[i]
            label, autoLabel = self.labelsList[i]

            if label is None:
                if autoLabel is not None:
                    label = self.getLabelFromData(autoLabel)

            else:
                if autoLabel is not None:
                    label = label.replace(
                        "__NAME__", self.getLabelFromData(autoLabel)
                    )

            if label is None:
                continue

            entryKey = self._legendEntryKey(autoLabel)
            override = self._legendEntryOverrides.get(entryKey) if entryKey else None
            if override is not None:
                label = override
            self._legendItemAutoLabels[id(item)] = autoLabel
            self.legend.addItem(item, label)

    # ----------------------------------------------------------------- #
    # Panel Display Override: editable legend (ADR 0029)
    # ----------------------------------------------------------------- #
    def _onLegendMoved(self, x, y):
        self._persistOverride(("legend", "position"), [x, y])

    def _setLegendFontSize(self, size):
        size = min(_MAX_LEGEND_FONT_PT, max(_MIN_LEGEND_FONT_PT, size))
        self._legendFontSize = size
        self.legend.setLabelTextSize(f"{size}pt")
        self._persistOverride(("legend", "font_size"), size)

    def _resizeLegendFontSize(self, delta):
        size = self._legendFontSize or _DEFAULT_LEGEND_FONT_PT
        self._setLegendFontSize(size + delta)

    def _onLegendWheel(self, delta):
        self._resizeLegendFontSize(1 if delta > 0 else -1)

    def _promptLegendFontSize(self):
        current = self._legendFontSize or _DEFAULT_LEGEND_FONT_PT

        def _apply(size, ok):
            if ok:
                self._setLegendFontSize(size)

        self._askFontSizeDialog(current, _MIN_LEGEND_FONT_PT, _MAX_LEGEND_FONT_PT, _apply)

    def _resetLegend(self):
        self._legendFontSize = None
        self.legend.setLabelTextSize(f"{_DEFAULT_LEGEND_FONT_PT}pt")
        self.legend.setPos(30, 10)
        self._persistOverride(("legend", "font_size"), None)
        self._persistOverride(("legend", "position"), None)

    def _buildLegendMenu(self):
        """Right-click menu on the legend -- mirrors the axis label menu
        (numeric entry + steppers), minus per-entry renaming (that stays
        double-click-only, since it needs picking which entry first)."""
        menu = QtWidgets.QMenu(self.plotWidget)
        menu.setObjectName("compactContextMenu")
        menu.addAction("Set Font Size…", self._promptLegendFontSize)
        menu.addAction("Increase Font Size", lambda: self._resizeLegendFontSize(1))
        menu.addAction("Decrease Font Size", lambda: self._resizeLegendFontSize(-1))
        menu.addSeparator()
        menu.addAction("Reset Legend", self._resetLegend)
        return menu

    def _showLegendMenu(self, globalPos):
        # popup(), not exec() -- see _showAxisLabelMenu for why.
        menu = self._buildLegendMenu()
        menu.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.popup(globalPos)

    def _startLegendEntryEdit(self, sample, label):
        entryKey = self._legendEntryKey(self._legendItemAutoLabels.get(id(sample.item)))
        self._openInlineEditor(
            label, label.text,
            lambda text: self._commitLegendEntryEdit(entryKey, text),
        )

    def _commitLegendEntryEdit(self, entryKey, text):
        if entryKey is not None:
            if text:
                self._legendEntryOverrides[entryKey] = text
            else:
                self._legendEntryOverrides.pop(entryKey, None)
            self._persistOverride(("legend", "entries", entryKey), text or None)
        self.refreshLegend()

    # ----------------------------------------------------------------- #
    # Panel Display Override: load/apply a saved override (ADR 0029)
    # ----------------------------------------------------------------- #
    def _loadDisplayOverride(self):
        if self._displayOverrideKey is None:
            return
        tab, kind, mids = self._displayOverrideKey
        override = display_overrides.get_panel_override(tab, kind, mids)
        self.applyDisplayOverride(override)

    def applyDisplayOverride(self, override):
        """Apply a saved Panel Display Override on top of the Panel Kind/TOML
        defaults already set via ``setXLabel``/``setYLabel``."""
        for axisName, role in (("bottom", "x_label"), ("left", "y_label")):
            field = override.get(role) or {}
            if "text" in field:
                self._labelState[axisName]["text"] = field["text"]
            if "font_size" in field:
                self._labelState[axisName]["font_size"] = field["font_size"]
            self._renderAxisLabel(axisName)

        if not self.hasLegend:
            return
        legend = override.get("legend") or {}
        if "position" in legend:
            x, y = legend["position"]
            self.legend.setPos(x, y)
        if "font_size" in legend:
            self._legendFontSize = legend["font_size"]
            self.legend.setLabelTextSize(f"{self._legendFontSize}pt")
        self._legendEntryOverrides = dict(legend.get("entries") or {})
        if self._legendEntryOverrides:
            self.refreshLegend()

    def clear(self):
        for rec in self._series.values():
            self.plotWidget.removeItem(rec["item"])
        self._series = {}
        self._visitOrder = []
        self._visitedKeys = set()
        self.plotItems = []

    def _seriesKey(self, scatter, autoColor):
        """Stable identity for a drawn Series across refreshes (ADR 0022).

        Content-based: (dataset_fp, model_fp, kind) plus a sub-index that
        disambiguates multiple same-pair draws within one refresh. Keyless
        draws (the y=x overlay) get a "__static__" base. Same draw order each
        refresh → same key → the item is matched and updated in place.
        """
        kind = "scatter" if scatter else "line"
        if isinstance(autoColor, dict):
            ds = autoColor.get("dataset")
            m = autoColor.get("model")
            base = (
                getattr(ds, "fingerprint", None),
                getattr(m, "fingerprint", None),
                kind,
            )
        else:
            base = ("__static__", kind)
        n = self._keyCounter.get(base, 0)
        self._keyCounter[base] = n + 1
        return base + (n,)

    def _dataSignature(self, x, y):
        """Hash of the drawn arrays — authoritative change signal.

        Keyed off the *drawn* (x, y), not upstream Metric Result checksums, so
        it stays correct for plots that transform client-side (e.g. the energy
        shift subtracts a value and the shift-enabled toggle lives in no
        checksum). A shape mismatch short-circuits before hashing.
        """
        xa = np.asarray(x)
        ya = np.asarray(y)
        return (xa.shape, ya.shape, hash(xa.tobytes()), hash(ya.tobytes()))

    def plot(
        self,
        x,
        y,
        scatter=False,
        color=None,
        autoColor=None,
        autoLabel=None,
        label=None,
        ignoreBounds=False,
        **kwargs,
    ):
        self.plotItem.disableAutoRange()
        kind = "scatter" if scatter else "line"
        key = self._seriesKey(scatter, autoColor)
        self._visitedKeys.add(key)
        self._visitOrder.append(key)

        # Resolve style up front so it folds into the signature: a recolour /
        # re-symbol with unchanged data still updates the item in place.
        if "pen" in kwargs:
            pen = kwargs.pop("pen")
        else:
            if autoColor is not None:
                color = self.env.getColorMix(
                    dataset=autoColor["dataset"], model=autoColor["model"]
                )
            elif color is None:
                color = getConfig("modelColors")[self.colorCount]
                self.colorCount += 1
            width = float(getConfig("plotPenWidth"))
            pen = pyqtgraph.mkPen(color, width=width)

        brush = None
        symbol = None
        if scatter:
            # Each scatter Series gets a distinct symbol so stacked clouds stay
            # distinguishable. Opaque fill (alpha=255) is much faster than alpha
            # blending in software render. antialias=False overrides the global
            # AA (per-point cost dominates with OpenGL off). DeviceCoordinateCache
            # blits the cloud on pan/zoom instead of repainting every point.
            _SYMBOLS = ['o', 's', 't', 'd']
            symbol = kwargs.pop('symbol', _SYMBOLS[self.symbolCount % len(_SYMBOLS)])
            self.symbolCount += 1
            c = pyqtgraph.mkColor(color)
            c.setAlpha(255)
            brush = pyqtgraph.mkBrush(c)

        sig = self._dataSignature(x, y) + (repr(color), symbol, bool(ignoreBounds))

        prev = self._series.get(key)
        if prev is not None and prev["kind"] == kind:
            item = prev["item"]
            if prev["sig"] != sig:
                # Same Series, changed data/style → update in place (no teardown).
                item.setData(x, y)
                if scatter:
                    item.setBrush(brush)
                    item.setSymbol(symbol)
                else:
                    item.setPen(pen)
                self._changedThisRefresh = True
            # else: identical → leave the existing item untouched.
        else:
            if prev is not None:  # kind flipped at this key → replace the item
                self.plotWidget.removeItem(prev["item"])
            if scatter:
                item = pyqtgraph.ScatterPlotItem(
                    x, y, brush=brush, pen=None, size=5, symbol=symbol,
                    antialias=False,
                )
                item.setCacheMode(
                    QtWidgets.QGraphicsItem.CacheMode.DeviceCoordinateCache
                )
            else:
                # Keep antialiasing for smooth lines, but cache the rendered
                # curve as a device pixmap: AA polylines are expensive to
                # re-rasterise in software render (OpenGL off on Apple), and a
                # QScrollArea scroll fully repaints each moved plot — so without
                # a cache every scroll step re-strokes every AA line (the
                # timeline-scroll freeze). The cache blits instead; it
                # re-rasterises only when the data or zoom actually changes.
                # PlotDataItem itself doesn't paint — its child `curve` does —
                # so the cache mode goes there. setdefault lets a caller override.
                kwargs.setdefault("antialias", True)
                item = pyqtgraph.PlotDataItem(x, y, pen=pen, **kwargs)
                if item.curve is not None:
                    item.curve.setCacheMode(
                        QtWidgets.QGraphicsItem.CacheMode.DeviceCoordinateCache
                    )
            # ignoreBounds keeps an item out of autoRange (e.g. the y=x guide,
            # whose endpoints would otherwise stretch the axes and squash the
            # scatter clouds into a sliver).
            self.plotItem.addItem(item, ignoreBounds=ignoreBounds)
            self._changedThisRefresh = True

        self._series[key] = {
            "item": item,
            "sig": sig,
            "kind": kind,
            "label": (label, autoLabel),
        }

    def stepPlot(self, x, y, width=1, **kwargs):
        xLeft, xRight = (x - width / 2, x + width / 2)
        newX = np.zeros(xLeft.shape[0] * 2)
        newX[::2] = xLeft
        newX[1::2] = xRight

        newY = np.repeat(y, 2)

        self.plot(newX, newY, **kwargs)

    def mapSceneToView(self, arg1, arg2=None):
        if arg2 is not None:
            arg1 = QtCore.QPointF(arg1, arg2)
        return self.plotItem.getViewBox().mapSceneToView(arg1)

    def onModelDatasetNameChanged(self, key):
        if not self.dataWatcher.isDependentOn(key):
            return

        self.refreshLegend()

    def onModelDatasetColorChanged(self, key):
        if not self.dataWatcher.isDependentOn(key):
            return

        self.visualRefresh()  # includes legend

    def addOption(self, widget):
        self.optionsToolbar.layout.insertWidget(0, widget)


class Table(Widget, EventChildClass, DataDependentObject):

    lastUpdatedStamp = -1
    styleSheet = """
    @OBJECT{
        border-radius:10px;
    }
    """

    def __init__(
        self, handler, name="N/A", title="N/A", isSubbable=True, **kwargs
    ):
        self.handler = handler
        self.env = handler.env
        self.name = name
        self.isSubbable = isSubbable

        super().__init__(
            layout="vertical",
            color="@BGColor3",
            styleSheet=self.styleSheet,
            **kwargs,
        )

        # self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)

        EventChildClass.__init__(self)
        DataDependentObject.__init__(self)

        self.layout.setContentsMargins(13, 13, 13, 13)
        self.layout.setSpacing(8)
        self.eventSubscribe(
            "OBJECT_NAME_CHANGED", self.onModelDatasetNameChanged
        )

        # TOOLBAR
        self.toolbar = Widget(parent=self, layout="horizontal")
        self.toolbar.setFixedHeight(30)
        self.toolbar.setObjectName("plotToolbar")
        self.layout.addWidget(self.toolbar)

        # DIVIDER
        divider = Widget(parent=self, color="@TextColor3")
        divider.setFixedHeight(1)
        self.layout.addWidget(divider)

        # OPTIONS
        self.optionsToolbar = Widget(parent=self, layout="horizontal")
        self.optionsToolbar.setObjectName("plotoptionsToolbar")
        self.optionsToolbar.layout.addStretch()
        self.layout.addWidget(self.optionsToolbar)

        # TABLEVIEW
        self.table = TableView(parent=self)
        self.layout.addWidget(self.table)
        self.applyToolbar(title=title)  # needs the table to exist (does it?)

        # REFRESH
        self.eventSubscribe("WIDGET_REFRESH", self.onWidgetRefresh)
        self.eventSubscribe("WIDGET_VISUAL_REFRESH", self.visualRefresh)

        # EVENTS
        self.eventSubscribe(
            "OBJECT_NAME_CHANGED", self.onModelDatasetNameChanged
        )

    def applyToolbar(self, title="N/A"):
        layout = self.toolbar.layout
        layout.setContentsMargins(20, 0, 0, 0)
        layout.setSpacing(8)

        self.titleLabel = QtWidgets.QLabel(title)
        self.titleLabel.setObjectName("plotTitleLabel")
        layout.addWidget(self.titleLabel)

        layout.addStretch()

        # add buttons here if needed

        self.loadButton = DataloaderButton(self.handler, self.dataWatcher)
        layout.addWidget(self.loadButton)

    def refreshHeaders(self):
        nRows, nCols = self.table.tableSize

        # HEADERS
        for col in range(nCols):
            self.setTopHeader(col, self.getTopHeader(col))

        for row in range(nRows):
            self.setLeftHeader(row, self.getLeftHeader(row))

    def refreshValues(self):
        nRows, nCols = self.table.tableSize

        for row in range(nRows):
            for col in range(nCols):
                self.setValue(row, col, self.getValue(row, col))

    def visualRefresh(self):
        self.table.setSize(*self.getSize())

        self.refreshHeaders()
        self.refreshValues()

        self.forceUpdateParent()

    def refresh(self):
        self.visualRefresh()

    def onModelDatasetNameChanged(self, key):
        pass

    def setValue(self, *args):
        self.table.setValue(*args)

    def setSize(self, *args):
        self.table.setSize(*args)

    def setLeftHeader(self, *args):
        self.table.setLeftHeader(*args)

    def setTopHeader(self, *args):
        self.table.setTopHeader(*args)

    def onWidgetRefresh(self, widget):
        if self is not widget:
            return
        self.refresh()

    def getValue(self, i, j):
        # placeholder, should be implemented by user
        return NotImplementedError

    def getSize(self):
        # placeholder, should be implemented by user
        return NotImplementedError

    def getLeftHeader(self, i):
        return NotImplementedError

    def getTopHeader(self, i):
        return NotImplementedError

    def onModelDatasetNameChanged(self, key):
        if not self.dataWatcher.isDependentOn(key):
            return

        self.refreshHeaders()

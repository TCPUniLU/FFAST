from UI.Templates import Widget, ToolButton, ToolCheckButton, PushButton, TableView, InfoToolButton
from PySide6 import QtCore, QtGui, QtWidgets
from config.uiConfig import config, configStyleSheet
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QWidget, QTabWidget
from config.uiConfig import config, getIcon
from PySide6.QtWidgets import QSizePolicy
from events import EventChildClass
import pyqtgraph
import logging
from client.dataWatcher import DataWatcher
import numpy as np
from datasetLoaders.loader import SubDataset
from config.userConfig import getConfig


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

        self.legend = pyqtgraph.LegendItem(
            offset=(30, 10),
            labelTextSize="12pt",
            labelTextColor=self.handler.config["envs"].get("TextColor1"),
        )
        self.legend.setParentItem(self.plotWidget.graphicsItem())
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
        fontOptions = {"font-size": "20px", "color": "lightgray"}
        if unit is not None:
            label = f"{label} [{unit}]"
        self.plotWidget.setLabel("bottom", label, **fontOptions)

    def setYLabel(self, label, unit=None):
        fontOptions = {"font-size": "20px", "color": "lightgray"}
        if unit is not None:
            label = f"{label} [{unit}]"
        self.plotWidget.setLabel("left", label, **fontOptions)

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

    def refreshLegend(self):
        dw = self.dataWatcher
        hasDataset = len(dw.getDatasetDependencies()) >= 1
        hasModel = len(dw.getModelDependencies()) >= 1

        self.legend.clear()

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
            self.legend.addItem(item, label)

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

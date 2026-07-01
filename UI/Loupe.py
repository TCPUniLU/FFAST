from events import EventChildClass
from UI.Templates import Widget, ObjectComboBox, SettingsPane
from UI.loupeMenu import LoupeMenuHandler
from UI.loupeCanvas import SideBar, InteractiveCanvas
from PySide6 import QtCore, QtWidgets
import logging
import asyncio
from config.userConfig import Settings
import uuid
from ffast.renderers.vispy.local_scene import available_prediction_refs
from ffast.visualization.scene import SceneSnapshot, ScenePatch

logger = logging.getLogger("FFAST")


class Loupe(Widget, EventChildClass):

    selectedDatasetKey = None
    index = 0
    videoPaused = True

    def __init__(self, handler, N, **kwargs):
        self.handler = handler
        self.env = handler.env
        super().__init__(layout="horizontal")
        EventChildClass.__init__(self)

        # Server-owned render path (ADR 0014): stable per-window view id used to
        # match incoming SCENE_SNAPSHOT / SCENE_PATCH to this Loupe window, plus
        # the latest snapshot cached for re-apply when toggling into adapter mode.
        self.viewId = str(uuid.uuid4())
        self._lastSnapshot = None
        # Latest server scene version, tracked for version-gated commands
        # (TOGGLE_FEATURE / SET_PARAMETER). Frame patches don't bump it.
        self._sceneVersion = 0
        # Working set of picked scientific atom ids (ADR 0015), accumulated
        # client-side and committed in full via SET_SELECTION.
        self._pickedSet = []
        # Debounce handle for SET_FRAME commands — cancels stale intermediate
        # frames when the slider is dragged faster than the server can respond.
        self._frameUpdateHandle = None
        # Event signalled when each SCENE_PATCH arrives; video loop waits on
        # this to sync frame advancement to server response rate.
        self._patchReceivedEvent = asyncio.Event()

        # SETTINGS
        self.initialiseSettings()

        self.resize(1100, 800)
        self.setWindowTitle(f"3D View {N}")

        # SIDEBAR HERE
        self.sideBarContainer = Widget(layout="vertical", parent=self)
        self.sideBarContainer.setFixedWidth(300)
        self.layout.addWidget(self.sideBarContainer)

        self.datasetComboBox = ObjectComboBox(handler, hasDatasets=True)
        self.datasetComboBox.setOnIndexChanged(self.onDatasetSelected)
        self.datasetComboBox.setToolTip("Select dataset to show")
        self.sideBarContainer.layout.addWidget(self.datasetComboBox)

        self.sideBar = SideBar(handler, parent=self)
        self.sideBarContainer.layout.addWidget(self.sideBar)
        self.panes = {}

        # MAIN WINDOW HERE
        self.contentWindow = Widget(
            color="@BGColor2", layout="horizontal", parent=self
        )
        self.contentLayout = self.contentWindow.layout
        self.layout.addWidget(self.contentWindow)

        # CANVAS
        self.canvas = InteractiveCanvas(self, parent=self)
        self.canvas.settings = self.settings
        self.contentLayout.addWidget(self.canvas)

        # EVENTS
        self.eventSubscribe("SUBDATASET_INDICES_CHANGED", self.onSubChanged)
        self.eventSubscribe("REMOTE_ARRAY_FETCH_DONE", self.onRemoteArrayFetchDone)
        self.eventSubscribe("MODEL_LOADED", self.updatePredictionComboBox)
        self.eventSubscribe("MODEL_DELETED", self.updatePredictionComboBox)
        self.eventSubscribe("DATA_UPDATED", self.updatePredictionComboBox)
        self.eventSubscribe("DATASET_UPDATED", self.updatePredictionComboBox)
        # Server-owned render path (ADR 0014): consume renderer-neutral scenes.
        self.eventSubscribe("SCENE_SNAPSHOT", self.onSceneSnapshot)
        self.eventSubscribe("SCENE_PATCH", self.onScenePatch)
        self.eventSubscribe("METRICS_UPDATED", self.onMetricsUpdated)

        #MENU BAR
        self.mBar = QtWidgets.QMenuBar(self)
        self.menuHandler = LoupeMenuHandler(self)
        self.layout.setMenuBar(self.mBar)

    # Adding menu bar getter for MenuHandler to be able to properly initialize the menu
    def menuBar(self):
        return self.mBar

    # SETTINGS
    def initialiseSettings(self):
        self.settings = Settings()
        self.settings.addAction("updateIndex", self.updateCurrentIndex)
        self.settings.addAction("updateGeometry", self.updateCurrentIndex)
        self.settings.addAction("cameraChange", self.onCameraChange)
        self.settings.addAction("pause", self.onPause)
        self.settings.addAction("datasetSelected", self.onDatasetSelected)
        self.settings.addAction("visualRefresh", self.visualRefresh)
        self.settings.addAction("toggleKabschAlign", self.onToggleKabschAlign)
        self.settings.addAction("toggleSceneLabels", self.onToggleSceneLabels)
        self.settings.addAction("applySceneFilter", self.onApplySceneFilter)
        self.settings.addAction("applySceneSelection", self.onApplySceneSelection)
        self.settings.addAction("applyScenePrediction", self.onApplyScenePrediction)
        self.settings.addAction("applyColorSource", self.onApplyColorSource)
        self.settings.addAction("applyColormap", self.onApplyColormap)
        self.settings.addAction("applyAtomAlign", self.onApplyAtomAlign)
        self.settings.addAction("applyForceVectors", self.onApplyForceVectors)
        self.settings.addAction("applyUnitCell", self.onApplyUnitCell)
        self.settings.addAction("applyBondStyle", self.onApplyBondStyle)
        self.settings.addAction("applyBonds", self.onApplyBonds)

    # DATASET
    def forceUpdate(self):
        self.datasetComboBox.forceUpdate()  # activate the selection
        if self.selectedDatasetKey is None:
            return
        self.updateCurrentIndex()

    # PREDICTION OVERLAY (ADR 0016): metric coloring + force arrows need a
    # prediction. The selector lists models with cached force predictions for
    # the currently selected dataset and sends its fingerprint as prediction_ref.
    def updatePredictionComboBox(self, *args):
        combo = getattr(self, "predictionComboBox", None)
        if combo is None:
            return

        refs = [""] + available_prediction_refs(self.env, self.selectedDatasetKey)
        current = self.settings.get("scenePredictionRef", "") or ""
        if current not in refs:
            current = ""

        labels = [self._predictionLabel(ref) for ref in refs]
        self._predictionComboUpdating = True
        self._predictionComboRefs = refs
        combo.clear()
        combo.addItems(labels)
        combo.setCurrentIndex(refs.index(current))
        self._predictionComboUpdating = False

        if self.settings.get("scenePredictionRef", "") != current:
            self.settings.setParameter("scenePredictionRef", current, refresh=True)

    def _predictionLabel(self, ref):
        if not ref:
            return "No prediction"
        try:
            model = self.env.models.get(ref)
            if model is not None:
                return model.getDisplayName()
        except Exception:
            pass
        return ref[:8]

    def onPredictionComboChanged(self, index):
        if getattr(self, "_predictionComboUpdating", False):
            return
        refs = getattr(self, "_predictionComboRefs", [""])
        ref = refs[index] if 0 <= index < len(refs) else ""
        self.settings.setParameter("scenePredictionRef", ref)

    def _currentPredictionRef(self):
        return self.settings.get("scenePredictionRef", "") or None

    def _openOrRefreshView(self):
        """(Re)open the server view for the current dataset + loaded prediction."""
        key = self.selectedDatasetKey
        if key is None:
            return
        self.env.remote.openRemoteView(
            self.viewId, key, prediction_ref=self._currentPredictionRef()
        )

    def _onPredictionChanged(self):
        """Force-error watcher fired (prediction loaded/changed): resync the view."""
        self._openOrRefreshView()

    def onApplyScenePrediction(self):
        self.updatePredictionComboBox()
        self._openOrRefreshView()

    def onDatasetSelected(self, key, force=False):
        # we force when sub indices change, becasue thats not reflected in the key
        if (not force) and (key == self.selectedDatasetKey):
            return
        self._ensureAdapterHooks()
        self.settings.saveForDataset(self.selectedDatasetKey)
        self.selectedDatasetKey = key
        self.updatePredictionComboBox()

        if key is not None:
            self._pickedSet = []  # picked indices are per-structure (ADR 0015)
            self._openOrRefreshView()

        dataset = self.getSelectedDataset()

        # Remote proxy — trigger array fetch and wait for REMOTE_ARRAY_FETCH_DONE
        if dataset is not None and getattr(dataset, "is_remote_proxy", False):
            logger.info(
                "Loupe: remote proxy selected (%r) — triggering array fetch",
                key,
            )
            self.env.remote.taskFetchRemoteDataset(dataset.fingerprint)
            # Don't call setDataset yet; onRemoteArrayFetchDone will resume
            return

        self.canvas.setDataset(dataset)

        self.index = 0
        self.indexSlider.setMinMax(0, dataset.getN() - 1)
        self.settings.restoreForDataset(key)
        self.updatePredictionComboBox()
        self.updateCurrentIndex()

    def getSelectedDataset(self):
        if self.selectedDatasetKey is None:
            return None
        return self.env.datasets.get(self.selectedDatasetKey)

    def onSubChanged(self, key):
        if self.selectedDatasetKey != key:
            return

        self.onDatasetSelected(key, force=True)

    def onRemoteArrayFetchDone(self, fingerprint):
        """Arrays for a remote dataset arrived — refresh Loupe if it's selected."""
        if fingerprint == self.selectedDatasetKey:
            logger.info("Loupe: remote arrays ready for %r — refreshing", fingerprint)
            self.onDatasetSelected(fingerprint, force=True)

    # SERVER-OWNED RENDER PATH (ADR 0014)
    def onSceneSnapshot(self, scene=None, **kwargs):
        """Apply a full RenderScene from the server via the scene adapter."""
        if scene is None:
            return
        try:
            snapshot = SceneSnapshot.model_validate({"scene": scene})
        except Exception as exc:
            logger.warning("Loupe: malformed SCENE_SNAPSHOT: %s", exc)
            return
        if snapshot.scene.view_id != self.viewId:
            return  # snapshot belongs to another Loupe window
        self._lastSnapshot = snapshot
        self._sceneVersion = snapshot.scene.version
        logger.info(
            "Loupe: SCENE_SNAPSHOT v%d → adapter", snapshot.scene.version
        )
        self.canvas.sceneAdapter.apply_snapshot(snapshot)
        # Fit camera to atoms on every snapshot (new dataset open / view refresh).
        # set_range() updates center + scale_factor but not azimuth/elevation,
        # so the user's rotation is preserved on refresh, reset on dataset switch.
        if snapshot.scene.atoms:
            self.canvas.view.camera.set_range()
        self.canvas.updateColorbar(self.canvas.sceneAdapter._color_by)
        self.canvas.canvas.update()

    def onScenePatch(self, **kwargs):
        """Apply a delta RenderScene update from the server."""
        try:
            patch = ScenePatch.model_validate(kwargs)
        except Exception as exc:
            logger.warning("Loupe: malformed SCENE_PATCH: %s", exc)
            return
        if patch.view_id != self.viewId:
            return
        self._sceneVersion = patch.to_version
        logger.info(
            "Loupe: SCENE_PATCH v%d→v%d changed=%s → adapter",
            patch.from_version, patch.to_version, sorted(patch.changed),
        )
        self.canvas.sceneAdapter.apply_patch(patch)
        # Track molecule centroid so atoms stay centred as the molecule drifts.
        # Only shift camera.center (preserves zoom/rotation); skip if no atoms
        # changed (e.g. selection-only patch).
        if "atoms" in set(patch.changed):
            self._trackMoleculeCentroid()
        self.canvas.updateColorbar(self.canvas.sceneAdapter._color_by)
        self.canvas.canvas.update()
        # Signal video loop that a patch arrived (used by runOnNext sync).
        self._patchReceivedEvent.set()

    def onMetricsUpdated(self, metric_ids=None, **kwargs):
        """Server loaded new user metrics — refresh the Coloring combo."""
        from modules.loupe.loupeAtoms import addMetricControls
        addMetricControls(self.handler, self)

    def _ensureAdapterHooks(self):
        """Wire bond style + prediction settings to the adapter (once, lazily).

        These live on settings/watchers created by other modules after Loupe
        __init__, so we hook them the first time the adapter is enabled:
          - bond width/color settings → keep the adapter look live;
          - the force-error data watcher → resync prediction_ref when a
            prediction is loaded/changed, so metric coloring updates.
        """
        if getattr(self, "_adapterHooked", False):
            return
        for key in ("bondWidth", "bondColor"):
            if key in self.settings:
                self.settings.addParameterActions(key, self._onBondStyleChanged)
        dw = getattr(self, "forceErrorDataWatcher", None)
        if dw is not None:
            dw.addCallback(self._onPredictionChanged)
        self._adapterHooked = True

    def _onBondStyleChanged(self):
        self.canvas._pushAdapterStyle()
        self.canvas.canvas.update()

    def _sendViewCommand(self, **fields):
        """Send a version-gated scientific view command and optimistically advance
        the expected view version.

        ``sendViewCommand`` is fire-and-forget and the server applies commands in
        order, bumping the view version by one per accepted scientific command. A
        single UI action often emits several commands (force vectors sends a
        feature toggle plus its parameters); stamping them all with the same
        version makes the server accept the first and reject the rest as
        STALE_VERSION. Advancing here keeps the batch in step. The next
        SCENE_PATCH re-syncs ``_sceneVersion`` to the server's authoritative value
        (see ``onScenePatch``). Frame/camera commands are last-write-wins and do
        not use this path.
        """
        self.env.remote.sendViewCommand(
            view_id=self.viewId, view_version=self._sceneVersion, **fields
        )
        self._sceneVersion += 1

    def _sendToggleFeature(self, feature, enabled):
        """ADR 0014: send a version-gated TOGGLE_FEATURE for a server pipeline stage."""
        self._sendViewCommand(
            type="TOGGLE_FEATURE", feature=feature, enabled=bool(enabled),
        )

    def _sendSetParameter(self, stage_id, parameter, value):
        """ADR 0014: send a SET_PARAMETER command for a server pipeline stage."""
        self._sendViewCommand(
            type="SET_PARAMETER", stage_id=stage_id, parameter=parameter, value=value,
        )

    def onApplyAtomAlign(self):
        """ADR 0014: server-side 3-atom frame alignment (ffast.atom_align)."""
        enabled = bool(self.settings.get("alignAtoms"))
        raw = self.settings.get("alignAtomsIndices") or ""
        indices = self._parseIndexList(raw) if isinstance(raw, str) else list(raw or [])
        ref_frame = int(self.settings.get("alignAtomsConfIndex") or 0)
        self._sendToggleFeature("atom_align", enabled)
        if enabled and len(indices) == 3:
            self._sendSetParameter("ffast.atom_align", "atom_indices", indices)
            self._sendSetParameter("ffast.atom_align", "reference_frame", ref_frame)

    def onApplyForceVectors(self):
        enabled = bool(self.settings.get("showForceVectors"))
        self._sendToggleFeature("forces", enabled)
        if enabled:
            self._sendSetParameter("ffast.force_arrows", "prediction_ref", self.settings.get("forceVectorsModelKey"))
            self._sendSetParameter("ffast.force_arrows", "length_factor", int(self.settings.get("forceVectorsLength") or 10))
            self._sendSetParameter("ffast.force_arrows", "normalised", bool(self.settings.get("forceVectorsNormalised")))
            self._sendSetParameter("ffast.force_arrows", "filter_enabled", bool(self.settings.get("forceVectorsFilterEnabled")))
            self._sendSetParameter("ffast.force_arrows", "atom_indices", list(self.settings.get("forceVectorsAtomIndices") or []))

    def onApplyUnitCell(self):
        # opt-out: add "no_unit_cell" when hiding, remove it when showing
        self._sendToggleFeature("no_unit_cell", not bool(self.settings.get("showUnitCell")))

    def onApplyBondStyle(self):
        self.canvas._pushAdapterStyle()

    def onApplyBonds(self):
        """Drive server-side bond topology (ffast.bonds, loupeBonds module).

        'Dynamic' → distance-based bonds recomputed per frame by the server.
        'Fixed'   → the explicit bond pairs the user selected. An empty Fixed
        set falls back to dynamic bonds server-side, so toggling to Fixed before
        choosing bonds never leaves the view bondless.
        """
        bond_type = self.settings.get("bondType") or "Dynamic"
        self._sendSetParameter("ffast.bonds", "bond_type", bond_type)
        pairs = []
        if bond_type == "Fixed":
            raw = self.settings.get("fixedBondIndices")
            if raw is not None:
                try:
                    pairs = [[int(a), int(b)] for a, b in raw]
                except (TypeError, ValueError):
                    pairs = []
        self._sendSetParameter("ffast.bonds", "fixed_indices", pairs)

    def onToggleKabschAlign(self):
        """Gate item 2: server-side Kabsch alignment to frame 0.

        The ``heavy_only`` stage parameter is sent alongside the toggle so the
        "Kabsch: heavy atoms only" checkbox actually reaches ``ffast.kabsch_alignment``
        (re-fired by ``alignKabschHeavyOnly`` changing, see loupeViewSettings).
        """
        enabled = self.settings.get("alignKabsch")
        self._sendToggleFeature("kabsch_align", enabled)
        if enabled:
            self._sendSetParameter(
                "ffast.kabsch_alignment", "heavy_only",
                bool(self.settings.get("alignKabschHeavyOnly")),
            )

    def onToggleSceneLabels(self):
        """Gate item 1: server-side atom index labels (ffast.atom_labels)."""
        self._sendToggleFeature("labels", self.settings.get("showSceneLabels"))

    @staticmethod
    def _parseIndexList(text):
        out = []
        for tok in str(text or "").replace(",", " ").split():
            try:
                out.append(int(tok))
            except ValueError:
                pass
        return out

    @staticmethod
    def _parseFilterTokens(text):
        """Tokenize a filter spec, keeping element symbols ('C', '-H') as
        strings and indices as ints. The server resolves them (ADR 0014)."""
        out = []
        for tok in str(text or "").replace(",", " ").split():
            try:
                out.append(int(tok))
            except ValueError:
                out.append(tok)
        return out

    def onApplySceneFilter(self):
        """Drive the server-side atom filter (ffast.atom_filter).

        The keep-mask is computed server-side; the client sends the raw spec
        (indices and/or element tokens). Empty = show all atoms.
        """
        self._sendViewCommand(
            type="SET_PARAMETER",
            stage_id="ffast.atom_filter",
            parameter="indices",
            value=self._parseFilterTokens(self.settings.get("sceneFilterIndices")),
        )

    def onApplySceneSelection(self):
        """Highlight atoms via a server-owned Scientific Selection.

        Sends SET_SELECTION; the server stores the selection and build_scene
        emits a SelectionOverlay the adapter renders. Empty list clears it.
        """
        indices = self._parseIndexList(self.settings.get("sceneSelectIndices"))
        self._pickedSet = list(indices)
        self._commitPicked()

    def _setColorParam(self, parameter, value):
        self._sendViewCommand(
            type="SET_PARAMETER",
            stage_id="ffast.atom_color",
            parameter=parameter,
            value=value,
        )

    def onApplyColorSource(self):
        source = self.settings.get("atomColorSource")
        logger.info(
            "Loupe[%s]: applyColorSource → source=%r (colorType=%r)",
            self.viewId, source, self.settings.get("atomColorType"),
        )
        self._setColorParam("source", source)

    def onApplyColormap(self):
        colormap = self.settings.get("atomColorMap")
        logger.info("Loupe[%s]: applyColormap → colormap=%r", self.viewId, colormap)
        self._setColorParam("colormap", colormap)

    # PICKING (ADR 0015): client accumulates a working set, commits the full set.
    def onAdapterPick(self, displayed_index):
        """Toggle a clicked atom in the working selection and commit it."""
        if displayed_index is None:
            return
        atom_id = self.canvas.sceneAdapter.displayed_to_atom_id(displayed_index)
        if atom_id in self._pickedSet:
            self._pickedSet.remove(atom_id)
        else:
            self._pickedSet.append(atom_id)
        self._commitPicked()

    def onAdapterPickRect(self, displayed_indices):
        """Add a rectangle of atoms to the working selection and commit it."""
        for k in displayed_indices:
            atom_id = self.canvas.sceneAdapter.displayed_to_atom_id(k)
            if atom_id not in self._pickedSet:
                self._pickedSet.append(atom_id)
        self._commitPicked()

    def _commitPicked(self):
        """Commit the working set as the server-owned 'picked' selection."""
        self._sendViewCommand(
            type="SET_SELECTION",
            name="picked",
            scope="current_structure",
            indices=list(self._pickedSet),
        )

    # INDEX
    def updateCurrentIndex(self):
        self.indexSlider.setValue(self.index, quiet=True)
        self.canvas.setIndex(self.index)
        self._sendRemoteFrame()

    def _sendRemoteFrame(self):
        """Drive the server view's frame. SET_FRAME is version-agnostic.

        Debounced at 12 ms: rapid slider drags cancel the previous scheduled
        request and only send the latest frame index. Arrow-button single
        clicks feel instant (<12 ms overhead); scrubbing at 100+ ticks/s is
        throttled to ≤80 fps, preventing intermediate-frame patch flooding.
        """
        if self._frameUpdateHandle is not None:
            self._frameUpdateHandle.cancel()
        self._frameUpdateHandle = asyncio.get_event_loop().call_later(
            0.012, self._sendRemoteFrameNow,
        )

    def _sendRemoteFrameNow(self):
        self._frameUpdateHandle = None
        self.env.remote.sendViewCommand(
            type="SET_FRAME",
            view_id=self.viewId,
            view_version=0,
            frame_index=self.index,
        )

    def _trackMoleculeCentroid(self):
        """Shift camera center to geometric centroid of current atoms.

        Respects the 'originCenterOfMass' setting (same toggle as the legacy
        CameraInfo property). Only fires when the adapter owns the render path.
        """
        if not self.canvas.settings.get("originCenterOfMass"):
            return
        import numpy as np
        pos = self.canvas.sceneAdapter._atom_positions
        if pos is None or len(pos) == 0:
            return
        centroid = np.mean(pos, axis=0)
        self.canvas.camera.center = tuple(float(c) for c in centroid)

    def setIndex(self, index):
        self.index = index
        if index == self.getNMax():
            self.onPause()

        self.updateCurrentIndex()

    # ELEMENTS
    def addVisualElement(self, Element, name, viewParent=False):
        self.canvas.addVisualElement(Element, name, viewParent=viewParent)

    def addCanvasProperty(self, Prop):
        self.canvas.addProperty(Prop)

    #  VIDEO/MOVING GEOMETRIES
    def toggleVideo(self):
        if self.videoPaused:
            self.onStart()
        else:
            self.onPause()

        if self.videoPaused:
            self.playButton.setIconByName("start")
        else:
            self.playButton.setIconByName("pause")

    def onPause(self):
        self.videoPaused = True

    def onStart(self):
        self.videoPaused = False
        self.videoTask = self.env.tm.simpleTask(self.runOnNext)

    async def runOnNext(self):
        loop = asyncio.get_event_loop()
        while not self.videoPaused:
            if self.selectedDatasetKey is None:
                return
            frame_start = loop.time()
            self.onNext(skip=self.settings.get("videoSkipFrames"))
            # Wait for the server's SCENE_PATCH before advancing; prevents
            # patch stacking when the server responds slower than target FPS.
            self._patchReceivedEvent.clear()
            try:
                await asyncio.wait_for(self._patchReceivedEvent.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
            # Sleep any remaining time to maintain the user's target FPS.
            elapsed = loop.time() - frame_start
            remaining = (1.0 / max(1, self.settings.get("videoFPS"))) - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)

    def onPrevious(self):
        index = max(0, self.index - 1)
        self.setIndex(index)

    def getNMax(self):
        return self.getSelectedDataset().getN() - 1

    def onNext(self, skip=0):
        index = min(self.getNMax(), self.index + 1 + skip)

        self.setIndex(index)

    def visualRefresh(self):
        self.canvas.visualRefresh(force=True)

    # SETTINGS PANE
    def addSidebarPane(self, name, pane):
        if name in self.panes:
            logger.error(
                f"Tried to add settings pane with name {name} but already exists"
            )
            return
        self.panes[name] = pane
        collapsibleWidget = self.sideBar.addContent(name, pane)
        # collapsibleWidget.setMaximumWidth(self.sideBar.width())
        if isinstance(pane, SettingsPane):
            collapsibleWidget.setCallback(pane.updateVisibilities)

    def getSettingsPane(self, name):
        return self.panes.get(name, None)

    def setSettingsPaneVisibility(self, *args):
        return self.sideBar.setContentVisibility(*args)

    # PICKING
    def setActiveAtomSelectTool(self, *args):
        return self.canvas.setActiveAtomSelectTool(*args)

    def isActiveAtomSelectTool(self, *args):
        return self.canvas.isActiveAtomSelectTool(*args)

    # SHORTCUTS
    # not yet working
    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Q:
            print("Killing")
        elif event.key() == QtCore.Qt.Key_Enter:
            print("enter")
        # print(event.key(), QtCore.Qt.Key_Escape)
        # print(event.key()==QtCore.Qt.Key_Escape)
        event.accept()

    def onCameraChange(self):
        self.canvas.onCameraChange()

    def closeEvent(self, event):
        self.env.remote.closeRemoteView(self.viewId)
        return super().closeEvent(event)

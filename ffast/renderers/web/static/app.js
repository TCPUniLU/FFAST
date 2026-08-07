/**
 * Application — wires UI, connection, and renderer together.
 */

import { FFastConnection } from './connection.js';
import { MoleculeRenderer } from './renderer.js';
import { PickController } from './picking.js';
import { infoReadout } from './measure.js';
import { MetricClient } from './metrics.js';
import { AnalysisManager } from './analysis.js';
import { createColorByPane } from './panes/colorby.js';
import { createCameraPane } from './panes/camera.js';
import { createDisplayPane } from './panes/display.js';
import { createBondsPane } from './panes/bonds.js';
import { createForcesPane } from './panes/forces.js';
import { createExtractPane } from './panes/extract.js';
import { createAlignPane } from './panes/align.js';
import { createExportPane } from './panes/export.js';
import { IN, OUT } from './events.js';
import { RemoteBrowser } from './remote_browser.js';
import { SessionOps } from './session_ops.js';

/**
 * The five pick tools (ADR 0045 Phase 2). Each mirrors a Qt AtomSelectionBase
 * subclass: `multiselect` caps the picked set, `cycle` makes it a rolling
 * window, `rectangle` enables box-select drag.
 * @type {Object<string,{label:string, icon:string, multiselect:number, cycle?:boolean, rectangle?:boolean}>}
 */
const PICK_TOOLS = {
  info:    { label: 'Info',    icon: '📐', multiselect: 4,     cycle: true },
  bonds:   { label: 'Bonds',   icon: '🔗', multiselect: 2 },
  align:   { label: 'Align',   icon: '△', multiselect: 3 },
  forces:  { label: 'Force',   icon: '➤', multiselect: 10000, rectangle: true },
  extract: { label: 'Extract', icon: '✂', multiselect: 10000, rectangle: true },
};

export class FFastApp {
  constructor() {
    this._conn = null;
    this._renderer = null;
    this._datasets = new Map();   // fingerprint → meta
    this._models = new Map();     // model fingerprint → {name, dataset_fingerprints}
    this._currentDatasetFp = null;  // selected dataset (object rail)
    this._currentModelFp = null;    // selected prediction, or null
    this._currentViewId = null;
    this._lastOpenedDatasetFp = null;  // last dataset_ref sent via OPEN_VIEW
    this._activeTab = 'loupe';
    // Last rendered snapshot. Read by _currentDynamicBondPairs(): the wire
    // ships bond segments as coordinates, never index pairs, so bonds
    // "fill from dynamic" recovers pairs by matching endpoints to these atoms.
    this._lastScene = null;
    this._frameCount = 0;
    this._cameraThrottle = null;

    // Server-side file browsing and server-side writes (ADR 0050). Ports are
    // closures so both work with whatever connection is live at call time.
    const send = (event, kwargs = {}, args = []) => this._conn?.send(event, kwargs, args);
    this._browser = new RemoteBrowser({
      send,
      getDatasets: () => this._datasets,
      getCurrentDatasetFp: () => this._currentDatasetFp,
      setStatus: (text, kind) => this._setStatus(text, kind),
    });
    this._sessionOps = new SessionOps({
      send,
      setStatus: (text, kind) => this._setStatus(text, kind),
      getCurrentDatasetFp: () => this._currentDatasetFp,
      getDatasetMeta: (fp) => this._datasets.get(fp),
      capturePng: (opts) => this._renderer.capturePng(opts),
    });

    // ADR 0045 Phase 1: scientific view-command plumbing (mirrors Qt's
    // window._sendViewCommand / _sceneVersion, UI/loupe/window.py:320-349).
    this._viewVersion = 0;
    this._metricCatalog = [];
    // Analysis plots (ADR 0045 Phase 3): the metric channel + tab manager,
    // created on connect (they need the live connection).
    this._metricClient = null;
    this._analysis = null;
    this._originCenterOfMass = true;
    this._forcesState = { show: false, modelKey: null, length: 10, normalised: true, filterEnabled: false, atomIndices: [] };
    this._dsSettings = new Map();  // dataset fp → restorable per-dataset settings
    this._playing = false;
    this._patchPending = false;

    // Picking (ADR 0045 Phase 2, issue 10): one armed tool at a time, its
    // accumulated picks (displayed index + scientific atom id), and the pick
    // radius the Display pane feeds in.
    this._pickController = null;
    this._activeTool = null;   // tool id, or null (orbit)
    this._picked = [];         // [{displayIndex, atomId}] for the active tool
    this._pickRadius = 12;

    // Save/load session (issue 21): the in-flight op's kind + path, so the
    // next TASK_DONE/TASK_FAILED (see _connect) can report completion.

    // Live pop-out controller (ADR 0044 Phase 4): when opened with
    // ?mode=loupe-live this instance auto-connects, hides the chrome
    // (body.loupe-only), and
    // selects the same dataset/prediction the opener had open — but as its
    // OWN connection, with its own view, driving its own frame/camera.
    this._autoDatasetFp = null;
    this._autoModelFp = null;

    this._initRenderer();
    this._initTabs();
    this._initUI();
    this._initSidebarPanes();
    this._initPickTools();
    this._applyUrlParams();
  }

  /** @returns {MoleculeRenderer} public accessor (tests, tooltips). */
  get renderer() { return this._renderer; }

  // ── tabs: 3D Loupe + analysis tabs (mirrors the Qt MainContentTabWidget) ──
  // Only the Loupe tab is static; the analysis tabs are built from the server's
  // TAB_LAYOUT by AnalysisManager (ADR 0045 Phase 3), so browser and desktop
  // read the same server-parsed layout.
  _initTabs() {
    const tabbar = document.getElementById('tabbar');
    const tab = document.createElement('div');
    tab.className = 'tab' + (this._activeTab === 'loupe' ? ' active' : '');
    tab.textContent = '3D Loupe';
    tab.dataset.tab = 'loupe';
    tab.addEventListener('click', () => this._selectTab('loupe'));
    tabbar.appendChild(tab);
  }

  _selectTab(id) {
    this._activeTab = id;
    for (const tab of document.querySelectorAll('#tabbar .tab'))
      tab.classList.toggle('active', tab.dataset.tab === id);
    for (const panel of document.querySelectorAll('#tabpanels .tabpanel'))
      panel.classList.toggle('active', panel.id === `panel-${id}`);
    // Opening/returning to the Loupe with a dataset selected ensures a live view.
    if (id === 'loupe' && this._conn && this._currentDatasetFp) this._openView();
    // An analysis tab renders (or refreshes) its panels lazily on activation.
    else if (id.startsWith('analysis-')) this._analysis?.activate(id);
  }

  _initRenderer() {
    const canvas = document.getElementById('canvas');
    this._renderer = new MoleculeRenderer(canvas);
    this._renderer._onCameraChange = (cam) => {
      this._sendSetCamera(cam);
      this._panes?.camera.syncFromCamera(cam);
    };
  }

  _initUI() {
    document.getElementById('connect-btn').addEventListener('click', () => this._connect());
    document.getElementById('disconnect-btn').addEventListener('click', () => this._disconnect());
    document.getElementById('reset-camera-btn').addEventListener('click', () => {
      this._renderer.resetCamera();
    });
    document.getElementById('popout-btn').addEventListener('click', () => this._openPopout());

    const slider = document.getElementById('frame-slider');
    slider.addEventListener('input', () => this._onFrameSlider());

    // Playback (issue 08): prev/next/play-pause + FPS/skip, response-synced.
    document.getElementById('prev-frame-btn').addEventListener('click', () => this._stepFrame(-1));
    document.getElementById('next-frame-btn').addEventListener('click', () => this._stepFrame(1));
    document.getElementById('play-pause-btn').addEventListener('click', () => this._togglePlayback());

    // Object rail load actions — dataset vs prediction mode.
    document.getElementById('add-dataset-btn').addEventListener('click', () => this._browser.open('dataset'));
    document.getElementById('add-prediction-btn').addEventListener('click', () => this._browser.open('prediction'));
    document.getElementById('export-dataset-btn').addEventListener('click', () => this._sessionOps.exportSelectedDataset());

    // Session save/load (issue 21) + subset export (issue 20) reuse one path
    // prompt — the browser has no native server-side save dialog.
    document.getElementById('save-session-btn').addEventListener('click', () => this._sessionOps.saveSession());
    document.getElementById('load-session-btn').addEventListener('click', () => this._sessionOps.loadSession());
    // Each modal owns its own controls (ADR 0050).
    this._sessionOps.bindControls();
    this._browser.bindControls();
  }

  // ── sidebar panes (ADR 0045 Phase 1: issues 03-07) ──────────────────────
  _initSidebarPanes() {
    const sidebarEl = document.getElementById('loupe-sidebar');

    const colorBy = createColorByPane(sidebarEl, {
      onSourceChange: (source) => this._sendSetParameter('ffast.atom_color', 'source', source),
      onPredictionChange: (modelKey) => this._sendSetParameter('ffast.atom_color', 'prediction_ref', modelKey),
      onColormapChange: (cm) => this._sendSetParameter('ffast.atom_color', 'colormap', cm),
      onMetricParam: (key, value) => this._sendSetParameter('ffast.atom_color', key, value),
    });

    const camera = createCameraPane(sidebarEl, {
      onOrtho: (enabled) => this._renderer.setOrthographic(enabled),
      onPreset: (az, el) => this._renderer.setCameraAngles({ azimuth: az, elevation: el }),
      onManual: (az, el, dist) => this._renderer.setCameraAngles({ azimuth: az, elevation: el, distance: dist }),
      onCOM: (enabled) => { this._originCenterOfMass = enabled; },
      onGizmo: (enabled) => this._renderer.setGizmoEnabled(enabled),
      onBackground: (hex) => {
        this._renderer.setBackgroundColor(hex);
        this._panes?.export.setBackground(hex);   // keep the Export bg in step
      },
    });

    const display = createDisplayPane(sidebarEl, {
      onAtomSize: (scale) => this._sendSetParameter('ffast.atom_sizes', 'scale', scale),
      onHideAtoms: (tokens) => this._sendSetParameter('ffast.atom_filter', 'indices', tokens),
      onHighlight: (indices) => this._sendSetSelection('picked', 'current_structure', indices),
      onPickRadius: (px) => { this._pickRadius = px; this._updatePickStrip(); },
      onUnitCell: (visible) => this._sendToggleFeature('no_unit_cell', !visible),
    });

    const bonds = createBondsPane(sidebarEl, {
      onStyle: (width, color) => this._renderer.setBondStyle(width, color),
      onApply: (bondType, fixedIndices) => {
        this._sendSetParameter('ffast.bonds', 'bond_type', bondType);
        this._sendSetParameter('ffast.bonds', 'fixed_indices', bondType === 'Fixed' ? fixedIndices : []);
      },
      getDynamicBondPairs: () => this._currentDynamicBondPairs(),
    });

    const forces = createForcesPane(sidebarEl, {
      onApply: (state) => {
        this._forcesState = state;
        this._applyForceVectorsState(state);
      },
      getModels: () => this._models,
    });

    const extract = createExtractPane(sidebarEl, {
      onExtract: (tokens) => this._sendCreateSubset(tokens),
    });

    const exportPane = createExportPane(sidebarEl, {
      onExport: (opts) => this._sessionOps.exportPng(opts),
    });

    const align = createAlignPane(sidebarEl, {
      onKabsch: (enabled, heavyOnly) => {
        this._sendToggleFeature('kabsch_align', enabled);
        if (enabled) this._sendSetParameter('ffast.kabsch_alignment', 'heavy_only', heavyOnly);
      },
      onAtomAlign: (enabled, indices) => {
        this._sendToggleFeature('atom_align', enabled);
        if (enabled && indices.length === 3) {
          this._sendSetParameter('ffast.atom_align', 'atom_indices', indices);
          this._sendSetParameter('ffast.atom_align', 'reference_frame', this._currentFrame());
        }
      },
    });

    this._panes = { colorBy, camera, display, bonds, forces, extract, export: exportPane, align };
  }

  _applyUrlParams() {
    const p = new URLSearchParams(window.location.search);
    const port  = p.get('port');
    const token = p.get('token');
    if (port) {
      const host = window.location.hostname || 'localhost';
      document.getElementById('ws-url').value = `ws://${host}:${port}`;
    }
    if (token) document.getElementById('token-input').value = token;

    // Live pop-out (ADR 0044 Phase 4): a second FFastApp instance opened by
    // _openPopout() when the server advertised multi_client. Hide the chrome
    // (body.loupe-only chrome) and auto-connect —
    // REMOTE_DATASET_META replay then drives _onDatasetMeta to select the
    // requested dataset and open this connection's own view.
    if (p.get('mode') === 'loupe-live') {
      document.body.classList.add('loupe-only');
      this._autoDatasetFp = p.get('ds') || null;
      this._autoModelFp = p.get('pred') || null;
      if (port) this._connect();
    }
  }

  async _connect() {
    const wsUrl = document.getElementById('ws-url').value.trim();
    const token = document.getElementById('token-input').value.trim() || null;
    // Explicit READ_ONLY viewer opt-in (ADR 0044 Phase 2, PRD story 73): drops
    // inbound control but still opens its own views and sees shared broadcasts.
    const readOnly = document.getElementById('readonly-toggle')?.checked || false;

    this._setStatus('Connecting…', '');

    try {
      const conn = new FFastConnection(wsUrl, token, readOnly);
      // Set before awaiting connect(): connect() dispatches buffered
      // handshake-time replay messages (REMOTE_DATASET_META, ...) to their
      // handlers *before* its promise resolves. A server with an
      // already-loaded dataset (e.g. a live pop-out's opener already has one
      // open) fires the auto-select-and-open-view path during that replay —
      // if this._conn were still null then, _openView()'s guard would bail
      // and the view would never open.
      this._conn = conn;

      // Metric channel + analysis-tab manager (ADR 0045 Phase 3). The metric
      // client registers the METRIC_RESULT handler; build both before connect
      // so buffered handshake-time replays reach them.
      this._metricClient = new MetricClient(conn);
      this._analysis = new AnalysisManager({
        tabbar: document.getElementById('tabbar'),
        tabpanels: document.getElementById('tabpanels'),
        metricClient: this._metricClient,
        onSelectTab: (id) => this._selectTab(id),
        onSub: (o) => this._sendDeclareSubset(o),
        onPointFrame: (ci) => this._jumpToFrame(ci),
      });
      conn.on(IN.TAB_LAYOUT, (kw) => this._analysis?.setLayout(kw.tabs || []));

      conn.on(IN.REMOTE_DATASET_META, (kw, args) => this._onDatasetMeta(args[0], kw));
      conn.on(IN.REMOTE_MODEL_META,   (kw, args) => this._onModelMeta(args[0], kw));
      conn.on(IN.DATASET_KEYS_RESPONSE, (kw, args) => this._browser.onDatasetKeys(args[0], kw));
      conn.on(IN.TASK_CREATED,  (kw) => console.debug('TASK_CREATED', kw));
      conn.on(IN.TASK_PROGRESS, (kw) => console.debug('TASK_PROGRESS', kw));
      // No per-task correlation id travels back to the browser today (Qt's
      // equivalent is a generic "Tasks" sidebar list, out of scope here), so
      // a pending save/load session is resolved by the next TASK_DONE/FAILED
      // to arrive — good enough for a single-user session (ADR 0044: shared
      // workspace, not multi-tenant) and gives issue 21 a real completion
      // signal instead of the previous silent console.debug-only handling.
      conn.on(IN.TASK_DONE,    (kw) => console.debug('TASK_DONE', kw));
      conn.on(IN.TASK_FAILED,  (kw) => console.warn('TASK_FAILED', kw));
      conn.on(IN.DATASET_LOADED, () => {});
      conn.on(IN.MODEL_LOADED,   () => {});
      conn.on(IN.SCENE_SNAPSHOT, (kw) => this._onSceneSnapshot(kw));
      conn.on(IN.SCENE_PATCH,    (kw) => this._onScenePatch(kw));
      conn.on(IN.COMMAND_RESULT, (kw) => this._onCommandResult(kw));
      conn.on(IN.DIR_LISTING,    (kw) => this._browser.onDirListing(kw));
      conn.on(IN.METRIC_CATALOG, (kw) => this._onMetricCatalog(kw));
      conn.on(IN.METRICS_UPDATED, () => this._conn?.send(OUT.REQUEST_METRIC_CATALOG, {}));
      conn.on(IN.SUBSET_EXPORTED, (kw) => this._sessionOps.onSubsetExported(kw));
      // Session outcomes are named events now, not a guess from the next
      // TASK_DONE — which resolved a pending save against an unrelated task's
      // completion (ADR 0050).
      conn.on(IN.SESSION_SAVED,  (kw) => this._sessionOps.onSessionResult('save', kw));
      conn.on(IN.SESSION_LOADED, (kw) => this._sessionOps.onSessionResult('load', kw));

      await conn.connect();

      this._setStatus(`Connected (${conn.role})`, 'connected');
      document.getElementById('connect-btn').disabled = true;
      document.getElementById('disconnect-btn').disabled = false;
      // A READ_ONLY viewer's mutating Control messages are dropped server-side
      // (ADR 0044 Phase 2) — grey out the buttons that would send one, rather
      // than let the click silently do nothing.
      const canMutate = conn.role !== 'READ_ONLY';
      document.getElementById('add-dataset-btn').disabled = !canMutate;
      document.getElementById('add-prediction-btn').disabled = !canMutate;
      document.getElementById('export-dataset-btn').disabled = !canMutate;
      document.getElementById('save-session-btn').disabled = !canMutate;
      document.getElementById('load-session-btn').disabled = !canMutate;
      this._renderObjects();
      // Fetch the analysis-tab layout (METRIC_CATALOG arrives via connect-replay).
      conn.send(OUT.REQUEST_TAB_LAYOUT, {});

    } catch (err) {
      this._conn = null;   // handshake failed — undo the early assignment above
      console.error('Connection failed:', err);
      this._setStatus(`Error: ${err.message}`, 'error');
    }
  }

  _disconnect() {
    if (this._conn) {
      this._conn.close();
      this._conn = null;
    }
    if (this._analysis) { this._analysis.clear(); this._analysis = null; }
    this._metricClient = null;
    if (this._activeTab.startsWith('analysis-')) this._selectTab('loupe');
    this._lastScene = null;
    this._datasets.clear();
    this._models.clear();
    this._currentDatasetFp = null;
    this._currentModelFp = null;
    this._currentViewId = null;
    this._lastOpenedDatasetFp = null;
    this._playing = false;
    this._pendingSessionOp = null;
    this._setActiveTool(null);   // release any armed pick tool
    this._renderObjects();
    document.getElementById('connect-btn').disabled = false;
    document.getElementById('disconnect-btn').disabled = true;
    document.getElementById('add-dataset-btn').disabled = true;
    document.getElementById('add-prediction-btn').disabled = true;
    document.getElementById('export-dataset-btn').disabled = true;
    document.getElementById('save-session-btn').disabled = true;
    document.getElementById('load-session-btn').disabled = true;
    document.getElementById('reset-camera-btn').disabled = true;
    document.getElementById('popout-btn').disabled = true;
    document.getElementById('frame-slider').disabled = true;
    for (const id of ['prev-frame-btn', 'play-pause-btn', 'next-frame-btn']) document.getElementById(id).disabled = true;
    this._browser.close();
    this._sessionOps.reset();
    this._setStatus('Disconnected', '');
  }

  _onDatasetMeta(fp, meta) {
    this._datasets.set(fp, meta);
    // The first dataset auto-selects so the Loupe has something to show. A
    // live pop-out (ADR 0044 Phase 4) overrides that with the dataset its
    // opener had open, whenever that one's metadata arrives during replay.
    const isAutoTarget = this._autoDatasetFp === fp;
    const firstSelect = !this._currentDatasetFp || isAutoTarget;
    if (firstSelect) this._currentDatasetFp = fp;
    this._renderObjects();
    if (firstSelect) this._syncAnalysisContext();
    if (firstSelect && this._conn && this._activeTab === 'loupe') this._openView();
  }

  _onModelMeta(fp, meta) {
    // meta = {name, dataset_fingerprints}. Fired on connect-replay and after
    // a LOAD_PREDICTION completes (the ghost model registers its forces cache).
    this._models.set(fp, meta || {});
    // Auto-select a freshly loaded prediction that applies to the current
    // dataset and refresh the view so its force overlay appears. A live
    // pop-out with a requested prediction (ADR 0044 Phase 4) only settles on
    // that one, so replay order among several candidates doesn't matter.
    const wantsSpecificModel = this._autoModelFp && fp !== this._autoModelFp;
    if (!wantsSpecificModel && this._currentDatasetFp &&
        (meta?.dataset_fingerprints || []).includes(this._currentDatasetFp)) {
      this._currentModelFp = fp;
      this._setStatus(`Prediction "${meta.name || fp.slice(0,8)}" ready`, 'connected');
      if (this._activeTab === 'loupe') this._openView();
      this._syncAnalysisContext();
    }
    this._panes?.forces.refreshModels();
    this._panes?.colorBy.refreshModels(this._models);
    this._renderObjects();
  }

  // ── object rail: datasets + predictions as selectable rows ──────────────
  _renderObjects() {
    this._renderDatasetList();
    this._renderModelList();
    // The analysis tabs offer their own multi-select over the same objects, so
    // they need the full lists, not just the rail's current pick.
    this._analysis?.setAvailable({ datasets: this._datasets, models: this._models });
  }

  _renderDatasetList() {
    const list = document.getElementById('dataset-list');
    list.innerHTML = '';
    if (this._datasets.size === 0) {
      list.innerHTML = '<div class="obj-empty">— none loaded —</div>';
      return;
    }
    for (const [fp, meta] of this._datasets) {
      const row = document.createElement('div');
      row.className = 'obj-row' + (fp === this._currentDatasetFp ? ' selected' : '');
      row.dataset.fp = fp;
      row.innerHTML =
        `<span class="name">${meta.name || fp.slice(0,8)}</span>` +
        `<span class="meta">${meta.n} fr</span>`;
      row.addEventListener('click', () => this._selectDataset(fp));
      list.appendChild(row);
    }
  }

  _renderModelList() {
    const list = document.getElementById('model-list');
    list.innerHTML = '';
    if (this._models.size === 0) {
      list.innerHTML = '<div class="obj-empty">— none —</div>';
      return;
    }
    const dsFp = this._currentDatasetFp;
    for (const [fp, meta] of this._models) {
      const fps = meta.dataset_fingerprints || [];
      // A prediction applies only to the dataset it was computed for.
      const applies = !dsFp || !fps.length || fps.includes(dsFp);
      const row = document.createElement('div');
      row.className = 'obj-row'
        + (fp === this._currentModelFp ? ' selected' : '')
        + (applies ? '' : ' disabled');
      row.dataset.fp = fp;
      row.innerHTML = `<span class="name">${meta.name || fp.slice(0,8)}</span>`;
      if (applies) row.addEventListener('click', () => this._selectModel(fp));
      list.appendChild(row);
    }
  }

  _selectDataset(fp) {
    this._currentDatasetFp = fp;
    // Drop a prediction that no longer applies to the selected dataset.
    const m = this._models.get(this._currentModelFp);
    const fps = m?.dataset_fingerprints || [];
    if (this._currentModelFp && fps.length && !fps.includes(fp)) this._currentModelFp = null;
    this._renderObjects();
    this._syncAnalysisContext();
    // Selecting an object drives the 3D view; an analysis tab stays put and
    // just refetches (via the context sync above).
    if (this._activeTab === 'loupe') this._openView();
    else if (!this._activeTab.startsWith('analysis-')) this._selectTab('loupe');
  }

  _selectModel(fp) {
    this._currentModelFp = fp;
    this._renderObjects();
    this._syncAnalysisContext();
    if (this._activeTab === 'loupe') this._openView();
    else if (!this._activeTab.startsWith('analysis-')) this._selectTab('loupe');
  }

  _openView() {
    if (!this._conn || !this._currentDatasetFp) return;
    const datasetChanged = this._currentDatasetFp !== this._lastOpenedDatasetFp;
    if (datasetChanged && this._lastOpenedDatasetFp) this._saveDatasetSettings(this._lastOpenedDatasetFp);

    this._currentViewId = 'view-0';
    this._conn.send(OUT.OPEN_VIEW, {
      view_id: this._currentViewId,
      dataset_ref: this._currentDatasetFp,
      prediction_ref: this._currentModelFp,   // null clears the force overlay
    });
    document.getElementById('popout-btn').disabled = false;

    if (datasetChanged) {
      this._lastOpenedDatasetFp = this._currentDatasetFp;
      this._restoreDatasetSettings(this._currentDatasetFp);
      this._clearPicks();   // picked atom ids are dataset-specific
    }
    // The Extract Subset pane is meaningless for datasets that are already
    // subsets (Qt's AtomFilterPaneHiding).
    this._panes.extract.setVisible(!(this._datasets.get(this._currentDatasetFp)?.is_sub));
  }

  // ── per-dataset settings (issues 04/07/08): Qt's Settings.markAsPerDataset,
  // reimplemented as a plain map since the web client has no such mechanism.
  // Bond/display/colour settings are intentionally NOT persisted here — Qt
  // doesn't mark those per-dataset either (they're global settings there too).
  _saveDatasetSettings(fp) {
    this._dsSettings.set(fp, {
      originCenterOfMass: this._originCenterOfMass,
      showForceVectors: this._forcesState.show,
      forceVectorsModelKey: this._forcesState.modelKey,
      videoFPS: this._videoFPS(),
      videoSkipFrames: this._videoSkipFrames(),
    });
  }

  _restoreDatasetSettings(fp) {
    const d = this._dsSettings.get(fp) || {
      originCenterOfMass: true, showForceVectors: false, forceVectorsModelKey: null,
      videoFPS: 30, videoSkipFrames: 0,
    };
    this._originCenterOfMass = d.originCenterOfMass;
    this._panes.camera.setCOM(d.originCenterOfMass);
    document.getElementById('fps-input').value = d.videoFPS;
    document.getElementById('skip-input').value = d.videoSkipFrames;

    this._forcesState = { ...this._forcesState, show: d.showForceVectors, modelKey: d.forceVectorsModelKey };
    this._panes.forces.setState(d.showForceVectors, d.forceVectorsModelKey);
    this._applyForceVectorsState(this._forcesState);
  }


  /** @param {import('./protocol.js').SceneSnapshotKwargs} kw */
  _onSceneSnapshot(kw) {
    const scene = kw.scene;
    if (!scene) return;
    this._viewVersion = scene.version;
    this._renderer.applyScene(scene);
    this._renderer.frameAtoms();
    document.getElementById('overlay').classList.add('hidden');
    document.getElementById('reset-camera-btn').disabled = false;
    for (const id of ['prev-frame-btn', 'play-pause-btn', 'next-frame-btn']) document.getElementById(id).disabled = false;

    if (scene.atoms) {
      this._panes.colorBy.setColorBy(scene.atoms.color_by || null);
      this._trackCameraCOM(scene.atoms.positions);
    }

    // Update frame slider
    if (scene.view_id) this._currentViewId = scene.view_id;
    const fp = this._getViewDataset(scene);
    if (fp) {
      const meta = this._datasets.get(fp);
      const n = meta?.n || 1;
      this._frameCount = n;
      const slider = document.getElementById('frame-slider');
      slider.max = n - 1;
      slider.value = 0;
      slider.disabled = false;
      this._updateFrameLabel(0, n);
    }

    this._lastScene = scene;
  }

  /** @param {import('./protocol.js').ScenePatchKwargs} kw */
  _onScenePatch(kw) {
    const changed = kw.changed || [];
    this._viewVersion = kw.to_version;
    this._renderer.applyPatch(kw, changed);
    if (changed.includes('atoms') && kw.atoms) {
      this._panes.colorBy.setColorBy(kw.atoms.color_by || null);
      this._trackCameraCOM(kw.atoms.positions);
    }
    this._patchPending = false;   // playback: unblock the wait in _playLoop
  }

  /** @param {import('./protocol.js').CommandResultKwargs} kw */
  _onCommandResult(kw) {
    if (!kw?.success) {
      console.warn('VIEW_COMMAND failed:', kw?.error);
      if (typeof kw?.new_version === 'number') this._viewVersion = kw.new_version;
    }
  }

  // ── scientific view commands (ADR 0014/0045 Phase 1) ────────────────────
  // Mirrors Qt's window._sendViewCommand: stamp the expected version, then
  // optimistically advance it; COMMAND_RESULT/SCENE_PATCH resync it above.
  _sendViewCommand(fields) {
    if (!this._conn || !this._currentViewId) return;
    this._conn.send(OUT.VIEW_COMMAND, { view_id: this._currentViewId, view_version: this._viewVersion, ...fields });
    this._viewVersion++;
  }

  /** @param {string} feature @param {boolean} enabled
   *  @see import('./protocol.js').ToggleFeatureCommand */
  _sendToggleFeature(feature, enabled) {
    this._sendViewCommand({ type: 'TOGGLE_FEATURE', feature, enabled: !!enabled });
  }

  /** @param {string} stageId @param {string} parameter @param {any} value
   *  @see import('./protocol.js').SetParameterCommand */
  _sendSetParameter(stageId, parameter, value) {
    this._sendViewCommand({ type: 'SET_PARAMETER', stage_id: stageId, parameter, value });
  }

  /** @param {string} name @param {string} scope @param {number[]} indices
   *  @see import('./protocol.js').SetSelectionCommand */
  _sendSetSelection(name, scope, indices) {
    this._sendViewCommand({ type: 'SET_SELECTION', name, scope, indices });
  }

  // ── picking (ADR 0045 Phase 2) ──────────────────────────────────────────
  // A pick toolbar (one button per tool) + a contextual strip (active tool,
  // pick count, radius, read-out, clear). The PickController owns the pointer
  // while a tool is armed and reports resolved atoms here.
  _initPickTools() {
    const toolbar = document.getElementById('pick-toolbar');
    for (const [id, t] of Object.entries(PICK_TOOLS)) {
      const btn = document.createElement('button');
      btn.className = 'pick-tool-btn';
      btn.dataset.tool = id;
      btn.textContent = t.icon;
      btn.title = `${t.label} pick tool`;
      btn.addEventListener('click', () => this._setActiveTool(this._activeTool === id ? null : id));
      toolbar.appendChild(btn);
    }
    document.getElementById('pick-clear').addEventListener('click', () => this._clearPicks());
    this._pickController = new PickController(
      document.getElementById('canvas'),
      document.getElementById('viewport'),
      this._renderer,
      { getRadius: () => this._pickRadius, onPick: (entries, opts) => this._onPick(entries, opts) },
    );
    this._pickReadout = '';
    this._updatePickStrip();
  }

  /** Arm a pick tool (or `null` to return to orbit); toggling the active one disarms. */
  _setActiveTool(id) {
    this._activeTool = id;
    this._clearPicks();   // switching tools starts a fresh picked set
    for (const btn of document.querySelectorAll('#pick-toolbar .pick-tool-btn'))
      btn.classList.toggle('active', btn.dataset.tool === id);
    if (id) {
      this._pickController.arm({ id, ...PICK_TOOLS[id] });
      if (id === 'align') this._panes.align.enableAtomAlignMode();
    } else if (this._pickController) {
      this._pickController.disarm();
    }
    this._updatePickStrip();
  }

  /** Toggle an entry in the picked set (Qt AtomSelectionBase.selectAtom). */
  _togglePick(entry, tool) {
    const i = this._picked.findIndex((e) => e.atomId === entry.atomId);
    if (i >= 0) this._picked.splice(i, 1);
    else this._picked.push(entry);
    if (tool.cycle && this._picked.length > tool.multiselect)
      this._picked.splice(0, this._picked.length - tool.multiselect);
  }

  /** @param {Array<{displayIndex:number, atomId:number}>} entries */
  _onPick(entries, { isBox } = {}) {
    if (!this._activeTool) return;
    const tool = PICK_TOOLS[this._activeTool];
    for (const e of entries) this._togglePick(e, tool);
    if (!tool.cycle && this._picked.length > tool.multiselect)
      this._picked.splice(0, this._picked.length - tool.multiselect);
    this._reactTool(this._activeTool);
    this._updatePickStrip();
  }

  /** Apply the active tool's effect to its accumulated picks. */
  _reactTool(id) {
    const ids = this._picked.map((e) => e.atomId);
    // Every tool shows the current picks as a selection overlay; bonds/align
    // clear the set after acting so the overlay tracks the fresh set.
    this._sendSetSelection('picked', 'current_structure', ids);
    if (id === 'info') {
      const pts = this._picked.map((e) => this._renderer.atomPosition(e.displayIndex)).filter(Boolean);
      this._pickReadout = infoReadout(pts, ids);
    } else if (id === 'extract') {
      this._panes.extract.setPickedIndices(ids);
    } else if (id === 'forces') {
      this._panes.forces.setPickedIndices(ids);
    } else if (id === 'bonds') {
      if (this._picked.length === 2) {
        this._panes.bonds.toggleBondPair(ids[0], ids[1]);
        this._picked = [];
        this._sendSetSelection('picked', 'current_structure', []);
      }
    } else if (id === 'align') {
      this._panes.align.setPickedIndices(ids);
      if (this._picked.length === 3) {
        this._panes.align.applyAtomAlign();
        this._picked = [];
        this._setActiveTool(null);   // Qt auto-disarms the align tool after 3
      }
    }
  }

  _clearPicks() {
    this._picked = [];
    this._pickReadout = '';
    this._sendSetSelection('picked', 'current_structure', []);
    this._updatePickStrip();
  }

  _updatePickStrip() {
    const strip = document.getElementById('pick-strip');
    if (!strip) return;
    if (!this._activeTool) { strip.classList.add('hidden'); return; }
    strip.classList.remove('hidden');
    document.getElementById('pick-strip-tool').textContent = PICK_TOOLS[this._activeTool].label;
    document.getElementById('pick-strip-count').textContent = `${this._picked.length} picked`;
    document.getElementById('pick-strip-radius').textContent = `radius ${this._pickRadius}px`;
    document.getElementById('pick-readout').textContent = this._pickReadout || '';
  }

  /** Extract-as-subset: ship the raw index/element tokens; the server resolves
   * them and announces the new AtomFilteredDataset via REMOTE_DATASET_META. */
  _sendCreateSubset(tokens) {
    if (!this._conn || !this._currentDatasetFp) return;
    this._conn.send(OUT.CREATE_SUBSET, {
      parent_fingerprint: this._currentDatasetFp,
      indices: tokens,
    });
    this._setStatus('Extracting subset…', 'connected');
  }

  /** Declare a frame-index SubDataset from an analysis-plot box-select
   * (ADR 0045 Phase 3 subbing). The server materialises it as a live
   * SubDataset announced via REMOTE_DATASET_META, so it appears in the object
   * rail and is usable by the 3D view and other tabs (PRD 61-62).
   * @param {{parentFp: string, modelFp: string|null, indices: number[], name: string}} o */
  _sendDeclareSubset(o) {
    if (!this._conn || !o.parentFp || !o.indices.length) return;
    this._conn.send(OUT.DECLARE_SUBSET, {
      parent_fingerprint: o.parentFp,
      indices: o.indices,
      model_fp: o.modelFp,
      name: o.name,
    });
    this._setStatus(`Sub-selecting ${o.indices.length} structure(s)…`, 'connected');
  }

  /** Jump the 3D view to a structure clicked in an analysis scatter (PRD 63). */
  _jumpToFrame(configIndex) {
    this._selectTab('loupe');
    this._setFrame(configIndex);
  }

  /** Push the current dataset/prediction selection into the analysis manager
   * so its active tab refetches against it (metric channel scope). */
  _syncAnalysisContext() {
    if (!this._analysis) return;
    const meta = this._currentDatasetFp
      ? this._datasets.get(this._currentDatasetFp) : null;
    // Series names come from the analysis manager's own object lists
    // (setAvailable), so this carries only the rail's selection — the default a
    // tab follows until it pins its own.
    this._analysis.setContext({
      datasetFp: this._currentDatasetFp,
      modelFp: this._currentModelFp,
      datasetMeta: meta,
    });
  }

  /** @param {import('./protocol.js').MetricCatalogKwargs} kw */
  _onMetricCatalog(kw) {
    this._metricCatalog = kw.metrics || [];
    this._panes.colorBy.setMetricCatalog(this._metricCatalog);
    this._analysis?.setMetricCatalog(this._metricCatalog);
  }

  /** Send TOGGLE_FEATURE("forces", ...) plus its SET_PARAMETERs together —
   * the Force Vectors pane always re-sends all four as one logical action
   * (mirrors Qt's onApplyForceVectors), so the callback and the per-dataset
   * restore path (`_restoreDatasetSettings`) share this one seam.
   * @param {{show: boolean, modelKey: string|null, length: number, normalised: boolean}} state
   */
  _applyForceVectorsState(state) {
    this._sendToggleFeature('forces', state.show);
    if (state.show) {
      this._sendSetParameter('ffast.force_arrows', 'prediction_ref', state.modelKey);
      this._sendSetParameter('ffast.force_arrows', 'length_factor', state.length);
      this._sendSetParameter('ffast.force_arrows', 'normalised', state.normalised);
      this._sendSetParameter('ffast.force_arrows', 'filter_enabled', !!state.filterEnabled);
      this._sendSetParameter('ffast.force_arrows', 'atom_indices', state.atomIndices || []);
    }
  }

  /** Origin-COM tracking (issue 04): recentre the camera on the atoms'
   * centroid as frames advance, preserving azimuth/elevation/distance. */
  _trackCameraCOM(positions) {
    if (!this._originCenterOfMass || !positions || positions.length === 0) return;
    const n = positions.length;
    const sum = [0, 0, 0];
    for (const [x, y, z] of positions) { sum[0] += x; sum[1] += y; sum[2] += z; }
    this._renderer.recenterTo([sum[0] / n, sum[1] / n, sum[2] / n]);
  }

  /** Bonds "fill from dynamic" (issue 06): the wire only ships bond segments
   * (coordinates), never atom-index pairs, so pairs are recovered by matching
   * each segment endpoint back to the last-rendered atom positions — exact
   * matches since both come from the same server-computed frame. */
  _currentDynamicBondPairs() {
    const scene = this._lastScene;
    const segs = scene?.bonds?.segments;
    const positions = scene?.atoms?.positions;
    if (!segs || !positions) return [];
    const indexByCoord = new Map();
    positions.forEach((p, i) => indexByCoord.set(p.join(','), i));
    const pairs = [];
    for (let i = 0; i < segs.length; i += 2) {
      const a = indexByCoord.get(segs[i].join(','));
      const b = indexByCoord.get(segs[i + 1].join(','));
      if (a !== undefined && b !== undefined) pairs.push([a, b]);
    }
    return pairs;
  }

  // ── playback (issue 08): prev/next/play-pause, FPS, skip-frames ─────────
  _onFrameSlider() {
    const frame = parseInt(document.getElementById('frame-slider').value, 10);
    this._updateFrameLabel(frame, this._frameCount);
    this._sendSetFrame(frame);
  }

  _sendSetFrame(frame) {
    if (!this._conn || !this._currentViewId) return;
    this._conn.send(OUT.VIEW_COMMAND, {
      type: 'SET_FRAME',
      view_id: this._currentViewId,
      view_version: 0,  // server applies SET_FRAME without version check
      frame_index: frame,
    });
  }

  _currentFrame() {
    return parseInt(document.getElementById('frame-slider').value, 10) || 0;
  }

  _setFrame(frame) {
    const slider = document.getElementById('frame-slider');
    const clamped = Math.max(0, Math.min(this._frameCount - 1, frame));
    slider.value = clamped;
    this._updateFrameLabel(clamped, this._frameCount);
    this._sendSetFrame(clamped);
    return clamped;
  }

  _videoFPS() {
    return Math.max(1, parseInt(document.getElementById('fps-input').value, 10) || 30);
  }

  _videoSkipFrames() {
    return parseInt(document.getElementById('skip-input').value, 10) || 0;
  }

  _stepFrame(direction) {
    if (this._playing) this._togglePlayback();  // manual stepping stops playback
    this._setFrame(this._currentFrame() + direction * (1 + this._videoSkipFrames()));
  }

  _togglePlayback() {
    this._playing = !this._playing;
    document.getElementById('play-pause-btn').textContent = this._playing ? '⏸' : '▶';
    if (this._playing) this._playLoop();
  }

  async _playLoop() {
    while (this._playing) {
      const frameStart = performance.now();
      const next = this._currentFrame() + 1 + this._videoSkipFrames();
      if (next > this._frameCount - 1) { this._togglePlayback(); break; }
      this._patchPending = true;
      this._setFrame(next);
      // Response-synced (mirrors Qt's runOnNext): wait for the server's
      // SCENE_PATCH before advancing again, capped so a slow/dropped reply
      // can't stall playback forever.
      const deadline = performance.now() + 500;
      while (this._patchPending && this._playing && performance.now() < deadline) {
        await new Promise((r) => setTimeout(r, 10));
      }
      const elapsed = performance.now() - frameStart;
      const remaining = (1000 / this._videoFPS()) - elapsed;
      if (remaining > 0) await new Promise((r) => setTimeout(r, remaining));
    }
  }

  // ── popped-out loupe ────────────────────────────────────────────────────
  _openPopout() {
    if (!this._currentDatasetFp) return;

    // ADR 0044 Phase 4: the pop-out is its OWN live controller connection —
    // its own state replay, view, and frame/camera control, sharing the
    // fingerprint-keyed cache with this tab. The ADR 0043 BroadcastChannel
    // mirror it replaced is deleted (ADR 0051): negotiate() advertises
    // multi_client unconditionally, and the client is served by the same
    // server process, so the single-client fallback was unreachable.
    const url = new URL(window.location.href);
    const wsMatch = /:(\d+)\/?$/.exec(document.getElementById('ws-url').value.trim());
    if (wsMatch) url.searchParams.set('port', wsMatch[1]);
    const token = document.getElementById('token-input').value.trim();
    if (token) url.searchParams.set('token', token);
    else url.searchParams.delete('token');
    url.searchParams.set('mode', 'loupe-live');
    url.searchParams.set('ds', this._currentDatasetFp);
    if (this._currentModelFp) url.searchParams.set('pred', this._currentModelFp);
    else url.searchParams.delete('pred');
    window.open(url.toString(), '_blank', 'noopener');
  }

  _sendSetCamera(cam) {
    if (!this._conn || !this._currentViewId) return;
    clearTimeout(this._cameraThrottle);
    this._cameraThrottle = setTimeout(() => {
      this._conn.send(OUT.VIEW_COMMAND, {
        type: 'SET_CAMERA',
        view_id: this._currentViewId,
        camera: cam,
      });
    }, 100);
  }

  _updateFrameLabel(frame, total) {
    document.getElementById('frame-label').textContent = `${frame} / ${Math.max(0, total - 1)}`;
  }

  _getViewDataset(scene) {
    // The scene belongs to the currently selected dataset (object rail).
    return this._currentDatasetFp;
  }

  _setStatus(text, cls) {
    const el = document.getElementById('status');
    el.textContent = text;
    el.className = cls;
  }
}

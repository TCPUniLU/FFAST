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
    this._frameCount = 0;
    this._cameraThrottle = null;
    this._bc = null;          // BroadcastChannel to popped-out loupe tabs
    this._chId = null;
    this._lastScene = null;   // cached snapshot for a late-joining satellite

    // Remote file browser state
    this._fbMode = 'dataset';  // 'dataset' | 'prediction'
    this._fbPath = null;       // current directory abspath (server-side)
    this._fbParent = null;     // parent abspath, or null at root
    this._fbHome = null;       // server user's home directory
    this._fbSelected = null;   // selected filename within _fbPath

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
    this._pendingSessionOp = null;   // {kind: 'save'|'load', path: string} | null

    // Live pop-out controller (ADR 0044 Phase 4): when opened with
    // ?mode=loupe-live this instance auto-connects, hides the chrome
    // (body.loupe-only, shared with the BroadcastChannel satellite), and
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
    document.getElementById('add-dataset-btn').addEventListener('click', () => this._openFileBrowser('dataset'));
    document.getElementById('add-prediction-btn').addEventListener('click', () => this._openFileBrowser('prediction'));
    document.getElementById('export-dataset-btn').addEventListener('click', () => this._exportSelectedDataset());

    // Session save/load (issue 21) + subset export (issue 20) reuse one path
    // prompt — the browser has no native server-side save dialog.
    document.getElementById('save-session-btn').addEventListener('click', () => this._saveSession());
    document.getElementById('load-session-btn').addEventListener('click', () => this._loadSession());
    document.getElementById('path-cancel').addEventListener('click', () => this._closePathModal());
    document.getElementById('path-ok').addEventListener('click', () => this._confirmPathModal());
    document.getElementById('path-input').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') this._confirmPathModal();
      else if (e.key === 'Escape') this._closePathModal();
    });
    document.getElementById('path-modal').addEventListener('click', (e) => {
      if (e.target.id === 'path-modal') this._closePathModal();
    });
    document.getElementById('fb-cancel').addEventListener('click', () => this._closeFileBrowser());
    document.getElementById('fb-load').addEventListener('click', () => this._fbLoad());
    document.getElementById('fb-force-key').addEventListener('change', () => this._updateFbLoadEnabled());
    document.getElementById('fb-up').addEventListener('click', () => {
      if (this._fbParent) this._fbNavigate(this._fbParent);
    });
    document.getElementById('fb-home').addEventListener('click', () => {
      this._fbNavigate(this._fbHome || null);
    });
    document.getElementById('fb-modal').addEventListener('click', (e) => {
      if (e.target.id === 'fb-modal') this._closeFileBrowser();
    });
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
      onExport: (opts) => this._exportPng(opts),
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
    // (same body.loupe-only CSS the satellite mirror uses) and auto-connect —
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
      conn.on('TAB_LAYOUT', (kw) => this._analysis?.setLayout(kw.tabs || []));

      conn.on('REMOTE_DATASET_META', (kw, args) => this._onDatasetMeta(args[0], kw));
      conn.on('REMOTE_MODEL_META',   (kw, args) => this._onModelMeta(args[0], kw));
      conn.on('DATASET_KEYS_RESPONSE', (kw, args) => this._onDatasetKeys(args[0], kw));
      conn.on('TASK_CREATED',  (kw) => console.debug('TASK_CREATED', kw));
      conn.on('TASK_PROGRESS', (kw) => console.debug('TASK_PROGRESS', kw));
      // No per-task correlation id travels back to the browser today (Qt's
      // equivalent is a generic "Tasks" sidebar list, out of scope here), so
      // a pending save/load session is resolved by the next TASK_DONE/FAILED
      // to arrive — good enough for a single-user session (ADR 0044: shared
      // workspace, not multi-tenant) and gives issue 21 a real completion
      // signal instead of the previous silent console.debug-only handling.
      conn.on('TASK_DONE',     (kw) => { console.debug('TASK_DONE', kw); this._resolveSessionOp(true); });
      conn.on('TASK_FAILED',   (kw) => { console.warn('TASK_FAILED', kw); this._resolveSessionOp(false); });
      conn.on('DATASET_LOADED', () => {});
      conn.on('MODEL_LOADED',   () => {});
      conn.on('SCENE_SNAPSHOT', (kw) => this._onSceneSnapshot(kw));
      conn.on('SCENE_PATCH',    (kw) => this._onScenePatch(kw));
      conn.on('COMMAND_RESULT', (kw) => this._onCommandResult(kw));
      conn.on('DIR_LISTING',    (kw) => this._onDirListing(kw));
      conn.on('METRIC_CATALOG', (kw) => this._onMetricCatalog(kw));
      conn.on('METRICS_UPDATED', () => this._conn?.send('REQUEST_METRIC_CATALOG', {}));
      conn.on('SUBSET_EXPORTED', (kw) => this._onSubsetExported(kw));

      await conn.connect();
      this._setupBroadcast(wsUrl);

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
      conn.send('REQUEST_TAB_LAYOUT', {});

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
    if (this._bc) {
      this._bc.postMessage({ t: 'bye' });   // tell popped-out loupes the main view is gone
      this._bc.close();
      this._bc = null;
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
    this._closeFileBrowser();
    this._closePathModal();
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
    this._conn.send('OPEN_VIEW', {
      view_id: this._currentViewId,
      dataset_ref: this._currentDatasetFp,
      prediction_ref: this._currentModelFp,   // null clears the force overlay
    });
    if (this._bc) document.getElementById('popout-btn').disabled = false;

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

  // ── remote file browser ────────────────────────────────────────────────

  _openFileBrowser(mode = 'dataset') {
    if (!this._conn) return;
    this._fbMode = mode;
    const isPred = mode === 'prediction';
    document.getElementById('fb-title').textContent = isPred ? 'Load Prediction' : 'Load Remote Dataset';
    document.getElementById('fb-dataset-fields').style.display = isPred ? 'none' : '';
    document.getElementById('fb-prediction-fields').style.display = isPred ? 'inline-flex' : 'none';
    if (isPred) this._populatePredictionTargets();
    document.getElementById('fb-modal').classList.remove('hidden');
    this._fbSelected = null;
    document.getElementById('fb-load').disabled = true;
    // null path → server starts at its home directory
    this._fbNavigate(this._fbPath || null);
  }

  _populatePredictionTargets() {
    // A prediction is loaded *against* an already-loaded dataset.
    const sel = document.getElementById('fb-target-ds');
    sel.innerHTML = '';
    for (const [fp, meta] of this._datasets) {
      const opt = document.createElement('option');
      opt.value = fp;
      opt.textContent = `${meta.name || fp.slice(0,8)} (${meta.n} frames)`;
      sel.appendChild(opt);
    }
    const dsFp = this._currentDatasetFp;
    if (dsFp && this._datasets.has(dsFp)) sel.value = dsFp;
    document.getElementById('fb-energy-key').innerHTML = '';
    document.getElementById('fb-force-key').innerHTML = '';
  }

  _closeFileBrowser() {
    document.getElementById('fb-modal').classList.add('hidden');
  }

  _fbNavigate(path) {
    if (!this._conn) return;
    this._fbSelected = null;
    document.getElementById('fb-load').disabled = true;
    // path travels as a positional arg; server reads args[0]
    this._conn.send('LIST_DIR', {}, [path]);
  }

  _onDirListing(kw) {
    if (kw.error) {
      const err = document.getElementById('fb-error');
      err.style.display = 'block';
      err.textContent = kw.error;
      document.getElementById('fb-list').innerHTML = '';
      // keep the previous path so ↑ still works
      document.getElementById('fb-path').value = kw.path || '';
      return;
    }
    this._fbPath = kw.path;
    this._fbParent = kw.parent;
    if (kw.home) this._fbHome = kw.home;
    document.getElementById('fb-error').style.display = 'none';
    document.getElementById('fb-path').value = kw.path || '';
    document.getElementById('fb-up').disabled = !kw.parent;
    this._fbRender(kw.entries || []);
  }

  _fbRender(entries) {
    const list = document.getElementById('fb-list');
    list.innerHTML = '';
    for (const e of entries) {
      const row = document.createElement('div');
      row.className = `fb-row ${e.is_dir ? 'dir' : 'file'}`;
      const icon = document.createElement('span');
      icon.className = 'icon';
      icon.textContent = e.is_dir ? '📁' : '📄';
      const name = document.createElement('span');
      name.className = 'name';
      name.textContent = e.name;
      row.append(icon, name);
      if (!e.is_dir) {
        const size = document.createElement('span');
        size.className = 'size';
        size.textContent = this._fmtSize(e.size);
        row.append(size);
      }
      if (e.is_dir) {
        row.addEventListener('click', () => this._fbNavigate(this._fbJoin(this._fbPath, e.name)));
      } else {
        row.addEventListener('click', () => this._fbSelectFile(row, e.name));
        row.addEventListener('dblclick', () => {
          this._fbSelectFile(row, e.name);
          if (!document.getElementById('fb-load').disabled) this._fbLoad();
        });
      }
      list.appendChild(row);
    }
  }

  _fbSelectFile(row, name) {
    this._fbSelected = name;
    for (const r of document.querySelectorAll('#fb-list .fb-row.selected')) r.classList.remove('selected');
    row.classList.add('selected');
    if (this._fbMode === 'prediction') {
      // Probe the chosen file for the energy/force keys it actually contains.
      const path = this._fbJoin(this._fbPath, name);
      document.getElementById('fb-energy-key').innerHTML = '<option value="">…probing…</option>';
      document.getElementById('fb-force-key').innerHTML = '<option value="">…probing…</option>';
      // Server route requires (path, dataset_type); ASE auto-detect reads the keys.
      this._conn.send('PROBE_DATASET_KEYS', {}, [path, 'ase (auto)']);
    }
    this._updateFbLoadEnabled();
  }

  _onDatasetKeys(path, kw) {
    if (this._fbMode !== 'prediction') return;
    // ASE's extxyz reader routes the standard `energy=`/`forces` columns into a
    // SinglePointCalculator, not atoms.info/.arrays — so a plain MACE/DFT dump
    // has empty key lists but has_calculator_*=true. Offer a "calculator"
    // option whose value is the literal 'energy'/'forces' the loader already
    // maps to the calculator (modules/loaders/aseDataset.py), so no named keys
    // are needed to load such a prediction.
    const fillKeys = (sel, keys, { allowNone = false, calc = false, calcValue }) => {
      sel.innerHTML = '';
      if (allowNone) {
        const o = document.createElement('option');
        o.value = ''; o.textContent = '— none —';
        sel.appendChild(o);
      }
      if (calc) {
        const o = document.createElement('option');
        o.value = calcValue; o.textContent = 'calculator (built-in)';
        sel.appendChild(o);
      }
      for (const k of (keys || [])) {
        const o = document.createElement('option');
        o.value = k; o.textContent = k;
        sel.appendChild(o);
      }
    };
    fillKeys(document.getElementById('fb-energy-key'), kw.energy_keys,
             { allowNone: true, calc: kw.has_calculator_energy, calcValue: 'energy' });
    fillKeys(document.getElementById('fb-force-key'), kw.force_keys,
             { calc: kw.has_calculator_forces, calcValue: 'forces' });  // force required for arrows
    if (kw.error) this._setStatus(`Probe error: ${kw.error}`, 'error');
    this._updateFbLoadEnabled();
  }

  _updateFbLoadEnabled() {
    const btn = document.getElementById('fb-load');
    if (!this._fbSelected) { btn.disabled = true; return; }
    if (this._fbMode === 'prediction') {
      const fKey = document.getElementById('fb-force-key').value;
      const tgt = document.getElementById('fb-target-ds').value;
      btn.disabled = !(fKey && tgt);
    } else {
      btn.disabled = false;
    }
  }

  _fbLoad() {
    if (!this._conn || !this._fbSelected) return;
    const path = this._fbJoin(this._fbPath, this._fbSelected);
    if (this._fbMode === 'prediction') {
      const dsFp = document.getElementById('fb-target-ds').value;
      const eKey = document.getElementById('fb-energy-key').value || null;
      const fKey = document.getElementById('fb-force-key').value || null;
      if (!dsFp || !fKey) return;
      // LOAD_PREDICTION reads args=[path, dataset_fp] + key kwargs; on success
      // the server fires REMOTE_MODEL_META → _onModelMeta selects it.
      this._conn.send('LOAD_PREDICTION',
        { selected_energy_key: eKey, selected_force_key: fKey },
        [path, dsFp]);
      this._setStatus(`Loading prediction ${this._fbSelected}…`, 'connected');
    } else {
      const typ = document.getElementById('fb-type').value;
      // LOAD_DATASET reads args=[path, datasetType]; "ase (auto)" auto-detects keys
      this._conn.send('LOAD_DATASET', {}, [path, typ]);
      this._setStatus(`Loading ${this._fbSelected}…`, 'connected');
    }
    this._closeFileBrowser();
  }

  _fbJoin(dir, name) {
    if (!dir) return name;
    return dir.endsWith('/') ? dir + name : dir + '/' + name;
  }

  _fmtSize(bytes) {
    if (!bytes) return '';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0, n = bytes;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
  }

  // ── server-side path prompt (issues 20/21): session save/load and subset
  // export all write on the server, which has no native file dialog reachable
  // from the browser, so one small modal collects a path and dispatches to
  // whichever action armed it. ────────────────────────────────────────────
  _openPathModal({ title, okLabel, defaultValue = '', onConfirm }) {
    this._pathModalConfirm = onConfirm;
    document.getElementById('path-title').textContent = title;
    document.getElementById('path-ok').textContent = okLabel;
    document.getElementById('path-error').style.display = 'none';
    const input = document.getElementById('path-input');
    input.value = defaultValue;
    document.getElementById('path-modal').classList.remove('hidden');
    input.focus();
    input.select();
  }

  _closePathModal() {
    document.getElementById('path-modal').classList.add('hidden');
    this._pathModalConfirm = null;
  }

  _confirmPathModal() {
    const path = document.getElementById('path-input').value.trim();
    if (!path) {
      const err = document.getElementById('path-error');
      err.textContent = 'Enter a path first';
      err.style.display = 'block';
      return;
    }
    const onConfirm = this._pathModalConfirm;
    this._closePathModal();
    onConfirm?.(path);
  }

  // ── save / load session (ADR 0045 issue 21) ─────────────────────────────
  // Reuses the existing SAVE_SESSION/LOAD_SESSION control messages (Stage 5,
  // client/environment.py requestSessionSave/Load) — session state is owned
  // and written server-side exactly as the Qt client already does; this only
  // adds the browser-side path prompt Qt's native save dialog provided.
  _saveSession() {
    if (!this._conn) return;
    this._openPathModal({
      title: 'Save Session',
      okLabel: 'Save',
      defaultValue: '~/ffast-session',
      onConfirm: (path) => {
        this._conn.send('SAVE_SESSION', { path });
        this._pendingSessionOp = { kind: 'save', path };
        this._setStatus(`Saving session to ${path}…`, 'connected');
      },
    });
  }

  _loadSession() {
    if (!this._conn) return;
    this._openPathModal({
      title: 'Load Session',
      okLabel: 'Load',
      defaultValue: '~/ffast-session',
      onConfirm: (path) => {
        this._conn.send('LOAD_SESSION', { path });
        this._pendingSessionOp = { kind: 'load', path };
        this._setStatus(`Loading session from ${path}…`, 'connected');
      },
    });
  }

  /** @param {boolean} ok */
  _resolveSessionOp(ok) {
    const op = this._pendingSessionOp;
    if (!op) return;   // this task wasn't a save/load (e.g. a dataset load)
    this._pendingSessionOp = null;
    const verb = op.kind === 'save' ? 'Saved' : 'Loaded';
    if (ok) this._setStatus(`${verb} session ${op.kind === 'save' ? 'to' : 'from'} ${op.path}`, 'connected');
    else this._setStatus(`Session ${op.kind} failed for ${op.path}`, 'error');
  }

  // ── PNG export (issue 19) ────────────────────────────────────────────────
  /** @param {{transparent: boolean, background: string}} opts */
  _exportPng({ transparent, background }) {
    const url = this._renderer.capturePng({ transparent, background });
    const name = (this._datasets.get(this._currentDatasetFp)?.name || 'ffast')
      .replace(/[^\w.-]+/g, '_');
    const a = document.createElement('a');
    a.href = url;
    a.download = `${name}_${transparent ? 'transparent' : 'opaque'}.png`;
    a.click();
    this._setStatus('PNG exported', 'connected');
  }

  // ── subset export to extxyz (issue 20) ──────────────────────────────────
  // The currently-selected object-rail dataset — a pick-derived
  // AtomFilteredDataset, a plot-derived SubDataset, or any loaded dataset —
  // is written server-side; EXPORT_SUBSET/SUBSET_EXPORTED report the result.
  _exportSelectedDataset() {
    if (!this._conn || !this._currentDatasetFp) return;
    const meta = this._datasets.get(this._currentDatasetFp);
    const base = (meta?.name || 'subset').replace(/[^\w.-]+/g, '_');
    this._openPathModal({
      title: 'Export Subset (extxyz)',
      okLabel: 'Export',
      defaultValue: `~/${base}.extxyz`,
      onConfirm: (path) => {
        this._conn.send('EXPORT_SUBSET', { fingerprint: this._currentDatasetFp, path });
        this._setStatus(`Exporting to ${path}…`, 'connected');
      },
    });
  }

  _onSubsetExported(kw) {
    if (kw.ok) this._setStatus(`Exported ${kw.n} structure(s) → ${kw.path}`, 'connected');
    else this._setStatus(`Export failed: ${kw.error}`, 'error');
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

    // Mirror to any popped-out loupe tab.
    this._lastScene = scene;
    this._broadcastScene();
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
    if (this._bc) this._bc.postMessage({ t: 'patch', patch: kw, changed });
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
    this._conn.send('VIEW_COMMAND', { view_id: this._currentViewId, view_version: this._viewVersion, ...fields });
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
    this._conn.send('CREATE_SUBSET', {
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
    this._conn.send('DECLARE_SUBSET', {
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
    this._analysis.setContext({
      datasetFp: this._currentDatasetFp,
      modelFp: this._currentModelFp,
      datasetMeta: meta,
      seriesName: (meta && meta.name)
        || (this._currentDatasetFp ? this._currentDatasetFp.slice(0, 8) : ''),
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
    this._broadcastMeta();  // keep any popped-out loupe's slider in step
  }

  _sendSetFrame(frame) {
    if (!this._conn || !this._currentViewId) return;
    this._conn.send('VIEW_COMMAND', {
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
    this._broadcastMeta();
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

  // ── popped-out loupe (BroadcastChannel satellite) ───────────────────────
  // The main tab owns the single WebSocket; a popped tab renders scenes we
  // relay here and posts frame intents back, which we drive over the WS.
  _setupBroadcast(wsUrl) {
    if (typeof BroadcastChannel === 'undefined') return;  // unsupported browser
    this._chId = 'ffast-loupe:' + wsUrl;
    this._bc = new BroadcastChannel(this._chId);
    this._bc.onmessage = (e) => this._onBroadcast(e.data);
  }

  _broadcastScene() {
    if (!this._bc || !this._lastScene) return;
    this._bc.postMessage({ t: 'scene', scene: this._lastScene });
    this._broadcastMeta();
  }

  _broadcastMeta() {
    if (!this._bc) return;
    const slider = document.getElementById('frame-slider');
    this._bc.postMessage({
      t: 'meta',
      frameCount: this._frameCount,
      frameIndex: parseInt(slider.value, 10) || 0,
      title: this._datasets.get(this._currentDatasetFp)?.name || '',
    });
  }

  _onBroadcast(msg) {
    if (!msg || !msg.t) return;
    if (msg.t === 'hello') {
      // A satellite loupe just opened — hand it the current scene + meta.
      this._broadcastScene();
    } else if (msg.t === 'frame') {
      // Satellite drove the frame; reflect it here and over the WS. Do NOT
      // re-broadcast meta (the satellite's own slider is already there).
      const slider = document.getElementById('frame-slider');
      slider.value = msg.index;
      this._updateFrameLabel(msg.index, this._frameCount);
      this._sendSetFrame(msg.index);
    }
  }

  _openPopout() {
    if (!this._currentDatasetFp) return;
    const url = new URL(window.location.href);

    // ADR 0044 Phase 4: when the server advertised multi-client support
    // (HELLO_ACK features), open the pop-out as its OWN live controller
    // connection instead of a same-tab-only BroadcastChannel mirror — it
    // gets its own state replay, view, and frame/camera control, and shares
    // the fingerprint-keyed cache with this tab. Older, single-client
    // servers fall back to the satellite mirror (ADR 0043).
    if (this._conn?.multiClient) {
      const wsMatch = /:(\d+)\/?$/.exec(document.getElementById('ws-url').value.trim());
      if (wsMatch) url.searchParams.set('port', wsMatch[1]);
      const token = document.getElementById('token-input').value.trim();
      if (token) url.searchParams.set('token', token);
      else url.searchParams.delete('token');
      url.searchParams.set('mode', 'loupe-live');
      url.searchParams.set('ds', this._currentDatasetFp);
      if (this._currentModelFp) url.searchParams.set('pred', this._currentModelFp);
      else url.searchParams.delete('pred');
      url.searchParams.delete('ch');
    } else {
      if (!this._chId) return;
      url.searchParams.set('mode', 'loupe');
      url.searchParams.set('ch', this._chId);
      url.searchParams.delete('ds');
      url.searchParams.delete('pred');
    }
    window.open(url.toString(), '_blank', 'noopener');
    // Satellite mode: the new tab sends 'hello' on load; _onBroadcast replies
    // with the scene. Live mode: the new tab connects and replays for itself.
  }

  _sendSetCamera(cam) {
    if (!this._conn || !this._currentViewId) return;
    clearTimeout(this._cameraThrottle);
    this._cameraThrottle = setTimeout(() => {
      this._conn.send('VIEW_COMMAND', {
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

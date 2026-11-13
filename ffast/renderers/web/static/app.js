/**
 * Application — wires UI, connection, and renderer together.
 */

import { FFastConnection } from './connection.js';
import { answerOf } from './requests.js';
import { Reconnector } from './reconnect.js';
import { Panel3D, ViewRenderers } from './views3d.js';
import { MainViews } from './views.js';
import { defaultSettings, startSettings } from './view_settings.js';
import { IndependentPanels, independentKey } from './independent_panels.js';
import { withColourBar } from './colorbar_overlay.js';
import {
  ensureMainView, isLinked3D, MAIN_VIEW_CHOICE, MAIN_VIEW_RULE, movedBefore, panelChoices, showsMainView,
  steppedOrder, whyPanelStays,
} from './tab_rules.js';
import { nearestStepFrame, sameConfiguration, predictionApplies } from './frame_links.js';
import { infoReadout } from './measure.js';
import { elementSymbol } from './panels.js';
import { toolHint, toolHintKey } from './tool_hints.js';
import { MetricClient } from './metrics.js';
import { AnalysisManager, framesKey } from './analysis.js';
import { createColorByPane } from './panes/colorby.js';
import { createCameraPane } from './panes/camera.js';
import { createDisplayPane } from './panes/display.js';
import { createBondsPane } from './panes/bonds.js';
import { createForcesPane, noForcesReason } from './panes/forces.js';
import { createExtractPane } from './panes/extract.js';
import { createAlignPane } from './panes/align.js';
import { createExportPane } from './panes/export.js';
import { IN, OUT } from './events.js';
import { LiveUi } from './live_ui.js';
import { openScoreDialog } from './score_dialog.js';
import { explainsIndex, scoredIncomplete } from './live_run.js';
import { Rail, framesText, nameProblem } from './rail.js';
import { RemoteBrowser } from './remote_browser.js';
import { SessionOps } from './session_ops.js';
import { TabOps } from './tab_ops.js';
import { metricPanel, plotTarget, setPanelView, setPanelLabels, toFineGrid, toSaved } from './tab_edit.js';
import { MetricOps } from './metric_ops.js';
import { TABLE_SHAPE, sessionMetricsQuestion } from './metric_logic.js';
import { createMetricsPane } from './panes/metrics.js';
import { askDialog, askText, infoDialog, textDialog, trapFocus } from './dialogs.js';
import { objectInfo } from './object_info.js';
import { TaskList } from './tasks.js';
import { ClusterClient, launchId } from './cluster_client.js';
import { setTimingSink } from './timing.js';
import { ClusterDialog } from './cluster_dialog.js';
import { FILE_SHORTCUTS, isMac, matchShortcut, shortcutLabel } from './shortcuts.js';
import { showWarning } from './toasts.js';
import { startTour } from './tour_runner.js';
import { buildTours } from './tours.js';
import { applyStaticIcons, icon } from './icons.js';
import { initBell } from './notification_bell.js';
import { initFullTextTitles } from './hover_tip.js';
import { initAppearance } from './appearance.js';
import { initLetterFont } from './letter_font.js';
import { datasetUnits } from './units.js';
import { TabEditor } from './edit_mode.js';
import { createPalette } from './palette.js';
import { bindMenu, runAction, sharedMenu, whyUnavailable } from './actions.js';
import { loadLayout, loadMainView, saveLayout, saveMainView } from './layout_state.js';
import { applyStyle, forceErrorStyle, PUBLICATION_STYLE, RESET_STYLE } from './quick_styles.js';
import { DEFAULT_LOOK, startCommands, startFromCurrent } from './start_settings.js';
import { SECTION_HELP } from './help.js';
import { addSectionHelp, bindSidebarSearch, checkboxRow, oneSectionOpen, selectRow } from './sidebar.js';
import { makeResizable } from './resizers.js';
import { ObjectColours, seriesLooks } from './object_colours.js';
import { plotTheme } from './theme.js';
import { loadRecentServers, rememberServer, saveRecentServers } from './recent_servers.js';
import { pageCodeFingerprint, serverCodeNotice } from './code_fingerprint.js';

/** What a tab in Edit mode locks (see _syncEditLock): all that is not the layout. The
 * dataset rail, the pick tools and the 3D Settings sidebar, the Metrics drawer,
 * Screen / Journal and Density, every tab's controls row (data selector, Pause, ...) and
 * every panel's contents, so the panel frames being arranged stand out (the edit chrome is
 * a sibling of the panels, not inside them). Left: the tab bar, the frame bar, and the
 * run safety controls: the Run panel holds Stop on alarm, so it is not greyed. */
const EDIT_LOCKED = '#objectbar, #pick-toolbar, #view-sidebar, #metrics-btn, #metrics-drawer, '
  + '#view-mode, #density, .analysis-controls, .analysis-panel:not([data-part="status"])';

/**
 * The five pick tools (ADR 0045 Phase 2). Each tool sets how it picks: `multiselect` caps the picked set, `cycle` makes it a rolling
 * window, `rectangle` enables box-select drag.
 * @type {Object<string,{label:string, icon:string, multiselect:number, cycle?:boolean, rectangle?:boolean, section?:string, tip?:string}>}
 */
const PICK_TOOLS = {
  // `section`: the sidebar section arming the tool opens (ADR 0055).
  // `tip`: what the tool does, on its button.
  info:    { label: 'Info',    icon: 'info', multiselect: 4,     cycle: true,
    tip: 'Click atoms to read a position, distance, angle or dihedral' },
  bonds:   { label: 'Bonds',   icon: 'bond', multiselect: 2, section: 'Bonds',
    tip: 'Click two atoms to add or remove a bond between them' },
  align:   { label: 'Align',   icon: 'align', multiselect: 3, section: 'Alignment',
    tip: 'Click three atoms to align every frame on them' },
  forces:  { label: 'Force',   icon: 'force', multiselect: 10000, rectangle: true, section: 'Force Vectors',
    tip: 'Shows force arrows; click atoms or drag a box to keep them on those atoms only' },
  extract: { label: 'Extract', icon: 'scissors', multiselect: 10000, rectangle: true, section: 'Extract Subset',
    tip: 'Pick atoms to make a new dataset with only those atoms' },
};

/** Numbered next steps for the hint bar, by situation (ADR 0055). */
const HINTS = {
  'no-prediction': [
    ['Load a prediction: ', 'File ▸ Load Prediction…'],
    ['Arm a pick tool and click atoms', ''],
    ['Find any setting with the search box', ''],
  ],
  'with-prediction': [
    ['Quick style ', 'Force error', ' shows where the model is wrong'],
    ['Arm a pick tool and click atoms', ''],
    ['Find any setting with the search box', ''],
  ],
};

/** Same length and the same items in order; both missing counts as equal. */
function sameItems(a, b) {
  const x = a || [], y = b || [];
  return x.length === y.length && x.every((v, i) => v === y[i]);
}


/** The delete dialog's checkbox: how many metric results go, and where they are. */
export function storedResultsLabel({ count, memory_bytes: memory = 0, disk_bytes: disk = 0 }) {
  const size = (n) => (n >= 1 << 30 ? `${(n / (1 << 30)).toFixed(1)} GB`
    : n >= 1 << 20 ? `${(n / (1 << 20)).toFixed(1)} MB`
      : `${Math.max(1, Math.round(n / 1024))} KB`);
  const where = [disk ? `${size(disk)} on disk` : '', memory ? `${size(memory)} in memory` : '']
    .filter(Boolean).join(', ');
  return `Also delete its ${count} computed metric result${count === 1 ? '' : 's'}`
    + (where ? ` (${where})` : '');
}

export class FFastApp {
  constructor() {
    applyStaticIcons();
    initBell();
    initFullTextTitles();
    initAppearance(() => this._analysis?.rerenderAll());
    initLetterFont();
    this._conn = null;
    /** @type {LiveUi|null} The live-run controls, made on first use by _live(). */
    this._liveUi = null;
    // The main view (ADR 0056): the one visualization view the rail drives,
    // drawn by every linked 3D panel. Its renderers come and go with the
    // tabs; this keeps its scene, settings and camera together.
    this._mainView = null;
    /** @type {Map<HTMLElement, Panel3D>} 3D grid cell → its panel */
    this._panels3d = new Map();
    /** The focused 3D panel of the tab on screen: the one its 3D controls
     * act on (ADR 0056 rule 8), or null in a tab without 3D panels. */
    this._activePanel = null;
    /** Name of the tab with a linked 3D panel shown last (rule 5). */
    this._recentMainViewTab = null;
    /** Independent 3D panels' views (ADR 0056 rule 2); a layout rebuild that
     * keeps a panel keeps its view, data and settings. */
    this._independent = new IndependentPanels({
      send: (event, kwargs) => this._conn?.send(event, kwargs),
      connected: () => !!this._conn,
      datasets: () => this._datasets,
      main: () => ({ datasetFp: this._currentDatasetFp, modelFp: this._currentModelFp, frame: this._main.current?.frame ?? 0 }),
      liveRun: (fp) => this._analysis?.liveRunOf(fp) ?? null,
      pausedFrameCount: (fp) => this._analysis?.pausedFrameCount(fp) ?? null,
      mainView: () => this._mainView,
      sendCamera: (view, cam) => this._sendSetCamera(view, cam),
      sendMainCamera: (cam) => this._sendSetCamera(this._main.current, cam),
      focused: () => this._focusedIndependent,
      activeTab: () => this._analysis?.tab(this._activeTab),
      tabPairs: (tabId) => this._analysis.tabPairs(tabId),
      predictionFor: (fp) => this._predictionFor(fp),
      placePanel: (el, panel) => this._panels3d.set(el, panel),
      start: (view) => this._startIndependent(view),
      retired: (view) => {
        if (view === this._focusedIndependent) this._switchFocusedView(null);
      },
      chosen: () => { this._syncPanelSection(); this._syncSidebarTitle(); this._syncMetricsContext(); },
      focusedCamera: (cam) => this._panes?.camera.syncFromCamera(cam),
      sameStepOwns: (view) => this._sameStepOwns(view),
      sameStepOn: () => this._analysis?.sameStepOn(this._activeTab) ?? false,
      frameShown: (view) => {
        this._syncSameStep({ view, datasetFp: view.datasetFp, frame: view.frame ?? 0 });
        if (this._playbackView() === view) this._syncPlayback();
        if (view === this._focused()) this._reloadForcePicks();
        this._liveUi?.sync();   // attribution may explain this panel's frame
      },
      sceneAtoms: (view, atoms) => {
        if (this._focusedIndependent === view) this._panes.colorBy.setColorBy(atoms.color_by || null);
        this._trackCameraCOM(atoms.positions, view);
      },
      caption: (panel) => this._syncCaption(panel),
    });
    /** Whose settings the sidebar shows and acts on: the focused independent
     * panel's view, or null for the main view (rule 8). */
    this._focusedIndependent = null;
    this._datasets = new Map();   // fingerprint → meta
    this._models = new Map();     // model fingerprint → {name, dataset_fingerprints}

    this._currentDatasetFp = null;  // selected dataset (object rail)
    this._currentModelFp = null;    // selected prediction, or null
    this._followSub = null;            // the SUB subset the main view moves to once made
    this._showWhenKnown = null;        // ...and its fingerprint, if announced after the reply
    this._activeTab = null;            // id of the tab on screen
    this._viewShown = false;        // a scene is drawn; else the empty state (ADR 0055)
    this._frameCount = 0;

    // Server-side file browsing and server-side writes (ADR 0050). Ports are
    // closures so both work with whatever connection is live at call time.
    const send = (event, kwargs = {}, args = []) => this._conn?.send(event, kwargs, args);
    this._browser = new RemoteBrowser({
      send,
      getDatasets: () => this._datasets,
      getCurrentDatasetFp: () => this._currentDatasetFp,
      setStatus: (text, kind) => this._setStatus(text, kind),
      serverKey: () => document.getElementById('ws-url').value.trim(),
      setUnit: (fp, dimension, unit) => this._setDatasetUnit(fp, dimension, unit),
      signal: (name, detail) => this._tourSignal(name, detail),
      // Loads are requests (ADR 0097): their final reply ends them.
      request: (event, kwargs = {}, args = [], opts = {}) => this._request(event, kwargs, args, opts),
      holds: (kind, fp, datasetFp) => (kind === 'dataset'
        ? this._datasets.has(fp)
        : this._models.has(fp) && (kind !== 'prediction'
          || (this._models.get(fp).dataset_fingerprints || []).includes(datasetFp))),
      loaded: (kind, fp, name) => this._onLoaded(kind, fp, name),
    });
    this._sessionOps = new SessionOps({
      send,
      request: (event, kwargs = {}, args = [], opts = {}) => this._request(event, kwargs, args, opts),
      setStatus: (text, kind) => this._setStatus(text, kind),
      getCurrentDatasetFp: () => this._currentDatasetFp,
      getDatasetMeta: (fp) => this._datasets.get(fp),
      // The predictions loaded for a dataset, for Export's tick boxes.
      getPredictions: (fp) => [...this._models]
        .filter(([, m]) => (m.dataset_fingerprints || []).includes(fp))
        .map(([modelFp, m]) => ({ fp: modelFp, name: m.name })),
      capturePng: (opts) => this._capturePng(opts),
      pick: (opts) => this._browser.pick(opts),
    });
    this._tasks = new TaskList({
      menu: document.getElementById('task-menu'),
      button: document.getElementById('task-btn'),
      panel: document.getElementById('task-panel'),
      onCancel: (id) => { if (!this._needsControl()) this._conn.send(OUT.CANCEL_TASK, { task_id: id }); },
      whyNoCancel: () => this._needsControl(),
    });
    this._rail = new Rail({
      datasetList: document.getElementById('dataset-list'),
      modelList: document.getElementById('model-list'),
      onSelectDataset: (fp) => this._selectDataset(fp),
      onSelectModel: (fp) => this._selectModel(fp),
      rowActions: (kind, fp) => this._railRowActions(kind, fp),
      colourOf: (_kind, fp) => this._colours.get(fp),
      onColour: (kind, fp, hex) => { this._colours.set(fp, hex); this._analysis?.rerenderAll(); },
    });
    this._colours = new ObjectColours(() => plotTheme().series);
    /** Fingerprints this window asked the server to delete. */
    this._deleting = new Set();
    this._storedAsks = new Map();    // fp → resolve of the delete dialog's STORED_RESULTS
    this._scoreInfo = new Map();     // dataset fp → its last SCORE_DIALOG_INFO_RESULT (ADR 0093)
    this._scoreFor = null;           // the dataset whose Score dialog was asked for
    this._scoreWanted = null;        // the scored item whose tab opens when it has one
    /** What happened, for the guided tours (ADR 0088): example-loaded,
     * example-load-failed, dataset-deleted, frame-changed, quick-style-applied,
     * tab-selected {name}, point-picked {frame} (a click on a 2D plot);
     * from the file browser: browser-opened {mode}, folder-listed {mode, path},
     * file-selected {mode, name, path}, target-changed {datasetFp},
     * browser-cancelled, load-duplicate {kind, name}; dataset-loaded {fp} and
     * prediction-loaded {fp} (a load this window's browser asked for),
     * load-failed {kind, error}; series-changed {tab, datasets, models}. */
    this.tourEvents = new EventTarget();
    /** A Load Example this window asked for: the dataset and prediction it brought, once known. */
    this._exampleWatch = null;
    /** The server's example files, {folder, files}, from LOADER_TYPES; null without them. */
    this._examples = null;
    /** The id of the guided tour that runs now, or null. */
    this._tourId = null;
    /** Datasets Load Example brought into this window, until they are deleted. */
    this._exampleFps = new Set();
    // User tabs on the server (ADR 0056): save, delete, hide, export.
    this._tabOps = new TabOps({ request: (event, kwargs) => this._request(event, kwargs) });
    // User metrics on the server (ADR 0057): the Metrics pane's wire.
    this._metricOps = new MetricOps({
      send: (event, kwargs) => this._conn?.send(event, kwargs),
      request: (event, kwargs) => this._request(event, kwargs),
    });
    /** The last layout the server sent, hidden tabs included. */
    this._layoutTabs = [];
    this._pendingShow = null;   // a tab to show once a layout brings it
    // Edit mode (ADR 0056 rule 15): the only way a tab changes.
    this._editor = new TabEditor({
      tabOps: this._tabOps,
      serverTabs: () => this._layoutTabs,
      catalog: () => this._metricCatalog,
      refresh: ({ show } = {}) => this._applyLayout(this._layoutTabs, { show }),
      place: (draft) => this._placeDraft(draft),
      setStatus: (text, kind) => this._setStatus(text, kind),
      resetTab: (name) => this._removeTab(name, 'reset'),
      currentStart: (tabName, panel) => this._currentStart(tabName, panel),
    });

    // ADR 0045 Phase 1: scientific view-command plumbing.
    this._metricCatalog = [];
    // Analysis plots (ADR 0045 Phase 3): the metric channel + tab manager,
    // created on connect (they need the live connection).
    this._metricClient = null;
    this._analysis = null;
    this._playing = false;
    this._patchPending = false;

    // Picking (ADR 0045 Phase 2, issue 10): one armed tool at a time and its
    // accumulated picks (displayed index + scientific atom id). The tool is
    // armed on the active 3D panel's pick controller.
    this._activeTool = null;   // tool id, or null (orbit)
    this._picked = [];         // [{displayIndex, atomId}] for the active tool

    // Save/load session (issue 21): the in-flight op's kind + path, so the
    // next TASK_DONE/TASK_FAILED (see _connect) can report completion.

    // Live pop-out controller (ADR 0044 Phase 4): when opened with
    // ?mode=view-live this instance auto-connects, hides the chrome
    // (body.popout-view), and
    // selects the same dataset/prediction the opener had open — but as its
    // OWN connection, with its own view, driving its own frame/camera. A
    // reloaded page settles the same way on what its main view showed. Both
    // hold only while the connection's replay lasts; `_autoModelFp` '' means
    // "no prediction", null "whichever applies".
    this._autoDatasetFp = null;
    this._autoModelFp = null;

    // The cluster broker lives in the ffast-web launcher; a page it did not
    // open has no launch id and no way to a cluster (web parity plan 2026-10-29).
    const launch = launchId(window.location.search);
    this._clusterClient = launch ? new ClusterClient(launch) : null;
    // The launcher also writes the timing log (ADR 0072).
    setTimingSink(launch);
    this._clusterReady = false;   // true once the launcher answered

    this._initViews();
    this._initUI();
    this._initSidebarPanes();
    this._initMetricsDrawer();
    this._initPickTools();
    this._initTabs();
    this._applyUrlParams();
  }

  /** The main-view renderer the 3D controls act on: the active tab's 3D
   * panel, else one drawn earlier. Public for tests and tooltips.
   * @returns {import('./renderer.js').MoleculeRenderer|null} */
  get renderer() { return this._activePanel?.renderer || this._mainView.shown; }

  // ── tabs (ADR 0056) ─────────────────────────────────────────────────────
  // Every tab, the built-in "3D" tab included, comes from the server's
  // TAB_LAYOUT (built by AnalysisManager, ADR 0045 Phase 3), so every
  // client reads the same server-parsed layout. Until a server has sent one,
  // the default 3D tab stands in. A tab may mix 2D and 3D panels; the one set
  // of 3D controls (pick bar, playback strip, settings sidebar) moves into
  // whichever tab is shown, when that tab has a 3D panel.
  _initTabs() {
    this._analysis = new AnalysisManager({
      tabbar: document.getElementById('tabbar'),
      tabpanels: document.getElementById('tabpanels'),
      metricClient: null,
      onSelectTab: (id) => this._selectTab(id),
      onMoveTab: (name, before) => this._moveTab(name, before),
      onTabContext: (id, el) => this._openTabContext(id, el),
      onStepTab: (name, dir, opts) => this._stepTab(name, dir, opts),
      whyNoMove: () => this._whyNoEdit(),
      onPanelLabels: (tabName, spec, patch) => this._savePanelLabels(tabName, spec, patch),
      onSub: (o) => this._onSub(o),
      onFreeze: (fp) => this._freezeSubset(fp),
      seriesLooks: (series) => seriesLooks(series, (fp) => !!this._datasets.get(fp)?.parent)
        .map(({ owner, style }) => ({ colour: this._colours.get(owner), style })),
      onPointFrame: (o) => {
        this._onPlotPoint(o);
        this._tourSignal('point-picked', { frame: o.frame, datasetFp: o.datasetFp });
      },
      onLive: (cmd) => this._liveCommand(cmd),
      explainsChoices: (t, spec) => this._explainsChoices(t, spec),
      onExplains: (tabName, spec, index) => this._setExplains(tabName, spec, index),
      onPauseChange: () => { this._independent.followLiveFrames(); this._syncPlayback(); },
      onSameStep: () => this._syncSameStep({ view: null, datasetFp: this._currentDatasetFp,
        frame: this._main.current?.frame ?? 0 }),
      onSetUnit: ({ datasetFp, dimension, unit }) => this._setDatasetUnit(datasetFp, dimension, unit),
      onLoadPrediction: (datasetFp) => this._browser.open('prediction', { datasetFp }),
      onSeriesChanged: (detail) => this._tourSignal('series-changed', detail),
    });
    this._applyLayout([]);
  }

  /** Build the tabs from a layout. Linked 3D panels already drawing move into
   * the new layout's linked cells, keeping their renderer and scene; the rest
   * are given back. The tab on screen stays on screen when it still exists. */
  _applyLayout(tabs, { show } = {}) {
    // Every tab is held on the fine grid (ADR 0069), so places found in one
    // (a panel's cell, a saved edit) mean the same in all.
    const redraw = tabs === this._layoutTabs;
    const fine = redraw ? tabs : (tabs || []).map((t) => toFineGrid(t));
    if (!redraw) this._editor.noteLayout(fine);
    this._layoutTabs = fine;
    tabs = fine;
    // A tab asked for (`show`) may only arrive with the next layout: a saved
    // new tab is announced just after the save's answer.
    const currentName = this._analysis.tab(this._activeTab)?.name;
    const wanted = show ?? this._pendingShow ?? currentName;
    const old = [...this._panels3d.values()];
    const reusable = old.filter((panel) => panel.linked);
    this._parkChrome();
    // The sidebar keeps showing the focused view across the rebuild; a view
    // the new layout drops hands it back to the main view (`retired` port).
    this._setActivePanel(null, { keepView: true });
    this._panels3d = new Map();
    const draft = this._editor.draft;
    const visible = this._visibleTabs();
    this._analysis.setLayout(visible);
    const oldIndependent = this._independent.startLayout();
    for (const { id } of this._analysis.tabList) {
      const tab = this._analysis.tab(id);
      for (const cell of tab.cells3d) {
        if (!isLinked3D(cell.spec)) {
          this._independent.keep(oldIndependent, independentKey(tab.name, cell.spec), cell);
          continue;
        }
        if (!reusable.length) continue;
        const panel = reusable.shift();
        panel.spec = cell.spec;
        panel.mount(cell.el);
        this._panels3d.set(cell.el, panel);
      }
    }
    this._independent.endLayout(oldIndependent);
    for (const panel of old) if (![...this._panels3d.values()].includes(panel)) this._disposePanel(panel);
    const list = this._analysis.tabList;
    const found = list.find((t) => t.name === wanted);
    this._pendingShow = found ? null : (show ?? this._pendingShow ?? null);
    const next = found || list.find((t) => t.name === currentName)
      || list.find((t) => t.id === this._mainViewTabId());
    this._activeTab = null;
    // No new snapshot needed: a reused panel still draws the scene, and a
    // new one starts from the main view's kept scene and camera.
    this._selectTab(next.id, { reopen: false });
    if (draft) {
      const id = list.find((t) => t.name === draft.name)?.id;
      this._editor.decorate(this._analysis.tab(id), visible);
    }
    this._syncTabButtons();
    if (this._refocusTab) {
      this._refocusTab = false;
      document.querySelector('#tabbar .tab.active')?.focus();
    }
  }

  /** The tabs in the bar. Hidden tabs stay out; the tab menu offers them
   * again. A tab being edited shows its draft. */
  _visibleTabs() {
    const draft = this._editor.draft;
    return ensureMainView(this._editor.withDraft(this._layoutTabs)
      .filter((t) => !t.hidden || t === draft));
  }

  /** Edit mode moved or resized panels and changed nothing else: the tab
   * moves its cells in place, so plots and 3D views keep drawing. False
   * when the tab is not on screen and must be built. */
  _placeDraft(draft) {
    const parts = this._analysis.placeCells(draft);
    if (!parts) return false;
    this._editor.decorate(parts, this._visibleTabs());
    return true;
  }

  /** The first tab with a linked 3D panel; there always is one (ADR 0056 rule 6). */
  _mainViewTabId() {
    return this._analysis.tabList.find((t) => showsMainView(this._analysis.tab(t.id).spec))?.id;
  }

  _disposePanel(panel) {
    if (panel.renderer) (panel.independent?.renderers || this._mainView).remove(panel.renderer);
    if (panel === this._activePanel) this._setActivePanel(null);
    panel.dispose();
  }

  _selectTab(id, { reopen = true } = {}) {
    const tab = this._analysis.tab(id);
    if (!tab) return;
    this._activeTab = id;
    queueMicrotask(() => this._syncTabButtons());
    for (const el of document.querySelectorAll('#tabbar .tab')) {
      const on = el.dataset.tab === id;
      el.classList.toggle('active', on);
      el.setAttribute('aria-selected', String(on));
      el.tabIndex = on ? 0 : -1;   // one tab stop; the arrow keys move along the tabs
    }
    for (const el of document.querySelectorAll('#tabpanels .tabpanel'))
      el.classList.toggle('active', el.id === `panel-${id}`);

    // The tab is on screen now, so its 3D panels have a size to draw at;
    // a panel's renderer is made the first time its tab is shown.
    const panels = [];
    for (const cell of tab.cells3d) {
      const independent = isLinked3D(cell.spec) ? null : this._independent.get(independentKey(tab.name, cell.spec));
      let panel = this._panels3d.get(cell.el);
      if (!panel) {
        panel = new Panel3D(cell.spec);
        panel.onChoose = (value) => this._chooseShown(panel, value);
        panel.independent = independent || null;
        if (independent) independent.panel = panel;
        panel.mount(cell.el);
        this._panels3d.set(cell.el, panel);
      }
      if (!panel.renderer) {
        (independent?.renderers || this._mainView).add(
          panel.ensureRenderer((entries, opts) => this._onPick(entries, opts)));
        // Clicking a 3D panel focuses it; the click still orbits or picks.
        panel.viewport.addEventListener('pointerdown', () => this._focusPanel(panel), true);
      }
      panels.push(panel);
    }
    if (tab.cells3d.length) this._mountChrome(tab);
    else this._parkChrome();
    // The focused panel of each tab is browser layout state (ADR 0056).
    const focus = loadLayout().focus?.[tab.name];
    this._setActivePanel(panels[Number.isInteger(focus) ? focus : 0] || panels[0] || null);
    const showsMain = panels.some((p) => p.linked);
    if (showsMain) this._recentMainViewTab = tab.name;
    // Showing the main view with a dataset selected ensures a live view.
    if (reopen && showsMain && this._conn && this._currentDatasetFp) this._openView();
    // Independent panels choose their data the first time they show (rule 7).
    // Only the tab on screen follows the main view's frame, so a tab shown
    // now catches up.
    for (const view of this._independent.forTab(tab)) {
      this._independent.ensureData(view, tab);
      this._independent.followMainFrame(view);   // a panel with its own frame stays
      if (view.links.camera && this._mainView.camera)
        view.renderers.setCamera(this._mainView.camera, { keepCenter: true });
    }
    // 2D panels render (or refresh) lazily on activation.
    this._analysis.activate(id);
    this._syncEmptyState();
    this._liveUi?.sync();   // attribution explains a 3D panel of the tab on screen
    this._tourSignal('tab-selected', { name: tab.name });
  }

  /** The 3D controls go into a tab with a 3D panel: the pick bar above its
   * grid, the playback strip below, the settings sidebar beside it. */
  _mountChrome(tab) {
    const byId = (x) => document.getElementById(x);
    tab.mainEl.prepend(byId('pick-bar'));
    tab.mainEl.append(byId('view-controls'));
    tab.bodyEl.append(byId('sidebar-resize'), byId('view-sidebar'));
  }

  /** Put the 3D controls away, for a tab without a 3D panel. */
  _parkChrome() {
    const byId = (x) => document.getElementById(x);
    byId('chrome3d').append(byId('pick-bar'), byId('view-controls'), byId('sidebar-resize'),
      byId('view-sidebar'), byId('overlay'), byId('hint-bar'));
    this._attachLegend();
  }

  /** The colour legend belongs to the focused 3D panel's viewport; with no 3D
   * panel on screen it is parked with the other 3D chrome. */
  _attachLegend() {
    const legend = this._panes?.colorBy.legend;
    if (!legend) return;
    (this._activePanel ? this._activePanel.viewport : document.getElementById('chrome3d')).append(legend);
  }

  /** Point the 3D controls at `panel`: an armed pick tool moves to it, and so
   * do the empty-state Load Dataset… button and the hint bar. */
  _setActivePanel(panel, { keepView = false } = {}) {
    if (panel === this._activePanel) return;
    this._activePanel?.pick?.disarm();
    this._activePanel?.viewport.parentElement?.classList.remove('focused');
    this._activePanel = panel;
    this._attachLegend();
    const view = panel?.independent || null;
    if (!keepView && view !== this._focusedIndependent) this._switchFocusedView(view);
    this._syncSidebarTitle();
    if (!panel) return;
    panel.viewport.parentElement?.classList.add('focused');
    panel.viewport.append(document.getElementById('overlay'), document.getElementById('hint-bar'));
    if (this._activeTool) panel.pick.arm({ id: this._activeTool, ...PICK_TOOLS[this._activeTool] });
  }

  /** The user clicked a 3D panel: the tab's 3D controls now act on it. */
  _focusPanel(panel) {
    if (panel === this._activePanel) return;
    const tab = this._analysis.tab(this._activeTab);
    const index = tab?.cells3d.findIndex((cell) => cell.el === panel.viewport.parentElement);
    if (index == null || index < 0) return;
    this._setActivePanel(panel);
    this._independent.followLiveFrames();
    saveLayout({ focus: { ...(loadLayout().focus || {}), [tab.name]: index } });
  }

  /** The sidebar title names the panel its settings act on (rule 8). */
  _syncSidebarTitle() {
    const el = document.getElementById('sidebar-title');
    if (!el) return;
    const owner = this._focusedIndependent;
    const shows = this._pairName(this._focusedDatasetFp(), this._focusedModelFp());
    const which = owner ? 'own view' : 'main view';
    el.textContent = this._activePanel && shows ? `Settings — ${shows} (${which})` : 'Settings';
    el.title = el.textContent;
  }

  /** 'aspirin · MACE': a dataset and prediction by name. */
  _pairName(datasetFp, modelFp) {
    const name = (meta, fp) => meta?.name || fp?.slice(0, 8);
    const ds = this._datasets.get(datasetFp);
    const model = this._models.get(modelFp);
    return [ds && name(ds, datasetFp), model && name(model, modelFp)].filter(Boolean).join(' · ');
  }

  /** The dataset, prediction and renderers of the view the sidebar acts on. */
  _focusedDatasetFp() { return this._focusedIndependent ? this._focusedIndependent.datasetFp : this._currentDatasetFp; }

  /** The focused view's frame when its dataset's atoms differ by frame, so
   * Force picks are kept per frame (to_fix_v2); else null. */
  _forcePickFrame() {
    if (!this._datasets.get(this._focusedDatasetFp())?.variable) return null;
    return this._focused()?.frame ?? 0;
  }
  _focusedModelFp() { return this._focusedIndependent ? this._focusedIndependent.modelFp : this._currentModelFp; }
  _focusedRenderers() { return this._focused()?.renderers ?? this._mainView; }

  /** The tab with a linked 3D panel used last; the first one if none was. */
  _recentMainViewTabId() {
    const list = this._analysis.tabList;
    const recent = list.find((t) => t.name === this._recentMainViewTab
      && showsMainView(this._analysis.tab(t.id).spec));
    return recent?.id || this._mainViewTabId();
  }

  _initViews() {
    this._mainView = new ViewRenderers({
      onCameraChange: (cam) => {
        this._sendSetCamera(this._main.current, cam);
        if (!this._focusedIndependent) this._panes?.camera.syncFromCamera(cam);
        this._independent.linkCameras(null, cam);
      },
    });
    /** The main view's views: one per dataset, the current one is what it shows now. */
    this._main = new MainViews({ renderers: this._mainView, parentOf: (fp) => this._lookParentOf(fp) });
  }

  _initUI() {
    document.getElementById('connect-btn').addEventListener('click', () => this._connect());
    document.getElementById('disconnect-btn').addEventListener('click', () => this._disconnect());
    // A dropped connection is retried by itself (ADR 0097 decision 3).
    this._reconnector = new Reconnector({
      tryConnect: () => this._reconnect(),
      onWait: (s) => this._showConnLost(`Connection lost. Trying again in ${s} s.`),
      onTrying: () => this._showConnLost('Connection lost. Reconnecting…'),
    });
    document.getElementById('conn-lost-now').addEventListener('click', () => this._reconnector.now());
    document.getElementById('status').addEventListener('click', () => this._openConnDialog());
    document.getElementById('conn-close').addEventListener('click', () => this._closeConnDialog());
    this._clusterDialog = this._clusterClient ? new ClusterDialog({
      client: this._clusterClient,
      onConnected: (s) => this._useCluster(s),
      onEnded: () => this._leaveCluster(),
    }) : null;
    document.getElementById('cluster-close').addEventListener('click', () => this._clusterDialog?.close());
    const dialog = document.getElementById('conn-dialog');
    dialog.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') this._closeConnDialog();
      if (e.key === 'Enter' && e.target.tagName === 'INPUT' && e.target.type === 'text') this._connect();
    });
    document.getElementById('reset-camera-btn').addEventListener('click', () => {
      this.renderer?.resetCamera();
    });
    document.getElementById('popout-btn').addEventListener('click', () => this._openPopout());

    const slider = document.getElementById('frame-slider');
    slider.addEventListener('input', () => this._onFrameSlider());

    // Playback (issue 08): prev/next/play-pause + FPS/skip, response-synced.
    document.getElementById('prev-frame-btn').addEventListener('click', () => this._stepFrame(-1));
    document.getElementById('next-frame-btn').addEventListener('click', () => this._stepFrame(1));
    this._initShortcuts();
    document.getElementById('play-pause-btn').addEventListener('click', () => this._togglePlayback());
    this._bindPlaybackPop();

    // Object rail load actions — dataset vs prediction mode.
    document.getElementById('add-dataset-btn').addEventListener('click', () => this._browser.open('dataset'));
    document.getElementById('add-prediction-btn').addEventListener('click', () => this._browser.open('prediction'));

    this._tours = buildTours(this);
    this._actions = this._buildActions();
    const mac = isMac();
    for (const s of FILE_SHORTCUTS) {
      const a = this._action(s.action);
      if (!a) continue;   // its plan is not built yet
      a.keys = shortcutLabel(s, mac);
      a.ariaKeys = `Alt+Shift+${s.code.replace(/^Key|^Digit/, '')}`;
    }
    this._fillEmptyKeys();
    this._palette = createPalette(
      () => this._actions.filter((a) => a.id !== 'command-palette' && !a.separator),
      (why) => this._setStatus(why, 'error'),
    );
    this._initFileShortcuts();
    bindMenu(
      document.getElementById('file-menu-btn'),
      document.getElementById('file-menu-list'),
      this._actions.filter((a) => a.menu === 'file'),
    );
    bindMenu(
      document.getElementById('lammps-menu-btn'),
      document.getElementById('lammps-menu-list'),
      this._actions.filter((a) => a.menu === 'lammps'),
    );
    bindMenu(
      document.getElementById('tab-menu-btn'),
      document.getElementById('tab-menu-list'),
      () => this._tabMenuActions(),
    );
    bindMenu(
      document.getElementById('tab-new-btn'),
      document.getElementById('tab-new-list'),
      () => this._newTabActions(),
    );
    document.getElementById('tab-edit-btn').addEventListener('click', () => this._editCurrentTab());
    // Right-click on a tab shows the tab menu under it (ADR 0070).
    const contextList = document.createElement('div');
    contextList.id = 'tab-context-list';
    contextList.className = 'menu-list tab-context-menu hidden';
    contextList.setAttribute('role', 'menu');
    document.body.appendChild(contextList);
    this._tabContext = sharedMenu(contextList);
    document.getElementById('hint-dismiss').addEventListener('click', () => this._dismissHint());
    document.getElementById('empty-load-btn').addEventListener('click',
      () => runAction(this._action('load-dataset')));
    document.getElementById('empty-example-btn').addEventListener('click',
      () => runAction(this._action('load-example')));
    document.getElementById('empty-tour-btn').addEventListener('click',
      () => runAction(this._defaultTourAction()));
    this._syncEmptyState();
    // Each modal owns its own controls (ADR 0050).
    this._browser.bindControls();
  }

  /** The one action list (ADR 0055): the File menu today; the Alt+Shift file
   * shortcuts and the command palette read the same entries. Session save/load (issue 21) and
   * subset export (issue 20) use the file browser — the browser has no
   * native server-side save dialog. */
  /** '' when this window may change the shared session, else why not. */
  _needsControl() {
    if (!this._conn) return 'Connect to a server first';
    if (this._conn.role === 'READ_ONLY') return 'Read-only connection';
    return '';
  }

  /** Run guided tour `tour` (ADR 0088); how it ended goes to the status line. */
  _startTour(tour) {
    if (this._tourId) return;   // one tour at a time (the runner says the same)
    this._tourId = tour.id;
    this._tourHandle = startTour(tour, {
      events: this.tourEvents,
      onEnd: (how, message) => {
        this._tourId = null;
        if (how === 'done') this._setStatus('Tour finished. Take it again from the File menu.', 'connected');
        else if (how === 'skip') this._setStatus('Tour skipped. Take it again from the File menu.', 'connected');
        else showWarning(message);
      },
    });
  }

  /** The action of the tour the empty screen's Take the Tour button starts. */
  _defaultTourAction() {
    const tour = this._tours.find((t) => t.isDefault);
    return tour && this._action(`tour-${tour.id}`);
  }

  /** Tell the guided tours (ADR 0088) that `name` happened. */
  _tourSignal(name, detail = {}) {
    this.tourEvents.dispatchEvent(new CustomEvent(name, { detail }));
    this.tourEvents.dispatchEvent(new CustomEvent('app-event', { detail: { name, ...detail } }));
  }

  _buildActions() {
    const needsControl = () => this._needsControl();
    // While a tab is edited the rest of the window is for looking only (CONTEXT.md,
    // "Edit mode"): each action but these says why not. Live Runs… stays, since it
    // holds Stop now (a run safety control); the popout only opens the view in a window.
    const ALWAYS = new Set(['command-palette', 'live-runs', 'open-popout']);
    return this._buildActionList(needsControl).map((a) => (a.separator || ALWAYS.has(a.id) ? a : {
      ...a, unavailable: () => (this._editor.editing ? this._whyNoEdit() : '') || a.unavailable?.() || '',
    }));
  }

  _buildActionList(needsControl) {
    return [
      // No `menu`: only the shortcut and this entry open it.
      { id: 'command-palette', label: 'Command Palette', run: () => this._palette.open() },
      { id: 'load-dataset', label: 'Load Dataset…', menu: 'file',
        run: () => this._browser.open('dataset'), unavailable: needsControl },
      { id: 'load-prediction', label: 'Load Prediction…', menu: 'file',
        run: () => this._browser.open('prediction'),
        unavailable: () => needsControl() || (this._datasets.size ? '' : 'Load a dataset first') },
      { id: 'load-model', label: 'Load Model…', menu: 'file',
        run: () => this._browser.open('model'), unavailable: needsControl },
      { id: 'load-zero-model', label: 'Load Zero Model', menu: 'file',
        run: () => this._browser.loadZeroModel(), unavailable: needsControl },
      // The example pair in examples/data: the server knows where it lives.
      { id: 'load-example', label: 'Load Example Dataset and Prediction', menu: 'file',
        run: () => {
          const watch = { datasetFp: null, modelFp: null };
          this._exampleWatch = watch;
          this._setStatus('Loading example…', 'connected');
          this._request(OUT.LOAD_EXAMPLE).then((r) => {
            if (this._exampleWatch !== watch) return;
            watch.datasetFp = r.kwargs.dataset_fp;
            watch.modelFp = r.kwargs.model_fp;
            this._signalExampleLoaded();
          }, (err) => {
            if (this._exampleWatch === watch) this._exampleWatch = null;
            if (err.connectionLost) return;
            this._setStatus(`Could not load the example: ${err.message}`, 'error');
            this._tourSignal('load-failed', { kind: 'dataset', error: err.message });
            this._tourSignal('example-load-failed', { error: err.message });
          });
        },
        unavailable: needsControl },
      // Live LAMMPS runs (ADR 0086/0087): the LAMMPS menu, in this order.
      { id: 'run-with-ffast', label: 'Run with FFAST…', menu: 'lammps',
        run: () => this._runWithFfast(), unavailable: needsControl },
      // The one way in to live runs: the list, whose "Open run folder…" opens
      // any other run folder on the server's disk (no second menu item to
      // choose between).
      { id: 'live-runs', label: 'Live Runs…', menu: 'lammps',
        run: () => this._live().openRuns(), unavailable: needsControl },
      { id: 'make-reference', label: 'Make Reference…', menu: 'lammps',
        run: () => this._makeReference(), unavailable: needsControl },
      { id: 'save-session', label: 'Save Session…', menu: 'file',
        run: () => this._sessionOps.saveSession(), unavailable: needsControl },
      { id: 'load-session', label: 'Load Session…', menu: 'file',
        run: () => this._sessionOps.loadSession(), unavailable: needsControl },
      { id: 'export-dataset', label: 'Export Selected Dataset…', menu: 'file',
        run: () => this._sessionOps.exportSelectedDataset(),
        unavailable: () => needsControl() || (this._currentDatasetFp ? '' : 'Select a dataset first') },
      { id: 'connect', label: 'Connect to Server…', menu: 'file', run: () => this._openConnDialog() },
      { id: 'connect-cluster', label: 'Connect to Cluster…', menu: 'file',
        run: () => this._clusterDialog.open(),
        unavailable: () => (this._clusterReady ? '' : 'Start FFAST with ffast-web to reach a cluster') },
      // The guided tours (ADR 0088), after a line in the File menu.
      { separator: true, menu: 'file' },
      ...this._tours.map((t) => ({
        id: `tour-${t.id}`, label: `Tour: ${t.title}`, menu: 'file',
        run: () => this._startTour(t), unavailable: t.unavailable,
      })),
      ...this._tours.filter((t) => t.lammps).map((t) => ({
        id: `lammps-tour-${t.id}`, label: `Tour: ${t.title}`, menu: 'lammps',
        run: () => this._startTour(t), unavailable: t.unavailable,
      })),
      // No `menu`: the File menu does not change; the Alt+Shift+N shortcut runs it.
      { id: 'open-popout', label: 'Open 3D View in New Window', run: () => this._openPopout(),
        unavailable: () => (document.getElementById('popout-btn').disabled ? 'Open a dataset first' : '') },
      // Quick styles (ADR 0055): buttons at the top of the sidebar.
      { id: 'style-force', label: 'Quick style: Force error', short: 'Force error',
        title: 'Colour atoms by force error of the selected prediction',
        run: () => {
          const modelFp = this._focusedModelFp();
          const meta = this._models.get(modelFp);
          this._applyQuickStyle(forceErrorStyle(meta?.name || modelFp.slice(0, 8)), 'style-force');
          this._sections.open('Colour By');
        },
        unavailable: () => {
          if (!this._models.size) return 'Load a prediction first';
          return this._focusedModelFp() ? '' : 'Select a prediction first';
        } },
      { id: 'style-publication', label: 'Quick style: Publication', short: 'Publication',
        title: 'White background, orthographic view, no axes',
        run: () => this._applyQuickStyle(PUBLICATION_STYLE, 'style-publication') },
      { id: 'style-reset', label: 'Quick style: Reset', short: 'Reset',
        title: 'Back to the default colouring, background and projection',
        run: () => this._applyQuickStyle(RESET_STYLE, 'style-reset') },
    ];
  }

  // ── tab actions: the ⋯ menu (ADR 0056 rules 9 and 10) ──────────────────

  /** Edit mode on the tab on screen: the ✎ button, and Edit tab in the
   * right-click menu. */
  _editCurrentTab() {
    const name = this._analysis.tab(this._activeTab)?.name;
    const away = this._editedTabAway();
    if (away) this._selectTab(away);
    else if (!this._editor.editing && !this._whyNoEdit()) this._editor.editTab(name);
  }

  /** The id of the tab being edited when another tab is on screen, else null. */
  _editedTabAway() {
    const draft = this._editor.draft;
    if (!draft || this._analysis.tab(this._activeTab)?.name === draft.name) return null;
    return this._analysis.tabList.find((t) => t.name === draft.name)?.id ?? null;
  }

  /** Right-click on a tab: open it, then show the tab menu under it with
   * Edit tab first (ADR 0070). */
  _openTabContext(id, tabEl) {
    this._selectTab(id);   // the tab actions act on the tab on screen
    this._tabContext.close();
    this._tabContext.open(tabEl, [
      { id: 'tab-edit', label: 'Edit tab', run: () => this._editCurrentTab(), unavailable: () => this._whyNoEdit() },
      { separator: true },
      ...this._tabMenuActions(),
    ]);
  }

  /** The ⋯ menu acts on the tab on screen; it also lists hidden tabs. The
   * right-click menu on a tab shows the same items (ADR 0070). */
  _tabMenuActions() {
    const spec = this._analysis.tab(this._activeTab)?.spec;
    const name = spec?.name;
    const needsControl = () => this._needsControl();
    /** @type {import('./actions.js').MenuItem[]} */
    const actions = [{
      id: 'tab-export', label: 'Export as TOML…',
      run: () => this._exportTab(name),
      unavailable: () => (this._conn ? '' : 'Connect to a server first'),
    }, {
      // Zooming one timeline's X zooms the tab's other timelines; saved in the tab.
      id: 'tab-link-x', label: spec?.link_x ? 'Unlink X axes' : 'Link X axes',
      run: () => this._setTabLinkX(name, !spec.link_x),
      unavailable: () => this._whyNoEdit() || (spec ? '' : 'Open a tab first'),
    }];
    if (spec?.layout_of) {
      // A live tab: Reset goes back to the built-in Live run layout, once it was edited.
      if (spec.layout_user) actions.push({ id: 'tab-reset', label: 'Reset to original…',
        run: () => this._removeTab(name, 'reset', spec.layout_of), unavailable: needsControl });
    } else if (spec?.source === 'user' && spec.replaces) {
      actions.push({ id: 'tab-reset', label: 'Reset to original…',
        run: () => this._removeTab(name, 'reset'), unavailable: needsControl });
    } else if (spec?.source === 'user') {
      actions.push({ id: 'tab-delete', label: 'Delete this tab…',
        run: () => this._removeTab(name, 'delete'), unavailable: needsControl });
    }
    actions.push({ id: 'tab-hide', label: 'Hide this tab',
      run: () => this._tabOps.setHidden(name, true).then((r) => this._reportTab(r)),
      unavailable: () => needsControl() || this._whyTabStays(name) });
    const stepAction = (dir, label, end) => ({
      id: dir < 0 ? 'tab-move-left' : 'tab-move-right', label,
      run: () => this._stepTab(name, dir),
      unavailable: () => this._whyNoEdit() || (this._steppedOrder(name, dir) ? '' : end),
    });
    actions.push(stepAction(-1, 'Move left', 'Already first'), stepAction(1, 'Move right', 'Already last'),
      { separator: true },
      { id: 'tab-order-reset', label: 'Reset tab order',
        run: () => this._tabOps.setOrder([]).then((r) => this._reportTab(r)),
        unavailable: () => this._whyNoEdit() });
    const hidden = this._layoutTabs.filter((t) => t.hidden);
    if (hidden.length) actions.push({ separator: true });
    for (const t of hidden) {
      actions.push({ id: `tab-show:${t.name}`, label: `Show ${t.name}`,
        run: () => this._tabOps.setHidden(t.name, false).then((r) => this._reportTab(r)),
        unavailable: needsControl });
    }
    return actions;
  }

  /** "+" (rule 14): an empty tab, a copy of the tab on screen, or a copy of
   * any tab. Each opens Tab settings, then Edit mode. */
  _newTabActions() {
    const why = () => this._whyNoEdit();
    const current = this._layoutTabs.find((t) => t.name === this._analysis.tab(this._activeTab)?.name);
    /** @type {import('./actions.js').MenuItem[]} */
    const actions = [
      { id: 'tab-new-empty', label: 'Empty tab', run: () => this._editor.newTab(), unavailable: why },
      { id: 'tab-new-copy', label: 'Copy of current tab', run: () => this._editor.newTab(current),
        unavailable: () => why() || (current ? '' : 'This tab is not from the server') },
    ];
    if (this._layoutTabs.length) actions.push({ separator: true });
    for (const t of this._layoutTabs) {
      actions.push({ id: `tab-new-copy:${t.name}`, label: `Copy of ${t.name}`,
        run: () => this._editor.newTab(t), unavailable: why });
    }
    const templates = this._layoutTabs.filter((t) => t.template);
    if (templates.length) actions.push({ separator: true }, { heading: 'From template' });
    for (const t of templates) {
      actions.push({ id: `tab-template:${t.name}`, label: t.name,
        run: () => this._showTemplate(t), unavailable: () => this._needsControl() });
    }
    return actions;
  }

  /** "From template": show the built-in tab itself. One already in the bar is
   * only switched to; a waiting one is un-hidden and shown. */
  _showTemplate(t) {
    const shown = this._analysis.tabList.find((x) => x.name === t.name);
    if (shown) { this._selectTab(shown.id); return; }
    this._pendingShow = t.name;
    this._tabOps.setHidden(t.name, false).then((r) => {
      if (!r.ok) this._pendingShow = null;
      this._reportTab(r);
    });
  }

  /** Why tabs cannot be edited now, or ''. */
  _whyNoEdit() {
    if (this._editor.editing) return 'Finish editing the tab first (Save or Cancel)';
    return this._needsControl();
  }

  /** A PNG of the focused 3D panel, with the colour bar drawn where it is on
   * screen when it shows on that panel (to_fix 17). */
  async _capturePng(opts) {
    const renderer = this.renderer;
    const url = renderer.capturePng(opts);
    const colourBar = this._panes?.colorBy.colourBar;
    const onPanel = this._activePanel?.viewport.contains(colourBar?.el);
    return colourBar && onPanel ? withColourBar(url, colourBar, renderer._canvas.clientWidth, opts) : url;
  }

  /** ✎ and + follow whether this window may edit, and Edit mode. */
  _syncTabButtons() {
    const edit = document.getElementById('tab-edit-btn');
    const spec = this._analysis.tab(this._activeTab)?.spec;
    const fromServer = this._layoutTabs.some((t) => t.name === spec?.name);
    // While a tab is edited and another is on screen, ✎ leads back to it, and
    // the edited tab is marked in the bar, so it is plain which one is edited.
    const draft = this._editor.draft;
    const away = this._editedTabAway();
    const why = away ? '' : this._whyNoEdit() || (fromServer ? '' : 'This tab is not from the server');
    edit.disabled = !!why;
    edit.title = away ? `Go back to ${draft.name}, the tab being edited` : why || 'Edit this tab';
    const text = away ? `Back to ${draft.name}` : 'Edit tab';
    if (edit.dataset.text !== text) {
      edit.dataset.text = text;
      edit.innerHTML = `${icon('pencil')} `;
      edit.append(text);
      edit.setAttribute('aria-label', away ? `Back to editing ${draft.name}` : 'Edit this tab');
    }
    edit.classList.toggle('active', !!draft);
    edit.classList.toggle('away', !!away);
    for (const el of document.querySelectorAll('#tabbar .tab')) {
      const edited = !!draft && el.dataset.name === draft.name;
      el.classList.toggle('editing', edited);
      if (edited) el.title = 'Being edited: Save or Cancel in its edit bar';
    }
    // the caption drop-downs grey what would change a tab that cannot change now
    this._syncCaptions();
    this._syncEditLock();
  }

  /** While a tab is in Edit mode the rest of the window is for looking only
   * (CONTEXT.md, "Edit mode"): what loads, deletes, runs or changes something
   * outside the edit is greyed out and cannot be reached, by mouse or keyboard,
   * until Save or Cancel. The tab bar, the frame bar, the 3D view, Journal page
   * and Density stay, and so do the run safety controls. Menus and shortcuts
   * give their reason through their actions (_buildActions). */
  _syncEditLock() {
    const locked = this._editor.editing;
    document.body.classList.toggle('edit-locked', locked);
    for (const el of document.querySelectorAll(EDIT_LOCKED)) el.inert = locked;
    if (locked && this._activeTool) this._setActiveTool(null);   // no tool stays armed
    this._analysis.syncExplains?.();
  }

  /** Why tab `name` cannot be hidden: the main view needs a visible place. */
  _whyTabStays(name) {
    if (this._layoutTabs.find((t) => t.name === name)?.layout_of) return "A live run's tab closes with its run";
    const others = this._layoutTabs.filter((t) => !t.hidden && t.name !== name);
    return others.some(showsMainView) ? '' : MAIN_VIEW_RULE;
  }

  async _removeTab(name, how, label = name) {
    const reset = how === 'reset';
    const yes = reset ? 'Confirm' : 'Delete';   // the red button; focus starts on Cancel
    const answer = await askDialog({
      title: reset ? 'Reset to original' : 'Delete tab',
      message: reset
        ? `Reset ${label} to the original? Your edited version is deleted.`
        : `Delete ${name}? Its file on the server is deleted.`,
      buttons: ['Cancel', yes],
      danger: yes,
    });
    if (answer !== yes) return false;
    const r = await this._tabOps.remove(name);
    this._reportTab(r, reset ? 'Reset' : 'Deleted');
    return !!r.ok;
  }

  async _exportTab(name) {
    const r = await this._tabOps.exportToml(name);
    if (r.error || !r.toml) { this._setStatus(r.error || 'Export failed', 'error'); return; }
    textDialog({
      title: `Export ${name}`,
      text: r.toml,
      filename: `${name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'tab'}.toml`,
      note: "Paste it into a project's ffast.toml to share the tab with that project, "
        + "or send it to someone.",
    });
  }

  /** Drag a tab before `before` (null: to the end). The whole order goes to the
   * server, hidden tabs included, so they keep their places (ADR 0070). */
  _moveTab(name, before) {
    const why = this._whyNoEdit();
    if (why) { this._setStatus(why, 'error'); return; }
    const names = this._layoutTabs.map((t) => t.name);
    this._tabOps.setOrder(movedBefore(names, name, before)).then((r) => this._reportTab(r));
  }

  /** The bar order with `name` moved one visible tab in direction `dir`, or
   * null at that end (ADR 0070). */
  _steppedOrder(name, dir) {
    const hidden = new Set(this._layoutTabs.filter((t) => t.hidden).map((t) => t.name));
    return steppedOrder(this._layoutTabs.map((t) => t.name), hidden, name, dir);
  }

  /** Move a tab one place left (-1) or right (+1), skipping hidden tabs. */
  _stepTab(name, dir, { refocus = false } = {}) {
    const why = this._whyNoEdit();
    if (why) { this._setStatus(why, 'error'); return; }
    const order = this._steppedOrder(name, dir);
    if (!order) return;
    this._refocusTab = refocus;   // the layout that follows rebuilds the tab elements
    this._tabOps.setOrder(order).then((r) => this._reportTab(r));
  }

  /** Say how a tab change went, in the status label. */
  _reportTab(r, verb) {
    if (!r.ok) { this._setStatus(r.error || 'The tab could not be changed', 'error'); return; }
    if (r.action === 'order') { this._setStatus('Tab order changed', 'connected'); return; }
    const done = { save: 'Saved', delete: verb || 'Deleted', hide: 'Hid', show: 'Showing' }[r.action];
    this._setStatus(`${done} ${r.name}`, 'connected');
  }

  /** Apply a quick style's `steps`; `id` is its action, for the tours. */
  _applyQuickStyle(steps, id) {
    const missed = applyStyle(document.getElementById('view-sidebar'), steps);
    if (missed.length) console.warn('Quick style: could not set', missed);
    this._tourSignal('quick-style-applied', { id });
  }

  /** The quick-style buttons, drawn from the action list. */
  _renderQuickStyles() {
    const box = document.getElementById('quick-styles');
    if (!box.childElementCount) {
      const label = document.createElement('span');
      label.className = 'ctl-label';
      label.textContent = 'Quick style';
      box.append(label, ...this._actions.filter((a) => a.short).map((action) => {
        const btn = document.createElement('button');
        btn.dataset.action = action.id;
        btn.textContent = action.short;
        btn.addEventListener('click', () => runAction(action));
        return btn;
      }));
    }
    for (const btn of box.querySelectorAll('button')) {
      const action = this._action(btn.dataset.action);
      const why = whyUnavailable(action);
      btn.disabled = !!why;
      btn.title = why || action.title;
    }
  }

  /** FPS and Skip live in a ⚙ pop-up (ADR 0055); Escape or a click outside closes it. */
  _bindPlaybackPop() {
    const gear = document.getElementById('playback-gear');
    const pop = document.getElementById('playback-pop');
    const show = (open) => {
      pop.classList.toggle('hidden', !open);
      gear.setAttribute('aria-expanded', String(open));
      if (open) document.getElementById('fps-input').focus();
    };
    gear.addEventListener('click', () => show(pop.classList.contains('hidden')));
    // The focused view keeps the frame rate and skip it was given.
    for (const id of ['fps-input', 'skip-input']) {
      document.getElementById(id).addEventListener('change', () => {
        const view = this._focused()?.settings?.view;
        if (view) Object.assign(view, { videoFPS: this._videoFPS(), videoSkipFrames: this._videoSkipFrames() });
      });
    }
    pop.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { show(false); gear.focus(); }
    });
    document.addEventListener('pointerdown', (e) => {
      if (!pop.contains(e.target) && !gear.contains(e.target)) show(false);
    });
  }

  /** The hint bar: numbered next steps while a view is open, until dismissed.
   * Dismissals are browser layout state (ADR 0055). */
  _syncHint() {
    const bar = document.getElementById('hint-bar');
    // An armed pick tool shows its own steps, live by pick count; each tool's
    // hint is dismissed on its own.
    const steps = this._activeTool
      ? toolHint(this._activeTool, this._picked.length, { perFrame: this._forcePickFrame() != null }) : null;
    const key = steps ? toolHintKey(this._activeTool) : this._models.size ? 'with-prediction' : 'no-prediction';
    const lines = steps || HINTS[key];
    const dismissed = loadLayout().hintsDismissed || [];
    const show = this._viewShown && !dismissed.includes(key);
    bar.classList.toggle('hidden', !show);
    const shown = JSON.stringify([key, lines]);
    if (!show || bar.dataset.shown === shown) return;
    bar.dataset.hint = key;
    bar.dataset.shown = shown;
    const text = document.getElementById('hint-text');
    text.replaceChildren(...lines.map(([lead, strong, tail = ''], i) => {
      const step = document.createElement('span');
      step.className = 'hint-step';   // kept on one line; the bar wraps between steps
      if (!steps) {
        const n = document.createElement('span');
        n.className = 'hint-n';
        n.textContent = String(i + 1);
        step.append(n);
      }
      const b = document.createElement('b');
      b.textContent = strong;
      step.append(lead, b, tail);
      return step;
    }));
  }

  _dismissHint() {
    const key = document.getElementById('hint-bar').dataset.hint;
    const dismissed = loadLayout().hintsDismissed || [];
    if (key && !dismissed.includes(key)) saveLayout({ hintsDismissed: [...dismissed, key] });
    document.getElementById('hint-bar').classList.add('hidden');
  }

  _action(id) { return this._actions.find((a) => a.id === id); }

  /** The one key hint on the empty screen: the palette key, from the action
   * list, so the Mac shows ⌥⇧K. */
  _fillEmptyKeys() {
    const keysOf = (id) => this._action(id)?.keys;
    const rows = [[keysOf('command-palette'), 'Find and run any command']];
    document.getElementById('empty-keys').replaceChildren(...rows.filter(([k]) => k).map(([k, text]) => {
      const li = document.createElement('li');
      const key = document.createElement('kbd');
      key.textContent = k;
      li.append(key, `Use this to ${text[0].toLowerCase()}${text.slice(1)}`);
      return li;
    }));
  }

  /** Until a view is open the 3D tab shows only a Load Dataset… button (or
   * why it cannot load yet); its sidebar, pick bar and playback strip wait
   * for something to act on (ADR 0055). */
  _syncEmptyState() {
    const shown = this._viewShown;
    document.getElementById('content').classList.toggle('no-view', !shown);
    document.getElementById('overlay').classList.toggle('hidden', shown);
    const why = whyUnavailable(this._action('load-dataset'));
    const btn = document.getElementById('empty-load-btn');
    btn.disabled = !!why;
    document.getElementById('empty-example-btn').disabled = !!why;
    const tour = this._defaultTourAction();
    const tourBtn = document.getElementById('empty-tour-btn');
    tourBtn.disabled = !tour || !!whyUnavailable(tour);
    tourBtn.title = tour ? whyUnavailable(tour) : '';
    document.getElementById('overlay-hint').textContent = why || 'Load a dataset to see it here.';
    this._syncHint();
  }

  // ── Metrics drawer: the metric editor on every tab ──────────────────────
  // The editor is not a 3D setting: a metric goes into any tab. It lists
  // inputs and runs Try on its own dataset and prediction, which start from
  // the view the sidebar acts on and follow it when that changes.
  _initMetricsDrawer() {
    this._metricsContext = { datasetFp: null, modelFp: null };
    const drawer = document.getElementById('metrics-drawer');
    const dsSelect = document.getElementById('metrics-dataset');
    const modelSelect = document.getElementById('metrics-model');
    document.getElementById('metrics-btn').addEventListener('click', () => this._setMetricsOpen(drawer.hidden));
    document.getElementById('metrics-close').addEventListener('click', () => this._setMetricsOpen(false));
    dsSelect.addEventListener('change', () => {
      const datasetFp = dsSelect.value || null;
      const model = this._models.get(this._metricsContext.modelFp);
      const keep = model && predictionApplies(model, datasetFp, this._datasets);
      this._metricsContext = { datasetFp, modelFp: keep ? this._metricsContext.modelFp : null };
      this._renderMetricsPicker();
      this._panes.metrics.contextChanged();
    });
    modelSelect.addEventListener('change', () => {
      this._metricsContext = { ...this._metricsContext, modelFp: modelSelect.value || null };
      this._panes.metrics.contextChanged();
    });
    const content = document.getElementById('content');
    makeResizable(document.getElementById('metrics-resize'), drawer, {
      name: 'metrics', side: 'right', width: 320, min: 240,
      max: () => Math.max(240, content.clientWidth + drawer.clientWidth - 300 - 6),
    });
    // Open or closed is browser layout state, never session state (ADR 0055).
    this._setMetricsOpen(loadLayout().metricsOpen === true, false);
  }

  _setMetricsOpen(open, remember = true) {
    document.getElementById('metrics-drawer').hidden = !open;
    document.getElementById('metrics-resize').hidden = !open;
    document.getElementById('metrics-btn').setAttribute('aria-pressed', String(open));
    if (remember) saveLayout({ metricsOpen: open });
  }

  /** The view the sidebar acts on changed: the drawer takes its data. */
  _syncMetricsContext() {
    if (!this._metricsContext) return;
    this._metricsContext = { datasetFp: this._focusedDatasetFp() ?? null, modelFp: this._focusedModelFp() ?? null };
    this._renderMetricsPicker();
    this._panes?.metrics.contextChanged();   // asks again: the inputs may have changed
  }

  /** The drawer's dataset and prediction lists; a dataset that has gone is
   * replaced by the one the sidebar acts on. */
  _renderMetricsPicker() {
    if (!this._metricsContext) return;
    const datasets = this._listedDatasets();
    const before = this._metricsContext;
    if (before.datasetFp && !datasets.has(before.datasetFp)) {
      const fp = this._focusedDatasetFp();
      this._metricsContext = datasets.has(fp)
        ? { datasetFp: fp, modelFp: this._focusedModelFp() ?? null } : { datasetFp: null, modelFp: null };
    }
    const { datasetFp } = this._metricsContext;
    const name = (meta, fp) => meta?.name || fp.slice(0, 8);
    const dsSelect = document.getElementById('metrics-dataset');
    dsSelect.replaceChildren(...(datasets.size ? [] : [new Option('No dataset', '')]),
      ...[...datasets].map(([fp, meta]) => new Option(name(meta, fp), fp)));
    dsSelect.value = datasetFp ?? '';
    const modelSelect = document.getElementById('metrics-model');
    const models = [...this._models].filter(([, meta]) => !datasetFp || predictionApplies(meta, datasetFp, this._datasets));
    modelSelect.replaceChildren(new Option('No prediction', ''),
      ...models.map(([fp, meta]) => new Option(name(meta, fp), fp)));
    if (!models.some(([fp]) => fp === this._metricsContext.modelFp))
      this._metricsContext = { datasetFp, modelFp: null };
    modelSelect.value = this._metricsContext.modelFp ?? '';
    const after = this._metricsContext;
    if (after.datasetFp !== before.datasetFp || after.modelFp !== before.modelFp)
      this._panes?.metrics.contextChanged();
  }

  // ── sidebar panes (ADR 0045 Phase 1: issues 03-07) ──────────────────────
  _initSidebarPanes() {
    const sidebarEl = document.getElementById('view-sidebar');
    const search = document.createElement('input');
    search.type = 'search';
    search.id = 'sidebar-search';
    search.placeholder = 'Search settings';
    search.setAttribute('aria-label', 'Search settings');
    const searchEmpty = document.createElement('div');
    searchEmpty.id = 'sidebar-search-empty';
    searchEmpty.textContent = 'No setting matches.';
    searchEmpty.hidden = true;
    const quickStyles = document.createElement('div');
    quickStyles.id = 'quick-styles';
    const title = document.createElement('div');
    title.id = 'sidebar-title';
    title.textContent = 'Settings';
    sidebarEl.append(title, search, quickStyles, searchEmpty);

    const colorBy = createColorByPane(sidebarEl, {
      onSourceChange: (source) => this._sendSetParameter('ffast.atom_color', 'source', source),
      onPredictionChange: (modelKey) => this._sendSetParameter('ffast.atom_color', 'prediction_ref', modelKey),
      onColormapChange: (cm) => this._sendSetParameter('ffast.atom_color', 'colormap', cm),
      onMetricParam: (key, value) => this._sendSetParameter('ffast.atom_color', key, value),
      onLimit: (key, value) => this._sendSetParameter('ffast.atom_color', key, value),
      onRangeChange: (range) => this._sendSetParameter('ffast.atom_color', 'range', range),
      unitsOf: () => datasetUnits(this._focusedDatasetFp(), this._datasets),
      onSetUnit: (dimension, unit) => this._setDatasetUnit(this._focusedDatasetFp(), dimension, unit),
      // Keep boxes (ADR 0065): the focused view's dataset says whether by=atom applies.
      boxes: (request) => this._metricOps.boxes(request, this._focusedDatasetFp() ?? null),
      saveMetric: (metric) => this._metricOps.save(metric),
      defaultPrediction: () => this._focusedModelFp(),
    });

    const metricsPane = createMetricsPane(document.getElementById('metrics-body'), {
      ops: this._metricOps,
      // The drawer's own dataset and prediction (_syncMetricsContext).
      getContext: () => ({ ...this._metricsContext }),
      nameOf: ({ datasetFp, modelFp }) => this._pairName(datasetFp, modelFp),
      onGoToFrame: (frame, { datasetFp, modelFp }) => this._onPlotPoint({ datasetFp, modelFp, frame }),
      onColourAtoms: (id) => this._colourAtomsBy(id),
      onPlot: (id) => this._plotMetric(id),
    });

    const camera = createCameraPane(sidebarEl, {
      onOrtho: (enabled) => this.renderer?.setOrthographic(enabled),
      onPreset: (az, el) => this.renderer?.setCameraAngles({ azimuth: az, elevation: el, roll: 0 }),
      onManual: (az, el, dist, roll) => this.renderer?.setCameraAngles({ azimuth: az, elevation: el, distance: dist, roll }),
      onCOM: (enabled) => {
        const s = this._focused()?.settings;
        if (s) s.view.originCenterOfMass = enabled;
        if (enabled) this._panes?.align.turnOffAtomAlign();
      },
      onGizmo: (enabled) => this._focusedRenderers().setGizmoEnabled(enabled),
      onBackground: (hex) => {
        this._focusedRenderers().setBackgroundColor(hex);
        if (!this._showingSettings) this._panes?.export.setBackground(hex);   // keep the Export bg in step
      },
    });

    const display = createDisplayPane(sidebarEl, {
      onAtomSize: (scale) => this._sendSetParameter('ffast.atom_sizes', 'scale', scale),
      onLabels: (on) => this._sendToggleFeature('labels', on),
      onHideAtoms: (tokens) => this._sendSetParameter('ffast.atom_filter', 'indices', tokens),
      onHighlight: (indices) => this._sendSetSelection('picked', 'current_structure', indices),
      onUnitCell: (visible) => this._sendToggleFeature('no_unit_cell', !visible),
    });

    const bonds = createBondsPane(sidebarEl, {
      onStyle: (width, color, chosen) => this._focusedRenderers().setBondStyle(width, color, chosen),
      onApply: (bondType, fixedIndices) => {
        this._sendSetParameter('ffast.bonds', 'bond_type', bondType);
        this._sendSetParameter('ffast.bonds', 'fixed_indices', bondType === 'Fixed' ? fixedIndices : []);
      },
      getDynamicBondPairs: () => this._currentDynamicBondPairs(),
    });

    const forces = createForcesPane(sidebarEl, {
      onApply: (state) => {
        const forces = this._focused()?.settings?.forces;
        if (forces) Object.assign(forces, state);
        this._applyForceVectorsState(state);
        // Arrows asked for where there are no forces: said once, as it happens
        // (the pane's note keeps saying it; the pane may be closed).
        const why = noForcesReason(state, this._datasets.get(this._focusedDatasetFp()));
        if (why && why !== this._noForcesSaid) showWarning(why);
        this._noForcesSaid = why;
      },
      getModels: () => this._models,
      getDatasetMeta: () => this._datasets.get(this._focusedDatasetFp()),
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
          this._sendSetParameter('ffast.atom_align', 'reference_frame', this._focusedFrame());
        }
      },
      onAtomAlignOn: () => this._setOriginCOM(false),
    });

    this._panes = { colorBy, metrics: metricsPane, camera, display, bonds, forces, extract, export: exportPane, align };
    this._attachLegend();
    // The rich look sizes ball-and-stick atoms from the Atom size setting of
    // the view it draws.
    this._mainView.atomScale = () => (this._main.current?.settings.display.atomSize ?? display.atomScale()) || 1;
    this._initPanelSection(sidebarEl, title);

    // One section open at a time; which one, and whether the sidebar is
    // hidden, is browser layout state, never session state (ADR 0055).
    const layout = loadLayout();
    this._sections = oneSectionOpen(sidebarEl, {
      initial: 'openSection' in layout ? layout.openSection : 'Colour By',
      onChange: (title) => saveLayout({ openSection: title }),
    });
    this._sidebarSearch = bindSidebarSearch(search, sidebarEl, this._sections, searchEmpty);
    addSectionHelp(sidebarEl, SECTION_HELP, this._sections);
    this._renderQuickStyles();
    this._setSidebarHidden(layout.sidebarHidden === true, false);
    // Draggable edges for the sidebar and the object list; the tab's grid
    // keeps at least 300 px.
    const content = document.getElementById('content');
    makeResizable(document.getElementById('sidebar-resize'), sidebarEl, {
      name: 'sidebar', side: 'right', width: 250, min: 200,
      max: () => Math.max(200, content.clientWidth - 300 - 6),
    });
    makeResizable(document.getElementById('rail-resize'), document.getElementById('objectbar'), {
      name: 'rail', side: 'left', width: 220, min: 150,
      max: () => Math.max(150, Math.min(480, window.innerWidth * 0.4)),
    });
    document.getElementById('sidebar-toggle').addEventListener('click', () =>
      this._setSidebarHidden(!document.getElementById('content').classList.contains('sidebar-hidden')));
  }

  _setSidebarHidden(hidden, remember = true) {
    document.getElementById('content').classList.toggle('sidebar-hidden', hidden);
    const btn = document.getElementById('sidebar-toggle');
    btn.setAttribute('aria-pressed', String(hidden));
    btn.title = hidden ? 'Show the settings sidebar' : 'Hide the settings sidebar';
    if (remember) saveLayout({ sidebarHidden: hidden });
  }

  // ── independent 3D panels (ADR 0056 rules 2, 3, 7, 8, 13) ──────────────
  // An independent panel shows its own visualization view: its own dataset
  // and prediction, colouring and display. The sidebar acts on the focused
  // panel's view; for an independent one it starts with a Panel section (its
  // picker and its two link ticks). Its frame and camera follow the main
  // view while those ticks are on.

  /** The Panel section at the top of the sidebar, shown for an independent panel. */
  _initPanelSection(sidebarEl, titleEl) {
    const box = document.createElement('div');
    box.id = 'panel-section';
    box.className = 'panel-section';
    box.hidden = true;
    const head = document.createElement('div');
    head.className = 'panel-section-title';
    head.textContent = 'Panel';
    box.appendChild(head);
    const owner = () => this._focusedIndependent;
    this._panelDataset = selectRow(box, 'Dataset', [], '', (fp) => {
      const view = owner();
      if (view) this._setPanelDataset(view, fp);
    });
    this._panelModel = selectRow(box, 'Prediction', [], '', (fp) => {
      const view = owner();
      if (!view) return;
      const old = view.modelFp;
      view.modelFp = fp || null;
      view.noPrediction = !fp;   // the user's "— none —" stays until the dataset changes
      this._independent.open(view);
      this._followPanelPrediction(old, view.modelFp);
      this._syncSidebarTitle();
      this._renderQuickStyles();
    });
    this._panelLinkFrame = checkboxRow(box, "Follow main view's frame", true, (on) => {
      const view = owner();
      if (!view) return;
      view.links.frame = on;
      if (on) this._independent.followMainFrame(view);
      else view.missing = '';
      this._independent.syncCaption(view);
      this._syncPlayback();
    });
    this._panelLinkCamera = checkboxRow(box, "Follow main view's camera", true, (on) => {
      const view = owner();
      if (!view) return;
      view.links.camera = on;
      if (on && this._mainView.camera) view.renderers.setCamera(this._mainView.camera, { keepCenter: true });
    });
    this._panelNote = document.createElement('div');
    this._panelNote.className = 'ctl-hint';
    box.appendChild(this._panelNote);
    titleEl.after(box);
  }

  /** An own view shows dataset `fp` (its Panel section or its caption). */
  _setPanelDataset(view, fp) {
    const focused = view === this._focusedIndependent;
    if (focused) this._clearPicks();   // picked atom ids belong to the dataset left
    if (fp !== view.datasetFp) view.noPrediction = false;   // "— none —" was for the dataset left
    view.datasetFp = fp;
    view.modelFp = view.noPrediction ? null : this._predictionFor(fp, view.modelFp);
    this._independent.open(view);
    if (!focused) return;
    this._syncDatasetPanes(this._datasets.get(fp));
    this._syncPanelSection();
    this._syncSidebarTitle();
  }

  // ── what a 3D panel shows: its caption drop-down (to_fix 7) ────────────

  /** Where `panel` sits: its tab on screen, its cell, the visible tabs with
   * its tab's index and its own index there (for the last-linked-panel rule),
   * and the server's copy of its tab (null for a tab not from the server). */
  _panelPlace(panel) {
    if (!this._analysis) return null;
    for (const { id } of this._analysis.tabList) {
      const tab = this._analysis.tab(id);
      const cell = tab.cells3d.find((c) => c.el === panel.viewport.parentElement);
      if (!cell) continue;
      const at = (p) => p.kind === cell.spec.kind && p.row === cell.spec.row && p.col === cell.spec.col;
      const tabs = this._visibleTabs();
      const tabIndex = tabs.findIndex((t) => t.name === tab.name);
      return {
        tab, cell, tabs, tabIndex,
        panelIndex: tabs[tabIndex]?.panels.findIndex(at) ?? -1,
        server: this._layoutTabs.find((t) => t.name === tab.name) || null,
        serverIndex: (this._layoutTabs.find((t) => t.name === tab.name)?.panels || []).findIndex(at),
      };
    }
    return null;
  }

  /** Why the caption cannot change `panel`'s tab now, or ''. */
  _whyNoCaptionSave(place) {
    return this._whyNoEdit() || (place?.server && place.serverIndex >= 0 ? '' : 'This tab is not from the server');
  }

  /** Draw a 3D panel's caption drop-down: "Main view" or the dataset of its
   * own view, the prediction beside it, and why a choice is greyed. */
  _syncCaption(panel) {
    const view = panel.independent;
    const place = this._panelPlace(panel);
    const stays = panel.linked && place ? whyPanelStays(place.tabs, place.tabIndex, place.panelIndex) : '';
    const datasets = [...this._listedDatasets()].map(([fp, meta]) => [fp, meta.name || fp.slice(0, 8)]);
    const model = view?.modelFp ? this._models.get(view.modelFp)?.name || view.modelFp.slice(0, 8) : '';
    panel.showChoices(panelChoices({
      linked: panel.linked, shows: view?.datasetFp ?? null, datasets, stays,
      noSave: this._whyNoCaptionSave(place),
    }), model, view?.note || '');
    this._analysis?.syncExplains();   // the attribution pickers name what each 3D panel shows
  }

  _syncCaptions() {
    for (const panel of this._panels3d.values()) this._syncCaption(panel);
  }

  /** The caption drop-down picked `value`. An own view switches dataset at
   * once. A linked panel that picks a dataset becomes an own view showing it,
   * and an own view that picks "Main view" is linked again: either saves the
   * tab, as Edit mode's "Own view" does. */
  /** Save label fields of one plot panel in its tab (web parity plan
   * 2026-10-29: "Edit labels…" and a dragged legend). A null value
   * removes the field. A built-in or project tab gets a user tab
   * replacing it, as an Edit mode save does.
   * @returns {Promise<{ok: boolean, error?: string|null}>} */
  /** Save the tab's "Link X axes" switch. */
  async _setTabLinkX(tabName, on) {
    const server = this._layoutTabs.find((t) => t.name === tabName);
    if (!server) { this._setStatus('This tab is not from the server', 'error'); return; }
    // A live tab saves the Live run layout it is made from (ADR 0089).
    const next = { ...server, link_x: on ? true : null, name: server.layout_of || server.name };
    this._reportTab(await this._tabOps.save(toSaved(next), server.name));
  }

  async _savePanelLabels(tabName, spec, patch) {
    const server = this._layoutTabs.find((t) => t.name === tabName);
    const index = (server?.panels || []).findIndex(
      (p) => p.kind === spec.kind && p.row === spec.row && p.col === spec.col);
    const why = this._whyNoEdit() || (index < 0 ? 'This tab is not from the server' : '');
    if (why) { this._setStatus(why, 'error'); return { ok: false, error: why }; }
    // A live tab saves the Live run layout it is made from (ADR 0089).
    const next = { ...setPanelLabels(server, index, patch), name: server.layout_of || server.name };
    const r = await this._tabOps.save(toSaved(next), server.name);
    this._reportTab(r);
    return r;
  }

  /** What attribution explains: the 3D panel picked in the attribution card
   * of the tab on screen (the tab's first 3D panel by default), its dataset,
   * frame and renderers; the main view when the tab has no such card. */
  _attributionSource() {
    const main = { datasetFp: this._currentDatasetFp, frame: this._main.current?.frame ?? null,
      view: this._mainView };
    const tab = this._analysis?.tab(this._activeTab);
    const card = tab?.spec.panels.find((p) => p.kind === 'live' && p.options?.part === 'attribution');
    const cells = tab?.cells3d || [];
    if (!card || !cells.length) return main;
    const view = this._panels3d.get(cells[explainsIndex(card, cells.length)].el)?.independent;
    return view ? { datasetFp: view.datasetFp, frame: view.frame ?? null, view: view.renderers } : main;
  }

  /** The choices of an attribution card's "Explains" picker: the tab's 3D
   * panels, each by its letter and what it shows. */
  _explainsChoices(t, spec) {
    const cells = t.cells3d || [];
    const name = (fp) => (fp ? this._datasets.get(fp)?.name || fp.slice(0, 8) : 'nothing yet');
    const options = cells.map((cell, i) => {
      const view = this._panels3d.get(cell.el)?.independent;
      const letter = t.letters?.get(t.spec.panels.indexOf(cell.spec)) || String(i + 1);
      const what = view ? `${name(view.datasetFp)} (own view)` : `Main view (${name(this._currentDatasetFp)})`;
      return { value: i, label: `${letter} · ${what}` };
    });
    const fromServer = this._layoutTabs.some((x) => x.name === t.spec.name);
    return { options, value: explainsIndex(spec, cells.length),
      why: this._whyNoEdit() || (fromServer ? '' : 'This tab is not from the server') };
  }

  /** The "Explains" picker chose the tab's `index`-th 3D panel: saved in the
   * tab, as a 3D panel's caption choice is (a live tab saves the Live run
   * layout, ADR 0089). */
  async _setExplains(tabName, spec, index) {
    const server = this._layoutTabs.find((t) => t.name === tabName);
    const at = (server?.panels || []).findIndex(
      (p) => p.kind === spec.kind && p.row === spec.row && p.col === spec.col);
    const why = this._whyNoEdit() || (at < 0 ? 'This tab is not from the server' : '');
    if (why) { this._setStatus(why, 'error'); return; }
    const panels = server.panels.map((p, i) => (i === at
      ? { ...p, options: { ...(p.options || {}), explains: index } } : p));
    const next = { ...server, name: server.layout_of || server.name, panels };
    this._reportTab(await this._tabOps.save(toSaved(next), server.name));
  }

  async _chooseShown(panel, value) {
    const view = panel.independent;
    if (value === MAIN_VIEW_CHOICE ? panel.linked : view?.datasetFp === value) return;
    if (view && value !== MAIN_VIEW_CHOICE) { this._setPanelDataset(view, value); return; }
    const place = this._panelPlace(panel);
    const why = this._whyNoCaptionSave(place)
      || (panel.linked ? whyPanelStays(place.tabs, place.tabIndex, place.panelIndex) : '');
    if (why) {
      this._setStatus(why, 'error');
      this._syncCaption(panel);
      return;
    }
    const own = value !== MAIN_VIEW_CHOICE;
    const next = setPanelView(place.server, place.serverIndex, { view: own ? 'independent' : 'linked' });
    if (own) {
      const modelFp = this._predictionFor(value, this._currentModelFp);
      this._independent.want(independentKey(next.name, next.panels[place.serverIndex]), { datasetFp: value, modelFp });
    }
    const r = await this._tabOps.save(toSaved(next), place.server.name);
    if (!r.ok) this._syncCaption(panel);
    this._reportTab(r);
  }

  /** The focused panel's prediction changed from `old`: metric colouring and
   * force arrows on `old` move to the new one, as they use the panel's own
   * prediction (rule 13). */
  _followPanelPrediction(old, now) {
    if (!old) return;
    if (this._panes.colorBy.predictionOf() === old) {
      this._panes.colorBy.setPrediction(now);
      this._sendSetParameter('ffast.atom_color', 'prediction_ref', now);
    }
    const forces = this._focused()?.settings?.forces;
    if (forces?.modelKey === old) {
      forces.modelKey = now;
      this._panes.forces.setState(forces.show, now, forces);
      this._applyForceVectorsState(forces);
    }
  }

  /** Fill the Panel section from the focused independent panel, or hide it. */
  _syncPanelSection() {
    const box = document.getElementById('panel-section');
    if (!box) return;
    const view = this._focusedIndependent;
    box.hidden = !view;
    if (!view) return;
    const option = (value, text) => {
      const o = document.createElement('option');
      o.value = value;
      o.textContent = text;
      return o;
    };
    const datasets = [...this._listedDatasets()];
    this._panelDataset.replaceChildren(...datasets.map(([fp, meta]) => option(fp, meta.name || fp.slice(0, 8))));
    if (!view.datasetFp) this._panelDataset.prepend(option('', '— none —'));
    this._panelDataset.value = view.datasetFp || '';
    const models = [...this._models].filter(([, meta]) => predictionApplies(meta, view.datasetFp, this._datasets));
    this._panelModel.replaceChildren(option('', '— none —'),
      ...models.map(([fp, meta]) => option(fp, meta.name || fp.slice(0, 8))));
    this._panelModel.value = view.modelFp || '';
    this._panelLinkFrame.checked = view.links.frame;
    // One switch, not two: in a tab with Same step, a panel showing a run is moved by it.
    const linkRow = this._panelLinkFrame.closest('.ctl-row');
    if (linkRow) linkRow.style.display = this._sameStepOwns(view) ? 'none' : '';
    this._panelLinkCamera.checked = view.links.camera;
    this._panelNote.textContent = view.note;
    this._panelNote.hidden = !view.note;
    // Force error waits for the focused panel's prediction, which can change
    // without the rail being drawn again (a prediction arriving, for one).
    this._renderQuickStyles();
  }

  /** The sidebar now shows and acts on `owner`'s view (null: the main view).
   * The view left keeps its settings (the panes wrote every change into them);
   * each view's own server view and renderers already hold its look, so this
   * only swaps what the sidebar shows. */
  _switchFocusedView(owner) {
    this._clearPicks();   // picks name atoms of the view left
    this._focusedIndependent = owner;
    if (owner) {
      if (!owner.settings) this._presetIndependent(owner);
      this._showSettings(owner);
    } else if (this._main.current) {
      this._showSettings(this._main.current);
    }
    this._syncDatasetPanes(this._datasets.get(this._focusedDatasetFp()));
    this._syncPanelSection();
    this._syncSidebarTitle();
    this._syncPlayback();
    this._renderQuickStyles();
    this._syncMetricsContext();   // its inputs are those of the view now shown
  }

  /** Give an independent panel its starting look as its settings, so the
   * sidebar shows it the first time the panel is focused. */
  _presetIndependent(view) {
    const ctx = { modelFp: view.modelFp, catalog: this._metricCatalog };
    view.settings = startSettings(view.spec.start, ctx);
  }

  /** Give an independent panel's new view its starting look (rule 13), once
   * the view is open and the metric catalog says which colour metrics exist. */
  _startIndependent(view) {
    if (view.started || !view.isOpen || !this._metricCatalog.length) return;
    view.started = true;
    const { commands, bondStyle, note } = startCommands(view.spec.start,
      { modelFp: view.modelFp, catalog: this._metricCatalog });
    for (const command of commands) this._sendViewCommand(command, view);
    if (bondStyle) view.renderers.setBondStyle(...bondStyle);
    view.note = note;
    this._presetIndependent(view);
    if (view === this._focusedIndependent) this._showSettings(view);
    this._independent.syncCaption(view);
    if (view === this._focusedIndependent) this._syncPanelSection();
  }

  /** The look a 3D panel has on screen, as its starting-look keys ("Use
   * current 3D settings as start", rule 15): an independent panel's own, a
   * linked one's the main view's. */
  _currentStart(tabName, panel) {
    const view = this._independent.get(independentKey(tabName, panel)) || this._main.current;
    const forces = view?.settings?.forces || defaultSettings().forces;
    const own = view?.settings || defaultSettings();
    return startFromCurrent({
      source: own.colorBy.source, colormap: own.colorBy.colormap,
      atomSize: own.display.atomSize,
      bondWidth: own.bonds.width, bondColour: own.bonds.color,
      forces: { show: !!forces.show, length: forces.length, normalised: forces.normalised },
    });
  }

  /** The URL's `port` (from the launcher or `ffast-server --web-port`) names
   * the server, so the page connects by itself; other parameters, such as the
   * launcher's `launch=` id, are ignored. With no port the dialog opens,
   * offering the most recent server (ADR 0055). */
  _applyUrlParams() {
    const p = new URLSearchParams(window.location.search);
    const port  = p.get('port');
    const token = p.get('token');
    const recent = loadRecentServers();
    if (port) {
      document.getElementById('ws-url').value = this._localServerUrl();
    } else if (recent.length) {
      document.getElementById('ws-url').value = recent[0];
    }
    if (token) document.getElementById('token-input').value = token;

    // Live pop-out (ADR 0044 Phase 4): a second FFastApp instance opened by
    // _openPopout() when the server advertised multi_client. Hide the chrome
    // (body.popout-view) and auto-connect —
    // REMOTE_DATASET_META replay then drives _onDatasetMeta to select the
    // requested dataset and open this connection's own view. 'loupe-live' is
    // the name before 2026-11-13: a pop-out opened then still reloads as one.
    if (['view-live', 'loupe-live'].includes(p.get('mode'))) {
      document.body.classList.add('popout-view');
      this._autoDatasetFp = p.get('ds') || null;
      this._autoModelFp = p.get('pred') || null;
    } else {
      const shown = loadMainView();
      if (shown) {
        this._autoDatasetFp = shown.datasetFp;
        this._autoModelFp = shown.modelFp || '';
      }
    }
    // A live cluster connection wins over the URL's local server, so a reload
    // goes back to the cluster (web parity plan 2026-10-29).
    const fallback = () => (port ? this._connect() : this._openConnDialog());
    if (!this._clusterClient) { fallback(); return; }
    this._clusterClient.state().then((s) => {
      this._clusterReady = true;
      if (s.status?.state === 'connected') this._useCluster(s.status);
      else fallback();
    }).catch(fallback);
  }

  // ── connection dialog (ADR 0055) ────────────────────────────────────────

  /** @param {string} [message] why the dialog opened by itself, if it did */
  _openConnDialog(message = '') {
    const msg = document.getElementById('conn-message');
    msg.textContent = message;
    msg.classList.toggle('hidden', !message);
    this._renderRecentServers();
    document.getElementById('conn-dialog').classList.remove('hidden');
    this._connUntrap ||= trapFocus(document.getElementById('conn-dialog'));
    const focus = message && /token/i.test(message) ? 'token-input' : 'ws-url';
    document.getElementById(focus).focus();
  }

  /** Connect the page to the cluster's server through the launcher's
   * tunnel (web parity plan 2026-10-29). Not a recent server: the port
   * changes every time. */
  _useCluster(status) {
    this._clusterServerUrl = status.ws_url;
    document.getElementById('ws-url').value = status.ws_url;
    document.getElementById('token-input').value = status.token || '';
    this._connect({ remember: false });
  }

  /** The launcher's own local server named by the URL's `port`, or null. */
  _localServerUrl() {
    const port = new URLSearchParams(window.location.search).get('port');
    return port ? `ws://${window.location.hostname || 'localhost'}:${port}` : null;
  }

  /** The cluster job ended: let go of its tunnel and go back to the
   * launcher's local server, when the URL names one. */
  _leaveCluster() {
    this._disconnect();
    const url = this._localServerUrl();
    if (!url) return;
    document.getElementById('ws-url').value = url;
    document.getElementById('token-input').value = new URLSearchParams(window.location.search).get('token') || '';
    this._connect();
  }

  _closeConnDialog() {
    this._connUntrap?.();
    this._connUntrap = null;
    document.getElementById('conn-dialog').classList.add('hidden');
  }

  _renderRecentServers() {
    const list = loadRecentServers();
    const box = document.getElementById('recent-servers');
    box.replaceChildren(...list.map((url) => {
      const btn = document.createElement('button');
      btn.textContent = url;
      btn.title = `Connect to ${url}`;
      btn.addEventListener('click', () => {
        document.getElementById('ws-url').value = url;
        this._connect();
      });
      return btn;
    }));
    document.getElementById('recent-block').classList.toggle('hidden', list.length === 0);
  }

  /** Connect to the server named in the connection dialog. Resolves true
   * when connected. ``reconnecting``: a retry after a lost connection, which
   * fails quietly (the banner says it) instead of opening the dialog. */
  async _connect({ remember = true, reconnecting = false } = {}) {
    if (!reconnecting) this._stopReconnecting();
    const wsUrl = document.getElementById('ws-url').value.trim();
    const token = document.getElementById('token-input').value.trim() || null;
    // Explicit READ_ONLY viewer opt-in (ADR 0044 Phase 2, PRD story 73): drops
    // inbound control but still opens its own views and sees shared broadcasts.
    const readOnly = document.getElementById('readonly-toggle')?.checked || false;

    // Connect doubles as reconnect, e.g. after typing a token.
    if (this._conn) this._disconnect();
    this._setStatus('Connecting…', '');

    try {
      const conn = new FFastConnection(wsUrl, token, readOnly);
      conn.onDisconnect(() => this._connectionLost(conn));
      // Set before awaiting connect(): connect() dispatches buffered
      // handshake-time replay messages (REMOTE_DATASET_META, ...) to their
      // handlers *before* its promise resolves. A server with an
      // already-loaded dataset (e.g. a live pop-out's opener already has one
      // open) fires the auto-select-and-open-view path during that replay —
      // if this._conn were still null then, _openView()'s guard would bail
      // and the view would never open.
      this._conn = conn;
      this._readyStatus = null;
      this._browser.forgetFolder();

      // Metric channel (ADR 0045 Phase 3). The metric client registers the
      // METRIC_RESULT handler; build it before connect so buffered
      // handshake-time replays reach it.
      this._metricClient = new MetricClient(conn);
      this._analysis.setMetricClient(this._metricClient);
      conn.on(IN.TAB_LAYOUT, (kw) => this._applyLayout(kw.tabs || []));
      // User metrics (ADR 0057)
      conn.on(IN.USER_METRICS, (kw) => this._panes.metrics.setUserMetrics(kw.metrics));
      conn.on(IN.METRIC_USERS, (kw) => this._panes.metrics.setMetricUsers(kw.users));
      conn.on(IN.METRIC_CHECKED, (kw) => this._metricOps.onChecked(kw));
      conn.on(IN.METRIC_INPUTS, (kw) => this._metricOps.onInputsReply(kw));
      conn.on(IN.METRIC_TRIED, (kw) => this._metricOps.onTried(kw));
      conn.on(IN.METRIC_BOXES, (kw) => this._metricOps.onBoxesReply(kw));

      conn.on(IN.REMOTE_DATASET_META, (kw, args) => this._onDatasetMeta(args[0], kw));
      conn.on(IN.REMOTE_MODEL_META,   (kw, args) => this._onModelMeta(args[0], kw));
      conn.on(IN.DATASET_DELETED,     (kw, args) => this._onDatasetDeleted(args[0]));
      conn.on(IN.MODEL_DELETED,       (kw, args) => this._onModelDeleted(args[0]));
      conn.on(IN.LOADER_TYPES, (kw) => {
        // The example files the guided tours load: {folder, files}, or null.
        this._examples = kw.examples ?? null;
        this._browser.setLoaderTypes(kw);
      });
      conn.on(IN.PRACTICE_INPUT_PREPARED, (kw) => {
        if (kw.error) { this._tourSignal('practice-failed', kw); return; }
        this._practiceInput = kw;
        this._tourSignal('practice-input-ready', kw);
      });
      conn.on(IN.PRACTICE_RUN_STARTED, (kw) => {
        if (kw.error) { this._tourSignal('practice-failed', kw); return; }
        this._practiceId = kw.run_id;
        this._liveRunWanted = true;
        this._tourSignal('practice-started', kw);
      });
      conn.on(IN.PRACTICE_RUN_RESULT, (kw) => {
        if (kw.error) {
          this._setStatus(`Practice run: ${kw.error}`, 'error');
          this._tourSignal('practice-failed', kw);
        } else this._tourSignal(kw.action === 'heat' ? 'practice-heated' : 'practice-deleted', kw);
      });
      conn.on(IN.LIVE_RUN_UPDATE, (kw) => this._onLiveRunUpdate(kw));
      conn.on(IN.SCORE_DIALOG_INFO_RESULT, (kw) => this._onScoreDialogInfo(kw));
      conn.on(IN.SCORE_STARTED, (kw) => this._onScoreStarted(kw));
      conn.on(IN.STORED_RESULTS_INFO, (kw) => this._storedAsks.get(kw.fingerprint)?.(kw));
      conn.on(IN.LIVE_ATTRIBUTION_RESULT, (kw) => this._live().onResult(kw));
      conn.on(IN.LIVE_RUNS, (kw) => this._live().onRuns(kw));
      conn.on(IN.REFERENCE_MADE, (kw) => this._live().onReferenceMade(kw));
      for (const ev of [IN.RUN_FOLDER_SCANNED, IN.RUN_WITH_FFAST_PREPARED, IN.RUN_WITH_FFAST_WRITTEN,
        IN.JOB_SUBMITTED, IN.JOB_STATE]) {
        conn.on(ev, (kw) => {
          if (ev === IN.RUN_WITH_FFAST_WRITTEN && kw.ok && kw.practice) this._practiceId = kw.run_id;
          this._live().onRunWithFfast(ev, kw);
        });
      }
      conn.on(IN.ENERGY_SAMPLE_PREVIEW, (kw, args) => this._browser.onEnergyPreview(args[0], kw));
      conn.on(IN.TASK_CREATED,  (kw) => console.debug('TASK_CREATED', kw));
      conn.on(IN.TASK_INFO, (kw, args) => this._tasks.info(args[0], kw));
      conn.on(IN.TASK_PROGRESS, (kw, args) => {
        this._metricOps.onProgress(kw, args);
        this._tasks.progress(args[0], kw);
        console.debug('TASK_PROGRESS', kw);
      });
      // The Tasks button lists running tasks. A load ends with its own final
      // reply (ADR 0097), not with a task's end.
      conn.on(IN.TASK_DONE,    (kw, args) => {
        console.debug('TASK_DONE', kw);
        this._tasks.done(args[0]);
      });
      conn.on(IN.TASK_FAILED,  (kw) => console.warn('TASK_FAILED', kw));
      conn.on(IN.DATASET_LOADED, () => {});
      conn.on(IN.MODEL_LOADED,   () => {});
      conn.on(IN.SCENE_SNAPSHOT, (kw) => this._onSceneSnapshot(kw));
      conn.on(IN.SCENE_PATCH,    (kw) => this._onScenePatch(kw));
      conn.on(IN.COMMAND_RESULT, (kw) => this._onCommandResult(kw));
      conn.on(IN.METRIC_CATALOG, (kw) => this._onMetricCatalog(kw));
      conn.on(IN.METRICS_UPDATED, (kw) => this._onMetricsUpdated(kw));
      conn.on(IN.SESSION_METRICS, (kw) => this._offerSessionMetrics(kw));
      // A warning the server logged (ffast.session.warning_relay).
      conn.on(IN.SERVER_WARNING, (kw) => showWarning(kw.message, kw.level));

      // Asked while the handshake runs, so it adds no wait.
      const pageFingerprint = pageCodeFingerprint();
      await conn.connect();

      // The server replays its datasets while connecting; the last one's "ready"
      // stays. The role is in the button's title.
      this._setStatus(this._readyStatus || 'Connected', 'connected');
      document.getElementById('disconnect-btn').disabled = false;
      if (remember) saveRecentServers(rememberServer(loadRecentServers(), wsUrl));   // never the token
      this._syncEmptyState();
      // A server that needs a token gives a client without a valid one the
      // read-only role rather than refusing it.
      const notices = [];
      if (conn.role === 'READ_ONLY' && !readOnly) {
        notices.push(token
          ? 'That token was not accepted, so you are connected read-only. Check it and connect again.'
          : 'This server needs a token to control the session; you are connected read-only. Enter the token and connect again.');
      }
      // A server still running other code (e.g. a cluster job started before
      // an update) is said at once, not found out from a failing feature.
      const pageFp = await pageFingerprint;
      const codeNotice = pageFp === null
        ? null : serverCodeNotice(conn.serverCodeFingerprint, pageFp);
      if (codeNotice) {
        console.warn('FFAST:', codeNotice);
        notices.push(codeNotice);
      }
      if (notices.length) this._openConnDialog(notices.join(' '));
      else this._closeConnDialog();
      // A READ_ONLY viewer's mutating Control messages are dropped server-side
      // (ADR 0044 Phase 2) — grey out the buttons that would send one, rather
      // than let the click silently do nothing.
      const canMutate = conn.role !== 'READ_ONLY';
      document.getElementById('add-dataset-btn').disabled = !canMutate;
      document.getElementById('add-prediction-btn').disabled = !canMutate;
      this._renderObjects();
      this._syncTabButtons();
      // The catalog, tab layout and metric lists come with the connect replay
      // (ADR 0062); only the inputs list is asked for, since it depends on
      // what this window has selected.
      this._syncMetricsContext();

      return true;
    } catch (err) {
      this._conn = null;   // handshake failed — undo the early assignment above
      if (reconnecting) {
        console.warn('Reconnect failed:', err);
        this._setStatus('Disconnected', '');
        return false;
      }
      console.error('Connection failed:', err);
      this._setStatus(`Error: ${err.message}`, 'error');
      this._openConnDialog(`Could not connect to ${wsUrl} (${err.message}).`);
      return false;
    }
  }

  /** The socket closed without the page asking (ADR 0097 decision 3): the
   * page drops what it held, as a reload would, and retries by itself. */
  _connectionLost(conn) {
    if (conn !== this._conn) return;
    console.warn('FFAST: connection lost');
    this._lostOnCluster = !!this._clusterServerUrl && conn._url === this._clusterServerUrl;
    this._disconnect({ lost: true });
    this._setStatus('Connection lost', 'error');
    this._reconnector.start();
  }

  /** One retry. On a cluster, a tunnel the launcher no longer holds is not
   * retried: the job or the tunnel is gone, and the Cluster dialog says why. */
  async _reconnect() {
    if (this._lostOnCluster && this._clusterClient) {
      let state = null;
      try { state = (await this._clusterClient.state()).status?.state; } catch (_) { /* launcher gone */ }
      if (state !== 'connected') {
        this._stopReconnecting();
        this._lostOnCluster = false;
        this._showConnLost('The cluster connection ended. Open the Cluster dialog to reconnect.', { button: false });
        return false;
      }
    }
    // What a reload would show again: the main view saved before the drop.
    const shown = loadMainView();
    if (shown) {
      this._autoDatasetFp = shown.datasetFp;
      this._autoModelFp = shown.modelFp || '';
    }
    const ok = await this._connect({ remember: false, reconnecting: true });
    if (ok) this._hideConnLost();
    return ok;
  }

  _showConnLost(text, { button = true } = {}) {
    document.getElementById('conn-lost-text').textContent = text;
    document.getElementById('conn-lost-now').hidden = !button;
    document.getElementById('conn-lost').classList.remove('hidden');
  }

  _hideConnLost() {
    document.getElementById('conn-lost').classList.add('hidden');
  }

  /** The user chose: no more retries, no banner. */
  _stopReconnecting() {
    this._reconnector?.stop();
    this._hideConnLost();
  }

  /** ``lost``: the socket is already gone (keep retrying); otherwise the
   * user disconnected, which ends any retrying. */
  _disconnect({ lost = false } = {}) {
    if (!lost) this._stopReconnecting();
    if (this._conn) {
      this._conn.close();
      this._conn = null;
    }
    this._metricClient = null;
    this._analysis.setMetricClient(null);
    this._viewShown = false;
    this._mainView.clear();
    this._panes.colorBy.setColorBy(null);   // no atoms, so no colour bar
    this._datasets.clear();
    this._models.clear();
    this._currentDatasetFp = null;
    this._currentModelFp = null;
    this._main.clear();
    this._followSub = null;
    this._showWhenKnown = null;
    this._deleting.clear();
    this._playing = false;
    this._tasks.reset();
    this._setActiveTool(null);   // release any armed pick tool
    this._applyLayout([]);       // back to the default 3D tab
    this._renderObjects();
    document.getElementById('disconnect-btn').disabled = true;
    document.getElementById('add-dataset-btn').disabled = true;
    document.getElementById('add-prediction-btn').disabled = true;
    document.getElementById('reset-camera-btn').disabled = true;
    document.getElementById('popout-btn').disabled = true;
    document.getElementById('frame-slider').disabled = true;
    for (const id of ['prev-frame-btn', 'play-pause-btn', 'next-frame-btn']) document.getElementById(id).disabled = true;
    this._browser.close();
    this._browser.forgetLoads();
    this._tabOps.reset();
    this._metricOps.reset();
    this._panes.metrics.disconnected();
    this._editor.end(false);   // a draft cannot be saved without a server
    this._setStatus('Disconnected', '');
    this._syncEmptyState();
  }

  _onDatasetMeta(fp, meta) {
    const before = this._datasets.get(fp);
    this._datasets.set(fp, meta);
    this._independent.followLiveFrames();
    // Whether DetectAna is there decides if Score against reference… is greyed out.
    if (!before && this._canScore(meta)) this._conn?.send(OUT.SCORE_DIALOG_INFO, { fingerprint: fp, quick: true });
    if (!before && !meta.is_sub) {
      this._browser.announced(fp);   // its "ready" below ends the "Loading…"
      this._readyStatus = `Dataset "${meta.name || fp.slice(0, 8)}" ready · ${framesText(meta.n)}`;
      // It replaces a "Loading…" that is over, not a result the user should
      // still read, such as "Loaded session from …", which can arrive first.
      const shown = document.getElementById('status').textContent;
      if (/^(Loading|Connect|Dataset ".*" ready|Prediction ".*" ready)/.test(shown))
        this._setStatus(this._readyStatus, 'connected');
    }
    this._panes?.colorBy.refreshUnits();
    // An independent panel on a subset whose Sub was unticked goes to its
    // parent, at the same structure, as the main view does.
    if (meta.active === false && meta.parent) {
      for (const view of this._independent.values()) {
        if (view.datasetFp !== fp) continue;
        const at = before?.parent_frames ? before.parent_frames[view.frame ?? 0] : view.frame;
        view.datasetFp = meta.parent;
        this._independent.open(view);
        if (this._ownFrame(view) && at != null) this._independent.setFrame(view, at);
      }
    }
    if (before && fp === this._currentDatasetFp && this._followMainViewSubset(fp, before, meta)) return;
    if (fp === this._showWhenKnown) {
      this._showWhenKnown = null;
      this._showInMainView(fp);
      return;
    }
    // The first dataset auto-selects so the 3D view has something to show. A
    // live pop-out (ADR 0044 Phase 4) overrides that with the dataset its
    // opener had open, whenever that one's metadata arrives during replay.
    const isAutoTarget = this._autoDatasetFp === fp;
    const firstSelect = !this._currentDatasetFp || isAutoTarget;
    if (firstSelect) this._currentDatasetFp = fp;
    this._renderObjects();
    if (firstSelect) this._syncAnalysisContext();
    if (firstSelect && this._conn && this._activePanel) this._openView();
    this._independent.ensureShown();
    if (this._focusedIndependent) this._syncPanelSection();
    this._syncCaptions();
  }

  _onModelMeta(fp, meta) {
    const before = this._models.get(fp);
    // A rename announces a known prediction again with the same datasets:
    // only the name changes, and the main view's choice stays.
    if (before && before.name !== meta?.name && sameItems(before.dataset_fingerprints, meta?.dataset_fingerprints)) {
      this._models.set(fp, meta || {});
      this._panes?.colorBy.refreshModels(this._models);
      this._renderObjects();
      this._syncCaptions();
      return;
    }
    // meta = {name, dataset_fingerprints}. Fired on connect-replay and after
    // a LOAD_PREDICTION completes (the ghost model registers its forces cache).
    // Whether this window asked for it is the load's final reply's to say
    // (RemoteBrowser.announced, ADR 0097).
    this._models.set(fp, meta || {});
    // Auto-select a freshly loaded prediction that applies to the current
    // dataset and refresh the view so its force overlay appears. A live
    // pop-out with a requested prediction (ADR 0044 Phase 4) only settles on
    // that one, so replay order among several candidates doesn't matter.
    // A model this window just loaded has no results yet, so it is not selected;
    // the user picks it in the rail (web parity plan 2026-10-29, decision 5).
    // An announcement of the prediction already shown changes nothing on
    // screen: it must not say "ready" over the status of what happened since.
    const wantsSpecificModel = this._autoModelFp !== null && fp !== this._autoModelFp;
    if (!wantsSpecificModel && fp !== this._currentModelFp && this._currentDatasetFp &&
        (meta?.dataset_fingerprints || []).length
        && predictionApplies(meta, this._currentDatasetFp, this._datasets)) {
      this._currentModelFp = fp;
      this._setStatus(`Prediction "${meta.name || fp.slice(0,8)}" ready`, 'connected');
      if (this._activePanel) this._openView();
      this._syncAnalysisContext();
    }
    // A load this window asked for that the server named (ADR 0097) ends here.
    this._browser.announced(fp);
    this._panes?.forces.refreshModels();
    this._panes?.colorBy.refreshModels(this._models);
    this._renderObjects();
    // A 3D panel on a dataset this prediction has numbers for, showing none
    // only because there was none, takes it (not one where the user chose none).
    if ((meta?.dataset_fingerprints || []).length) {
      for (const view of this._independent.values()) {
        if (!view.datasetFp || view.modelFp || view.noPrediction
            || !predictionApplies(meta, view.datasetFp, this._datasets)) continue;
        view.modelFp = fp;
        this._independent.open(view);
      }
    }
    this._independent.ensureShown();
    if (this._focusedIndependent) this._syncPanelSection();
    this._syncCaptions();
    // A Load Example whose reply came first ends once its pair is here.
    if (this._exampleWatch?.modelFp === fp) this._signalExampleLoaded();
  }

  /** Send a request on the live connection (ADR 0097); without one it
   * fails as a lost connection would. */
  _request(event, kwargs = {}, args = [], opts = {}) {
    if (this._conn) return this._conn.request(event, kwargs, args, opts);
    const err = new Error('connection lost');
    err.connectionLost = true;
    return Promise.reject(err);
  }

  /** A load this window asked for is in, and announced (ADR 0097). */
  _onLoaded(kind, fp, name) {
    if (kind === 'dataset') { this._tourSignal('dataset-loaded', { fp }); return; }
    if (kind === 'prediction') this._tourSignal('prediction-loaded', { fp });
    // A selected prediction already says "ready"; a model is never selected.
    if (this._currentModelFp !== fp)
      this._setStatus(`${kind === 'model' ? 'Model' : 'Prediction'} "${name}" loaded`, 'connected');
  }

  /** A Load Example this window asked for is done once its pair is in and
   * the view shows it: the 3D chrome is hidden until the first frame is drawn. */
  _signalExampleLoaded() {
    const watch = this._exampleWatch;
    if (!watch?.datasetFp || !watch.modelFp || !this._viewShown) return;
    if (!this._datasets.has(watch.datasetFp) || !this._models.has(watch.modelFp)) return;
    this._exampleWatch = null;
    this._exampleFps.add(watch.datasetFp);
    this._tourSignal('example-loaded', { datasetFp: watch.datasetFp, modelFp: watch.modelFp });
  }

  /** A rail row's ⋯ menu. A live SUB subset belongs to its plot: it can be
   * frozen but not deleted (unticking SUB hides it). */
  _railRowActions(kind, fp) {
    const meta = kind === 'dataset' ? this._datasets.get(fp) : null;
    const why = () => this._whyNoEdit();
    const rename = { id: `${kind}-rename`, label: 'Rename…', run: () => this._renameObject(kind, fp), unavailable: why };
    const main = meta?.parent_frames && !meta.frozen
      ? { id: 'dataset-freeze', label: 'Freeze', run: () => this._freezeSubset(fp), unavailable: why }
      : { id: `${kind}-delete`, label: 'Delete…', run: () => this._deleteObject(kind, fp), unavailable: why };
    const info = { id: `${kind}-info`, label: 'Info', run: () => this._showInfo(kind, fp) };
    if (meta?.scored) return this._scoredRowActions(fp, info, rename, why);
    const exportTo = this._exportTarget(kind, fp);
    const exporting = { id: `${kind}-export`, label: 'Export…',
      run: () => this._sessionOps.exportDataset(exportTo, { prediction: kind === 'model' ? fp : null }),
      unavailable: () => this._needsControl() || (exportTo ? '' : 'Its dataset is not loaded') };
    // Any loaded dataset, and a live run, can be scored. Without DetectAna the item
    // stays in the menu, greyed out, and says how to install it.
    const score = this._canScore(meta)
      ? [{ id: 'dataset-score', label: 'Score against reference…', run: () => this._scoreAgainstReference(fp),
        unavailable: () => why() || this._scoreInfo.get(fp)?.detectana || '' }] : [];
    return [info, rename, exporting, ...score, { separator: true }, main];
  }

  /** The dataset a row's Export… writes: the row's own dataset; for a
   * prediction, the dataset on screen when it is one of its datasets, else
   * the first of them that is loaded. */
  _exportTarget(kind, fp) {
    if (kind === 'dataset') return fp;
    const own = (this._models.get(fp)?.dataset_fingerprints || []).filter((d) => this._datasets.has(d));
    return own.includes(this._currentDatasetFp) ? this._currentDatasetFp : own[0] ?? null;
  }

  /** Whether a dataset row offers Score against reference…: loaded datasets, frozen
   * subsets and live runs; not a subset that follows a zoom, nor a scored item. */
  _canScore(meta) {
    return !!meta && !meta.scored && (!meta.is_sub || !!meta.frozen);
  }

  /** Ask the server what the dialog needs for dataset `fp`; the dialog opens on the answer. */
  _scoreAgainstReference(fp) {
    if (this._needsControl()) return;
    this._scoreFor = fp;
    this._conn.send(OUT.SCORE_DIALOG_INFO, { fingerprint: fp });
  }

  /** SCORE_DIALOG_INFO_RESULT: remember it (it decides the greyed menu item), and open
   * the dialog when this is the answer to a click. */
  _onScoreDialogInfo(kw) {
    if (kw.error) {
      if (this._scoreFor === kw.fingerprint) { this._scoreFor = null; this._setStatus(`Scoring: ${kw.error}`, 'error'); }
      return;
    }
    // A quick answer only says whether DetectAna is there; keep the full one's other parts.
    this._scoreInfo.set(kw.fingerprint, kw.quick ? { ...this._scoreInfo.get(kw.fingerprint), ...kw } : kw);
    if (kw.quick || this._scoreFor !== kw.fingerprint) return;
    this._scoreFor = null;
    openScoreDialog({
      info: kw,
      send: (payload) => this._conn.send(OUT.SCORE_DATASET, payload),
      pickBundle: (onPick) => this._browser.pick({ mode: 'save-file', title: 'Choose a reference', okLabel: 'Choose',
        itemDirs: /\.bundle$/, onPick }),
    });
  }

  /** A scored item's ⋯ menu (ADR 0093): never Freeze (its frames do not move) and no
   * Delete: Remove hides it, and its scores stay saved. Continue scoring only while it
   * is incomplete. */
  _scoredRowActions(fp, info, rename, why) {
    const items = [info, rename];
    if (scoredIncomplete(this._analysis?.liveRunOf(fp))) {
      items.push({ id: 'dataset-continue-scoring', label: 'Continue scoring',
        run: () => this._continueScoring(fp), unavailable: why });
    }
    items.push({ separator: true },
      { id: 'dataset-remove-scored', label: 'Remove…', run: () => this._removeScoredItem(fp), unavailable: why });
    return items;
  }

  /** Score the frames an incomplete scored item is missing. */
  _continueScoring(fp) {
    if (this._needsControl()) return;
    this._scoreWanted = this._analysis?.liveRunOf(fp)?.run_id ?? null;
    this._conn.send(OUT.CONTINUE_SCORING, { fingerprint: fp });
  }

  /** Hide a scored item, once confirmed. Its analysis stays on disk. */
  async _removeScoredItem(fp) {
    const name = this._datasets.get(fp)?.name || fp.slice(0, 8);
    const answer = await askDialog({
      title: 'Remove scored item',
      message: `Hide ${name}? Its scores stay saved and come back if you score again with the same settings.`,
      buttons: ['Cancel', 'Remove'], danger: 'Remove',
    });
    if (answer !== 'Remove' || this._needsControl()) return;
    this._deleting.add(fp);
    this._conn.send(OUT.DELETE_OBJECT, { fingerprint: fp });
  }

  /** The Info dialog: what a dataset or prediction is, from the metadata this window holds. */
  _showInfo(kind, fp) {
    const meta = (kind === 'dataset' ? this._datasets : this._models).get(fp);
    if (!meta) return;
    infoDialog(objectInfo(kind, fp, meta, this._datasets));
  }

  /** Rename a dataset or prediction on the server; every window then gets
   * it announced again with the new name. */
  async _renameObject(kind, fp) {
    const meta = (kind === 'dataset' ? this._datasets : this._models).get(fp);
    if (!meta) return;
    const name = await askText({
      title: kind === 'dataset' ? 'Rename dataset' : 'Rename prediction',
      label: 'Name', value: meta.name || '', problem: nameProblem, okLabel: 'Rename',
    });
    if (name === null || name === meta.name || this._needsControl()) return;
    this._conn.send(OUT.RENAME_OBJECT, { fingerprint: fp, name });
  }

  /** Name the unit of a dataset's energies or forces (a subset names its
   * dataset's). The server announces the dataset again to every window. */
  _setDatasetUnit(fp, dimension, unit) {
    if (!fp) return;
    const why = this._needsControl();
    if (why) { this._setStatus(why, 'error'); return; }
    this._conn.send(OUT.SET_DATASET_UNIT, { fingerprint: fp, dimension, unit });
  }

  /** Keep a live subset's frames as a frozen subset; the live one carries on
   * following its plot. The server announces the frozen one to every window. */
  _freezeSubset(fp) {
    if (this._needsControl()) return;
    this._conn.send(OUT.FREEZE_SUBSET, { fingerprint: fp });
    this._setStatus(`Froze ${this._datasets.get(fp)?.name || fp.slice(0, 8)}`, 'connected');
  }

  /** Delete a dataset or prediction on the server, once confirmed. The
   * confirmation names what goes with it; the server's announcement of each
   * deletion then does the rest, in this window as in every other. */
  async _deleteObject(kind, fp) {
    const dataset = kind === 'dataset';
    const meta = (dataset ? this._datasets : this._models).get(fp);
    if (!meta) return;
    const name = meta.name || fp.slice(0, 8);
    const count = (n, one) => `${n} ${one}${n === 1 ? '' : 's'}`;
    let also;
    if (dataset) {
      const cut = [...this._datasets.keys()].filter((other) => other !== fp && this._cutFrom(other, fp)).length;
      also = cut ? ` Its ${count(cut, 'subset')} ${cut === 1 ? 'is' : 'are'} deleted too.` : '';
    } else {
      const on = (meta.dataset_fingerprints || []).filter((d) => this._datasets.has(d)).length;
      also = on ? ` Its results on ${count(on, 'dataset')} are deleted.` : '';
    }
    const stored = await this._storedResults(fp);
    const reply = await askDialog({
      title: dataset ? 'Delete dataset' : 'Delete prediction',
      message: `Delete ${name}?${also}`,
      buttons: ['Cancel', 'Delete'],
      danger: 'Delete',
      check: stored?.count ? { label: storedResultsLabel(stored), checked: true } : null,
    });
    // With a check box the dialog answers {answer, checked}; without, the button text.
    const answer = reply && typeof reply === 'object' ? reply.answer : reply;
    if (answer !== 'Delete' || this._needsControl()) return;
    this._deleting.add(fp);
    this._conn.send(OUT.DELETE_OBJECT, {
      fingerprint: fp, delete_results: !!(reply && typeof reply === 'object' && reply.checked),
    });
  }

  /** The metric results deleting `fp` could delete (STORED_RESULTS), or null
   * when the server does not answer within a few seconds: the dialog then
   * asks only about the object. */
  _storedResults(fp) {
    return new Promise((resolve) => {
      const finish = (kw) => { clearTimeout(timer); this._storedAsks.delete(fp); resolve(kw); };
      const timer = setTimeout(() => finish(null), 3000);
      this._storedAsks.set(fp, finish);
      this._conn.send(OUT.STORED_RESULTS, { fingerprint: fp });
    });
  }

  /** For the tour's Back on step 1: only the pair that the tour itself loaded.
   * Deletes prediction `modelFp`, then dataset `datasetFp`, without asking,
   * and settles once the server says the dataset is gone. */
  _removeLoaded(datasetFp, modelFp) {
    const why = this._needsControl();
    if (why) return Promise.reject(new Error(why));
    if (!this._datasets.has(datasetFp)) return Promise.resolve();
    const gone = new Promise((resolve) => {
      const onDeleted = (e) => {
        if (e.detail.fp !== datasetFp) return;
        this.tourEvents.removeEventListener('dataset-deleted', onDeleted);
        resolve();
      };
      this.tourEvents.addEventListener('dataset-deleted', onDeleted);
    });
    for (const fp of [modelFp, datasetFp]) {
      if (!fp || !(this._models.has(fp) || this._datasets.has(fp))) continue;
      this._deleting.add(fp);
      this._conn.send(OUT.DELETE_OBJECT, { fingerprint: fp });
    }
    return gone;
  }

  /** Dataset `fp` was cut from `from`, directly or through other subsets. */
  _cutFrom(fp, from) {
    let cur = this._datasets.get(fp)?.parent;
    for (let depth = 0; cur && depth < 64; depth++) {
      if (cur === from) return true;
      cur = this._datasets.get(cur)?.parent;
    }
    return false;
  }

  /** How the status line reports deleting `fp` (`meta` its last known
   * details): asked for here, it or what it was cut from, or elsewhere. */
  _deletedText(fp, meta) {
    const name = meta.name || fp.slice(0, 8);
    let cur = fp;
    for (let depth = 0; cur && depth < 64; depth++) {
      if (this._deleting.has(cur)) {
        if (cur === fp) this._deleting.delete(fp);
        return `Deleted ${name}`;
      }
      cur = cur === fp ? meta.parent : this._datasets.get(cur)?.parent;
    }
    return `${name} was deleted in another window`;
  }

  /** A dataset was deleted. The lists drop it, and a view that showed it
   * moves to the nearest thing left: a subset to its parent at the same
   * structure (as unticking SUB does), else the first dataset listed (as on
   * start-up), else nothing. Whatever was cut from it is deleted too, each
   * announced before it. */
  _onDatasetDeleted(fp) {
    const meta = this._datasets.get(fp);
    if (!meta) return;
    this._datasets.delete(fp);
    this._exampleFps.delete(fp);
    if (this._showWhenKnown === fp) this._showWhenKnown = null;
    const mainFrame = this._main.current?.frame ?? 0;
    const gone = this._main.drop(fp);
    if (gone) {   // its main-view view on the server goes with it
      clearTimeout(gone.cameraThrottle);
      if (gone.isOpen) this._conn?.send(OUT.CLOSE_VIEW, { view_id: gone.viewId });
    }
    const parent = meta.parent && this._datasets.has(meta.parent) ? meta.parent : null;
    const target = parent ?? [...this._listedDatasets().keys()][0] ?? null;
    const frameIn = (frame) => (parent && meta.parent_frames ? meta.parent_frames[frame] : parent ? frame : 0) ?? 0;

    for (const view of this._independent.values()) {
      if (view.datasetFp !== fp) continue;
      view.datasetFp = target;
      view.noPrediction = false;
      view.modelFp = this._predictionFor(target, view.modelFp);
      if (!target) { this._independent.empty(view); continue; }
      this._independent.open(view);
      if (this._ownFrame(view)) this._independent.setFrame(view, frameIn(view.frame ?? 0));
    }
    if (fp === this._currentDatasetFp) {
      if (target) this._showInMainView(target, frameIn(mainFrame));
      else this._emptyMainView();
    } else {
      this._renderObjects();
    }
    if (this._focusedIndependent) this._syncPanelSection();
    this._syncCaptions();
    this._setStatus(this._deletedText(fp, meta), 'connected');
    this._tourSignal('dataset-deleted', { fp });
  }

  /** A prediction was deleted. The rail drops it; the views that showed it
   * keep their dataset, without it. */
  _onModelDeleted(fp) {
    const meta = this._models.get(fp);
    if (!meta) return;
    this._models.delete(fp);
    // Colouring and force arrows that used it, in the view the sidebar shows.
    this._followPanelPrediction(fp, null);
    for (const view of this._independent.values()) {
      if (view.modelFp !== fp) continue;
      view.modelFp = null;
      this._independent.open(view);
    }
    if (this._currentModelFp === fp) this._selectModel(null);
    else this._renderObjects();
    this._panes?.forces.refreshModels();
    this._panes?.colorBy.refreshModels(this._models);
    if (this._focusedIndependent) this._syncPanelSection();
    this._syncCaptions();
    // A prediction from a file goes with the last dataset it had numbers for
    // (the server deletes it after them): say so, not "in another window".
    const fps = meta.dataset_fingerprints || [];
    const withDataset = !this._deleting.has(fp) && fps.length && !fps.some((d) => this._datasets.has(d));
    this._setStatus(withDataset ? `Deleted ${meta.name || fp.slice(0, 8)} with its dataset`
      : this._deletedText(fp, meta), 'connected');
  }

  /** The main view with nothing left to show: the empty screen. */
  _emptyMainView() {
    this._currentDatasetFp = null;
    this._currentModelFp = null;
    if (this._main.current) this._main.current.frameOnSnapshot = null;
    this._main.point(null);
    this._playing = false;
    this._viewShown = false;
    this._mainView.clear();
    this._panes.colorBy.setColorBy(null);   // no atoms, so no colour bar
    this._renderObjects();
    this._syncAnalysisContext();
    document.getElementById('reset-camera-btn').disabled = true;
    for (const id of ['prev-frame-btn', 'play-pause-btn', 'next-frame-btn']) document.getElementById(id).disabled = true;
    this._syncEmptyState();
  }


  // ── object rail: datasets + predictions as selectable rows ──────────────
  _renderObjects() {
    const dsFp = this._currentDatasetFp;
    this._rail.render({
      datasets: this._listedDatasets(), models: this._models,
      datasetFp: dsFp, modelFp: this._currentModelFp,
      applies: (meta) => !dsFp || predictionApplies(meta, dsFp, this._datasets),
    });
    this._renderQuickStyles();
    this._syncHint();
    this._syncSidebarTitle();
    this._renderMetricsPicker();
    // The analysis tabs offer their own multi-select over the same objects, so
    // they need the full lists, not just the rail's current pick.
    this._analysis?.setAvailable({ datasets: this._listedDatasets(), models: this._models });
  }

  _selectDataset(fp) {
    const moved = fp !== this._currentDatasetFp;   // the same dataset: its prediction choice stays
    this._currentDatasetFp = fp;
    if (moved) this._dropInapplicableModel();
    this._renderObjects();
    this._syncAnalysisContext();
    // Selecting an object drives the main view; a tab without one stays put
    // and just refetches (via the context sync above).
    if (this._activePanel) this._openView();
  }

  /** The main view moved to another dataset: keep the prediction if it
   * applies, else take one that does (or none when none does). */
  _dropInapplicableModel() {
    this._currentModelFp = this._predictionFor(this._currentDatasetFp, this._currentModelFp);
  }

  /** The prediction a view on dataset `datasetFp` shows: `keep` when it
   * applies, else the first loaded prediction with numbers for the dataset
   * (or the one it was cut from), else null. A model with no results yet is
   * never picked: the user picks it. */
  _predictionFor(datasetFp, keep = null) {
    if (!datasetFp) return null;
    if (keep && predictionApplies(this._models.get(keep), datasetFp, this._datasets)) return keep;
    for (const [fp, meta] of this._models)
      if ((meta.dataset_fingerprints || []).length && predictionApplies(meta, datasetFp, this._datasets)) return fp;
    return null;
  }

  _selectModel(fp) {
    this._currentModelFp = fp;
    this._renderObjects();
    this._syncAnalysisContext();
    if (this._activePanel) this._openView();
  }

  _openView() {
    if (!this._conn || !this._currentDatasetFp) return;
    this._flushCamera(this._main.current);
    const old = this._main.current;
    const datasetChanged = this._currentDatasetFp !== old?.datasetFp;
    // While the sidebar shows an independent panel it holds that panel's
    // settings; the main view's are swapped in when it is focused again.
    const sidebarIsMain = !this._focusedIndependent;
    if (datasetChanged && old && sidebarIsMain) {
      // Picked atom ids are dataset-specific: the view being left clears them.
      this._clearPicks(old);
    }

    const view = this._main.ensure(this._currentDatasetFp);
    let startFrom = null;
    if (!view.isOpen) {
      // A frame subset opens looking like its parent, camera included.
      const parent = this._lookParentOf(this._currentDatasetFp);
      const parentView = parent && this._main.get(parent);
      if (parentView?.isOpen) {
        startFrom = parentView.viewId;
        view.framed = true;
      }
    }
    this._main.point(view);
    view.modelFp = this._currentModelFp;
    view.openedPair = `${this._currentDatasetFp}|${this._currentModelFp ?? ''}`;
    this._conn.send(OUT.OPEN_VIEW, {
      view_id: view.viewId,
      dataset_ref: this._currentDatasetFp,
      prediction_ref: this._currentModelFp,   // null clears the force overlay
      ...(startFrom ? { start_from: startFrom } : {}),
    });
    if (!this._playbackView()) this._frameCount = this._datasets.get(this._currentDatasetFp)?.n || this._frameCount;
    document.getElementById('popout-btn').disabled = false;

    if (datasetChanged && sidebarIsMain) this._showSettings(view);
    if (sidebarIsMain) this._syncDatasetPanes(this._datasets.get(this._currentDatasetFp));
  }

  // ── per-view settings (ADR 0060): each view carries its sidebar settings
  // (`view.settings`); the panes write every change into the shown view's and
  // show another's when the focus moves, so nothing is saved on leaving.

  /** The dataset a frame subset takes its look from until it has its own:
   * its parent. Not an atom subset, whose atoms are numbered differently. */
  _lookParentOf(fp) {
    const meta = this._datasets.get(fp);
    return meta?.parent && meta.parent_frames ? meta.parent : null;
  }

  /** Show `view`'s settings in the sidebar. */
  _showSettings(view) {
    const s = view.settings;
    this._panes.camera.setCOM(s.view.originCenterOfMass);
    document.getElementById('fps-input').value = s.view.videoFPS;
    document.getElementById('skip-input').value = s.view.videoSkipFrames;
    this._panes.forces.setState(s.forces.show, s.forces.modelKey, s.forces);
    this._applyForceVectorsState(s.forces);
    this._panes.colorBy.show(s.colorBy);
    // The camera pane's handlers reach the renderers; the Export background
    // is its own setting, not the camera's.
    this._showingSettings = true;
    try {
      this._panes.camera.show(s.camera);
    } finally {
      this._showingSettings = false;
    }
    this._panes.display.show(s.display);
    this._panes.bonds.show(s.bonds);
    this._panes.extract.show(s.extract);
    this._panes.export.show(s.export);
    this._panes.align.show(s.align);
  }


  /** @param {import('./protocol.js').SceneSnapshotKwargs} kw */
  _onSceneSnapshot(kw) {
    const scene = kw.scene;
    if (!scene) return;
    // A scene with no view id is the main view's current one. One for a view
    // no longer known (a closed panel's or dataset's late answer) is dropped.
    const view = this._viewById(scene.view_id);
    if (!view) return;
    if (!view.isMain) {
      this._independent.onSnapshot(view, scene);
      return;
    }
    view.sync(scene.version);
    // A main view the user has moved on from is not drawn.
    if (view !== this._main.current) return;
    // OPEN_VIEW replies can already be in flight when the first fit or a
    // camera movement is sent. Keep this view's latest local camera rather
    // than letting an older snapshot undo it. Cameras belong to each view, so
    // switching datasets still restores the camera last used for that dataset.
    const currentScene = view.framed && view.localCamera
      ? { ...scene, camera: view.localCamera } : scene;
    this._mainView.applyScene(currentScene);
    // Fit the atoms only on a view's first snapshot. Later snapshots (a tab
    // switch reopens the view) carry the camera the user left, which the
    // server keeps via SET_CAMERA (457dcaa); fitting again would discard it.
    if (!view.framed) {
      view.framed = true;
      this._mainView.frameAtoms();
    }
    this._viewShown = true;
    this._syncEmptyState();
    this._signalExampleLoaded();
    document.getElementById('reset-camera-btn').disabled = false;
    for (const id of ['prev-frame-btn', 'play-pause-btn', 'next-frame-btn']) document.getElementById(id).disabled = false;

    if (scene.atoms) {
      if (!this._focusedIndependent) this._panes.colorBy.setColorBy(scene.atoms.color_by || null);
      this._trackCameraCOM(scene.atoms.positions, view);
    }

    // Update frame slider
    if (Number.isInteger(scene.frame_index)) {
      view.frame = scene.frame_index;
      this._liveUi?.sync();   // attribution colours belong to one frame
      if (view === this._focused()) this._reloadForcePicks();
    }
    if (view === this._focused() && scene.atoms) this._followPicks();
    if (this._getViewDataset(scene)) this._syncPlayback();
    this._independent.syncLinkedFrames();
    // A plot click that had to open the view first (_onPlotPoint): the
    // snapshot shows the view's old frame, so ask for the clicked one now.
    if (view.frameOnSnapshot != null) {
      const frame = view.frameOnSnapshot;
      view.frameOnSnapshot = null;
      this._setMainFrame(frame);
    }
  }

  /** The view with server id `viewId` (an independent panel's or a main
   * view's); with no id, the main view's current one; null when unknown. */
  _viewById(viewId) {
    if (!viewId) return this._main.current;
    return this._independent.byViewId(viewId) ?? this._main.byViewId(viewId);
  }

  /** @param {import('./protocol.js').ScenePatchKwargs} kw */
  _onScenePatch(kw) {
    const changed = kw.changed || [];
    const view = this._viewById(kw.view_id);
    if (!view) return;   // a closed panel's or dataset's
    view.sync(kw.to_version);
    if (view.isMain && view !== this._main.current) return;
    const renderers = view.renderers;
    renderers.applyPatch(kw, changed);
    if (changed.includes('atoms') && kw.atoms) {
      if (this._isFocused(view)) this._panes.colorBy.setColorBy(kw.atoms.color_by || null);
      this._trackCameraCOM(kw.atoms.positions, view);
      if (this._isFocused(view)) this._followPicks();
    }
    // Playback: unblock the wait in _playLoop once the view it moves answers.
    if ((this._playbackView() ?? this._main.current) === view) this._patchPending = false;
  }


  /** @param {import('./protocol.js').CommandResultKwargs} kw */
  _onCommandResult(kw) {
    if (!kw?.success) {
      console.warn('VIEW_COMMAND failed:', kw?.error);
      showWarning(kw?.error ? `The 3D view could not do that: ${kw.error}` : 'The 3D view could not do that', 'error');
      if (typeof kw?.new_version !== 'number') return;
      const view = this._viewById(kw.view_id);
      if (view) view.version = kw.new_version;
    }
  }

  /** The sidebar acts on this view. */
  _isFocused(view) { return view === this._focused(); }

  /** The view the sidebar acts on: the focused independent panel's, else the
   * main view's current one. */
  _focused() { return this._focusedIndependent ?? this._main.current; }

  // ── scientific view commands (ADR 0014/0045 Phase 1) ────────────────────
  // Stamp the expected version, then
  // optimistically advance it; COMMAND_RESULT/SCENE_PATCH resync it above.
  // A command goes to the view the sidebar acts on (`view`), each view with
  // its own version.
  _sendViewCommand(fields, view = this._focused()) {
    if (!this._conn || !view) return;
    const command = view.command(fields);
    if (command) this._conn.send(OUT.VIEW_COMMAND, command);
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
  _sendSetSelection(name, scope, indices, view) {
    this._sendViewCommand({ type: 'SET_SELECTION', name, scope, indices }, view);
  }

  // ── picking (ADR 0045 Phase 2) ──────────────────────────────────────────
  // A pick toolbar (one named button per tool) + a contextual strip (active
  // tool, pick count, read-out, clear). The active 3D panel's PickController
  // owns the pointer while a tool is armed and reports resolved atoms here.
  _initPickTools() {
    const toolbar = document.getElementById('pick-toolbar');
    for (const [id, t] of Object.entries(PICK_TOOLS)) {
      const btn = document.createElement('button');
      btn.className = 'pick-tool-btn';
      btn.dataset.tool = id;
      const glyph = document.createElement('span');
      glyph.className = 'pick-tool-icon';
      glyph.innerHTML = icon(t.icon);
      btn.append(glyph, t.label);
      btn.title = `${t.label}: ${t.tip}`;
      btn.setAttribute('aria-pressed', 'false');   // a toggle: armed or not
      btn.addEventListener('click', () => this._setActiveTool(this._activeTool === id ? null : id));
      toolbar.appendChild(btn);
    }
    document.getElementById('pick-clear').addEventListener('click', () => {
      // Clear the Force picks of this frame too, not only the overlay (to_fix_v2).
      if (this._activeTool === 'forces') this._panes.forces.setPickedIndices([], this._forcePickFrame());
      this._clearPicks();
    });
    this._pickReadout = '';
    this._updatePickStrip();
  }

  /** Fit the dataset-bound panes to the focused view's dataset (its
   * REMOTE_DATASET_META): Extract only where it makes a fixed dataset; Align and its pick tool off where the atoms change
   * between frames. */
  _syncDatasetPanes(meta) {
    this._panes.extract.setDataset(meta);
    this._panes.align.setDataset(meta);
    const btn = document.querySelector('#pick-toolbar .pick-tool-btn[data-tool="align"]');
    if (btn) {
      btn.disabled = !!meta?.variable;
      btn.title = meta?.variable
        ? `${PICK_TOOLS.align.label}: off, this dataset's atoms change between frames`
        : `${PICK_TOOLS.align.label}: ${PICK_TOOLS.align.tip}`;
    }
    if (meta?.variable && this._activeTool === 'align') this._setActiveTool(null);
  }

  /** Arm a pick tool (or `null` to return to orbit); toggling the active one disarms. */
  _setActiveTool(id) {
    this._activeTool = id;
    this._clearPicks();   // switching tools starts a fresh picked set
    for (const btn of document.querySelectorAll('#pick-toolbar .pick-tool-btn')) {
      btn.classList.toggle('active', btn.dataset.tool === id);
      btn.setAttribute('aria-pressed', String(btn.dataset.tool === id));
    }
    const pick = this._activePanel?.pick;
    if (id) {
      pick?.arm({ id, ...PICK_TOOLS[id] });
      if (PICK_TOOLS[id].section) this._sections.open(PICK_TOOLS[id].section);
      if (id === 'align') this._panes.align.enableAtomAlignMode();
      if (id === 'forces') this._armForces();
    } else {
      pick?.disarm();
    }
    this._updatePickStrip();
  }

  /** Arming Force shows the arrows and picks up the atoms picked before,
   * so a click adds to them instead of replacing them (to_fix_v2). */
  _armForces() {
    this._panes.forces.armShow();
    this._loadForcePicks();
  }

  /** On a variable dataset the Force picks belong to the frame on screen:
   * after a frame change the new frame's picks replace the old (to_fix_v2).
   * Called on every frame move; does nothing while the picks shown are
   * already the focused view's frame's. */
  _reloadForcePicks() {
    if (this._activeTool !== 'forces' || this._forcePickFrame() == null) return;
    const at = this._forcePicksAt;
    if (at && at.view === this._focused() && at.frame === this._forcePickFrame()) return;
    this._loadForcePicks();
    this._updatePickStrip();
  }

  /** Make the Force pane's picks for the focused frame the picked set. */
  _loadForcePicks() {
    const frame = this._forcePickFrame();
    const ids = this._panes.forces.pickedIndices(frame);
    this._picked = ids.map((atomId) => ({ displayIndex: null, atomId }));
    this._sendSetSelection('picked', 'current_structure', ids);
    this._forcePicksAt = { view: this._focused(), frame };
  }

  /** Toggle an entry in the picked set . */
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
    this._pickStructure = this._atomSignature();
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
      this._pickReadout = this._infoReadoutNow();
    } else if (id === 'extract') {
      this._panes.extract.setPickedIndices(ids);
    } else if (id === 'forces') {
      this._panes.forces.setPickedIndices(ids, this._forcePickFrame());
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
        this._setActiveTool(null);   // The align tool disarms itself after 3
      }
    }
  }

  /** The info readout of the picked atoms as drawn now. */
  _infoReadoutNow() {
    const ids = this._picked.map((e) => e.atomId);
    const pts = this._picked.map((e) => this.renderer.atomPosition(e.displayIndex)).filter(Boolean);
    const symbols = this._picked.map((e) => {
      const z = this.renderer.atomElement(e.displayIndex);
      return z == null ? null : elementSymbol(z);
    });
    return infoReadout(pts, ids, symbols);
  }

  /** The atomic numbers of the focused view's atoms, in order: two frames with
   * the same signature are the same atoms in the same places in the list. */
  _atomSignature() {
    const r = this.renderer;
    const n = r?.atomCount || 0;
    const out = new Array(n);
    for (let i = 0; i < n; i++) out[i] = r.atomElement(i);
    return out.join(',');
  }

  /** A new frame drew in the focused view (to_fix_v3 #12). The picked atoms
   * stay when the frame has the same elements in the same order, and the info
   * readout is worked out again; otherwise the picks are cleared with a note. */
  _followPicks() {
    if (!this._picked.length || this._activeTool === 'forces') return;
    if (this._atomSignature() === this._pickStructure) {
      if (this._activeTool === 'info') {
        this._pickReadout = this._infoReadoutNow();
        this._updatePickStrip();
      }
      return;
    }
    this._clearPicks();
    this._setStatus('Selection cleared: different structure', 'connected');
  }

  /** Forget the picks and clear them in `view` (default: the view the sidebar acts on). */
  _clearPicks(view = this._focused()) {
    this._picked = [];
    this._pickReadout = '';
    this._sendSetSelection('picked', 'current_structure', [], view);
    this._updatePickStrip();
    this._panes.extract.clearInput();
  }

  _updatePickStrip() {
    this._syncHint();
    const strip = document.getElementById('pick-strip');
    if (!strip) return;
    if (!this._activeTool) { strip.classList.add('hidden'); return; }
    strip.classList.remove('hidden');
    document.getElementById('pick-strip-tool').textContent = PICK_TOOLS[this._activeTool].label;
    document.getElementById('pick-strip-count').textContent = `${this._picked.length} picked`;
    const readout = document.getElementById('pick-readout');
    readout.textContent = this._pickReadout || '';
    readout.title = this._pickReadout || '';   // the full text when "…" cuts it
  }

  /** Extract-as-subset: ship the raw index/element tokens; the server resolves
   * them and announces the new AtomFilteredDataset via REMOTE_DATASET_META. */
  _sendCreateSubset(tokens) {
    if (!this._conn || !this._focusedDatasetFp()) return;
    this._setStatus('Extracting subset…', 'connected');
    // What Extract made ("C in 812 of 1000 frames"), or why it made nothing.
    answerOf(this._request(OUT.CREATE_SUBSET, {
      parent_fingerprint: this._focusedDatasetFp(),
      indices: tokens,
    })).then((kw) => {
      if (kw.connectionLost) return;
      const ok = !kw.error;
      this._panes.extract.showResult({ ...kw, ok });
      this._setStatus(ok ? `Extracted ${kw.text}` : `Extract refused: ${kw.error}`,
        ok ? 'connected' : 'error');
    });
  }

  /** Make, move or hide the subset of a plot with SUB ticked (ADR 0045
   * Phase 3 subbing). With a `view` the server makes the subset the frames
   * that view covers, or moves it there; `active: false` hides it. The server
   * announces the result via REMOTE_DATASET_META, so it appears in the object
   * rail and is usable by the 3D view and other tabs (PRD 61-62). Given
   * `indices` instead, the subset is those parent frames.
   * @param {{parentFp: string, modelFp: string|null, name: string,
   *   view?: object, indices?: number[], active?: boolean}} o */
  _sendDeclareSubset(o) {
    if (!this._conn || !o.parentFp) return;
    const msg = { parent_fingerprint: o.parentFp, model_fp: o.modelFp, name: o.name };
    if (o.active === false) msg.active = false;
    else if (o.indices) msg.indices = o.indices;
    else msg.view = o.view;
    // Its final reply names the subset made, or none (ADR 0097).
    answerOf(this._request(OUT.DECLARE_SUBSET, msg)).then((kw) => {
      if (kw.fingerprint) this._onSubsetDeclared(kw);
    });
  }

  /** A plot with SUB ticked made, moved or hid subsets (analysis.js). The
   * main view moves to the one just made: the series showing the main view's
   * dataset and prediction, else the first.
   * @param {{name: string, series: Array<{parentFp: string, modelFp: string|null}>,
   *   view?: object, active?: boolean, tick?: boolean}} o */
  _onSub({ name, series, view, active, tick }) {
    for (const s of series) this._sendDeclareSubset({ ...s, name, view, active });
    if (active === false) {
      if (this._followSub?.name === name) this._followSub = null;
      return;
    }
    if (!tick) return;
    const main = series.find((s) => s.parentFp === this._currentDatasetFp
        && s.modelFp === this._currentModelFp)
      || series.find((s) => s.parentFp === this._currentDatasetFp) || series[0];
    this._followSub = { ...main, name };
  }

  /** The server made the subset SUB asked for; show the one the main view
   * follows. */
  _onSubsetDeclared({ fingerprint, parent_fingerprint, model_fp, name }) {
    this._analysis?.noteSubset({ fingerprint, parent_fingerprint, model_fp, name });
    const f = this._followSub;
    if (!f || f.parentFp !== parent_fingerprint || (f.modelFp || null) !== (model_fp || null)
        || f.name !== name) return;
    this._followSub = null;
    if (this._datasets.has(fingerprint)) this._showInMainView(fingerprint);
    else this._showWhenKnown = fingerprint;
  }

  /** Show dataset `fp` in the main view at `frame`, or at the same structure
   * as now where it has it, else its first frame. */
  /** Pick a live run folder in the server's file browser and follow it (ADR 0087). */
  _openLiveRun() {
    this._browser.pick({
      mode: 'open-folder', title: 'Open live run (the folder ffast-lammps printed)',
      okLabel: 'Open', onPick: (path) => { if (path) this._followLiveRun(path); },
    });
  }

  /** Follow the run folder at `path` and go to its tab once it has frames. */
  _followLiveRun(path) {
    if (!this._conn) return;
    this._liveRunWanted = true;
    this._conn.send(OUT.OPEN_LIVE_RUN, { path }, []);
    this._setStatus('Opening live run…', 'connected');
  }

  /** A live panel's command (stop, stop on alarm, attribution; live_ui.js). */
  _liveCommand(cmd) {
    if (this._conn) this._live().command(cmd);
  }

  /** "Make reference" over the loaded datasets (not live runs' frames). */
  _makeReference() {
    const datasets = [...this._datasets.entries()]
      .filter(([fp]) => !fp.startsWith('live:'))
      .map(([fp, meta]) => ({ fp, name: meta.name || fp.slice(0, 8), n: meta.n }));
    this._live().openMakeReference({
      datasets,
      pickFolder: (onPick) => this._browser.pick({ mode: 'open-folder', title: 'Save reference in',
        okLabel: 'Choose', onPick }),
    });
  }

  /** "Run with FFAST" (ADR 0086): files on the server's disk; sbatch from
   * this computer over SSH when connected through a cluster profile, else
   * from the server's node. */
  _runWithFfast() {
    const client = this._clusterClient;
    const practice = this._tourId === 'own-lammps';
    if (practice && !this._practiceInput) return;
    this._live().openRunWithFfast({
      initialFolder: practice ? this._practiceInput.folder : '',
      initialReference: practice ? this._practiceInput.reference : '', practice,
      // The practice run's id, for the own-LAMMPS tour's later steps.
      written: (kw) => { if (kw.ok && kw.practice) this._practiceId = kw.run_id; },
      localAvailable: !!this._conn?.localLammps && this._conn?._url !== this._clusterServerUrl,
      signal: (name, detail) => {
        this._tourSignal(name, detail);
        if (name === 'rwf-closed' && this._tourId === 'own-lammps' && !detail.started)
          this._tourSignal('practice-failed', { error: 'Run with FFAST was closed. Take the tour again from the LAMMPS menu.' });
      },
      // a folder (the run folder), a file, or a reference: a .bundle folder chosen as one item
      pick: (kind, title, onPick) => this._browser.pick({
        mode: kind === 'folder' ? 'open-folder' : 'save-file', title, okLabel: 'Choose', onPick,
        itemDirs: kind === 'bundle' ? /\.bundle$/ : null }),
      laptop: async () => {
        if (!client) return { ok: false, reason: 'FFAST was not started with ffast-web' };
        const s = await client.state();
        return s.can_submit ? { ok: true }
          : { ok: false, reason: 'not connected through a cluster profile' };
      },
      submitFromLaptop: (folder, script) => client.submit(folder, script),
      status: (text, kind = 'connected') => this._setStatus(text, kind),
    });
  }

  /** The page's live-run controls, made on first use. */
  _live() {
    this._liveUi ??= new LiveUi({
      send: (event, kw) => this._conn?.send(event, kw, []),
      request: (event, kw) => this._request(event, kw, []),
      analysis: this._analysis, mainView: this._mainView,
      shown: () => this._attributionSource(),
      runOf: (fp) => this._analysis.liveRunOf(fp),
      follow: (path) => this._followLiveRun(path),
      askPath: () => this._openLiveRun(),
      // following a run changes the datasets: not while a tab is edited
      whyNoOpen: () => (this._editor.editing ? this._whyNoEdit() : ''),
    });
    return this._liveUi;
  }

  /** A live run's state and scores (ADR 0087): its tab's score panel draws
   * them. The window that opened the run goes to the run's tab, once the run
   * has frames and its tab is listed; the tab's own 3D panel shows them. */
  _onLiveRunUpdate(kw) {
    if (!kw.run_id) {
      if (kw.error) this._setStatus(`Live run: ${kw.error}`, 'error');
      return;
    }
    if (kw.kind === 'scored') { this._onScoredUpdate(kw); return; }
    this._live()._rwf?.onLocalUpdate(kw);
    const previous = this._practiceRun;
    const practice = kw.run_id === this._practiceId;
    if (practice) this._practiceRun = kw;
    this._analysis.setLiveRun(kw);
    this._independent.followLiveFrames();
    this._live().sync();   // the shown frame is explained once the run has a bundle
    const tabId = kw.tab && this._analysis.liveTabId(kw.tab);
    if (practice) {
      const detail = { run_id: kw.run_id };
      if (kw.paused && !previous?.paused) this._tourSignal('practice-paused', detail);
      if (kw.stop_on_alarm !== previous?.stop_on_alarm) this._tourSignal('stop-on-alarm-changed', { ...detail, on: kw.stop_on_alarm });
      for (const alarm of kw.progress?.alarms || []) {
        if (!(previous?.progress?.alarms || []).some((a) => a.kind === alarm.kind)) this._tourSignal('live-alarm', { ...detail, kind: alarm.kind });
      }
      if (kw.end && !previous?.end) {
        if (kw.end.state === 'stopped') this._tourSignal('live-stopped', detail);
        else this._tourSignal('practice-failed', { ...detail, error: `Practice run ${kw.end.state}` });
      }
      // A refusal can precede the analysis directory and its progress file.
      const failure = (kw.events || []).find((e) => ['refused', 'scorer_failed'].includes(e.kind));
      const error = kw.error || failure?.reason || (['failed', 'refused'].includes(kw.progress?.state)
        ? kw.progress?.reason || 'The practice scorer could not read this run' : '');
      if (error) {
        this._setStatus(`Practice run: ${error}`, 'error');
        this._tourSignal('practice-failed', { ...detail, error });
        if (!kw.end && !kw.stop_requested && !(previous?.events || []).some((e) => ['refused', 'scorer_failed'].includes(e.kind))) {
          this._conn?.send(OUT.LIVE_STOP, { run_id: kw.run_id }, []);
        }
        this._liveRunWanted = false;
        return;
      }
      this._tourSignal('practice-progress', detail);
    }
    if (!this._liveRunWanted || !kw.fingerprint || !tabId || ['watch-lammps', 'own-lammps'].includes(this._tourId) && this._practiceId && !practice) return;
    this._liveRunWanted = false;
    this._setStatus(`Live run ${kw.run_id}`, 'connected');
    // The tab's own 3D panel shows the run; the main view keeps what it shows.
    this._selectTab(tabId);
    this._live()._rwf?.onLocalFrame(kw);
    if (practice) this._tourSignal('live-first-frame', { run_id: kw.run_id });
  }

  /** A scored item's state and scores (ADR 0093). Its tab opens once, when the item
   * this window asked for first has frames and a tab. Nothing of a live run's own
   * (practice runs, Run with FFAST, stop) applies. */
  _onScoredUpdate(kw) {
    this._analysis.setLiveRun(kw);
    this._independent.followLiveFrames();
    this._live().sync();
    this._openScoredTab(kw);
  }

  /** Open the tab of the scored item this window asked for, once it has frames and a tab. */
  _openScoredTab(kw) {
    if (this._scoreWanted !== kw.run_id || !kw.fingerprint || !kw.tab) return;
    this._scoreWanted = null;
    const tabId = this._analysis.liveTabId(kw.tab);
    if (tabId) this._selectTab(tabId);
    else this._pendingShow = kw.tab;     // the tab list that brings it shows it
  }

  /** The answer to Score / Continue scoring: open the item's tab (now, or when its
   * first update arrives), or say why nothing started. */
  _onScoreStarted(kw) {
    if (kw.error) { this._setStatus(`Scoring: ${kw.error}`, 'error'); return; }
    // The server announces the item again right after this answer, on the same queue:
    // its tab opens on that update. (Not on one held from before: an item removed and
    // scored again still has its old update here, naming a tab that is going away.)
    this._scoreWanted = kw.item_id;
  }

  _showInMainView(fp, frame = null) {
    const from = this._currentDatasetFp;
    const same = from ? sameConfiguration(from, this._main.current?.frame ?? 0, fp, this._datasets) : null;
    this._currentDatasetFp = fp;
    if (fp !== from) this._dropInapplicableModel();
    this._renderObjects();
    this._syncAnalysisContext();
    this._main.ensure(fp).frameOnSnapshot = frame ?? same ?? 0;
    if (this._activePanel) this._openView();
  }

  /** The main view's dataset was announced again. A subset whose SUB was
   * unticked hands the main view back to its parent; one that moved with a
   * zoom is reopened, on the same structure where it still has it. True when
   * it moved the main view. */
  _followMainViewSubset(fp, before, meta) {
    const frame = this._main.current?.frame ?? 0;
    if (meta.active === false && meta.parent) {
      this._showInMainView(meta.parent, before.parent_frames ? before.parent_frames[frame] : frame);
      return true;
    }
    if (framesKey(before) === framesKey(meta)) return false;
    const at = meta.parent_frames ? meta.parent_frames.indexOf(before.parent_frames?.[frame]) : -1;
    this._showInMainView(fp, at >= 0 ? at : 0);
    return true;
  }

  /** The datasets the lists show: all but subsets hidden by unticking SUB. */
  _listedDatasets() {
    return new Map([...this._datasets].filter(([, meta]) => meta.active !== false));
  }

  /**
   * Show the structure behind a clicked plot point (PRD 63, ADR 0056 rules
   * 4 and 5). The 3D panel in this tab that shows the clicked curve's data
   * moves, at the frame that is the same configuration (rule 3: a subset's
   * frame is its parent's frame indices[i]). An independent panel with its
   * own frame moves alone; otherwise the main view moves, and frame-linked
   * panels follow it. When the main view holds no such frame (an unrelated
   * dataset, or a subset that left it out), it switches to the clicked
   * curve's dataset and prediction, so the structure on screen is always the
   * one clicked. A tab where no panel shows it hands over to the tab with a
   * linked 3D panel used last.
   * @param {{datasetFp: string, modelFp: string|null, frame: number}} point
   */
  _onPlotPoint({ datasetFp, modelFp, frame }) {
    if (!this._conn || !this._datasets.has(datasetFp)) return;
    // An independent panel in this tab showing the clicked data moves itself;
    // one whose frame follows the main view moves the main view (rule 2).
    const tab = this._analysis.tab(this._activeTab);
    const showsMain = !!tab && showsMainView(tab.spec);
    const shown = [
      ...(showsMain ? [{ view: null, ds: this._currentDatasetFp, model: this._currentModelFp }] : []),
      ...this._independent.forTab(tab).filter((v) => v.isOpen)
        .map((v) => ({ view: v, ds: v.datasetFp, model: v.modelFp })),
    ];
    const holds = (c) => c.ds && sameConfiguration(datasetFp, frame, c.ds, this._datasets) != null;
    const chosen = shown.find((c) => c.ds === datasetFp && (c.model || null) === (modelFp || null))
      || shown.find((c) => c.ds === datasetFp) || shown.find(holds);
    if (chosen?.view && this._ownFrame(chosen.view)) {
      this._independent.setFrame(chosen.view, sameConfiguration(datasetFp, frame, chosen.view.datasetFp, this._datasets));
      return;
    }
    let target = this._currentDatasetFp
      ? sameConfiguration(datasetFp, frame, this._currentDatasetFp, this._datasets) : null;
    if (target == null) {
      this._currentDatasetFp = datasetFp;
      if (modelFp && predictionApplies(this._models.get(modelFp), datasetFp, this._datasets))
        this._currentModelFp = modelFp;
      else this._dropInapplicableModel();
      this._renderObjects();
      this._syncAnalysisContext();
      target = frame;
    }
    if (!showsMain && !chosen) this._selectTab(this._recentMainViewTabId(), { reopen: false });
    const current = this._main.current;
    const viewIsCurrent = current
      && current.datasetFp === this._currentDatasetFp
      && current.modelFp === this._currentModelFp;
    if (viewIsCurrent) {
      this._setMainFrame(target);
    } else {
      this._main.ensure(this._currentDatasetFp).frameOnSnapshot = target;
      this._openView();
    }
  }

  /** Push the current dataset/prediction selection into the analysis manager
   * so its active tab refetches against it (metric channel scope). */
  /** A loaded session carries user metrics this server lacks: ask once. No
   * sends nothing, so the session behaves as if it had carried nothing. */
  async _offerSessionMetrics({ offer_id, metrics }) {
    const answer = await askDialog({
      ...sessionMetricsQuestion(metrics || []), buttons: ['No', 'Add them'] });
    if (answer !== 'Add them') return;
    const r = await this._metricOps.addSessionMetrics(offer_id);
    this._setStatus(r.ok ? 'Added the session\'s metrics' : (r.error || 'Could not add the metrics'),
      r.ok ? 'connected' : 'error');
  }

  /** "Colour atoms by this" (ADR 0057 rule 13): the main view's colouring. */
  _colourAtomsBy(id) {
    if (!this._focusedDatasetFp()) { this._setStatus('Select a dataset to colour', 'error'); return; }
    const why = this._needsControl();
    if (why) { this._setStatus(why, 'error'); return; }
    if (!this._panes.colorBy.chooseMetric(id))
      this._setStatus(`${id} has no value per atom here`, 'error');
    else if (!this._activePanel)
      this._setStatus(`Atoms coloured by ${id}; a tab with a 3D panel shows them`, 'connected');
  }

  /** "Plot it" (rule 13): a panel for a per-structure metric, in the "My
   * metrics" tab; saved like any tab edit. */
  async _plotMetric(id) {
    const why = this._whyNoEdit();
    if (why) { this._setStatus(why, 'error'); return; }
    const metric = this._metricCatalog.find((m) => m.id === id);
    if (!metric) return;
    const { tab, previous } = plotTarget(this._layoutTabs, metricPanel(metric),
      { elementPicker: metric.shape === TABLE_SHAPE });
    this._pendingShow = tab.name;      // the layout that follows the save shows it
    const r = await this._tabOps.save(toSaved(tab), previous);
    if (!r.ok) this._pendingShow = null;
    this._reportTab(r);
  }

  /** A user metric was added, changed or removed (ADR 0057). The server pushes
   * the catalog, layout and lists that changed (ADR 0062); this forgets
   * requests in flight and has every view coloured by a metric ask for its
   * colours again. */
  /** A metric changed, or (`kw.fingerprint`) a load replaced that dataset or
   * prediction under the same fingerprint, so its results may differ. */
  _onMetricsUpdated(kw = {}) {
    this._metricClient?.forgetInFlight();
    // A metric edit rebuilds the tabs through the TAB_LAYOUT that follows; a
    // replaced object does not, so the plots showing it ask again.
    if (kw?.fingerprint) this._analysis?.refreshFor(kw.fingerprint);
    // The metric may have been edited, and a view keeps its old colours until
    // told: the same source again rebuilds its scene.
    for (const view of [...this._main.all(), ...this._independent.values()]) {
      const source = view.settings?.colorBy.source;
      if (view.isOpen && source?.startsWith('metric:'))
        this._sendViewCommand({ type: 'SET_PARAMETER', stage_id: 'ffast.atom_color', parameter: 'source', value: source }, view);
    }
  }

  _syncAnalysisContext() {
    this._syncMetricsContext();
    // The rail's choice is what a reload shows again; while replay settles on
    // what a reload showed, the first dataset it passes is not.
    if (this._autoDatasetFp === null && this._currentDatasetFp)
      saveMainView(this._currentDatasetFp, this._currentModelFp);
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
    // Replay sends the catalog after every dataset and prediction, so a
    // pop-out's or reload's target that has not come by now is gone; and a
    // subset announced again later must not pull the main view back to it.
    if (this._autoDatasetFp !== null || this._autoModelFp !== null) {
      this._autoDatasetFp = this._autoModelFp = null;
      if (this._currentDatasetFp) saveMainView(this._currentDatasetFp, this._currentModelFp);
    }
    this._metricCatalog = kw.metrics || [];
    this._panes.colorBy.setMetricCatalog(this._metricCatalog);
    this._panes.metrics.setCatalog(this._metricCatalog);
    this._analysis?.setMetricCatalog(this._metricCatalog);
    for (const view of this._independent.values()) this._startIndependent(view);
  }

  /** Send TOGGLE_FEATURE("forces", ...) plus its SET_PARAMETERs together —
   * the Force Vectors pane always re-sends all four as one logical action,
   * so the callback and the per-view
   * show path (`_showSettings`) share this one seam.
   * @param {{show: boolean, onlyForces, modelKey: string|null, length: number, normalised: boolean,
   *   thickness?: number, filterEnabled?: boolean, atomIndices?: number[],
   *   atomIndicesByFrame?: object|null}} state
   */
  _applyForceVectorsState(state) {
    this._focusedRenderers()?.setArrowThickness(state.thickness ?? 100);   // drawn here, not by the server
    this._sendToggleFeature('forces', state.show);
    if (state.show) {
      this._sendToggleFeature('only_forces', state.onlyForces)
      this._sendSetParameter('ffast.force_arrows', 'prediction_ref', state.modelKey);
      this._sendSetParameter('ffast.force_arrows', 'length_factor', state.length);
      this._sendSetParameter('ffast.force_arrows', 'normalised', state.normalised);
      this._sendSetParameter('ffast.force_arrows', 'filter_enabled', !!state.filterEnabled);
      this._sendSetParameter('ffast.force_arrows', 'atom_indices', state.atomIndices || []);
      this._sendSetParameter('ffast.force_arrows', 'atom_indices_by_frame', state.atomIndicesByFrame ?? null);
    }
  }

  /** Set the focused view's Origin COM and its checkbox, without the
   * checkbox's own handler (used when 3-atom alignment turns it off). */
  _setOriginCOM(enabled) {
    const s = this._focused()?.settings;
    if (s) s.view.originCenterOfMass = enabled;
    this._panes.camera.setCOM(enabled);
  }

  /** Follow the atoms' centroid as frames advance, preserving the camera's
   * offset from it. This keeps the initial and reset framing intact. */
  _trackCameraCOM(positions, view) {
    if (!positions || positions.length === 0 || !view) return;
    const n = positions.length;
    const sum = [0, 0, 0];
    for (const [x, y, z] of positions) { sum[0] += x; sum[1] += y; sum[2] += z; }
    const centroid = sum.map((v) => v / n);
    const previous = view.trackingCenter;
    view.trackingCenter = centroid;
    const renderer = view.renderers.shown;
    if (!previous || !renderer || !(view.settings?.view.originCenterOfMass ?? true)) return;
    const center = renderer._exportCamera().center;
    renderer.recenterTo(center.map((v, i) => v + centroid[i] - previous[i]));
  }

  /** Bonds "fill from dynamic" (issue 06): the wire only ships bond segments
   * (coordinates), never atom-index pairs, so pairs are recovered by matching
   * each segment endpoint back to the last-rendered atom positions — exact
   * matches since both come from the same server-computed frame. */
  _currentDynamicBondPairs() {
    const scene = this._focusedRenderers().scene;
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
    this._tourSignal('frame-changed');
  }

  /** The active tab when it has the Same step control (ADR 0089), else null. */
  _sameStepTab() {
    const tab = this._analysis?.tab(this._activeTab);
    return tab && (tab.spec.controls || []).includes('same_step') ? tab : null;
  }

  /** Whether the panel shows a live run in a tab with Same step: then its
   * frame is its own, whatever "Follow main view's frame" says. */
  _sameStepOwns(view) {
    return !!this._sameStepTab() && !!this._analysis.liveRunOf(view.datasetFp);
  }

  /** Whether the panel keeps a frame of its own: it does not follow the main
   * view's, or Same step moves it. */
  _ownFrame(view) {
    return !view.links.frame || this._sameStepOwns(view);
  }

  /** Same step in all 3D panels (ADR 0089): the frame of a live run just
   * shown (`source`: its panel's view, or null for the main view) sets the
   * step every other 3D panel showing a live run goes to: its frame nearest
   * that step, or greyed out when the run has none in range. */
  _syncSameStep(source) {
    if (this._syncingStep) return;
    const tab = this._sameStepTab();
    if (!tab || !this._analysis.sameStepOn(tab.id)) return;
    const step = this._analysis.liveRunOf(source.datasetFp)?.steps?.[source.frame];
    if (step == null) return;
    this._syncingStep = true;
    try {
      const where = (kw, fp) => {
        const i = nearestStepFrame(kw.steps, step);
        return i == null ? { missing: `No frame near step ${step} (${kw.name} runs `
          + `${kw.steps[0]}–${kw.steps[kw.steps.length - 1]})` } : { frame: i };
      };
      for (const view of this._independent.forTab(tab)) {
        const kw = view !== source.view && view.isOpen && this._analysis.liveRunOf(view.datasetFp);
        if (!kw) continue;
        const at = where(kw);
        view.missing = at.missing || '';
        const following = source.view ? source.view.followingLive
          : source.frame === (this._datasets.get(source.datasetFp)?.n || 0) - 1;
        if (following != null) view.followingLive = following;
        if (at.frame != null && at.frame !== view.frame) this._independent.setFrame(view, at.frame, { live: true });
        else this._independent.syncCaption(view);
      }
      const mainKw = source.view && showsMainView(tab.spec) && this._analysis.liveRunOf(this._currentDatasetFp);
      if (mainKw) {
        const at = where(mainKw);
        if (at.frame != null && at.frame !== this._main.current?.frame) this._setMainFrame(at.frame);
      }
    } finally {
      this._syncingStep = false;
    }
  }

  /** The view the playback strip moves: the focused independent panel when
   * its frame is its own, else the main view, which frame-linked panels
   * follow (ADR 0056 rule 2). */
  _playbackView() {
    const owner = this._focusedIndependent;
    return owner && this._ownFrame(owner) ? owner : null;
  }

  /** The frame of the view the sidebar acts on. */
  _focusedFrame() {
    return this._focused()?.frame ?? 0;
  }

  /** The playback strip shows the frame and length of the view it moves. */
  _syncPlayback() {
    const view = this._playbackView();
    const fp = view ? view.datasetFp : this._currentDatasetFp;
    if (!fp) return;
    // A paused tab keeps the frame range it had (ADR 0091).
    const n = this._analysis.pausedFrameCount(fp) || this._datasets.get(fp)?.n || 1;
    const frame = view ? (view.frame ?? 0) : (this._main.current?.frame ?? 0);
    this._frameCount = n;
    const slider = document.getElementById('frame-slider');
    slider.max = n - 1;
    slider.value = frame;
    slider.disabled = false;
    this._updateFrameLabel(frame, n);
  }

  _sendSetFrame(frame) {
    const view = this._playbackView();
    if (view) this._independent.setFrame(view, frame);
    else this._sendMainFrame(frame);
  }

  /** Move the main view to `frame`; frame-linked panels follow. */
  _sendMainFrame(frame) {
    const view = this._main.current;
    if (!this._conn || !view) return;
    this._conn.send(OUT.VIEW_COMMAND, {
      type: 'SET_FRAME',
      view_id: view.viewId,
      view_version: 0,  // server applies SET_FRAME without version check
      frame_index: frame,
    });
    view.frame = frame;
    this._syncSameStep({ view: null, datasetFp: view.datasetFp, frame });
    this._liveUi?.sync();   // attribution colours belong to one frame
    if (view === this._focused()) this._reloadForcePicks();
    this._independent.syncLinkedFrames();
  }

  /** Move the main view to `frame` whatever the playback strip moves (a
   * plot click, a view that had to open first). */
  _setMainFrame(frame) {
    const n = this._datasets.get(this._currentDatasetFp)?.n || 1;
    const clamped = Math.max(0, Math.min(n - 1, frame));
    this._sendMainFrame(clamped);
    if (!this._playbackView()) this._syncPlayback();
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

  /** Four shortcuts (ADR 0063): ←/→ step a frame, Space plays or pauses, Esc
   * puts down the armed pick tool, / searches the settings. They are ignored
   * while typing, while a dialog is open, and on a control that uses the key. */
  /** Alt+Shift file shortcuts from the one action list (ADR 0055). A
   *  key whose action does not exist yet does nothing; one that cannot
   *  run now says why. Keys typed in a text field are left alone. */
  _initFileShortcuts() {
    document.addEventListener('keydown', (e) => {
      if (e.defaultPrevented || e.repeat) return;
      if (document.querySelector('.warning-modal:not(.hidden), #fb-modal:not(.hidden), #unit-popover')) return;
      const inText = !!e.target.closest?.('input, textarea, select, [contenteditable]');
      const s = matchShortcut(e, { inText });
      const action = s && this._action(s.action);
      if (!action) return;
      e.preventDefault();
      const why = whyUnavailable(action);
      if (why) { this._setStatus(why, 'error'); return; }
      runAction(action);
    });
  }

  _initShortcuts() {
    document.addEventListener('keydown', (e) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
      const el = e.target;
      if (el.closest?.('input, textarea, select, [contenteditable]')) return;
      if (document.querySelector('.warning-modal:not(.hidden), #fb-modal:not(.hidden), #unit-popover')) return;
      const click = (id) => { e.preventDefault(); document.getElementById(id).click(); };
      // The frame, play and search keys act on the 3D controls, which a tab
      // without a 3D panel keeps parked out of sight (_parkChrome).
      const parked = document.getElementById('chrome3d').contains(document.getElementById('view-controls'));
      if (parked && ['ArrowLeft', 'ArrowRight', ' ', '/'].includes(e.key)) return;
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        if (el.closest?.('[role=tab], [role=radio], [role=menuitem]')) return;
        click(e.key === 'ArrowLeft' ? 'prev-frame-btn' : 'next-frame-btn');
      } else if (e.key === ' ') {
        if (el.closest?.('button, a, [role=button], [role=tab], [role=radio], [role=menuitem]')) return;
        click('play-pause-btn');
      } else if (e.key === 'Escape') {
        if (this._activeTool) this._setActiveTool(null);
      } else if (e.key === '/') {
        e.preventDefault();
        if (document.getElementById('content').classList.contains('sidebar-hidden')) this._setSidebarHidden(false);
        document.getElementById('sidebar-search').focus();
      }
    });
  }

  _togglePlayback() {
    this._playing = !this._playing;
    const btn = document.getElementById('play-pause-btn');
    btn.innerHTML = icon(this._playing ? 'pause' : 'play');
    btn.title = this._playing ? 'Pause' : 'Play';   // the name says what a press does
    btn.setAttribute('aria-label', btn.title);
    if (this._playing) this._playLoop();
  }

  async _playLoop() {
    while (this._playing) {
      const frameStart = performance.now();
      const next = this._currentFrame() + 1 + this._videoSkipFrames();
      if (next > this._frameCount - 1) { this._togglePlayback(); break; }
      this._patchPending = true;
      this._setFrame(next);
      // Response-synced: wait for the server's
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

  // ── popped-out 3D view ────────────────────────────────────────────────────
  _openPopout() {
    const datasetFp = this._focusedDatasetFp();
    const modelFp = this._focusedModelFp();
    if (!datasetFp) return;

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
    url.searchParams.set('mode', 'view-live');
    url.searchParams.set('ds', datasetFp);
    if (modelFp) url.searchParams.set('pred', modelFp);
    else url.searchParams.delete('pred');
    window.open(url.toString(), '_blank', 'noopener');
  }

  /** Tell the server `view`'s camera, once it has stopped moving for 100 ms. */
  _sendSetCamera(view, cam) {
    if (!this._conn || !view?.isOpen) return;
    view.localCamera = cam;
    clearTimeout(view.cameraThrottle);
    view.cameraSend = () => {
      view.cameraSend = null;
      if (view.isOpen) this._conn?.send(OUT.VIEW_COMMAND, { type: 'SET_CAMERA', view_id: view.viewId, camera: cam });
    };
    view.cameraThrottle = setTimeout(view.cameraSend, 100);
  }

  /** Send `view`'s camera still waiting out the throttle now, before a request
   * whose reply carries the server's camera (OPEN_VIEW's snapshot) could
   * bring back the one from before it. */
  _flushCamera(view) {
    if (!view) return;
    clearTimeout(view.cameraThrottle);
    view.cameraSend?.();
  }

  _updateFrameLabel(frame, total) {
    document.getElementById('frame-label').textContent = `${frame} / ${Math.max(0, total - 1)}`;
  }

  _getViewDataset(scene) {
    // The scene belongs to the currently selected dataset (object rail).
    return this._currentDatasetFp;
  }

  /** The status line; an error is also a red pop-up, so the next message
   * cannot replace it unread (ADR 0066 rule 7). */
  _setStatus(text, cls) {
    if (cls === 'error') showWarning(text, 'error');
    const el = document.getElementById('status');
    el.textContent = text;
    el.className = cls;
    el.title = this._conn?.role ? `Connection… (${this._conn.role})` : 'Connection…';
  }
}

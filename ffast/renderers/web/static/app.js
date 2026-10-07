/**
 * Application — wires UI, connection, and renderer together.
 */

import { FFastConnection } from './connection.js';
import { Panel3D, ViewRenderers } from './views3d.js';
import { ensureMainView, isLinked3D, MAIN_VIEW_RULE, showsMainView } from './tab_rules.js';
import { sameConfiguration, predictionApplies } from './frame_links.js';
import { infoReadout } from './measure.js';
import { MetricClient } from './metrics.js';
import { AnalysisManager, framesKey } from './analysis.js';
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
import { TabOps } from './tab_ops.js';
import { askDialog, textDialog } from './dialogs.js';
import { TabEditor } from './edit_mode.js';
import { bindMenu, runAction, whyUnavailable } from './actions.js';
import { loadLayout, saveLayout } from './layout_state.js';
import { applyStyle, forceErrorStyle, PUBLICATION_STYLE, RESET_STYLE } from './quick_styles.js';
import { addSectionHelp, bindSidebarSearch, oneSectionOpen } from './sidebar.js';
import { makeResizable } from './resizers.js';
import { loadRecentServers, rememberServer, saveRecentServers } from './recent_servers.js';

/**
 * The five pick tools (ADR 0045 Phase 2). Each mirrors a Qt AtomSelectionBase
 * subclass: `multiselect` caps the picked set, `cycle` makes it a rolling
 * window, `rectangle` enables box-select drag.
 * @type {Object<string,{label:string, icon:string, multiselect:number, cycle?:boolean, rectangle?:boolean}>}
 */
const PICK_TOOLS = {
  // `section`: the sidebar section arming the tool opens (ADR 0055).
  info:    { label: 'Info',    icon: '📐', multiselect: 4,     cycle: true },
  bonds:   { label: 'Bonds',   icon: '🔗', multiselect: 2, section: 'Bonds' },
  align:   { label: 'Align',   icon: '△', multiselect: 3, section: 'Alignment' },
  forces:  { label: 'Force',   icon: '➤', multiselect: 10000, rectangle: true, section: 'Force Vectors' },
  extract: { label: 'Extract', icon: '✂', multiselect: 10000, rectangle: true, section: 'Extract Subset' },
};

/** What each sidebar section is for, behind its "?" (ADR 0055). */
const SECTION_HELP = {
  'Colour By': 'Colours atoms by element, by how far each atom moved, or by a per-atom '
    + 'metric such as force error. Any colouring other than Elements shows a colour bar.',
  'Camera': 'Where you look from. Drag to orbit, scroll to zoom. XY, XZ and YZ snap to an '
    + 'axis view; "Exact angles" sets the angles by number.',
  'Display': 'How atoms are drawn. Hide atoms by index (0 1 2) or element (C, -H); '
    + 'highlight atoms by index.',
  'Bonds': 'Dynamic bonds follow atom distances in every frame. Fixed bonds use your own '
    + 'list of pairs; the Bonds pick tool adds or removes a pair.',
  'Force Vectors': 'Arrows for the force on each atom. Source is the reference data '
    + '(Ground Truth) or a loaded prediction. The Force pick tool limits the arrows to chosen atoms.',
  'Extract Subset': 'Makes a new dataset from chosen atoms. Type indices or elements, or arm '
    + 'the Extract pick tool and click atoms.',
  'Export': 'Saves the current 3D frame as a PNG, on a background colour or transparent.',
  'Alignment': 'Rotates every frame onto a reference so the motion you see is internal. Kabsch '
    + 'aligns the whole structure; 3-atom frame align uses three atoms you pick or type.',
};

/** Numbered next steps for the hint bar, by situation (ADR 0055). */
const HINTS = {
  'no-prediction': [
    ['Load a prediction: ', 'File ▸ Load Prediction…'],
    ['Quick style ', 'Force error'],
    ['Find any setting with the search box', ''],
  ],
  'with-prediction': [
    ['Quick style ', 'Force error', ' shows where the model is wrong'],
    ['Arm a pick tool and click atoms', ''],
    ['Find any setting with the search box', ''],
  ],
};

export class FFastApp {
  constructor() {
    this._conn = null;
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
    this._datasets = new Map();   // fingerprint → meta
    this._models = new Map();     // model fingerprint → {name, dataset_fingerprints}

    this._datasetsViewId = new Map(); // fingerprint → view id in 3D View
    this._viewIdCounter = 0;

    this._currentDatasetFp = null;  // selected dataset (object rail)
    this._currentModelFp = null;    // selected prediction, or null
    this._currentViewId = null;
    this._lastOpenedDatasetFp = null;  // last dataset_ref sent via OPEN_VIEW
    this._openedModelFp = null;        // last prediction_ref sent via OPEN_VIEW
    this._frameOnSnapshot = null;      // frame to show once the opening view's snapshot is in
    this._followSub = null;            // the SUB subset the main view moves to once made
    this._showWhenKnown = null;        // ...and its fingerprint, if announced after the reply
    this._activeTab = null;            // id of the tab on screen
    this._viewShown = false;        // a scene is drawn; else the empty state (ADR 0055)
    this._framedViews = new Set();  // view ids whose first snapshot fitted the atoms
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
      capturePng: (opts) => this.renderer.capturePng(opts),
    });
    // User tabs on the server (ADR 0056): save, delete, hide, export.
    this._tabOps = new TabOps({ send: (event, kwargs) => this._conn?.send(event, kwargs) });
    /** The last layout the server sent, hidden tabs included. */
    this._layoutTabs = [];
    this._pendingShow = null;   // a tab to show once a layout brings it
    // Edit mode (ADR 0056 rule 15): the only way a tab changes.
    this._editor = new TabEditor({
      tabOps: this._tabOps,
      serverTabs: () => this._layoutTabs,
      catalog: () => this._metricCatalog,
      refresh: ({ show } = {}) => this._applyLayout(this._layoutTabs, { show }),
      setStatus: (text, kind) => this._setStatus(text, kind),
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

    // Picking (ADR 0045 Phase 2, issue 10): one armed tool at a time and its
    // accumulated picks (displayed index + scientific atom id). The tool is
    // armed on the active 3D panel's pick controller.
    this._activeTool = null;   // tool id, or null (orbit)
    this._picked = [];         // [{displayIndex, atomId}] for the active tool

    // Save/load session (issue 21): the in-flight op's kind + path, so the
    // next TASK_DONE/TASK_FAILED (see _connect) can report completion.

    // Live pop-out controller (ADR 0044 Phase 4): when opened with
    // ?mode=loupe-live this instance auto-connects, hides the chrome
    // (body.loupe-only), and
    // selects the same dataset/prediction the opener had open — but as its
    // OWN connection, with its own view, driving its own frame/camera.
    this._autoDatasetFp = null;
    this._autoModelFp = null;

    this._initViews();
    this._initUI();
    this._initSidebarPanes();
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
  // TAB_LAYOUT (built by AnalysisManager, ADR 0045 Phase 3), so browser and
  // desktop read the same server-parsed layout. Until a server has sent one,
  // the default 3D tab stands in. A tab may mix 2D and 3D panels; the one set
  // of 3D controls (pick bar, playback strip, settings sidebar) moves into
  // whichever tab is shown, when that tab has a 3D panel.
  _initTabs() {
    this._analysis = new AnalysisManager({
      tabbar: document.getElementById('tabbar'),
      tabpanels: document.getElementById('tabpanels'),
      metricClient: null,
      onSelectTab: (id) => this._selectTab(id),
      onSub: (o) => this._onSub(o),
      onPointFrame: (o) => this._onPlotPoint(o),
    });
    this._applyLayout([]);
  }

  /** Build the tabs from a layout. Linked 3D panels already drawing move into
   * the new layout's linked cells, keeping their renderer and scene; the rest
   * are given back. The tab on screen stays on screen when it still exists. */
  _applyLayout(tabs, { show } = {}) {
    if (tabs !== this._layoutTabs) this._editor.noteLayout(tabs || []);
    this._layoutTabs = tabs || [];
    // A tab asked for (`show`) may only arrive with the next layout: a saved
    // new tab is announced just after the save's answer.
    const currentName = this._analysis.tab(this._activeTab)?.name;
    const wanted = show ?? this._pendingShow ?? currentName;
    const old = [...this._panels3d.values()];
    const reusable = old.filter((panel) => panel.linked);
    this._parkChrome();
    this._setActivePanel(null);
    this._panels3d = new Map();
    // Hidden tabs stay out of the bar; the tab menu offers them again. A tab
    // being edited shows its draft.
    const draft = this._editor.draft;
    const visible = ensureMainView(this._editor.withDraft(this._layoutTabs)
      .filter((t) => !t.hidden || t === draft));
    this._analysis.setLayout(visible);
    for (const { id } of this._analysis.tabList) {
      for (const cell of this._analysis.tab(id).cells3d) {
        if (!isLinked3D(cell.spec) || !reusable.length) continue;
        const panel = reusable.shift();
        panel.spec = cell.spec;
        panel.mount(cell.el);
        this._panels3d.set(cell.el, panel);
      }
    }
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
  }

  /** The first tab with a linked 3D panel; there always is one (ADR 0056 rule 6). */
  _mainViewTabId() {
    return this._analysis.tabList.find((t) => showsMainView(this._analysis.tab(t.id).spec))?.id;
  }

  _disposePanel(panel) {
    if (panel.renderer) this._mainView.remove(panel.renderer);
    if (panel === this._activePanel) this._setActivePanel(null);
    panel.dispose();
  }

  _selectTab(id, { reopen = true } = {}) {
    const tab = this._analysis.tab(id);
    if (!tab) return;
    this._activeTab = id;
    queueMicrotask(() => this._syncTabButtons());
    for (const el of document.querySelectorAll('#tabbar .tab'))
      el.classList.toggle('active', el.dataset.tab === id);
    for (const el of document.querySelectorAll('#tabpanels .tabpanel'))
      el.classList.toggle('active', el.id === `panel-${id}`);

    // The tab is on screen now, so its 3D panels have a size to draw at;
    // a panel's renderer is made the first time its tab is shown.
    const panels = [];
    for (const cell of tab.cells3d) {
      if (!isLinked3D(cell.spec)) continue;   // independent panels: ADR 0056 step 7
      let panel = this._panels3d.get(cell.el);
      if (!panel) {
        panel = new Panel3D(cell.spec);
        panel.mount(cell.el);
        this._panels3d.set(cell.el, panel);
      }
      if (!panel.renderer) {
        this._mainView.add(panel.ensureRenderer((entries, opts) => this._onPick(entries, opts)));
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
    if (panels.length) this._recentMainViewTab = tab.name;
    // Showing the main view with a dataset selected ensures a live view.
    if (reopen && this._activePanel && this._conn && this._currentDatasetFp) this._openView();
    // 2D panels render (or refresh) lazily on activation.
    this._analysis.activate(id);
    this._syncEmptyState();
  }

  /** The 3D controls go into a tab with a 3D panel: the pick bar above its
   * grid, the playback strip below, the settings sidebar beside it. */
  _mountChrome(tab) {
    const byId = (x) => document.getElementById(x);
    tab.mainEl.prepend(byId('pick-bar'));
    tab.mainEl.append(byId('loupe-controls'));
    tab.bodyEl.append(byId('sidebar-resize'), byId('loupe-sidebar'));
  }

  /** Put the 3D controls away, for a tab without a 3D panel. */
  _parkChrome() {
    const byId = (x) => document.getElementById(x);
    byId('chrome3d').append(byId('pick-bar'), byId('loupe-controls'), byId('sidebar-resize'),
      byId('loupe-sidebar'), byId('overlay'), byId('hint-bar'));
  }

  /** Point the 3D controls at `panel`: an armed pick tool moves to it, and so
   * do the empty-state Load Dataset… button and the hint bar. */
  _setActivePanel(panel) {
    if (panel === this._activePanel) return;
    this._activePanel?.pick?.disarm();
    this._activePanel?.viewport.parentElement?.classList.remove('focused');
    this._activePanel = panel;
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
    saveLayout({ focus: { ...(loadLayout().focus || {}), [tab.name]: index } });
  }

  /** The sidebar title names the panel its settings act on (rule 8). */
  _syncSidebarTitle() {
    const el = document.getElementById('sidebar-title');
    if (!el) return;
    const ds = this._datasets.get(this._currentDatasetFp);
    const model = this._models.get(this._currentModelFp);
    const name = (meta, fp) => meta?.name || fp?.slice(0, 8);
    const shows = [ds && name(ds, this._currentDatasetFp), model && name(model, this._currentModelFp)]
      .filter(Boolean).join(' · ');
    el.textContent = this._activePanel && shows ? `Settings — ${shows} (main view)` : 'Settings';
    el.title = el.textContent;
  }

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
        this._sendSetCamera(cam);
        this._panes?.camera.syncFromCamera(cam);
      },
    });
  }

  _initUI() {
    document.getElementById('connect-btn').addEventListener('click', () => this._connect());
    document.getElementById('disconnect-btn').addEventListener('click', () => this._disconnect());
    document.getElementById('status').addEventListener('click', () => this._openConnDialog());
    document.getElementById('conn-close').addEventListener('click', () => this._closeConnDialog());
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
    document.getElementById('play-pause-btn').addEventListener('click', () => this._togglePlayback());
    this._bindPlaybackPop();

    // Object rail load actions — dataset vs prediction mode.
    document.getElementById('add-dataset-btn').addEventListener('click', () => this._browser.open('dataset'));
    document.getElementById('add-prediction-btn').addEventListener('click', () => this._browser.open('prediction'));

    this._actions = this._buildActions();
    bindMenu(
      document.getElementById('file-menu-btn'),
      document.getElementById('file-menu-list'),
      this._actions.filter((a) => a.menu === 'file'),
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
    document.getElementById('tab-edit-btn').addEventListener('click', () => {
      const name = this._analysis.tab(this._activeTab)?.name;
      if (!this._editor.editing && !this._whyNoEdit()) this._editor.editTab(name);
    });
    document.getElementById('hint-dismiss').addEventListener('click', () => this._dismissHint());
    document.getElementById('empty-load-btn').addEventListener('click',
      () => runAction(this._action('load-dataset')));
    this._syncEmptyState();
    // Each modal owns its own controls (ADR 0050).
    this._sessionOps.bindControls();
    this._browser.bindControls();
  }

  /** The one action list (ADR 0055): the File menu today; shortcuts and the
   * command palette read the same entries. Session save/load (issue 21) and
   * subset export (issue 20) reuse one path prompt — the browser has no
   * native server-side save dialog. */
  /** '' when this window may change the shared session, else why not. */
  _needsControl() {
    if (!this._conn) return 'Connect to a server first';
    if (this._conn.role === 'READ_ONLY') return 'Read-only connection';
    return '';
  }

  _buildActions() {
    const needsControl = () => this._needsControl();
    return [
      { id: 'load-dataset', label: 'Load Dataset…', menu: 'file',
        run: () => this._browser.open('dataset'), unavailable: needsControl },
      { id: 'load-prediction', label: 'Load Prediction…', menu: 'file',
        run: () => this._browser.open('prediction'),
        unavailable: () => needsControl() || (this._datasets.size ? '' : 'Load a dataset first') },
      { id: 'save-session', label: 'Save Session…', menu: 'file',
        run: () => this._sessionOps.saveSession(), unavailable: needsControl },
      { id: 'load-session', label: 'Load Session…', menu: 'file',
        run: () => this._sessionOps.loadSession(), unavailable: needsControl },
      { id: 'export-dataset', label: 'Export Selected Dataset…', menu: 'file',
        run: () => this._sessionOps.exportSelectedDataset(),
        unavailable: () => needsControl() || (this._currentDatasetFp ? '' : 'Select a dataset first') },
      { id: 'connect', label: 'Connect to Server…', menu: 'file', run: () => this._openConnDialog() },
      // Quick styles (ADR 0055): buttons at the top of the sidebar.
      { id: 'style-force', label: 'Quick style: Force error', short: 'Force error',
        title: 'Colour atoms by force error of the selected prediction',
        run: () => {
          const meta = this._models.get(this._currentModelFp);
          this._applyQuickStyle(forceErrorStyle(meta?.name || this._currentModelFp.slice(0, 8)));
          this._sections.open('Colour By');
        },
        unavailable: () => {
          if (!this._models.size) return 'Load a prediction first';
          return this._currentModelFp ? '' : 'Select a prediction first';
        } },
      { id: 'style-publication', label: 'Quick style: Publication', short: 'Publication',
        title: 'White background, orthographic view, no axes',
        run: () => this._applyQuickStyle(PUBLICATION_STYLE) },
      { id: 'style-reset', label: 'Quick style: Reset', short: 'Reset',
        title: 'Back to the default colouring, background and projection',
        run: () => this._applyQuickStyle(RESET_STYLE) },
    ];
  }

  // ── tab actions: the ⋯ menu (ADR 0056 rules 9 and 10) ──────────────────

  /** The ⋯ menu acts on the tab on screen; it also lists hidden tabs. */
  _tabMenuActions() {
    const spec = this._analysis.tab(this._activeTab)?.spec;
    const name = spec?.name;
    const needsControl = () => this._needsControl();
    const actions = [{
      id: 'tab-export', label: 'Export as TOML…',
      run: () => this._exportTab(name),
      unavailable: () => (this._conn ? '' : 'Connect to a server first'),
    }];
    if (spec?.source === 'user' && spec.replaces) {
      actions.push({ id: 'tab-reset', label: 'Reset to original…',
        run: () => this._removeTab(name, 'reset'), unavailable: needsControl });
    } else if (spec?.source === 'user') {
      actions.push({ id: 'tab-delete', label: 'Delete this tab…',
        run: () => this._removeTab(name, 'delete'), unavailable: needsControl });
    }
    actions.push({ id: 'tab-hide', label: 'Hide this tab',
      run: () => this._tabOps.setHidden(name, true).then((r) => this._reportTab(r)),
      unavailable: () => needsControl() || this._whyTabStays(name) });
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
    return actions;
  }

  /** Why tabs cannot be edited now, or ''. */
  _whyNoEdit() {
    if (this._editor.editing) return 'Finish editing the tab first (Save or Cancel)';
    return this._needsControl();
  }

  /** ✎ and + follow whether this window may edit, and Edit mode. */
  _syncTabButtons() {
    const edit = document.getElementById('tab-edit-btn');
    const spec = this._analysis.tab(this._activeTab)?.spec;
    const fromServer = this._layoutTabs.some((t) => t.name === spec?.name);
    const why = this._whyNoEdit() || (fromServer ? '' : 'This tab is not from the server');
    edit.disabled = !!why;
    edit.title = why || 'Edit this tab';
    edit.classList.toggle('active', this._editor.editing);
  }

  /** Why tab `name` cannot be hidden: the main view needs a visible place. */
  _whyTabStays(name) {
    const others = this._layoutTabs.filter((t) => !t.hidden && t.name !== name);
    return others.some(showsMainView) ? '' : MAIN_VIEW_RULE;
  }

  async _removeTab(name, how) {
    const reset = how === 'reset';
    const answer = await askDialog({
      title: reset ? 'Reset to original' : 'Delete tab',
      message: reset
        ? `Reset ${name} to the original? Your edited version is deleted.`
        : `Delete ${name}? Its file on the server is deleted.`,
      buttons: ['Cancel', reset ? 'Reset' : 'Delete'],
    });
    if (answer === 'Reset' || answer === 'Delete')
      this._reportTab(await this._tabOps.remove(name), reset ? 'Reset' : 'Deleted');
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

  /** Say how a tab change went, in the status label. */
  _reportTab(r, verb) {
    if (!r.ok) { this._setStatus(r.error || 'The tab could not be changed', 'error'); return; }
    const done = { save: 'Saved', delete: verb || 'Deleted', hide: 'Hid', show: 'Showing' }[r.action];
    this._setStatus(`${done} ${r.name}`, 'connected');
  }

  _applyQuickStyle(steps) {
    const missed = applyStyle(document.getElementById('loupe-sidebar'), steps);
    if (missed.length) console.warn('Quick style: could not set', missed);
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
    const key = this._models.size ? 'with-prediction' : 'no-prediction';
    const dismissed = loadLayout().hintsDismissed || [];
    const show = this._viewShown && !dismissed.includes(key);
    bar.classList.toggle('hidden', !show);
    if (!show || bar.dataset.hint === key) return;
    bar.dataset.hint = key;
    const text = document.getElementById('hint-text');
    text.replaceChildren(...HINTS[key].map(([lead, strong, tail = ''], i) => {
      const step = document.createElement('span');
      step.className = 'hint-step';   // kept on one line; the bar wraps between steps
      const n = document.createElement('span');
      n.className = 'hint-n';
      n.textContent = String(i + 1);
      const b = document.createElement('b');
      b.textContent = strong;
      step.append(n, lead, b, tail);
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
    document.getElementById('overlay-hint').textContent = why || 'Load a dataset to see it here.';
    this._syncHint();
  }

  // ── sidebar panes (ADR 0045 Phase 1: issues 03-07) ──────────────────────
  _initSidebarPanes() {
    const sidebarEl = document.getElementById('loupe-sidebar');
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
    });

    const camera = createCameraPane(sidebarEl, {
      onOrtho: (enabled) => this.renderer?.setOrthographic(enabled),
      onPreset: (az, el) => this.renderer?.setCameraAngles({ azimuth: az, elevation: el }),
      onManual: (az, el, dist) => this.renderer?.setCameraAngles({ azimuth: az, elevation: el, distance: dist }),
      onCOM: (enabled) => { this._originCenterOfMass = enabled; },
      onGizmo: (enabled) => this._mainView.setGizmoEnabled(enabled),
      onBackground: (hex) => {
        this._mainView.setBackgroundColor(hex);
        this._panes?.export.setBackground(hex);   // keep the Export bg in step
      },
    });

    const display = createDisplayPane(sidebarEl, {
      onAtomSize: (scale) => this._sendSetParameter('ffast.atom_sizes', 'scale', scale),
      onHideAtoms: (tokens) => this._sendSetParameter('ffast.atom_filter', 'indices', tokens),
      onHighlight: (indices) => this._sendSetSelection('picked', 'current_structure', indices),
      onUnitCell: (visible) => this._sendToggleFeature('no_unit_cell', !visible),
    });

    const bonds = createBondsPane(sidebarEl, {
      onStyle: (width, color, chosen) => this._mainView.setBondStyle(width, color, chosen),
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
    // The rich look sizes ball-and-stick atoms from the Atom size setting.
    this._mainView.atomScale = () => display.atomScale();

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
    btn.textContent = hidden ? '◂ Settings' : 'Settings ▸';
    btn.title = hidden ? 'Show the settings sidebar' : 'Hide the settings sidebar';
    if (remember) saveLayout({ sidebarHidden: hidden });
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
      const host = window.location.hostname || 'localhost';
      document.getElementById('ws-url').value = `ws://${host}:${port}`;
    } else if (recent.length) {
      document.getElementById('ws-url').value = recent[0];
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
    }
    if (port) this._connect();
    else this._openConnDialog();
  }

  // ── connection dialog (ADR 0055) ────────────────────────────────────────

  /** @param {string} [message] why the dialog opened by itself, if it did */
  _openConnDialog(message = '') {
    const msg = document.getElementById('conn-message');
    msg.textContent = message;
    msg.classList.toggle('hidden', !message);
    this._renderRecentServers();
    document.getElementById('conn-dialog').classList.remove('hidden');
    const focus = message && /token/i.test(message) ? 'token-input' : 'ws-url';
    document.getElementById(focus).focus();
  }

  _closeConnDialog() {
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

  async _connect() {
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
      // Set before awaiting connect(): connect() dispatches buffered
      // handshake-time replay messages (REMOTE_DATASET_META, ...) to their
      // handlers *before* its promise resolves. A server with an
      // already-loaded dataset (e.g. a live pop-out's opener already has one
      // open) fires the auto-select-and-open-view path during that replay —
      // if this._conn were still null then, _openView()'s guard would bail
      // and the view would never open.
      this._conn = conn;

      // Metric channel (ADR 0045 Phase 3). The metric client registers the
      // METRIC_RESULT handler; build it before connect so buffered
      // handshake-time replays reach it.
      this._metricClient = new MetricClient(conn);
      this._analysis.setMetricClient(this._metricClient);
      conn.on(IN.TAB_LAYOUT, (kw) => this._applyLayout(kw.tabs || []));
      conn.on(IN.TAB_SAVED, (kw) => this._tabOps.onSaved(kw));
      conn.on(IN.TAB_EXPORTED, (kw) => this._tabOps.onExported(kw));

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
      conn.on(IN.SUBSET_DECLARED, (kw) => this._onSubsetDeclared(kw));
      // Session outcomes are named events now, not a guess from the next
      // TASK_DONE — which resolved a pending save against an unrelated task's
      // completion (ADR 0050).
      conn.on(IN.SESSION_SAVED,  (kw) => this._sessionOps.onSessionResult('save', kw));
      conn.on(IN.SESSION_LOADED, (kw) => this._sessionOps.onSessionResult('load', kw));

      await conn.connect();

      this._setStatus(`Connected (${conn.role})`, 'connected');
      document.getElementById('disconnect-btn').disabled = false;
      saveRecentServers(rememberServer(loadRecentServers(), wsUrl));   // never the token
      this._syncEmptyState();
      // A server that needs a token gives a client without a valid one the
      // read-only role rather than refusing it.
      if (conn.role === 'READ_ONLY' && !readOnly) {
        this._openConnDialog(token
          ? 'That token was not accepted, so you are connected read-only. Check it and connect again.'
          : 'This server needs a token to control the session; you are connected read-only. Enter the token and connect again.');
      } else {
        this._closeConnDialog();
      }
      // A READ_ONLY viewer's mutating Control messages are dropped server-side
      // (ADR 0044 Phase 2) — grey out the buttons that would send one, rather
      // than let the click silently do nothing.
      const canMutate = conn.role !== 'READ_ONLY';
      document.getElementById('add-dataset-btn').disabled = !canMutate;
      document.getElementById('add-prediction-btn').disabled = !canMutate;
      this._renderObjects();
      this._syncTabButtons();
      // Fetch the analysis-tab layout (METRIC_CATALOG arrives via connect-replay).
      conn.send(OUT.REQUEST_TAB_LAYOUT, {});

    } catch (err) {
      this._conn = null;   // handshake failed — undo the early assignment above
      console.error('Connection failed:', err);
      this._setStatus(`Error: ${err.message}`, 'error');
      this._openConnDialog(`Could not connect to ${wsUrl} (${err.message}).`);
    }
  }

  _disconnect() {
    if (this._conn) {
      this._conn.close();
      this._conn = null;
    }
    this._metricClient = null;
    this._analysis.setMetricClient(null);
    this._viewShown = false;
    this._framedViews.clear();
    this._mainView.clear();
    this._datasets.clear();
    this._models.clear();
    this._currentDatasetFp = null;
    this._currentModelFp = null;
    this._currentViewId = null;
    this._lastOpenedDatasetFp = null;
    this._openedModelFp = null;
    this._frameOnSnapshot = null;
    this._followSub = null;
    this._showWhenKnown = null;
    this._playing = false;
    this._pendingSessionOp = null;
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
    this._sessionOps.reset();
    this._tabOps.reset();
    this._editor.end(false);   // a draft cannot be saved without a server
    this._setStatus('Disconnected', '');
    this._syncEmptyState();
  }

  _onDatasetMeta(fp, meta) {
    const before = this._datasets.get(fp);
    this._datasets.set(fp, meta);
    if (before && fp === this._currentDatasetFp && this._followMainViewSubset(fp, before, meta)) return;
    if (fp === this._showWhenKnown) {
      this._showWhenKnown = null;
      this._showInMainView(fp);
      return;
    }
    // The first dataset auto-selects so the Loupe has something to show. A
    // live pop-out (ADR 0044 Phase 4) overrides that with the dataset its
    // opener had open, whenever that one's metadata arrives during replay.
    const isAutoTarget = this._autoDatasetFp === fp;
    const firstSelect = !this._currentDatasetFp || isAutoTarget;
    if (firstSelect) this._currentDatasetFp = fp;
    this._renderObjects();
    if (firstSelect) this._syncAnalysisContext();
    if (firstSelect && this._conn && this._activePanel) this._openView();
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
        (meta?.dataset_fingerprints || []).length
        && predictionApplies(meta, this._currentDatasetFp, this._datasets)) {
      this._currentModelFp = fp;
      this._setStatus(`Prediction "${meta.name || fp.slice(0,8)}" ready`, 'connected');
      if (this._activePanel) this._openView();
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
    this._renderQuickStyles();
    this._syncHint();
    this._syncSidebarTitle();
    // The analysis tabs offer their own multi-select over the same objects, so
    // they need the full lists, not just the rail's current pick.
    this._analysis?.setAvailable({ datasets: this._listedDatasets(), models: this._models });
  }

  _renderDatasetList() {
    const list = document.getElementById('dataset-list');
    list.innerHTML = '';
    const listed = this._listedDatasets();
    if (listed.size === 0) {
      list.innerHTML = '<div class="obj-empty">— none loaded —</div>';
      return;
    }
    for (const [fp, meta] of listed) {
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
      // A prediction applies to the dataset it was computed for and its subsets.
      const applies = !dsFp || predictionApplies(meta, dsFp, this._datasets);
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
    this._dropInapplicableModel();
    this._renderObjects();
    this._syncAnalysisContext();
    // Selecting an object drives the main view; a tab without one stays put
    // and just refetches (via the context sync above).
    if (this._activePanel) this._openView();
  }

  /** Drop a prediction that does not apply to the selected dataset. */
  _dropInapplicableModel() {
    if (this._currentModelFp && !predictionApplies(
      this._models.get(this._currentModelFp), this._currentDatasetFp, this._datasets))
      this._currentModelFp = null;
  }

  _selectModel(fp) {
    this._currentModelFp = fp;
    this._renderObjects();
    this._syncAnalysisContext();
    if (this._activePanel) this._openView();
  }

  _openView() {
    if (!this._conn || !this._currentDatasetFp) return;
    this._flushCamera();
    const datasetChanged = this._currentDatasetFp !== this._lastOpenedDatasetFp;
    if (datasetChanged && this._lastOpenedDatasetFp) this._saveDatasetSettings(this._lastOpenedDatasetFp);

    let startFrom = null;
    if (this._datasetsViewId.has(this._currentDatasetFp)) {
      this._currentViewId = `view-${this._datasetsViewId.get(this._currentDatasetFp)}`
    } else {
      this._datasetsViewId.set(this._currentDatasetFp, this._viewIdCounter)
      this._currentViewId = `view-${this._viewIdCounter}`;
      this._viewIdCounter += 1;
      // A frame subset opens looking like its parent, camera included.
      const parent = this._lookParentOf(this._currentDatasetFp);
      if (parent && this._datasetsViewId.has(parent)) {
        startFrom = `view-${this._datasetsViewId.get(parent)}`;
        this._framedViews.add(this._currentViewId);
      }
    }
    this._conn.send(OUT.OPEN_VIEW, {
      view_id: this._currentViewId,
      dataset_ref: this._currentDatasetFp,
      prediction_ref: this._currentModelFp,   // null clears the force overlay
      ...(startFrom ? { start_from: startFrom } : {}),
    });
    this._openedModelFp = this._currentModelFp;
    this._frameCount = this._datasets.get(this._currentDatasetFp)?.n || this._frameCount;
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
    this._panes.colorBy.saveState(fp);
    this._panes.camera.saveState(fp);
    this._panes.display.saveState(fp);
  }

  /** The dataset a frame subset takes its look from until it has its own:
   * its parent. Not an atom subset, whose atoms are numbered differently. */
  _lookParentOf(fp) {
    const meta = this._datasets.get(fp);
    return meta?.parent && meta.parent_frames ? meta.parent : null;
  }

  _restoreDatasetSettings(fp) {
    const parent = this._lookParentOf(fp);
    if (!this._dsSettings.has(fp) && parent && this._dsSettings.has(parent)) fp = parent;
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
    this._panes.colorBy.loadState(fp);
    this._panes.camera.loadState(fp);
    this._panes.display.loadState(fp);
  }


  /** @param {import('./protocol.js').SceneSnapshotKwargs} kw */
  _onSceneSnapshot(kw) {
    const scene = kw.scene;
    if (!scene) return;
    this._viewVersion = scene.version;
    this._mainView.applyScene(scene);
    // Fit the atoms only on a view's first snapshot. Later snapshots (a tab
    // switch reopens the view) carry the camera the user left, which the
    // server keeps via SET_CAMERA (457dcaa); fitting again would discard it.
    const viewKey = scene.view_id || this._currentViewId;
    if (!this._framedViews.has(viewKey)) {
      this._framedViews.add(viewKey);
      this._mainView.frameAtoms();
    }
    this._viewShown = true;
    this._syncEmptyState();
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
      const frame_index = scene.frame_index;
      const n = meta?.n || 1;
      this._frameCount = n;
      const slider = document.getElementById('frame-slider');
      slider.max = n - 1;
      slider.value = frame_index;
      slider.disabled = false;
      this._updateFrameLabel(frame_index, n);
    }
    // A plot click that had to open the view first (_onPlotPoint): the
    // snapshot shows the view's old frame, so ask for the clicked one now.
    if (this._frameOnSnapshot != null && scene.view_id === this._currentViewId) {
      const frame = this._frameOnSnapshot;
      this._frameOnSnapshot = null;
      this._setFrame(frame);
    }
  }

  /** @param {import('./protocol.js').ScenePatchKwargs} kw */
  _onScenePatch(kw) {
    const changed = kw.changed || [];
    this._viewVersion = kw.to_version;
    this._mainView.applyPatch(kw, changed);
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
  // A pick toolbar (one named button per tool) + a contextual strip (active
  // tool, pick count, read-out, clear). The active 3D panel's PickController
  // owns the pointer while a tool is armed and reports resolved atoms here.
  _initPickTools() {
    const toolbar = document.getElementById('pick-toolbar');
    for (const [id, t] of Object.entries(PICK_TOOLS)) {
      const btn = document.createElement('button');
      btn.className = 'pick-tool-btn';
      btn.dataset.tool = id;
      const icon = document.createElement('span');
      icon.className = 'pick-tool-icon';
      icon.textContent = t.icon;
      btn.append(icon, t.label);
      btn.title = `${t.label} pick tool`;
      btn.addEventListener('click', () => this._setActiveTool(this._activeTool === id ? null : id));
      toolbar.appendChild(btn);
    }
    document.getElementById('pick-clear').addEventListener('click', () => this._clearPicks());
    this._pickReadout = '';
    this._updatePickStrip();
  }

  /** Arm a pick tool (or `null` to return to orbit); toggling the active one disarms. */
  _setActiveTool(id) {
    this._activeTool = id;
    this._clearPicks();   // switching tools starts a fresh picked set
    for (const btn of document.querySelectorAll('#pick-toolbar .pick-tool-btn'))
      btn.classList.toggle('active', btn.dataset.tool === id);
    const pick = this._activePanel?.pick;
    if (id) {
      pick?.arm({ id, ...PICK_TOOLS[id] });
      if (PICK_TOOLS[id].section) this._sections.open(PICK_TOOLS[id].section);
      if (id === 'align') this._panes.align.enableAtomAlignMode();
    } else {
      pick?.disarm();
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
      const pts = this._picked.map((e) => this.renderer.atomPosition(e.displayIndex)).filter(Boolean);
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
    const readout = document.getElementById('pick-readout');
    readout.textContent = this._pickReadout || '';
    readout.title = this._pickReadout || '';   // the full text when "…" cuts it
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
    this._conn.send(OUT.DECLARE_SUBSET, msg);
  }

  /** A plot with SUB ticked made, moved or hid subsets (analysis.js). The
   * main view moves to the one just made: the series showing the main view's
   * dataset and prediction, else the first. */
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
    const f = this._followSub;
    if (!f || f.parentFp !== parent_fingerprint || (f.modelFp || null) !== (model_fp || null)
        || f.name !== name) return;
    this._followSub = null;
    if (this._datasets.has(fingerprint)) this._showInMainView(fingerprint);
    else this._showWhenKnown = fingerprint;
  }

  /** Show dataset `fp` in the main view at `frame`, or at the same structure
   * as now where it has it, else its first frame. */
  _showInMainView(fp, frame = null) {
    const from = this._currentDatasetFp;
    const same = from ? sameConfiguration(from, this._currentFrame(), fp, this._datasets) : null;
    this._currentDatasetFp = fp;
    this._dropInapplicableModel();
    this._renderObjects();
    this._syncAnalysisContext();
    this._frameOnSnapshot = frame ?? same ?? 0;
    if (this._activePanel) this._openView();
  }

  /** The main view's dataset was announced again. A subset whose SUB was
   * unticked hands the main view back to its parent; one that moved with a
   * zoom is reopened, on the same structure where it still has it. True when
   * it moved the main view. */
  _followMainViewSubset(fp, before, meta) {
    const frame = this._currentFrame();
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
   * 4 and 5). The 3D view that holds the clicked curve's data moves: every
   * 3D panel shows the main view for now, so that is the main view, at the
   * frame that is the same configuration (rule 3: a subset's frame is its
   * parent's frame indices[i]). When the main view holds no such frame (an
   * unrelated dataset, or a subset that left it out), it switches to the
   * clicked curve's dataset and prediction, so the structure on screen is
   * always the one clicked. A tab without the main view hands over to the tab
   * with a linked 3D panel used last.
   * @param {{datasetFp: string, modelFp: string|null, frame: number}} point
   */
  _onPlotPoint({ datasetFp, modelFp, frame }) {
    if (!this._conn || !this._datasets.has(datasetFp)) return;
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
    if (!this._activePanel) this._selectTab(this._recentMainViewTabId(), { reopen: false });
    const viewIsCurrent = this._currentViewId
      && this._lastOpenedDatasetFp === this._currentDatasetFp
      && this._openedModelFp === this._currentModelFp;
    if (viewIsCurrent) {
      this._setFrame(target);
    } else {
      this._frameOnSnapshot = target;
      this._openView();
    }
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
   * @param {{show: boolean, onlyForces, modelKey: string|null, length: number, normalised: boolean}} state
   */
  _applyForceVectorsState(state) {
    this._sendToggleFeature('forces', state.show);
    if (state.show) {
      this._sendToggleFeature('only_forces', state.onlyForces)
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
    this.renderer?.recenterTo([sum[0] / n, sum[1] / n, sum[2] / n]);
  }

  /** Bonds "fill from dynamic" (issue 06): the wire only ships bond segments
   * (coordinates), never atom-index pairs, so pairs are recovered by matching
   * each segment endpoint back to the last-rendered atom positions — exact
   * matches since both come from the same server-computed frame. */
  _currentDynamicBondPairs() {
    const scene = this._mainView.scene;
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
    const viewId = this._currentViewId;
    this._cameraSend = () => {
      this._cameraSend = null;
      this._conn?.send(OUT.VIEW_COMMAND, { type: 'SET_CAMERA', view_id: viewId, camera: cam });
    };
    this._cameraThrottle = setTimeout(this._cameraSend, 100);
  }

  /** Send a camera still waiting out the throttle now, before a request
   * whose reply carries the server's camera (OPEN_VIEW's snapshot) could
   * bring back the one from before it. */
  _flushCamera() {
    clearTimeout(this._cameraThrottle);
    this._cameraSend?.();
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

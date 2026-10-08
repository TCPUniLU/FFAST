/**
 * Tab manager (ADR 0045 Phase 3, ADR 0056) — the browser twin of the desktop's
 * config-driven Analysis Tabs (ADR 0021). It builds every tab header and
 * panel grid from the server's `TAB_LAYOUT`, fetches each 2D Panel's metric
 * arrays over the metric channel for the current dataset/prediction, and
 * renders them with `panels.js`.
 *
 * A tab may mix 2D panels with 3D panels (ADR 0056). The grid cells are built
 * once per layout; a 3D cell is left empty here for app.js to put a 3D panel
 * in, and stays put while the 2D cards around it are redrawn. The built-in
 * "3D" tab is one of these tabs. Tabs are appended to `#tabbar` /
 * `#tabpanels`; activation is delegated back to app's `_selectTab`, which
 * toggles `.active` by `panel-<id>`.
 *
 * 2D panels render lazily: a tab's panels are (re)fetched when it becomes the
 * active tab or when the selection context changes while it is active. A
 * monotonic render token discards results that a newer refresh has superseded.
 *
 * Tab-level analysis controls (PRD 59-60):
 *   energy_shift → a shared `shifted` compute-param across the tab's panels
 *   smoothing    → a shared `window` compute-param
 *   element-picker (selector === 'atomic') → which element groups the
 *     grouped_* kinds draw (their row order = the dataset's sorted-unique-Z).
 */

import { helpToggle } from './help.js';
import { renderPanel, PLOT_KINDS, elementSymbol } from './panels.js';
import { KIND_3D } from './tab_rules.js';
import { COLUMN_PX, ROW_PX, sizeTemplate } from './tab_edit.js';
import { predictionApplies } from './frame_links.js';
import { loadTabState, saveTabState } from './layout_state.js';

const PICKER_HELP = 'Each analysis tab chooses its own datasets and predictions to compare. '
  + 'Outlined buttons follow the selection in the left list; click one to pin this '
  + "tab's choice, then click others to add them. The 3D view is not affected.";

/** control name → the shared compute-param it drives. */
const CONTROL_PARAM = { energy_shift: 'shifted', smoothing: 'window' };

/** Plot kinds whose view picks out structures, so SUB can make a subset. */
const SUB_KINDS = new Set(['timeline', 'overlay_timeline', 'scatter', 'density']);

/** A panel's name in a tab's kept state (a reload): what it is and where,
 * so an edit that changes or moves it does not hand it another's zoom. */
function panelKey(spec) {
  return `${spec.kind}|${spec.title || ''}|${spec.row ?? ''},${spec.col ?? ''}`;
}

/** The data a plot draws; a kept zoom applies only to the same data. */
function seriesKey(series) {
  return (series || []).map((s) => `${s.datasetFp}|${s.modelFp || ''}`).join(' ');
}

/** Wait this long after the last zoom or pan before moving the subset. */
const SUB_DELAY_MS = 250;

/** The visible x and y ranges of a Plotly plot, or null before it is drawn. */
export function plotRange(el) {
  const layout = el && el._fullLayout;
  const x = layout?.xaxis?.range, y = layout?.yaxis?.range;
  if (!x) return null;
  return { x: x.map(Number), y: y ? y.map(Number) : null };
}

/**
 * The series the other plots of a tab draw while one plot has SUB ticked:
 * each dataset × prediction pair swapped for the subset that plot made of
 * it, once that subset is listed. Legend names stay as they were.
 * @param {Array<{datasetFp: string, modelFp: string|null, name: string, datasetName: string}>} refs
 * @param {Map<string, string>} subsets `${parentFp}|${modelFp}|${plot}` → subset fingerprint
 * @param {string} plot the name of the plot with SUB ticked
 * @param {Map<string, {name?: string}>} datasets the listed datasets
 */
export function subsetRefs(refs, subsets, plot, datasets) {
  return refs.map((r) => {
    const fp = subsets.get(`${r.datasetFp}|${r.modelFp || ''}|${plot}`);
    const meta = fp && datasets.get(fp);
    return meta ? { ...r, datasetFp: fp, datasetName: meta.name || r.datasetName } : r;
  });
}

/** A short string that changes when a dataset's frames change. */
export function framesKey(meta) {
  const pf = meta?.parent_frames;
  if (!pf) return String(meta?.n ?? '');
  let sum = 0;
  for (const f of pf) sum += f;
  return `${pf.length}:${pf[0]}:${pf[pf.length - 1]}:${sum}`;
}

/**
 * Pair the selected datasets with the selected predictions — one series per
 * drawable combination, in dataset-major order.
 *
 * A prediction applies only to the dataset it was computed for
 * (`datasetFps`); an empty list means "unknown, allow it", matching the object
 * rail's own applicability test. `models` may contain a single `null` to mean
 * reference-only (no prediction selected).
 *
 * Naming follows the desktop's compaction rule for the same problem
 * (`GroupedTableKind.table_left_header`): the prediction alone identifies a
 * series while one dataset is in play, and the dataset joins the name once
 * several are — so four models against one dataset do not produce four copies
 * of its name in the legend.
 *
 * @param {Array<{fp: string, name: string}>} datasets
 * @param {Array<{fp: string, name: string, datasetFps?: string[]}|null>} models
 * @returns {Array<{datasetFp: string, modelFp: string|null, name: string,
 *   datasetName: string, modelName: string}>}
 */
export function pairSeries(datasets, models) {
  const ds = datasets || [];
  const ms = (models && models.length) ? models : [null];
  const manyDatasets = ds.length > 1;
  const out = [];
  for (const d of ds) {
    for (const m of ms) {
      if (m) {
        const fps = m.datasetFps || [];
        if (fps.length && !fps.includes(d.fp)) continue;
      }
      const modelName = m ? m.name : '';
      let name;
      if (!m) name = d.name;
      else if (manyDatasets) name = `${d.name} & ${modelName}`;
      else name = modelName;
      out.push({
        datasetFp: d.fp,
        modelFp: m ? m.fp : null,
        name,
        datasetName: d.name,
        modelName,
      });
    }
  }
  return out;
}

/** Put a grid cell where its panel sits; a scroll strip is one row high. */
function placeCell(el, spec) {
  el.style.gridColumn = `${spec.col + 1} / span ${spec.colspan || 1}`;
  el.style.gridRow = el.classList.contains('analysis-scrollstrip')
    ? String(spec.row + 1) : `${spec.row + 1} / span ${spec.rowspan || 1}`;
}

export class AnalysisManager {
  /**
   * @param {{
   *   tabbar: HTMLElement, tabpanels: HTMLElement,
   *   metricClient?: import('./metrics.js').MetricClient|null,
   *   onSelectTab: (id: string) => void,
   *   onSub: (o: {name: string, series: Array<{parentFp: string, modelFp: string|null}>,
   *     view?: object, active?: boolean, tick?: boolean}) => void,
   *   onPointFrame: (o: {datasetFp: string, modelFp: string|null, frame: number}) => void,
   * }} deps
   */
  constructor(deps) {
    this._tabbar = deps.tabbar;
    this._tabpanels = deps.tabpanels;
    this._metrics = deps.metricClient;
    this._onSelectTab = deps.onSelectTab;
    this._onSub = deps.onSub;
    this._onPointFrame = deps.onPointFrame;

    /** @type {Map<string, object>} id → catalog entry */
    this._catalog = new Map();
    /** @type {Array<object>} per-tab state */
    this._tabs = [];
    this._activeId = null;
    this._ctx = { datasetFp: null, modelFp: null, datasetMeta: null };
    /** Everything loaded, for the per-tab selectors: fp → meta. */
    this._available = { datasets: new Map(), models: new Map() };
    this._renderToken = 0;
    /** Subsets SUB made: `${parentFp}|${modelFp}|${plot}` → fingerprint. */
    this._subsets = new Map();
  }

  /**
   * Publish the loaded datasets/predictions the per-tab selectors offer.
   * Separate from `setContext`, which carries the *rail's* single selection —
   * the rail drives the 3D view, a tab's own selection drives its panels.
   * @param {{datasets: Map<string, object>, models: Map<string, object>}} avail
   */
  setAvailable({ datasets, models }) {
    const active = this._activeTab();
    const before = active && this._drawnKeys(active);
    this._available = {
      datasets: datasets || new Map(),
      models: models || new Map(),
    };
    for (const t of this._tabs) {
      // Drop anything that has since been deleted; an empty list falls back to
      // following the rail rather than showing nothing.
      const before = `${t.selectedDatasets?.length}|${t.selectedModels?.length}`;
      if (t.selectedDatasets)
        t.selectedDatasets = t.selectedDatasets.filter((fp) => this._available.datasets.has(fp));
      if (t.selectedModels)
        t.selectedModels = t.selectedModels.filter((fp) => this._available.models.has(fp));
      if (`${t.selectedDatasets?.length}|${t.selectedModels?.length}` !== before) this._keep(t);
      if (t.seriesSelectorEl) this._renderSeriesSelector(t);
    }
    // A subset following a plot's zoom is announced again on every zoom;
    // redrawing a tab that does not draw it would reset that plot's zoom.
    this._redrawIfChanged(active, before);
  }

  /** The server made a subset for a plot with SUB ticked (SUBSET_DECLARED). */
  noteSubset({ fingerprint, parent_fingerprint, model_fp, name }) {
    const active = this._activeTab();
    const before = active && this._drawnKeys(active);
    this._subsets.set(`${parent_fingerprint}|${model_fp || ''}|${name}`, fingerprint);
    this._redrawIfChanged(active, before);
  }

  /** The live connection's metric channel; null while disconnected. */
  setMetricClient(client) {
    this._metrics = client || null;
    if (!client) this._catalog = new Map();
  }

  /** @param {Array<object>} entries METRIC_CATALOG entries */
  setMetricCatalog(entries) {
    this._catalog = new Map((entries || []).map((e) => [e.id, e]));
    if (this._activeTab()) this._renderTab(this._activeTab());
  }

  /** Build (or rebuild) every tab from a TAB_LAYOUT payload. */
  setLayout(tabs) {
    this.clear();
    (tabs || []).forEach((spec, i) => this._buildTab(spec, i));
  }

  /** @returns {Array<{id: string, name: string}>} the tabs, in bar order */
  get tabList() {
    return this._tabs.map((t) => ({ id: t.id, name: t.spec.name }));
  }

  /**
   * A tab's parts that app.js lays the 3D controls around, and its 3D cells.
   * @returns {null|{id: string, name: string, spec: object, panelEl: HTMLElement,
   *   bodyEl: HTMLElement, mainEl: HTMLElement, gridEl: HTMLElement,
   *   cells3d: Array<{spec: object, index: number, el: HTMLElement}>,
   *   slots: Array<{el: HTMLElement, indices: number[]}>}}
   */
  tab(id) {
    const t = this._tabs.find((x) => x.id === id);
    if (!t) return null;
    const { panelEl, bodyEl, mainEl, gridEl, cells3d, slots } = t;
    return { id, name: t.spec.name, spec: t.spec, panelEl, bodyEl, mainEl, gridEl, cells3d, slots };
  }

  /** Update the current selection context and refresh the active tab. */
  setContext({ datasetFp, modelFp, datasetMeta }) {
    const active = this._activeTab();
    const before = active && this._drawnKeys(active);
    this._ctx = { datasetFp, modelFp, datasetMeta };
    // Element order for the picker/grouped kinds: sorted unique atomic numbers.
    // A frame subset offers its parent's, so the picker (and the plots) stay
    // put as the zoom moves the subset over structures of other make-up.
    let source = datasetMeta;
    while (source?.parent && source.parent_frames && this._available.datasets.has(source.parent))
      source = this._available.datasets.get(source.parent);
    const zs = (source && source.elements) || [];
    this._elementOrder = [...new Set(zs.map(Number))].sort((a, b) => a - b);
    for (const t of this._tabs) {
      // Prune element selection to the new dataset's elements.
      t.selectedElements = t.selectedElements.filter(
        (z) => z === 'All' || this._elementOrder.includes(z));
      if (t.selectorEl) this._renderElementPicker(t);
      // The rail moved, so a tab still following it shows a different default.
      if (t.seriesSelectorEl) this._renderSeriesSelector(t);
    }
    // A tab with its own datasets and predictions draws the same plots as
    // before; redrawing would only lose the zoom (a plot click can move the
    // rail, ADR 0056 rule 4).
    this._redrawIfChanged(active, before);
  }

  /** What a tab's plots depend on: its series, the frames of each (a subset
   * changes them as it follows a zoom), and the elements. */
  _drawnKey(t) {
    return this._drawnKeys(t).all;
  }

  /** What a tab draws: `all` of it, and `subbed`, what its plots other than
   * the one with SUB ticked draw (the subsets, which move with the zoom). */
  _drawnKeys(t) {
    const key = (refs) => JSON.stringify(
      refs.map((r) => [r.datasetFp, framesKey(this._available.datasets.get(r.datasetFp))]));
    const refs = this.seriesRefs(t);
    const all = JSON.stringify([refs, key(refs), this._elementOrder || [], t.selectedElements]);
    const subbed = this._cardRefs(t, null);
    const swapped = subbed.some((r, i) => r.datasetFp !== refs[i].datasetFp);
    return { all, subbed: swapped ? key(subbed) : '' };
  }

  /** Redraw the active tab if what it draws changed: all of it, or only the
   * plots other than the one with SUB ticked, so that plot keeps its zoom. */
  _redrawIfChanged(t, before) {
    if (!t) return;
    const now = this._drawnKeys(t);
    if (now.all !== before.all) this._renderTab(t);
    else if (now.subbed !== before.subbed) this._refreshSubbed(t);
  }

  /** The panel index of the plot with SUB ticked in this tab, or null. */
  _subDriver(t) {
    const [key] = t.subbing.keys();
    return key ?? null;
  }

  /** The series card `spec` draws: the tab's, or, while another plot has SUB
   * ticked, each series' subset from that plot (null: any other plot). */
  _cardRefs(t, spec) {
    const refs = this.seriesRefs(t);
    const driver = this._subDriver(t);
    if (driver == null || (spec && t.spec.panels.indexOf(spec) === driver)) return refs;
    return subsetRefs(refs, this._subsets, this._subName(t, t.spec.panels[driver]),
      this._available.datasets);
  }

  /** Redraw in place every card but the one with SUB ticked. */
  _refreshSubbed(t) {
    const driver = this._subDriver(t);
    for (const slot of t.slots) {
      (slot.cards || []).forEach((card, i) => {
        if (t.spec.panels.indexOf(slot.specs[i]) !== driver)
          this._fetchAndRenderPanel(t, slot.specs[i], card, this._renderToken);
      });
    }
  }

  /** Called by app when a tab is activated (renders analysis tabs lazily). */
  activate(id) {
    this._activeId = id;
    const t = this._tabs.find((x) => x.id === id);
    if (t) this._renderTab(t);
  }

  /** Remove every tab (on relayout). */
  clear() {
    for (const t of this._tabs) {
      t.tabEl.remove();
      t.panelEl.remove();
    }
    this._tabs = [];
  }

  _activeTab() {
    return this._tabs.find((t) => t.id === this._activeId) || null;
  }

  // ── tab construction ────────────────────────────────────────────────────
  _buildTab(spec, index) {
    const id = `tab-${index}`;
    const tabEl = document.createElement('div');
    tabEl.className = 'tab';
    tabEl.textContent = spec.name;
    tabEl.dataset.tab = id;
    // An edited copy of a built-in or project tab says so (ADR 0056 rule 10),
    // and says when the original has changed since.
    if (spec.source === 'user' && spec.replaces) {
      const mark = document.createElement('span');
      mark.className = 'tab-mark' + (spec.original_changed ? ' changed' : '');
      mark.textContent = spec.original_changed ? 'edited · original changed' : 'edited';
      tabEl.title = `Your edited copy of the ${spec.replaces} tab`
        + (spec.original_changed ? '; the original has changed since you edited it' : '')
        + '. Tab actions ⋯ ▸ Reset to original brings the original back.';
      tabEl.appendChild(mark);
    }
    tabEl.addEventListener('click', () => this._onSelectTab(id));
    this._tabbar.appendChild(tabEl);

    // .tabpanel > controls row + body; the body holds the main column (pick
    // bar, grid, playback strip) and, beside it, the 3D settings sidebar —
    // app.js moves those 3D controls into a tab that has a 3D panel.
    const panelEl = document.createElement('div');
    panelEl.className = 'tabpanel analysis-tab';
    panelEl.id = `panel-${id}`;

    const controlsEl = document.createElement('div');
    controlsEl.className = 'analysis-controls';
    const bodyEl = document.createElement('div');
    bodyEl.className = 'tab-body';
    const mainEl = document.createElement('div');
    mainEl.className = 'tab-main';
    const msgEl = document.createElement('div');
    msgEl.className = 'panel-msg tab-msg';
    msgEl.hidden = true;
    const gridEl = document.createElement('div');
    gridEl.className = 'analysis-grid';
    mainEl.append(msgEl, gridEl);
    bodyEl.append(mainEl);
    panelEl.append(controlsEl, bodyEl);
    this._tabpanels.appendChild(panelEl);

    const t = {
      id, spec, tabEl, panelEl, controlsEl, bodyEl, mainEl, msgEl, gridEl,
      slots: [],                        // 2D grid cells: {el, specs}
      cells3d: [],                      // 3D grid cells, for app.js
      sharedParams: {},                 // shifted / window
      selectedElements: ['All'],        // element picker state
      selectorEl: null,
      // null = follow the object rail's single selection (the behaviour before
      // per-tab comparison existed); a list = this tab's own choice.
      selectedDatasets: null,
      selectedModels: null,
      seriesSelectorEl: null,
      // Plots with SUB ticked: panel index → the series it made subsets of.
      subbing: new Map(),
      // Zoomed plots: panelKey → {x, y, series}, kept for a reload.
      zoom: {},
    };
    this._restoreTab(t);
    this._tabs.push(t);
    this._layoutGrid(t);
    this._buildControls(t);
  }

  /** Bring a tab back as it was being explored before a reload (or a layout
   * rebuild): its own datasets and predictions, the plot with SUB ticked —
   * which goes on following the zoom, without moving the main view — and
   * each plot's zoom (applied as the plots are drawn, `_applyZoom`). */
  _restoreTab(t) {
    const kept = loadTabState(t.spec.name);
    if (!kept) return;
    const known = (list, have) => {
      const left = (list || []).filter((fp) => have.has(fp));
      return left.length ? left : null;
    };
    t.selectedDatasets = known(kept.datasets, this._available.datasets);
    t.selectedModels = known(kept.models, this._available.models);
    t.zoom = { ...(kept.zoom || {}) };
    const sub = t.spec.panels.findIndex((sp) => panelKey(sp) === kept.sub);
    if (sub >= 0 && t.selectedDatasets) t.subbing.set(sub, []);
  }

  /** Keep how a tab is being explored, for a reload (`_restoreTab`). */
  _keep(t) {
    const driver = this._subDriver(t);
    saveTabState(t.spec.name, {
      datasets: t.selectedDatasets, models: t.selectedModels,
      sub: driver == null ? null : panelKey(t.spec.panels[driver]),
      zoom: t.zoom,
    });
  }

  /** Note a plot's zoom, or that it shows everything, for a reload. */
  _noteZoom(t, spec, card) {
    const layout = card.body._fullLayout;
    if (!layout?.xaxis) return;
    const key = panelKey(spec);
    const before = JSON.stringify(t.zoom[key] ?? null);
    const range = (axis) => (!axis || axis.autorange ? null : axis.range.map(Number));
    const x = range(layout.xaxis), y = range(layout.yaxis);
    if (!x && !y) delete t.zoom[key];
    else t.zoom[key] = { x, y, series: seriesKey(card.series) };
    if (JSON.stringify(t.zoom[key] ?? null) !== before) this._keep(t);
  }

  /** A plot just drawn takes the zoom it had on the same data; a zoom kept
   * for other data is dropped. */
  async _applyZoom(t, spec, card) {
    const key = panelKey(spec);
    const zoom = t.zoom[key];
    if (!zoom) return;
    if (zoom.series !== seriesKey(card.series)) {
      delete t.zoom[key];
      this._keep(t);
      return;
    }
    const update = {};
    if (zoom.x) update['xaxis.range'] = zoom.x;
    if (zoom.y) update['yaxis.range'] = zoom.y;
    await globalThis.Plotly.relayout(card.body, update);
  }

  /**
   * Make the grid cells once: honour row/col/span, fold scroll_group members
   * into one horizontal strip at the first member's cell, and leave each 3D
   * panel an empty cell. A tab's `column_widths` and `row_heights` are
   * kept as set, 1 being 400 px wide or 300 px high (ADR 0056 rule 12).
   * Without them columns share the width, at least 400 px each; a tab with a
   * single cell gives it the whole height (the built-in "3D" tab); otherwise
   * rows are at least 300 px. The grid scrolls what does not fit.
   */
  _layoutGrid(t) {
    const grid = t.gridEl;
    const panels = t.spec.panels || [];
    this._sizeGrid(t);

    const strips = new Map();   // scroll_group → slot
    panels.forEach((spec, index) => {
      const place = (el) => {
        placeCell(el, spec);
        grid.appendChild(el);
      };
      if (spec.kind === KIND_3D) {
        const el = document.createElement('div');
        el.className = 'analysis-panel panel-3d';
        el.dataset.kind = KIND_3D;
        if (spec.title) {
          el.dataset.title = spec.title;
          const title = document.createElement('div');
          title.className = 'panel-title';
          title.textContent = spec.title;
          el.appendChild(title);
        }
        place(el);
        t.cells3d.push({ spec, index, el });
      } else if (spec.scroll_group) {
        let slot = strips.get(spec.scroll_group);
        if (!slot) {
          const el = document.createElement('div');
          el.className = 'analysis-scrollstrip';
          place(el);
          slot = { el, specs: [], indices: [] };
          strips.set(spec.scroll_group, slot);
          t.slots.push(slot);
        }
        slot.specs.push(spec);
        slot.indices.push(index);
      } else {
        const el = document.createElement('div');
        el.className = 'grid-slot';
        place(el);
        t.slots.push({ el, specs: [spec], indices: [index] });
      }
    });
    t.has2d = t.slots.length > 0;
    grid.classList.toggle('fill', grid.childElementCount === 1);
    // With several 3D panels, the focused one is outlined (ADR 0056 rule 8).
    grid.classList.toggle('multi3d', t.cells3d.length > 1);
  }

  /** The grid's column and row sizes, from the tab's spec. Set sizes are
   * kept as they are; columns without them share the width but keep a
   * minimum (--col-min, index.html). Either way the tab scrolls when they
   * do not fit. */
  _sizeGrid(t) {
    const panels = t.spec.panels || [];
    const widths = t.spec.column_widths, heights = t.spec.row_heights;
    const maxCol = Math.max(1, widths?.length || 0, ...panels.map((p) => p.col + (p.colspan || 1)));
    t.gridEl.style.gridTemplateColumns = widths?.length === maxCol
      ? sizeTemplate(widths, COLUMN_PX) : `repeat(${maxCol}, minmax(var(--col-min), 1fr))`;
    t.gridEl.style.gridTemplateRows = heights?.length ? sizeTemplate(heights, ROW_PX) : '';
  }

  /**
   * Edit mode moved or resized panels of tab `spec.name` and changed nothing
   * else (tab_edit.onlyPlacesChanged): move its cells in place, so the plots
   * keep drawing and follow their cells instead of being drawn again. The
   * cards and their handlers know the panels by object, so the new places
   * are copied onto the panels the tab has.
   * @returns {ReturnType<AnalysisManager['tab']>} the tab's parts, or null
   */
  placeCells(spec) {
    const t = this._tabs.find((x) => x.spec.name === spec.name);
    if (!t || t.spec.panels.length !== spec.panels.length) return null;
    t.spec.panels.forEach((panel, i) => {
      const { row, col, rowspan, colspan } = spec.panels[i];
      Object.assign(panel, { row, col, rowspan, colspan });
    });
    t.spec.column_widths = spec.column_widths;
    t.spec.row_heights = spec.row_heights;
    this._sizeGrid(t);
    for (const slot of t.slots) placeCell(slot.el, slot.specs[0]);
    for (const cell of t.cells3d) placeCell(cell.el, cell.spec);
    return this.tab(t.id);
  }

  _buildControls(t) {
    const el = t.controlsEl;
    el.innerHTML = '';
    const names = new Set([
      ...(t.spec.controls || []),
      ...t.spec.panels.flatMap((p) => p.controls || []),
    ]);
    // Params driven by a tab-rendered control are hidden from EVERY panel's
    // per-panel controls (a shared smoothing slider must not also appear as a
    // redundant per-panel `window` input on a panel that didn't declare it).
    t.controlParams = new Set(
      [...names].map((c) => CONTROL_PARAM[c]).filter(Boolean));

    if (names.has('energy_shift')) {
      const item = document.createElement('div');
      item.className = 'ac-item';
      item.dataset.control = 'energy_shift';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.addEventListener('change', () => {
        if (cb.checked) t.sharedParams.shifted = true;
        else delete t.sharedParams.shifted;
        this._renderTab(t);
      });
      const lbl = document.createElement('label');
      lbl.textContent = 'Energy shift';
      item.append(lbl, cb);
      el.appendChild(item);
    }

    if (names.has('smoothing')) {
      const item = document.createElement('div');
      item.className = 'ac-item';
      item.dataset.control = 'smoothing';
      const lbl = document.createElement('label');
      lbl.textContent = 'Smoothing';
      const range = document.createElement('input');
      range.type = 'range';
      range.min = '1'; range.max = '100'; range.step = '1'; range.value = '1';
      const out = document.createElement('span');
      out.className = 'ac-empty';
      out.textContent = '1';
      range.addEventListener('input', () => { out.textContent = range.value; });
      range.addEventListener('change', () => {
        const w = parseInt(range.value, 10);
        if (w > 1) t.sharedParams.window = w;
        else delete t.sharedParams.window;
        this._renderTab(t);
      });
      item.append(lbl, range, out);
      el.appendChild(item);
    }

    if (t.spec.selector === 'atomic') {
      const item = document.createElement('div');
      item.className = 'ac-item';
      item.dataset.control = 'element-picker';
      const lbl = document.createElement('label');
      lbl.textContent = 'Elements';
      const holder = document.createElement('span');
      holder.className = 'ac-item';
      item.append(lbl, holder);
      el.appendChild(item);
      t.selectorEl = holder;
      this._renderElementPicker(t);
    }

    // Every tab with 2D panels gets the comparison selector — it is not a
    // configured control but the tab's own data scope, the desktop's per-tab
    // DatasetModelSelector. A tab of 3D panels showing the main view has no
    // use for it.
    if (t.has2d) {
      const series = document.createElement('div');
      series.className = 'ac-item ac-series';
      series.dataset.control = 'series-selector';
      el.appendChild(series);
      t.seriesSelectorEl = series;
      this._renderSeriesSelector(t);
    }

    el.hidden = !el.children.length;
  }

  // ── series resolution (dataset × prediction) ────────────────────────────
  //
  // The desktop's per-tab `DatasetModelSelector` holds *lists* and its panels
  // draw one entry per (model, dataset) pair (UI/ContentTab.py,
  // UI/panels.py:211). This is that, resolved per tab.

  _nameOf(which, fp) {
    const meta = this._available[which].get(fp);
    return (meta && meta.name) || (fp ? fp.slice(0, 8) : '');
  }

  /** Datasets this tab draws — its own selection, else the rail's. */
  _tabDatasets(t) {
    if (t.selectedDatasets && t.selectedDatasets.length) return t.selectedDatasets;
    return this._ctx.datasetFp ? [this._ctx.datasetFp] : [];
  }

  /** Predictions this tab draws; `[null]` means reference-only. */
  _tabModels(t) {
    if (t.selectedModels && t.selectedModels.length) return t.selectedModels;
    return this._ctx.modelFp ? [this._ctx.modelFp] : [null];
  }

  /** The datasets a prediction draws on: those it was made for and their
   * subsets; empty when unknown (see `pairSeries`). */
  _datasetsOf(modelFp) {
    const meta = this._available.models.get(modelFp) || {};
    if (!(meta.dataset_fingerprints || []).length) return [];
    const all = this._available.datasets;
    return [...all.keys()].filter((fp) => predictionApplies(meta, fp, all));
  }

  /** The dataset × prediction pairs tab `id`'s picker has selected (or the
   * rail's, while it follows the rail): what a new independent 3D panel in
   * it chooses from (ADR 0056 rule 7). */
  tabPairs(id) {
    const t = this._tabs.find((x) => x.id === id);
    return t ? this.seriesRefs(t).map((r) => ({ datasetFp: r.datasetFp, modelFp: r.modelFp })) : [];
  }

  /** The (dataset × prediction) pairs this tab draws (see `pairSeries`). */
  seriesRefs(t) {
    return pairSeries(
      this._tabDatasets(t).map((fp) => ({ fp, name: this._nameOf('datasets', fp) })),
      this._tabModels(t).map((fp) => fp && {
        fp,
        name: this._nameOf('models', fp),
        datasetFps: this._datasetsOf(fp),
      }),
    );
  }

  _renderSeriesSelector(t) {
    const holder = t.seriesSelectorEl;
    if (!holder) return;
    holder.innerHTML = '';

    const group = (which, label, selected, follow) => {
      const entries = [...this._available[which].entries()];
      if (!entries.length) return;
      const wrap = document.createElement('span');
      wrap.className = 'ac-item';
      wrap.dataset.series = which;
      const lbl = document.createElement('label');
      lbl.textContent = label;
      wrap.appendChild(lbl);
      for (const [fp, meta] of entries) {
        const btn = document.createElement('button');
        const isOn = selected ? selected.includes(fp) : follow.includes(fp);
        btn.className = 'elem-btn' + (isOn ? ' active' : '')
          + (selected ? '' : ' following');
        btn.textContent = meta.name || fp.slice(0, 8);
        btn.dataset.fp = fp;
        btn.title = selected ? '' : 'Following the object rail — click to pin';
        btn.addEventListener('click', () => this._toggleSeries(t, which, fp));
        wrap.appendChild(btn);
      }
      holder.appendChild(wrap);
    };

    group('datasets', 'Datasets', t.selectedDatasets, this._tabDatasets(t));
    group('models', 'Predictions', t.selectedModels,
      this._tabModels(t).filter(Boolean));
    if (!holder.childElementCount) return;
    const { button, note } = helpToggle(PICKER_HELP, {
      open: !!t.pickerHelpOpen,
      onToggle: (open) => { t.pickerHelpOpen = open; },
    });
    holder.append(button, note);
  }

  _toggleSeries(t, which, fp) {
    const key = which === 'datasets' ? 'selectedDatasets' : 'selectedModels';
    // First click on a following tab pins the rail's current choice, then
    // applies the toggle to it — so clicking a second prediction *adds* it
    // rather than silently discarding the one already on screen.
    let list = t[key];
    if (!list) {
      list = which === 'datasets'
        ? [...this._tabDatasets(t)]
        : this._tabModels(t).filter(Boolean);
    }
    list = list.includes(fp) ? list.filter((x) => x !== fp) : [...list, fp];
    // Datasets cannot all be off — a panel with no dataset has nothing to say.
    if (which === 'datasets' && !list.length) list = [...this._tabDatasets(t)];
    t[key] = list;
    this._keep(t);
    this._renderSeriesSelector(t);
    this._renderTab(t);
  }

  _renderElementPicker(t) {
    const holder = t.selectorEl;
    if (!holder) return;
    holder.innerHTML = '';
    const options = ['All', ...(this._elementOrder || [])];
    for (const z of options) {
      const btn = document.createElement('button');
      btn.className = 'elem-btn' + (t.selectedElements.includes(z) ? ' active' : '');
      btn.textContent = z === 'All' ? 'All' : elementSymbol(z);
      btn.dataset.element = String(z);
      btn.addEventListener('click', () => {
        const has = t.selectedElements.includes(z);
        if (has) t.selectedElements = t.selectedElements.filter((x) => x !== z);
        else t.selectedElements = [...t.selectedElements, z];
        if (!t.selectedElements.length) t.selectedElements = ['All'];
        this._renderElementPicker(t);
        this._renderTab(t);
      });
      holder.appendChild(btn);
    }
  }

  // ── rendering ─────────────────────────────────────────────────────────────
  /** Redraw the tab's 2D panels; its 3D cells are left alone. */
  _renderTab(t) {
    const token = ++this._renderToken;
    if (!t.has2d) return;
    let msg = '';
    if (!this._metrics || !this._catalog.size) msg = 'Waiting for metric catalog…';
    else if (!this._tabDatasets(t).length) msg = 'Select a dataset to view this analysis.';
    // A tab of plots only says it once; around a 3D panel each card says it.
    const whole = !!msg && !t.cells3d.length;
    t.msgEl.hidden = !whole;
    t.msgEl.textContent = whole ? msg : '';
    t.gridEl.hidden = whole;
    if (whole) return;

    for (const slot of t.slots) {
      const cards = slot.specs.map((spec) => this._buildPanelCard(t, spec, token));
      slot.el.replaceChildren(...cards.map((c) => c.el));
      slot.cards = cards;
      cards.forEach((card, i) => {
        if (msg) card.body.innerHTML = `<div class="panel-msg">${msg}</div>`;
        else this._fetchAndRenderPanel(t, slot.specs[i], card, token);
      });
    }
  }

  _buildPanelCard(t, spec, token) {
    const el = document.createElement('div');
    el.className = 'analysis-panel';
    el.dataset.kind = spec.kind;
    if (spec.title) el.dataset.title = spec.title;

    const title = document.createElement('div');
    title.className = 'panel-title';
    title.innerHTML = `<span>${spec.title || ''}</span>`;
    if (spec.tooltip) title.title = spec.tooltip;
    el.appendChild(title);

    const body = document.createElement('div');
    const isPlot = PLOT_KINDS.has(spec.kind);
    body.className = isPlot ? 'panel-plot' : '';
    el.appendChild(body);

    const params = document.createElement('div');
    params.className = 'panel-params';
    el.appendChild(params);

    return { el, title, body, params };
  }

  async _fetchAndRenderPanel(t, spec, card, token) {
    const refs = this._cardRefs(t, spec);
    const seq = card.seq = (card.seq || 0) + 1;

    // Assemble the fetch jobs (a role is one id, except `series` = list of ids)
    // and issue them for every series. Requests are keyed per
    // (metric, params, model, dataset), so two series sharing a reference-only
    // metric hit one cache slot rather than computing it twice.
    const jobs = [];
    for (const [role, val] of Object.entries(spec.metrics || {})) {
      if (Array.isArray(val)) val.forEach((id, k) => jobs.push({ role, id, k }));
      else jobs.push({ role, id: val });
    }
    const perSeries = await Promise.all(refs.map((ref) =>
      Promise.all(jobs.map((j) => this._metrics.request(j.id, {
        datasetFp: ref.datasetFp,
        modelFp: ref.modelFp,
        params: this._metricParams(t, spec, j.id),
      })))));
    if (token !== this._renderToken || seq !== card.seq) return;   // superseded

    // A series whose every metric came back empty is dropped rather than drawn
    // as a gap: a prediction that cannot compute this panel should not cost the
    // panel its other predictions.
    const series = [];
    refs.forEach((ref, si) => {
      const results = perSeries[si];
      const data = {};
      jobs.forEach((j, i) => {
        if (j.k !== undefined) { (data[j.role] ||= [])[j.k] = results[i]; }
        else data[j.role] = results[i];
      });
      if (results.some((r) => r && r.nd)) series.push({ ...ref, data });
    });

    if (!series.length) {
      card.body.className = '';
      const anyModel = refs.some((r) => r.modelFp);
      card.body.innerHTML =
        `<div class="panel-msg">${anyModel ? 'No data for this selection.'
          : 'Select a prediction to compute this panel.'}</div>`;
      card.params.innerHTML = '';
      return;
    }
    card.body.className = PLOT_KINDS.has(spec.kind) ? 'panel-plot' : '';

    const ctx = {
      units: this._panelUnits(spec),
      perFrame: this._isPerFrameScatter(spec),
      elementOrder: this._elementOrder || [],
      selectedElements: t.selectedElements,
    };
    renderPanel(card.body, spec, series, ctx);
    card.series = series;
    card.el.dataset.datasets = series.map((x) => x.datasetFp).join(' ');
    if (PLOT_KINDS.has(spec.kind)) {
      // Before SUB is wired: it sends the zoom this plot shows.
      await this._applyZoom(t, spec, card);
      if (token !== this._renderToken || seq !== card.seq) return;   // superseded
    }
    this._wirePanelInteractions(t, spec, card);
    this._buildPanelParams(t, spec, card);
  }

  /** Compute-param values to send for one metric of a panel. */
  _metricParams(t, spec, metricId) {
    const entry = this._catalog.get(metricId);
    const out = {};
    if (!entry || !entry.parameters) return out;
    const overrides = (t.overrides && t.overrides[metricId]) || {};
    for (const [name, p] of Object.entries(entry.parameters)) {
      if (p.role && p.role !== 'compute') continue;
      if (name in t.sharedParams) out[name] = t.sharedParams[name];
      else if (name in overrides) out[name] = overrides[name];
      // else omit → server applies the schema default (better cache reuse).
    }
    return out;
  }

  /** Per-axis units, taken from the bound metrics' catalog units. */
  _panelUnits(spec) {
    const unitOf = (role) => {
      const id = spec.metrics && spec.metrics[role];
      const first = Array.isArray(id) ? id[0] : id;
      const entry = first && this._catalog.get(first);
      return entry ? entry.unit || '' : '';
    };
    return { x: unitOf('x'), y: unitOf('y'), value: unitOf('value') };
  }

  /** A scatter panel is subbable only when its x metric is per-frame. */
  _isPerFrameScatter(spec) {
    if (spec.kind !== 'scatter') return false;
    const xid = spec.metrics && spec.metrics.x;
    const entry = xid && this._catalog.get(Array.isArray(xid) ? xid[0] : xid);
    return !!entry && entry.shape === 'N_frames';
  }

  // ── per-panel compute controls (retune inputs) ──────────────────────────────
  _buildPanelParams(t, spec, card) {
    const holder = card.params;
    holder.innerHTML = '';
    const hidden = new Set(spec.hidden_params || []);
    // Params driven by any tab-rendered control are hidden from the panel too.
    for (const pn of t.controlParams || []) hidden.add(pn);
    const seen = new Set();
    const ids = Object.values(spec.metrics || {}).flat();
    for (const id of ids) {
      const entry = this._catalog.get(id);
      if (!entry || !entry.parameters) continue;
      for (const [name, p] of Object.entries(entry.parameters)) {
        if (p.role && p.role !== 'compute') continue;
        if (hidden.has(name) || seen.has(`${id}:${name}`)) continue;
        seen.add(`${id}:${name}`);
        this._makeParamControl(t, id, name, p, holder, card);
      }
    }
  }

  _makeParamControl(t, metricId, name, p, holder, card) {
    const wrap = document.createElement('div');
    wrap.className = 'pp-item';
    wrap.dataset.param = name;
    const lbl = document.createElement('label');
    lbl.textContent = p.label || name;
    wrap.appendChild(lbl);

    const ensureOverrides = () => {
      t.overrides = t.overrides || {};
      t.overrides[metricId] = t.overrides[metricId] || {};
    };
    const commit = (v) => {
      ensureOverrides();
      t.overrides[metricId][name] = v;
      this._refreshCard(t, card);
    };

    let input;
    if (p.type === 'choice') {
      input = document.createElement('select');
      for (const c of p.choices || []) {
        const o = document.createElement('option');
        o.value = o.textContent = c;
        input.appendChild(o);
      }
      input.value = p.default;
      input.addEventListener('change', () => commit(input.value));
    } else if (p.type === 'bool') {
      input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = !!p.default;
      input.addEventListener('change', () => commit(input.checked));
    } else {
      input = document.createElement('input');
      input.type = 'number';
      input.value = String(p.default ?? 0);
      if (p.min != null) input.min = String(p.min);
      if (p.max != null) input.max = String(p.max);
      input.step = p.type === 'int' ? '1' : 'any';
      input.addEventListener('change', () => {
        const value = p.type === 'int' ? parseInt(input.value, 10) : parseFloat(input.value);
        commit(value);
        p.default = value;
      })
    }
    wrap.appendChild(input);
    holder.appendChild(wrap);
  }

  /** Re-fetch + redraw one panel card in place (a param control changed). */
  _refreshCard(t, card) {
    const spec = t.spec.panels.find((p) =>
      (p.title || '') === (card.el.dataset.title || '') && p.kind === card.el.dataset.kind);
    if (spec) this._fetchAndRenderPanel(t, spec, card, this._renderToken);
  }

  // ── SUB: a subset that follows the plot's zoom ─────────────────────────────
  //
  // As on the desktop, ticking SUB on a plot makes a subset of each series it
  // draws: the frames inside the plot's visible range. It follows every zoom
  // and pan; unticking hides it. The tab's other plots and tables draw the
  // subset instead of the full data and redraw as it moves; the plot with SUB
  // keeps the full data and its zoom. One plot per tab has SUB at a time. The server works out the frames from the
  // view (ffast/session/subbing.py), so a density plot subs by value and a
  // force scatter by structure, as the desktop does.

  _wireSub(t, spec, card) {
    const key = t.spec.panels.indexOf(spec);
    if (!card.subToggle) {
      const toggle = document.createElement('label');
      toggle.className = 'sub-toggle';
      toggle.title = 'Make a subset of the structures on screen; it follows the zoom';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.addEventListener('change', () => {
        const before = this._drawnKeys(t);
        // One plot per tab has SUB: the tab's other plots draw its subset,
        // and two plots would keep subsetting each other's.
        this._untickSub(t);
        if (cb.checked) {
          // The main view moves to the subset; pinning keeps this tab on
          // the data it shows, rather than following the rail to the subset.
          this._pinSeries(t);
          t.subbing.set(key, []);
          t.subTicked = key;
          // A plot that was drawing another plot's subset first redraws on
          // the full data; drawn, it sends its view (below).
          const made = new Set(this._subsets.values());
          if ((card.series || []).some((x) => made.has(x.datasetFp)))
            this._fetchAndRenderPanel(t, spec, card, this._renderToken);
          else this._sendSubViews(t, spec, card);
        }
        this._keep(t);
        if (this._activeTab() === t) this._redrawIfChanged(t, before);
      });
      toggle.append(cb, document.createTextNode('Sub'));
      card.title.appendChild(toggle);
      card.subToggle = cb;
    }
    card.subToggle.checked = t.subbing.has(key);

    const el = card.body;   // its relayout listeners were cleared by the caller
    el.on('plotly_relayout', () => {
      if (!t.subbing.has(key)) return;
      clearTimeout(card.subTimer);
      card.subTimer = setTimeout(() => this._sendSubViews(t, spec, card), SUB_DELAY_MS);
    });
    // A redrawn plot shows its full range again, so the subsets follow.
    if (t.subbing.has(key)) this._sendSubViews(t, spec, card);
  }

  /** Untick SUB on the tab's plot that has it, hiding its subsets. */
  _untickSub(t) {
    const key = this._subDriver(t);
    if (key == null) return;
    this._hideSubsets(t.subbing.get(key) || [], this._subName(t, t.spec.panels[key]));
    t.subbing.delete(key);
    for (const slot of t.slots) {
      const i = slot.specs.findIndex((sp) => t.spec.panels.indexOf(sp) === key);
      const toggle = i >= 0 ? slot.cards?.[i]?.subToggle : null;
      if (toggle) toggle.checked = false;
    }
  }

  /** A subset is named after the plot it was made in. */
  _subName(t, spec) {
    return spec.title || t.spec.name;
  }

  /** Fix the tab's datasets and predictions to what it draws now. */
  _pinSeries(t) {
    if (!t.selectedDatasets) t.selectedDatasets = [...this._tabDatasets(t)];
    const models = this._tabModels(t).filter(Boolean);
    if (!t.selectedModels && models.length) t.selectedModels = models;
    this._renderSeriesSelector(t);
  }

  /** Send the plot's view for each series it draws; hide the subsets of
   * series it no longer draws. The first view after SUB is ticked says so,
   * so the main view moves to the subset. */
  _sendSubViews(t, spec, card) {
    const key = t.spec.panels.indexOf(spec);
    const range = plotRange(card.body);
    if (!this._onSub || !range || !t.subbing.has(key)) return;
    const tick = t.subTicked === key;
    if (tick) t.subTicked = null;
    const name = this._subName(t, spec);
    const ids = Object.values(spec.metrics || {}).flat();
    const view = {
      kind: spec.kind,
      metrics: spec.metrics || {},
      params: Object.fromEntries(ids.map((id) => [id, this._metricParams(t, spec, id)])),
      ...range,
    };
    const series = (card.series || []).map((s) => ({ parentFp: s.datasetFp, modelFp: s.modelFp }));
    const same = (a, b) => a.parentFp === b.parentFp && a.modelFp === b.modelFp;
    this._hideSubsets((t.subbing.get(key) || []).filter((old) => !series.some((s) => same(s, old))), name);
    t.subbing.set(key, series);
    if (series.length) this._onSub({ name, series, view, tick });
  }

  _hideSubsets(series, name) {
    if (series.length) this._onSub?.({ name, series, active: false });
  }

  // ── subbing + point→frame (PRD 61-63) ──────────────────────────────────────
  _wirePanelInteractions(t, spec, card) {
    const el = card.body;
    if (!PLOT_KINDS.has(spec.kind) || typeof el.on !== 'function') return;
    if (el.removeAllListeners) el.removeAllListeners('plotly_relayout');
    el.on('plotly_relayout', () => this._noteZoom(t, spec, card));
    if (SUB_KINDS.has(spec.kind)) this._wireSub(t, spec, card);
    if (!el._subInfo || !el._subInfo.perFrame) return;   // only per-frame kinds

    // Point → frame: click a per-frame point to show that structure (PRD 63).
    // The click names the clicked curve's data, so the structure is looked up
    // in the 3D view that holds it (ADR 0056 rule 4).
    if (el.removeAllListeners) el.removeAllListeners('plotly_click');
    el.on('plotly_click', (ev) => {
      const info = el._subInfo;
      const pt = ev && ev.points && ev.points[0];
      if (!pt || !info) return;
      if (info.dataCurveCount != null && pt.curveNumber >= info.dataCurveCount) return;
      const src = (card.series || [])[(info.curveSeries || [])[pt.curveNumber] ?? 0];
      if (pt.pointIndex != null && src && this._onPointFrame)
        this._onPointFrame({ datasetFp: src.datasetFp, modelFp: src.modelFp, frame: pt.pointIndex });
    });
  }
}

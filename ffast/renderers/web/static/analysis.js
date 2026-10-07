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

const PICKER_HELP = 'Each analysis tab chooses its own datasets and predictions to compare. '
  + 'Outlined buttons follow the selection in the left list; click one to pin this '
  + "tab's choice, then click others to add them. The 3D view is not affected.";

/** control name → the shared compute-param it drives. */
const CONTROL_PARAM = { energy_shift: 'shifted', smoothing: 'window' };

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

export class AnalysisManager {
  /**
   * @param {{
   *   tabbar: HTMLElement, tabpanels: HTMLElement,
   *   metricClient?: import('./metrics.js').MetricClient|null,
   *   onSelectTab: (id: string) => void,
   *   onSub: (o: {parentFp: string, modelFp: string|null, indices: number[], name: string}) => void,
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
  }

  /**
   * Publish the loaded datasets/predictions the per-tab selectors offer.
   * Separate from `setContext`, which carries the *rail's* single selection —
   * the rail drives the 3D view, a tab's own selection drives its panels.
   * @param {{datasets: Map<string, object>, models: Map<string, object>}} avail
   */
  setAvailable({ datasets, models }) {
    this._available = {
      datasets: datasets || new Map(),
      models: models || new Map(),
    };
    for (const t of this._tabs) {
      // Drop anything that has since been deleted; an empty list falls back to
      // following the rail rather than showing nothing.
      if (t.selectedDatasets)
        t.selectedDatasets = t.selectedDatasets.filter((fp) => this._available.datasets.has(fp));
      if (t.selectedModels)
        t.selectedModels = t.selectedModels.filter((fp) => this._available.models.has(fp));
      if (t.seriesSelectorEl) this._renderSeriesSelector(t);
    }
    const active = this._activeTab();
    if (active) this._renderTab(active);
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
   *   bodyEl: HTMLElement, mainEl: HTMLElement,
   *   cells3d: Array<{spec: object, index: number, el: HTMLElement}>}}
   */
  tab(id) {
    const t = this._tabs.find((x) => x.id === id);
    if (!t) return null;
    const { panelEl, bodyEl, mainEl, cells3d } = t;
    return { id, name: t.spec.name, spec: t.spec, panelEl, bodyEl, mainEl, cells3d };
  }

  /** Update the current selection context and refresh the active tab. */
  setContext({ datasetFp, modelFp, datasetMeta }) {
    const active = this._activeTab();
    const before = active && this._drawnKey(active);
    this._ctx = { datasetFp, modelFp, datasetMeta };
    // Element order for the picker/grouped kinds: sorted unique atomic numbers.
    const zs = (datasetMeta && datasetMeta.elements) || [];
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
    if (active && this._drawnKey(active) !== before) this._renderTab(active);
  }

  /** What a tab's plots depend on from the rail: its series and elements. */
  _drawnKey(t) {
    return JSON.stringify([this.seriesRefs(t), this._elementOrder || [], t.selectedElements]);
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
    };
    this._tabs.push(t);
    this._layoutGrid(t);
    this._buildControls(t);
  }

  /**
   * Make the grid cells once: honour row/col/span, fold scroll_group members
   * into one horizontal strip at the first member's cell, and leave each 3D
   * panel an empty cell. A tab with a single cell gives it the whole height
   * (the built-in "3D" tab); otherwise rows are at least 300 px and the grid
   * scrolls.
   */
  _layoutGrid(t) {
    const grid = t.gridEl;
    const panels = t.spec.panels || [];
    const maxCol = Math.max(1, ...panels.map((p) => p.col + (p.colspan || 1)));
    grid.style.gridTemplateColumns = `repeat(${maxCol}, minmax(0, 1fr))`;

    const strips = new Map();   // scroll_group → slot
    panels.forEach((spec, index) => {
      const place = (el) => {
        el.style.gridColumn = `${spec.col + 1} / span ${spec.colspan || 1}`;
        el.style.gridRow = `${spec.row + 1} / span ${spec.rowspan || 1}`;
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
          el.style.gridRow = String(spec.row + 1);
          slot = { el, specs: [] };
          strips.set(spec.scroll_group, slot);
          t.slots.push(slot);
        }
        slot.specs.push(spec);
      } else {
        const el = document.createElement('div');
        el.className = 'grid-slot';
        place(el);
        t.slots.push({ el, specs: [spec] });
      }
    });
    t.has2d = t.slots.length > 0;
    grid.classList.toggle('fill', grid.childElementCount === 1);
    // With several 3D panels, the focused one is outlined (ADR 0056 rule 8).
    grid.classList.toggle('multi3d', t.cells3d.length > 1);
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

  /** The (dataset × prediction) pairs this tab draws (see `pairSeries`). */
  seriesRefs(t) {
    return pairSeries(
      this._tabDatasets(t).map((fp) => ({ fp, name: this._nameOf('datasets', fp) })),
      this._tabModels(t).map((fp) => fp && {
        fp,
        name: this._nameOf('models', fp),
        datasetFps: (this._available.models.get(fp) || {}).dataset_fingerprints || [],
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
    const refs = this.seriesRefs(t);

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
    if (token !== this._renderToken) return;   // superseded

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

  /**
   * Map a Plotly box-select event to parent configuration indices, and to the
   * series they came from.
   *
   * timeline/overlay: x IS the config index, so use the box's x-*range* (a
   * lines trace reports no points) — a range names no curve, so the sub goes to
   * the first series. scatter: markers are selectable, so each selected point's
   * index within its curve *is* its config index, and `curveSeries` says which
   * series that curve belongs to.
   * @returns {{indices: number[], seriesIndex: number}}
   */
  _selectionToIndices(info, ev) {
    if (info.xIsConfigIndex && ev.range && ev.range.x) {
      const [a, b] = ev.range.x;
      const lo = Math.max(0, Math.ceil(Math.min(a, b)));
      const hi = Math.min((info.n || 0) - 1, Math.floor(Math.max(a, b)));
      const out = [];
      for (let i = lo; i <= hi; i++) out.push(i);
      return { indices: out, seriesIndex: 0 };
    }
    const cfg = new Set();
    let seriesIndex = 0;
    let named = false;
    for (const pt of ev.points || []) {
      if (info.dataCurveCount != null && pt.curveNumber >= info.dataCurveCount) continue;
      if (pt.pointIndex != null) cfg.add(pt.pointIndex);
      if (!named && info.curveSeries) {
        // A box can straddle series; the first selected point decides whose
        // subset this is, rather than mixing two datasets into one SubDataset.
        seriesIndex = info.curveSeries[pt.curveNumber] ?? 0;
        named = true;
      }
    }
    return { indices: [...cfg].sort((a, b) => a - b), seriesIndex };
  }

  // ── subbing + point→frame (PRD 61-63) ──────────────────────────────────────
  _wirePanelInteractions(t, spec, card) {
    const el = card.body;
    if (!PLOT_KINDS.has(spec.kind) || typeof el.on !== 'function') return;
    if (!el._subInfo || !el._subInfo.perFrame) return;   // only per-frame kinds

    // A "Sub" toggle (mirrors the desktop subbing checkbox): opt in to putting
    // the plot in box-select mode so a drag declares a SubDataset instead of
    // zooming.
    if (!card.subToggle) {
      const toggle = document.createElement('label');
      toggle.className = 'sub-toggle';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.addEventListener('change', () => {
        globalThis.Plotly.relayout(card.body, { dragmode: cb.checked ? 'select' : 'zoom' });
      });
      toggle.append(cb, document.createTextNode('Sub'));
      card.title.appendChild(toggle);
      card.subToggle = cb;
    }

    // Subbing: box-select → covered configuration indices → live SubDataset.
    if (el.removeAllListeners) el.removeAllListeners('plotly_selected');
    el.on('plotly_selected', (ev) => {
      const info = el._subInfo;
      if (!ev || !info) return;
      const { indices, seriesIndex } = this._selectionToIndices(info, ev);
      const src = (card.series || [])[seriesIndex];
      if (indices.length && src && this._onSub) {
        this._onSub({
          parentFp: src.datasetFp,
          modelFp: src.modelFp,
          indices,
          name: t.spec.name,
        });
      }
    });

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

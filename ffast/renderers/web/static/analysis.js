/**
 * Analysis-tab manager (ADR 0045 Phase 3) — the browser twin of the desktop's
 * config-driven Analysis Tabs (ADR 0021). It builds the tab headers + panel
 * grids from the server's `TAB_LAYOUT`, fetches each Panel's metric arrays over
 * the metric channel for the current dataset/prediction, and renders them with
 * `panels.js`.
 *
 * It owns only the analysis tabs (the 3D Loupe tab stays in app.js). Tabs are
 * appended to `#tabbar` / `#tabpanels`; activation is delegated back to app's
 * generic `_selectTab`, which toggles `.active` by `panel-<id>`.
 *
 * Panels render lazily: a tab's panels are (re)fetched when it becomes the
 * active tab or when the selection context changes while it is active. A
 * monotonic render token discards results that a newer refresh has superseded.
 *
 * Tab-level analysis controls (PRD 59-60):
 *   energy_shift → a shared `shifted` compute-param across the tab's panels
 *   smoothing    → a shared `window` compute-param
 *   element-picker (selector === 'atomic') → which element groups the
 *     grouped_* kinds draw (their row order = the dataset's sorted-unique-Z).
 */

import { renderPanel, PLOT_KINDS, elementSymbol } from './panels.js';

/** control name → the shared compute-param it drives. */
const CONTROL_PARAM = { energy_shift: 'shifted', smoothing: 'window' };

export class AnalysisManager {
  /**
   * @param {{
   *   tabbar: HTMLElement, tabpanels: HTMLElement,
   *   metricClient: import('./metrics.js').MetricClient,
   *   onSelectTab: (id: string) => void,
   *   onSub: (o: {parentFp: string, modelFp: string|null, indices: number[], name: string}) => void,
   *   onPointFrame: (configIndex: number) => void,
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
    this._ctx = { datasetFp: null, modelFp: null, datasetMeta: null, seriesName: '' };
    this._renderToken = 0;
  }

  /** @param {Array<object>} entries METRIC_CATALOG entries */
  setMetricCatalog(entries) {
    this._catalog = new Map((entries || []).map((e) => [e.id, e]));
    if (this._activeTab()) this._renderTab(this._activeTab());
  }

  /** Build (or rebuild) the analysis tabs from a TAB_LAYOUT payload. */
  setLayout(tabs) {
    this.clear();
    (tabs || []).forEach((spec, i) => this._buildTab(spec, i));
  }

  /** Update the current selection context and refresh the active tab. */
  setContext({ datasetFp, modelFp, datasetMeta, seriesName }) {
    this._ctx = { datasetFp, modelFp, datasetMeta, seriesName: seriesName || '' };
    // Element order for the picker/grouped kinds: sorted unique atomic numbers.
    const zs = (datasetMeta && datasetMeta.elements) || [];
    this._elementOrder = [...new Set(zs.map(Number))].sort((a, b) => a - b);
    for (const t of this._tabs) {
      // Prune element selection to the new dataset's elements.
      t.selectedElements = t.selectedElements.filter(
        (z) => z === 'All' || this._elementOrder.includes(z));
      if (t.selectorEl) this._renderElementPicker(t);
    }
    const active = this._activeTab();
    if (active) this._renderTab(active);
  }

  /** Called by app when a tab is activated (renders analysis tabs lazily). */
  activate(id) {
    this._activeId = id;
    const t = this._tabs.find((x) => x.id === id);
    if (t) this._renderTab(t);
  }

  /** Remove all analysis tabs (on disconnect / relayout). */
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
    const id = `analysis-${index}`;
    const tabEl = document.createElement('div');
    tabEl.className = 'tab';
    tabEl.textContent = spec.name;
    tabEl.dataset.tab = id;
    tabEl.addEventListener('click', () => this._onSelectTab(id));
    this._tabbar.appendChild(tabEl);

    const panelEl = document.createElement('div');
    panelEl.className = 'tabpanel analysis-tab';
    panelEl.id = `panel-${id}`;

    const controlsEl = document.createElement('div');
    controlsEl.className = 'analysis-controls';
    const gridEl = document.createElement('div');
    gridEl.className = 'analysis-grid';
    panelEl.append(controlsEl, gridEl);
    this._tabpanels.appendChild(panelEl);

    const t = {
      id, spec, tabEl, panelEl, controlsEl, gridEl,
      sharedParams: {},                 // shifted / window
      selectedElements: ['All'],        // element picker state
      selectorEl: null,
      panelStates: [],                  // one per rendered panel
      built: false,
    };
    this._tabs.push(t);
    this._buildControls(t);
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

    if (!el.children.length) {
      const empty = document.createElement('span');
      empty.className = 'ac-empty';
      empty.textContent = t.spec.name;
      el.appendChild(empty);
    }
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
  _renderTab(t) {
    const token = ++this._renderToken;
    const grid = t.gridEl;
    if (!this._catalog.size) {
      grid.innerHTML = '<div class="panel-msg">Waiting for metric catalog…</div>';
      return;
    }
    if (!this._ctx.datasetFp) {
      grid.innerHTML = '<div class="panel-msg">Select a dataset to view this analysis.</div>';
      return;
    }
    // Lay out the grid: honour row/col, folding scroll_group members into one
    // horizontal strip at the first member's cell.
    grid.innerHTML = '';
    t.panelStates = [];
    const maxCol = Math.max(1, ...t.spec.panels.map((p) => p.col + p.colspan));
    grid.style.gridTemplateColumns = `repeat(${maxCol}, minmax(0, 1fr))`;

    const stripCells = new Map();   // scroll_group → strip element
    for (const spec of t.spec.panels) {
      const card = this._buildPanelCard(t, spec, token);
      if (spec.scroll_group) {
        let strip = stripCells.get(spec.scroll_group);
        if (!strip) {
          strip = document.createElement('div');
          strip.className = 'analysis-scrollstrip';
          strip.style.gridColumn = `${spec.col + 1} / span ${spec.colspan}`;
          strip.style.gridRow = String(spec.row + 1);
          grid.appendChild(strip);
          stripCells.set(spec.scroll_group, strip);
        }
        strip.appendChild(card.el);
      } else {
        card.el.style.gridColumn = `${spec.col + 1} / span ${spec.colspan}`;
        card.el.style.gridRow = `${spec.row + 1} / span ${spec.rowspan}`;
        grid.appendChild(card.el);
      }
      this._fetchAndRenderPanel(t, spec, card, token);
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
    const { datasetFp, modelFp } = this._ctx;

    // Assemble the fetch jobs (a role is one id, except `series` = list of ids).
    const jobs = [];
    for (const [role, val] of Object.entries(spec.metrics || {})) {
      if (Array.isArray(val)) val.forEach((id, k) => jobs.push({ role, id, k }));
      else jobs.push({ role, id: val });
    }
    const results = await Promise.all(jobs.map((j) =>
      this._metrics.request(j.id, {
        datasetFp, modelFp, params: this._metricParams(t, spec, j.id),
      })));
    if (token !== this._renderToken) return;   // superseded

    const data = {};
    jobs.forEach((j, i) => {
      if (j.k !== undefined) { (data[j.role] ||= [])[j.k] = results[i]; }
      else data[j.role] = results[i];
    });

    // Any required data missing → a friendly message rather than a blank plot.
    const anyResult = results.some((r) => r && r.nd);
    if (!anyResult) {
      card.body.className = '';
      card.body.innerHTML =
        `<div class="panel-msg">${modelFp ? 'No data for this selection.'
          : 'Select a prediction to compute this panel.'}</div>`;
      card.params.innerHTML = '';
      return;
    }

    const ctx = {
      seriesName: this._ctx.seriesName,
      units: this._panelUnits(spec),
      perFrame: this._isPerFrameScatter(spec),
      elementOrder: this._elementOrder || [],
      selectedElements: t.selectedElements,
    };
    renderPanel(card.body, spec, data, ctx);
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
      input.addEventListener('change', () =>
        commit(p.type === 'int' ? parseInt(input.value, 10) : parseFloat(input.value)));
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
   * Map a Plotly box-select event to parent configuration indices.
   * timeline/overlay: x IS the config index, so use the box's x-*range* (a
   * lines trace reports no points). scatter: markers are selectable, so map
   * each selected data point's index through subInfo.x.
   */
  _selectionToIndices(info, ev) {
    if (info.xIsConfigIndex && ev.range && ev.range.x) {
      const [a, b] = ev.range.x;
      const lo = Math.max(0, Math.ceil(Math.min(a, b)));
      const hi = Math.min((info.n || 0) - 1, Math.floor(Math.max(a, b)));
      const out = [];
      for (let i = lo; i <= hi; i++) out.push(i);
      return out;
    }
    const cfg = new Set();
    for (const pt of ev.points || []) {
      if (info.dataCurveCount != null && pt.curveNumber >= info.dataCurveCount) continue;
      const ci = info.x ? info.x[pt.pointIndex] : pt.pointIndex;
      if (ci != null) cfg.add(ci);
    }
    return [...cfg].sort((a, b) => a - b);
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
        globalThis.Plotly.relayout(el, { dragmode: cb.checked ? 'select' : 'zoom' });
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
      const indices = this._selectionToIndices(info, ev);
      if (indices.length && this._onSub) {
        this._onSub({
          parentFp: this._ctx.datasetFp,
          modelFp: this._ctx.modelFp,
          indices,
          name: t.spec.name,
        });
      }
    });

    // Point → frame: click a per-frame point to jump the 3D view (PRD 63).
    if (el.removeAllListeners) el.removeAllListeners('plotly_click');
    el.on('plotly_click', (ev) => {
      const info = el._subInfo;
      const pt = ev && ev.points && ev.points[0];
      if (!pt || !info) return;
      if (info.dataCurveCount != null && pt.curveNumber >= info.dataCurveCount) return;
      const ci = info.x ? info.x[pt.pointIndex] : pt.pointIndex;
      if (ci != null && this._onPointFrame) this._onPointFrame(ci);
    });
  }
}

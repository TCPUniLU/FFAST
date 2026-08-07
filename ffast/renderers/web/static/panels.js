/**
 * Panel Kind renderers (ADR 0045 Phase 3) — the browser twin of the desktop's
 * `UI/panels.py` Panel Kinds, drawn with Plotly.js instead of pyqtgraph.
 *
 * Panels are dumb (ADR 0021): each kind renders whatever its resolved metric
 * arrays say. The seven kinds map to Plotly as:
 *   timeline / density / scatter / overlay_timeline / grouped_density  → scatter traces
 *   table / grouped_table                                              → plain HTML tables
 * matching the data→plot contract each Qt kind implements.
 *
 * **A panel draws a list of series**, one per (dataset × prediction) pair the
 * tab has selected — the desktop's "one watched-data entry" (`UI/panels.py`
 * `PanelKind.draw`). Comparing two models in Basic Errors is two series in one
 * panel. Each kind decides what the colour channel means; the grouped kinds
 * hand it to the element picker when more than one element is selected and to
 * the series otherwise, exactly as `GroupedDensityKind.draw` does with its
 * `atom_mode` flag.
 *
 * Each kind is split into a **pure builder** (`spec + series + ctx` → traces and
 * layout, or an HTML string) and a thin draw that hands the result to Plotly or
 * the DOM. The builders are unit-tested without a browser canvas or a Plotly
 * global; nothing about a trace's colour, label or ordering needs a screenshot
 * to verify.
 *
 * Plotly is the vendored UMD global (`globalThis.Plotly`), loaded by a classic
 * <script> before this module (see index.html / vendor/plotly/README.md).
 *
 * Subbing (PRD 61): a plot renderer records `el._subInfo` describing how a
 * Plotly box-select maps selected points → parent **configuration** indices;
 * analysis.js reads it in the `plotly_selected` handler. `curveSeries` maps each
 * trace back to its series index, so a box-select in a multi-series panel subs
 * the dataset that was actually selected. Per-frame kinds (timeline, scatter
 * over per-frame metrics, overlay_timeline) are subbable; density/grouped/table
 * are not (parity-pragmatic — the desktop's per-atom and reduced-source cases
 * are out of the daily-driver scope).
 */

/** @typedef {import('./protocol.js').PanelLayout} PanelLayout */
/** @typedef {import('./metrics.js').MetricResult} MetricResult */

/**
 * One drawable series: a (dataset, prediction) pair and its resolved metrics.
 * @typedef {{
 *   datasetFp: string|null,
 *   modelFp: string|null,
 *   name: string,
 *   datasetName?: string,
 *   modelName?: string,
 *   data: Object<string, MetricResult|MetricResult[]|null>,
 * }} PanelSeries
 */

// Element symbols by atomic number (index 0 unused). Enough of the table to
// label any real dataset; unknown Z falls back to "Z<n>".
const ELEMENTS = [
  '', 'H', 'He', 'Li', 'Be', 'B', 'C', 'N', 'O', 'F', 'Ne', 'Na', 'Mg', 'Al',
  'Si', 'P', 'S', 'Cl', 'Ar', 'K', 'Ca', 'Sc', 'Ti', 'V', 'Cr', 'Mn', 'Fe',
  'Co', 'Ni', 'Cu', 'Zn', 'Ga', 'Ge', 'As', 'Se', 'Br', 'Kr', 'Rb', 'Sr', 'Y',
  'Zr', 'Nb', 'Mo', 'Tc', 'Ru', 'Rh', 'Pd', 'Ag', 'Cd', 'In', 'Sn', 'Sb', 'Te',
  'I', 'Xe', 'Cs', 'Ba', 'La', 'Ce', 'Pr', 'Nd', 'Pm', 'Sm', 'Eu', 'Gd', 'Tb',
  'Dy', 'Ho', 'Er', 'Tm', 'Yb', 'Lu', 'Hf', 'Ta', 'W', 'Re', 'Os', 'Ir', 'Pt',
  'Au', 'Hg', 'Tl', 'Pb', 'Bi', 'Po', 'At', 'Rn',
];

export function elementSymbol(z) {
  return ELEMENTS[z] || `Z${z}`;
}

// Distinct series/element colours (teal-anchored, colour-blind-friendly-ish).
const SERIES_COLORS = [
  '#1ca6bb', '#e0863a', '#7ac74f', '#c85bdb', '#d9534f', '#f0c33c',
  '#9b7ede', '#4e9ad6', '#e0679a', '#6fbf8e',
];

export function seriesColor(i) {
  return SERIES_COLORS[i % SERIES_COLORS.length];
}

// Shared dark Plotly theme, matching the app palette (index.html tokens).
function baseLayout() {
  return {
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: { color: '#b9bbc2', size: 10, family: 'system-ui, sans-serif' },
    margin: { l: 48, r: 12, t: 8, b: 36 },
    showlegend: false,
    // No `dragmode` here on purpose: Plotly.react would otherwise reset the mode
    // on every refresh, undoing a panel's "Sub" toggle (box-select) each redraw.
    xaxis: { gridcolor: '#36393f', zerolinecolor: '#494d55', automargin: true },
    yaxis: { gridcolor: '#36393f', zerolinecolor: '#494d55', automargin: true },
    hovermode: 'closest',
  };
}

/** Turn the legend on once a panel draws more than one thing worth naming. */
function withLegend(layout, traceCount) {
  if (traceCount > 1) {
    layout.showlegend = true;
    layout.legend = { orientation: 'h', y: 1.12, font: { size: 9 } };
    layout.margin.t = 22;
  }
  return layout;
}

export const PLOT_CONFIG = {
  responsive: true,
  displaylogo: false,
  modeBarButtonsToRemove: ['lasso2d', 'autoScale2d', 'toggleSpikelines'],
};

const Plotly = () => globalThis.Plotly;

// Reduce-based extent — never spread a large array into Math.min/Math.max
// (`Math.min(...arr)` overflows the call stack on tens-of-thousands of frames,
// exactly the daily-driver's target data).
function arrMin(a) { let m = Infinity; for (let i = 0; i < a.length; i++) if (a[i] < m) m = a[i]; return m; }
function arrMax(a) { let m = -Infinity; for (let i = 0; i < a.length; i++) if (a[i] > m) m = a[i]; return m; }

/**
 * Resolve an axis label spec (null | "label" | ["label","unitKey"]) to display
 * text. The desktop resolves the `unitKey` (e.g. "energyUnit") through the user
 * config; the browser has no such config, so it appends the bound metric's own
 * catalog unit (`unit`) when the label declares a unit slot — pragmatic-parity
 * units without a second config source.
 */
function axisTitle(label, unit) {
  if (label == null) return '';
  if (Array.isArray(label)) {
    const [text] = label;
    return unit ? `${text} (${unit})` : text;
  }
  return String(label);
}

/** A 1-D metric result as a plain number array (or []). */
function values1d(res) {
  return res && res.nd ? res.nd.values : [];
}

/** Row `i` of a 2-D metric result (e.g. a (2,G) density curve). */
function row(res, i) {
  if (!res || !res.nd) return [];
  const { values, shape } = res.nd;
  if (shape.length < 2) return values;
  const rowLen = shape.slice(1).reduce((a, b) => a * b, 1);
  return values.slice(i * rowLen, (i + 1) * rowLen);
}

/** Series with no usable array for `role` draw nothing — skip, don't blank. */
function seriesWith(series, role) {
  return (series || []).filter((s) => {
    const v = s.data && s.data[role];
    return Array.isArray(v) ? v.some((r) => r && r.nd) : !!(v && v.nd);
  });
}

// ── plot-kind builders (pure) ───────────────────────────────────────────────

export function buildTimeline(spec, series, ctx) {
  const u = ctx.units || {};
  const traces = [];
  const curveSeries = [];
  let n = 0;
  (series || []).forEach((s, i) => {
    const y = values1d(s.data.y);
    if (!y.length) return;
    n = Math.max(n, y.length);
    traces.push({
      x: y.map((_, k) => k), y, type: 'scatter', mode: 'lines',
      line: { color: seriesColor(i), width: 1.5 },
      name: s.name || '',
    });
    curveSeries.push(i);
  });
  const layout = withLegend(baseLayout(), traces.length);
  layout.xaxis.title = { text: axisTitle(spec.x_label, u.x) || 'Configuration index' };
  layout.yaxis.title = { text: axisTitle(spec.y_label, u.y) };
  // x IS the configuration index, so a box-select maps by its x-RANGE (a
  // lines-only trace reports no selected points, but does report a range).
  return {
    traces, layout,
    subInfo: { perFrame: true, xIsConfigIndex: true, n, curveSeries },
  };
}

export function buildDensity(spec, series, ctx) {
  const u = ctx.units || {};
  const drawn = seriesWith(series, 'value');
  // Fill reads as "the distribution" for one curve and as mud for several, so
  // it is a single-series affordance only.
  const fill = drawn.length === 1;
  const traces = drawn.map((s) => {
    const i = series.indexOf(s);
    return {
      x: row(s.data.value, 0), y: row(s.data.value, 1),
      type: 'scatter', mode: 'lines',
      ...(fill ? { fill: 'tozeroy', fillcolor: 'rgba(28,166,187,0.15)' } : {}),
      line: { color: seriesColor(i), width: 1.5 },
      name: s.name || '',
    };
  });
  const layout = withLegend(baseLayout(), traces.length);
  layout.xaxis.title = { text: axisTitle(spec.x_label, u.value) };
  layout.yaxis.title = { text: axisTitle(spec.y_label, u.y) || 'Density' };
  return { traces, layout, subInfo: null };
}

export function buildScatter(spec, series, ctx) {
  const u = ctx.units || {};
  const traces = [];
  const curveSeries = [];
  let lo = Infinity;
  let hi = -Infinity;
  (series || []).forEach((s, i) => {
    const x = values1d(s.data.x);
    const y = values1d(s.data.y);
    if (!x.length || !y.length) return;
    lo = Math.min(lo, arrMin(x), arrMin(y));
    hi = Math.max(hi, arrMax(x), arrMax(y));
    traces.push({
      x, y, type: 'scattergl', mode: 'markers',
      marker: { color: seriesColor(i), size: 4, opacity: 0.6 },
      name: s.name || '',
    });
    curveSeries.push(i);
  });
  // The diagonal spans every series' combined range and is appended last, so
  // dataCurveCount still excludes exactly the non-data trace.
  const dataCurveCount = traces.length;
  if (spec.diagonal && dataCurveCount && Number.isFinite(lo)) {
    traces.push({
      x: [lo, hi], y: [lo, hi], type: 'scatter', mode: 'lines',
      line: { color: '#8a8d94', width: 1, dash: 'dash' },
      hoverinfo: 'skip', showlegend: false,
    });
  }
  const layout = withLegend(baseLayout(), dataCurveCount);
  layout.xaxis.title = { text: axisTitle(spec.x_label, u.x) };
  layout.yaxis.title = { text: axisTitle(spec.y_label, u.y) };
  // Subbable only when the x metric is per-frame: within any one curve the
  // point index *is* the configuration index, whichever series it belongs to.
  return {
    traces, layout,
    subInfo: ctx.perFrame
      ? { perFrame: true, dataCurveCount, curveSeries }
      : null,
  };
}

export function buildOverlayTimeline(spec, series, ctx) {
  const labels = (spec.options && spec.options.series_labels) || [];
  const traces = [];
  const curveSeries = [];
  let n = 0;
  let color = 0;
  (series || []).forEach((s, si) => {
    const list = s.data.series || [];
    list.forEach((res, k) => {
      let y = values1d(res).slice();
      if (!y.length) return;
      // min-subtract + peak-normalise + abs (matches OverlayTimelineKind.draw).
      const mn = arrMin(y);
      y = y.map((v) => v - mn);
      const peak = arrMax(y);
      if (peak > 0) y = y.map((v) => v / peak);
      y = y.map((v) => Math.abs(v));
      n = Math.max(n, y.length);
      // `__NAME__` in a configured label is the series slot — the same
      // substitution the single-series build did, now with a name per pair.
      const label = (labels[k] || '').replace('__NAME__', s.name || '');
      traces.push({
        x: y.map((_, j) => j), y, type: 'scatter', mode: 'lines',
        line: { color: seriesColor(color++), width: 1.5 },
        name: label,
      });
      curveSeries.push(si);
    });
  });
  const layout = withLegend(baseLayout(), Math.max(2, traces.length));
  layout.xaxis.title = { text: axisTitle(spec.x_label, null) || 'Configuration index' };
  layout.yaxis.title = { text: axisTitle(spec.y_label, null) };
  // Overlay series x is the configuration index too → range-based subbing.
  return {
    traces, layout,
    subInfo: { perFrame: true, xIsConfigIndex: true, n, curveSeries },
  };
}

export function buildGroupedDensity(spec, series, ctx) {
  // value: (N_elements, 2, G); optional aggregate: (2, G). The element picker
  // (ctx.selectedElements) chooses which rows draw; row index = position of the
  // element in the dataset's sorted-unique-Z order (server `_element_order`).
  //
  // Colour channel, matching GroupedDensityKind's `atom_mode`: more than one
  // element selected → colour means element (so several series repeat colours
  // and are told apart by the legend label); one element → colour means series.
  const order = ctx.elementOrder || [];
  const selected = ctx.selectedElements || [];
  const multiElement = selected.length > 1;
  const list = series || [];
  const multiSeries = list.length > 1;
  const traces = [];
  const curveSeries = [];

  const suffix = (name) => (multiSeries && name ? ` — ${name}` : '');

  list.forEach((s, si) => {
    if (selected.includes('All') && s.data.aggregate) {
      traces.push({
        x: row(s.data.aggregate, 0), y: row(s.data.aggregate, 1),
        type: 'scatter', mode: 'lines', name: `All${suffix(s.name)}`,
        line: { color: multiElement ? '#b9bbc2' : seriesColor(si), width: 1.5 },
      });
      curveSeries.push(si);
    }
    const value = s.data.value;
    if (!value || !value.nd) return;
    const rowLen = value.nd.shape.slice(1).reduce((a, b) => a * b, 1); // 2*G
    const G = value.nd.shape[value.nd.shape.length - 1];
    for (const z of selected) {
      if (z === 'All') continue;
      const ri = order.indexOf(z);
      if (ri < 0) continue;
      const base = ri * rowLen;
      traces.push({
        x: value.nd.values.slice(base, base + G),
        y: value.nd.values.slice(base + G, base + 2 * G),
        type: 'scatter', mode: 'lines',
        name: `${elementSymbol(z)}${suffix(s.name)}`,
        line: { color: seriesColor(multiElement ? ri : si), width: 1.5 },
      });
      curveSeries.push(si);
    }
  });
  const layout = withLegend(baseLayout(), Math.max(2, traces.length));
  const u = ctx.units || {};
  layout.xaxis.title = { text: axisTitle(spec.x_label, u.value) };
  layout.yaxis.title = { text: axisTitle(spec.y_label, u.y) || 'Density' };
  return { traces, layout, subInfo: null };
}

// ── table-kind builders (pure, return HTML) ─────────────────────────────────

function fmt(v, precision) {
  if (v == null || Number.isNaN(v)) return '—';
  return Number(v).toFixed(precision);
}

function scalarOf(res) {
  const v = values1d(res);
  return v.length ? v[0] : null;
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

/**
 * Scalar metric over the dataset × prediction grid — the desktop's
 * `TableKind` (rows are predictions, columns are datasets).
 *
 * A single series still renders as a 1×1 grid, so the daily-driver case is
 * unchanged in content while gaining the headers that name it.
 */
export function buildTable(spec, series, ctx) {
  const list = series || [];
  if (!list.length) return '<div class="panel-msg">No data for this selection.</div>';

  // Column and row order follow first appearance in the series list, which is
  // the selector's order (dataset-major), not a re-sort.
  const cols = [];
  const rows = [];
  const cellOf = new Map();
  for (const s of list) {
    const cKey = s.datasetFp || '';
    const rKey = s.modelFp || '';
    if (!cols.some((c) => c.key === cKey)) {
      cols.push({ key: cKey, label: s.datasetName || 'Dataset' });
    }
    if (!rows.some((r) => r.key === rKey)) {
      rows.push({ key: rKey, label: s.modelName || 'Reference' });
    }
    cellOf.set(`${rKey}|${cKey}`, scalarOf(s.data.value));
  }

  const head = `<thead><tr><th>${esc(spec.title || 'Value')}</th>`
    + cols.map((c) => `<th>${esc(c.label)}</th>`).join('') + '</tr></thead>';
  const body = '<tbody>' + rows.map((r) =>
    `<tr><td>${esc(r.label)}</td>` + cols.map((c) => {
      const v = cellOf.get(`${r.key}|${c.key}`);
      return `<td>${v === undefined ? '—' : fmt(v, spec.precision)}</td>`;
    }).join('') + '</tr>').join('') + '</tbody>';
  return `<table class="panel-table">${head}${body}</table>`;
}

/**
 * Per-element MAE/RMSE — the desktop's `GroupedTableKind`, including its two
 * modes: more than one element selected → rows are elements; exactly one →
 * rows are the (dataset, prediction) series and the element moves into the
 * column headers, which is how comparing predictions per element works.
 *
 * Multi-element mode reads the **first** series only, as the desktop does
 * (`table_value` uses `datasets[0], models[0]`) — a 2-D element × series table
 * is not a shape either client has.
 */
export function buildGroupedTable(spec, series, ctx) {
  const order = ctx.elementOrder || [];
  const selected = ctx.selectedElements || [];
  const list = series || [];
  if (!list.length) return '<div class="panel-msg">No data for this selection.</div>';

  const cell = (s, z, role, allRole) => {
    if (z === 'All') return fmt(scalarOf(s.data[allRole]), spec.precision);
    const ri = order.indexOf(z);
    if (ri < 0) return '—';
    return fmt(values1d(s.data[role])[ri], spec.precision);
  };

  if (selected.length > 1) {
    const s = list[0];
    const shown = selected.filter((z) => z === 'All' || order.includes(z));
    const head = '<thead><tr><th>Element</th><th>MAE</th><th>RMSE</th></tr></thead>';
    const body = '<tbody>' + shown.map((z) =>
      `<tr><td>${z === 'All' ? 'All' : esc(elementSymbol(z))}</td>`
      + `<td>${cell(s, z, 'mae', 'mae_all')}</td>`
      + `<td>${cell(s, z, 'rmse', 'rmse_all')}</td></tr>`).join('') + '</tbody>';
    return `<table class="panel-table">${head}${body}</table>`;
  }

  // Single-element mode: name the element in the column header, or the table
  // gives no clue which element the error belongs to.
  const z = selected[0] ?? 'All';
  const label = z === 'All' ? 'All' : elementSymbol(z);
  const head = `<thead><tr><th>Object</th><th>${esc(label)} MAE</th>`
    + `<th>${esc(label)} RMSE</th></tr></thead>`;
  const body = '<tbody>' + list.map((s) =>
    `<tr><td>${esc(s.name || 'Reference')}</td>`
    + `<td>${cell(s, z, 'mae', 'mae_all')}</td>`
    + `<td>${cell(s, z, 'rmse', 'rmse_all')}</td></tr>`).join('') + '</tbody>';
  return `<table class="panel-table">${head}${body}</table>`;
}

// ── dispatch ────────────────────────────────────────────────────────────────

const PLOT_BUILDERS = {
  timeline: buildTimeline,
  density: buildDensity,
  scatter: buildScatter,
  overlay_timeline: buildOverlayTimeline,
  grouped_density: buildGroupedDensity,
};

const TABLE_BUILDERS = {
  table: buildTable,
  grouped_table: buildGroupedTable,
};

/** Kinds drawn with Plotly (vs. HTML tables). */
export const PLOT_KINDS = new Set(Object.keys(PLOT_BUILDERS));

/**
 * Build a panel's Plotly traces + layout, or its table HTML, without touching
 * the DOM. Returns null for an unknown kind.
 * @param {PanelLayout} spec
 * @param {PanelSeries[]} series one entry per (dataset × prediction) pair
 * @param {Object} ctx units, element-picker state, perFrame
 */
export function buildPanel(spec, series, ctx) {
  const plot = PLOT_BUILDERS[spec.kind];
  if (plot) return plot(spec, series || [], ctx || {});
  const table = TABLE_BUILDERS[spec.kind];
  if (table) return { html: table(spec, series || [], ctx || {}) };
  return null;
}

/**
 * Render one panel into `el`.
 * @param {HTMLElement} el the panel body (plot div or table container)
 * @param {PanelLayout} spec
 * @param {PanelSeries[]} series
 * @param {Object} ctx render context (units, perFrame, element picker state)
 */
export function renderPanel(el, spec, series, ctx) {
  const built = buildPanel(spec, series, ctx);
  if (!built) {
    el.innerHTML = `<div class="panel-msg">Unknown panel kind “${spec.kind}”.</div>`;
    return;
  }
  if (built.html !== undefined) {
    el.innerHTML = built.html;
    el._subInfo = null;
    return;
  }
  Plotly().react(el, built.traces, built.layout, PLOT_CONFIG);
  el._subInfo = built.subInfo;
}

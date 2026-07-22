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
 * Plotly is the vendored UMD global (`globalThis.Plotly`), loaded by a classic
 * <script> before this module (see index.html / vendor/plotly/README.md).
 *
 * Subbing (PRD 61): a plot renderer records `el._subInfo` describing how a
 * Plotly box-select maps selected points → parent **configuration** indices;
 * analysis.js reads it in the `plotly_selected` handler. Per-frame kinds
 * (timeline, scatter over per-frame metrics, overlay_timeline) are subbable;
 * density/grouped/table are not (parity-pragmatic — the desktop's per-atom and
 * reduced-source cases are out of the daily-driver scope).
 */

/** @typedef {import('./protocol.js').PanelLayout} PanelLayout */
/** @typedef {import('./metrics.js').MetricResult} MetricResult */

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

// ── plot kinds ──────────────────────────────────────────────────────────────

function drawTimeline(el, spec, data, ctx) {
  const y = values1d(data.y);
  const x = y.map((_, i) => i);
  const trace = {
    x, y, type: 'scatter', mode: 'lines',
    line: { color: seriesColor(0), width: 1.5 },
    name: ctx.seriesName || '',
  };
  const u = ctx.units || {};
  const layout = baseLayout();
  layout.xaxis.title = { text: axisTitle(spec.x_label, u.x) || 'Configuration index' };
  layout.yaxis.title = { text: axisTitle(spec.y_label, u.y) };
  Plotly().react(el, [trace], layout, PLOT_CONFIG);
  // x IS the configuration index, so a box-select maps by its x-RANGE (a
  // lines-only trace reports no selected points, but does report a range).
  el._subInfo = { perFrame: true, xIsConfigIndex: true, n: x.length };
}

function drawDensity(el, spec, data, ctx) {
  const gx = row(data.value, 0);
  const gy = row(data.value, 1);
  const trace = {
    x: gx, y: gy, type: 'scatter', mode: 'lines',
    fill: 'tozeroy', fillcolor: 'rgba(28,166,187,0.15)',
    line: { color: seriesColor(0), width: 1.5 },
  };
  const u = ctx.units || {};
  const layout = baseLayout();
  layout.xaxis.title = { text: axisTitle(spec.x_label, u.value) };
  layout.yaxis.title = { text: axisTitle(spec.y_label, u.y) || 'Density' };
  Plotly().react(el, [trace], layout, PLOT_CONFIG);
  el._subInfo = null;
}

function drawScatter(el, spec, data, ctx) {
  const x = values1d(data.x);
  const y = values1d(data.y);
  const traces = [{
    x, y, type: 'scattergl', mode: 'markers',
    marker: { color: seriesColor(0), size: 4, opacity: 0.6 },
    name: ctx.seriesName || '',
  }];
  if (spec.diagonal && x.length && y.length) {
    const lo = Math.min(arrMin(x), arrMin(y));
    const hi = Math.max(arrMax(x), arrMax(y));
    traces.push({
      x: [lo, hi], y: [lo, hi], type: 'scatter', mode: 'lines',
      line: { color: '#8a8d94', width: 1, dash: 'dash' },
      hoverinfo: 'skip', showlegend: false,
    });
  }
  const u = ctx.units || {};
  const layout = baseLayout();
  layout.xaxis.title = { text: axisTitle(spec.x_label, u.x) };
  layout.yaxis.title = { text: axisTitle(spec.y_label, u.y) };
  Plotly().react(el, traces, layout, PLOT_CONFIG);
  // Subbable only when the x metric is per-frame (point i ↔ config index i).
  // dataCurveCount excludes the diagonal reference trace from box-select.
  el._subInfo = ctx.perFrame
    ? { perFrame: true, x: x.map((_, i) => i), dataCurveCount: 1 }
    : null;
}

function drawOverlayTimeline(el, spec, data, ctx) {
  const seriesList = data.series || [];
  const labels = (spec.options && spec.options.series_labels) || [];
  const traces = seriesList.map((res, i) => {
    let y = values1d(res).slice();
    // min-subtract + peak-normalise + abs (matches OverlayTimelineKind.draw).
    if (y.length) {
      const mn = arrMin(y);
      y = y.map((v) => v - mn);
      const peak = arrMax(y);
      if (peak > 0) y = y.map((v) => v / peak);
      y = y.map((v) => Math.abs(v));
    }
    const label = (labels[i] || '').replace('__NAME__', ctx.seriesName || '');
    return {
      x: y.map((_, k) => k), y, type: 'scatter', mode: 'lines',
      line: { color: seriesColor(i), width: 1.5 }, name: label,
    };
  });
  const layout = baseLayout();
  layout.showlegend = true;
  layout.legend = { orientation: 'h', y: 1.12, font: { size: 9 } };
  layout.margin.t = 22;
  layout.xaxis.title = { text: axisTitle(spec.x_label, null) || 'Configuration index' };
  layout.yaxis.title = { text: axisTitle(spec.y_label, null) };
  Plotly().react(el, traces, layout, PLOT_CONFIG);
  const n = traces.length ? traces[0].x.length : 0;
  // Overlay series x is the configuration index too → range-based subbing.
  el._subInfo = { perFrame: true, xIsConfigIndex: true, n };
}

function drawGroupedDensity(el, spec, data, ctx) {
  // value: (N_elements, 2, G); optional aggregate: (2, G). The element picker
  // (ctx.selectedElements) chooses which rows draw; row index = position of the
  // element in the dataset's sorted-unique-Z order (server `_element_order`).
  const value = data.value;
  const order = ctx.elementOrder || [];
  const selected = ctx.selectedElements || [];
  const traces = [];
  if (selected.includes('All') && data.aggregate) {
    traces.push({
      x: row(data.aggregate, 0), y: row(data.aggregate, 1),
      type: 'scatter', mode: 'lines', name: 'All',
      line: { color: '#b9bbc2', width: 1.5 },
    });
  }
  if (value && value.nd) {
    const rowLen = value.nd.shape.slice(1).reduce((a, b) => a * b, 1); // 2*G
    const G = value.nd.shape[value.nd.shape.length - 1];
    for (const z of selected) {
      if (z === 'All') continue;
      const ri = order.indexOf(z);
      if (ri < 0) continue;
      const base = ri * rowLen;
      const gx = value.nd.values.slice(base, base + G);
      const gy = value.nd.values.slice(base + G, base + 2 * G);
      traces.push({
        x: gx, y: gy, type: 'scatter', mode: 'lines',
        name: elementSymbol(z), line: { color: seriesColor(order.indexOf(z)), width: 1.5 },
      });
    }
  }
  const layout = baseLayout();
  layout.showlegend = true;
  layout.legend = { orientation: 'h', y: 1.12, font: { size: 9 } };
  layout.margin.t = 22;
  const u = ctx.units || {};
  layout.xaxis.title = { text: axisTitle(spec.x_label, u.value) };
  layout.yaxis.title = { text: axisTitle(spec.y_label, u.y) || 'Density' };
  Plotly().react(el, traces, layout, PLOT_CONFIG);
  el._subInfo = null;
}

// ── table kinds (plain HTML) ──────────────────────────────────────────────────

function fmt(v, precision) {
  if (v == null || Number.isNaN(v)) return '—';
  return Number(v).toFixed(precision);
}

function scalarOf(res) {
  const v = values1d(res);
  return v.length ? v[0] : null;
}

function drawTable(el, spec, data, ctx) {
  // Single scalar for the current object (one row). The desktop grids
  // models×datasets; the daily-driver single-object case is one cell.
  const value = scalarOf(data.value);
  el.innerHTML = '';
  const table = document.createElement('table');
  table.className = 'panel-table';
  table.innerHTML =
    `<thead><tr><th>Object</th><th>${spec.title || 'Value'}</th></tr></thead>` +
    `<tbody><tr><td>${ctx.seriesName || 'Reference'}</td>` +
    `<td>${fmt(value, spec.precision)}</td></tr></tbody>`;
  el.appendChild(table);
  el._subInfo = null;
}

function drawGroupedTable(el, spec, data, ctx) {
  // Per-element MAE/RMSE + an "All atoms" row. mae/rmse are (N_elements,);
  // mae_all/rmse_all are scalars.
  const order = ctx.elementOrder || [];
  const selected = (ctx.selectedElements || []).filter((z) => z !== 'All');
  const mae = values1d(data.mae);
  const rmse = values1d(data.rmse);
  el.innerHTML = '';
  const table = document.createElement('table');
  table.className = 'panel-table';
  const rows = [`<thead><tr><th>Element</th><th>MAE</th><th>RMSE</th></tr></thead><tbody>`];
  if (data.mae_all || data.rmse_all) {
    rows.push(
      `<tr><td>All</td><td>${fmt(scalarOf(data.mae_all), spec.precision)}</td>` +
      `<td>${fmt(scalarOf(data.rmse_all), spec.precision)}</td></tr>`);
  }
  const showZ = selected.length ? selected : order;
  for (const z of showZ) {
    const ri = order.indexOf(z);
    if (ri < 0) continue;
    rows.push(
      `<tr><td>${elementSymbol(z)}</td><td>${fmt(mae[ri], spec.precision)}</td>` +
      `<td>${fmt(rmse[ri], spec.precision)}</td></tr>`);
  }
  rows.push('</tbody>');
  table.innerHTML = rows.join('');
  el.appendChild(table);
  el._subInfo = null;
}

const RENDERERS = {
  timeline: drawTimeline,
  density: drawDensity,
  scatter: drawScatter,
  overlay_timeline: drawOverlayTimeline,
  grouped_density: drawGroupedDensity,
  table: drawTable,
  grouped_table: drawGroupedTable,
};

/** Kinds drawn with Plotly (vs. HTML tables). */
export const PLOT_KINDS = new Set([
  'timeline', 'density', 'scatter', 'overlay_timeline', 'grouped_density',
]);

/**
 * Render one panel into `el`.
 * @param {HTMLElement} el the panel body (plot div or table container)
 * @param {PanelLayout} spec
 * @param {Object<string, MetricResult|MetricResult[]|null>} data role → result(s)
 * @param {Object} ctx render context (units, seriesName, perFrame, element picker state)
 */
export function renderPanel(el, spec, data, ctx) {
  const fn = RENDERERS[spec.kind];
  if (!fn) {
    el.innerHTML = `<div class="panel-msg">Unknown panel kind “${spec.kind}”.</div>`;
    return;
  }
  fn(el, spec, data, ctx || {});
}

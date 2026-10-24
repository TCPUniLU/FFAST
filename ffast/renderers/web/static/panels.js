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
 * Point → frame (PRD 63): a plot renderer records `el._subInfo` describing
 * how a clicked point maps to a **configuration** index; analysis.js reads it
 * in the `plotly_click` handler. `curveSeries` maps each trace back to its
 * series index, so a click in a multi-series panel shows the structure of the
 * dataset that was actually clicked. Per-frame kinds (timeline, scatter over
 * per-frame metrics, overlay_timeline) carry it. Subbing (PRD 61) needs none
 * of this: SUB sends the plot's visible range and the server works out the
 * frames (analysis.js `_wireSub`).
 */

import { withUnit, displayUnit } from './units.js';
import { pickPeaks, visitedKey } from './peaks.js';
import { plotTheme, hexAlpha } from './theme.js';

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

// Series and element colours: the page's `--series` list (theme.js).
export function seriesColor(i) {
  const { series } = plotTheme();
  return series[i % series.length];
}

// The Plotly theme: the page's tokens, read when a panel is drawn (theme.js).
export function baseLayout() {
  const t = plotTheme();
  const axis = () => ({
    gridcolor: t.line, zerolinecolor: t.line, linecolor: t.line,
    tickfont: { color: t.ink3 }, title: { font: { color: t.ink2 } },
    automargin: true,
  });
  return {
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: { color: t.ink2, size: t.fsPlot, family: t.font },
    margin: { l: 48, r: 12, t: 8, b: 36 },
    showlegend: false,
    // No `dragmode`: Plotly's default, a drag zooms, is what SUB follows.
    xaxis: axis(),
    yaxis: axis(),
    hovermode: 'closest',
  };
}

/** Turn the legend on once a panel draws more than one thing worth naming. */
function withLegend(layout, traceCount) {
  if (traceCount > 1) {
    layout.showlegend = true;
    // a point above the axis text, as the 12 px it was at Compact; it follows Density
    layout.legend = { orientation: 'h', y: 1.12, font: { size: plotTheme().fsPlot + 1 } };
    layout.margin.t = 22;
  }
  return layout;
}

export const PLOT_CONFIG = {
  responsive: true,
  displaylogo: false,
  modeBarButtonsToRemove: ['lasso2d', 'autoScale2d', 'toggleSpikelines'],
  // Back to the ranges the plot was drawn with, never autorange: a labelled
  // timeline's autorange counts the label arrows and squashes the curve.
  doubleClick: 'reset',
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
 * units without a second config source. A catalog unit that is only a dimension
 * ("energy") is not printed (units.js).
 */
export function axisTitle(label, unit) {
  if (label == null) return '';
  if (Array.isArray(label)) return withUnit(label[0], unit);
  return String(label);
}

/**
 * The metric role whose unit a click on `target` names: a table header marked
 * by `unitAttr`, or a Plotly axis title, through the axes → roles map its
 * builder returned (`unitAxes`). null for anything else.
 * @param {Element} target
 * @param {{x?: string, y?: string}|null} unitAxes
 */
export function unitRoleAt(target, unitAxes) {
  const named = target?.closest?.('[data-unit-role]');
  if (named) return named.dataset.unitRole;
  const title = target?.closest?.('.g-xtitle, .g-ytitle');
  if (!title || !unitAxes) return null;
  return unitAxes[title.classList.contains('g-xtitle') ? 'x' : 'y'] || null;
}

/** ` data-unit-role="role"` on a table header whose unit the user can name
 * (ctx.unitDims, from the metric's dimension), or nothing. */
function unitAttr(ctx, role) {
  return ctx && ctx.unitDims && ctx.unitDims[role] ? ` data-unit-role="${role}"` : '';
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

/**
 * Every warning the results behind a panel carry, once each (ADR 0064
 * amendment: bad values a formula's where= left out).
 * @param {PanelSeries[]} series @returns {string[]}
 */
export function panelWarnings(series) {
  const out = new Set();
  for (const s of series || []) {
    for (const v of Object.values(s.data || {})) {
      for (const res of Array.isArray(v) ? v : [v]) for (const w of res?.warnings || []) out.add(w);
    }
  }
  return [...out];
}

// ── plot-kind builders (pure) ───────────────────────────────────────────────

/** Length of a peak's tick, in px. */
const PEAK_TICK = 18;

/** A peak label's text, "73 · 0.442 eV/Å" (and " ✓" once its frame was opened). */
function peakText({ frame, value }, unit, seen) {
  const shown = displayUnit(unit);
  return `${frame} · ${formatSignificant(value)}${shown ? ` ${shown}` : ''}${seen ? ' ✓' : ''}`;
}

/** How long a label is when turned on its side, in px (digits are about 0.6 em wide). */
function labelPx(text, fontPx) {
  return Math.ceil(text.length * 0.6 * fontPx);
}

/**
 * The top margin that holds the longest peak label above the plot, whether or
 * not its frame was opened (the tick takes two characters more).
 */
export function peakMarginTop(peaks, unit) {
  const fontPx = plotTheme().fsPlot;
  const longest = Math.max(0, ...peaks.map((p) => labelPx(`${peakText(p, unit, true)}`, fontPx)));
  return PEAK_TICK + longest + 8;
}

/**
 * One Plotly annotation per peak: a short tick and a label turned on its side,
 * "73 · 0.442 eV/Å". Clicking one opens that frame; one already opened is dimmed
 * and ticked. `isVisited(frame)` says which. Plotly centres a turned label on
 * the far end of its arrow, so that end is moved up by half the label's length:
 * the label then stands on a tick of PEAK_TICK px.
 */
export function peakAnnotations(peaks, unit, isVisited = () => false) {
  const t = plotTheme();
  return peaks.map((peak) => {
    const seen = isVisited(peak.frame);
    const text = peakText(peak, unit, seen);
    return {
      x: peak.frame, y: peak.value, text,
      textangle: -90, showarrow: true, arrowhead: 0, arrowwidth: 1, arrowcolor: t.peak,
      ax: 0, ay: -(PEAK_TICK + labelPx(text, t.fsPlot) / 2),
      font: { color: t.peakInk, size: t.fsPlot },
      opacity: seen ? 0.55 : 1,
      captureevents: true, hovertext: `Open frame ${peak.frame} in 3D`,
    };
  });
}

/** Draw the peak labels of a drawn timeline again (a frame was opened). */
export function refreshPeaks(el, isVisited) {
  if (!el._peaks || !el._peaks.length) return;
  Plotly().relayout(el, { annotations: peakAnnotations(el._peaks, el._peakUnit, isVisited) })
    .catch(() => {});   // hidden or replaced meanwhile
}

/** Line styles that tell predictions apart when colour means element. */
const DASHES = ['solid', 'dash', 'dot', 'dashdot'];

/** Whether a result is a table per structure and element (ADR 0064 amendment). */
function isTable(res) {
  return !!(res && res.nd && res.nd.shape.length === 2);
}

/**
 * A timeline of tables (ADR 0064 amendment 2026-10-24): one line per element,
 * in the element's colour, and one line style per series. The element picker,
 * when the tab has one, chooses the lines. An empty cell is NaN, a gap.
 */
function buildTableTimeline(spec, series, ctx) {
  const u = ctx.units || {};
  const order = ctx.elementOrder || [];
  const selected = ctx.selectedElements || ['All'];
  const shown = selected.includes('All') ? order : order.filter((z) => selected.includes(z));
  const multiSeries = seriesWith(series, 'y').length > 1;
  const traces = [];
  const curveSeries = [];
  (series || []).forEach((s, i) => {
    if (!isTable(s.data.y)) return;
    const [frames, columns] = s.data.y.nd.shape;
    const values = s.data.y.nd.values;
    for (const z of shown) {
      const col = order.indexOf(z);
      if (col < 0 || col >= columns) continue;
      traces.push({
        x: Array.from({ length: frames }, (_, f) => f),
        y: Array.from({ length: frames }, (_, f) => values[f * columns + col]),
        type: 'scatter', mode: 'lines',
        line: { color: seriesColor(col), width: 1.5, dash: DASHES[i % DASHES.length] },
        name: `${elementSymbol(z)}${multiSeries && s.name ? ` — ${s.name}` : ''}`,
      });
      curveSeries.push(i);
    }
  });
  const layout = withLegend(baseLayout(), Math.max(2, traces.length));
  layout.xaxis.title = { ...layout.xaxis.title, text: axisTitle(spec.x_label, u.x) || 'Configuration index' };
  layout.yaxis.title = { ...layout.yaxis.title, text: axisTitle(spec.y_label, u.y) };
  return { traces, layout, peaks: [], extraHeight: 0, peakUnit: u.y, peaksEligible: false,
    subInfo: { perFrame: true, curveSeries }, unitAxes: { x: 'x', y: 'y' } };
}

export function buildTimeline(spec, series, ctx) {
  if ((series || []).some((s) => isTable(s.data.y))) return buildTableTimeline(spec, series, ctx);
  const u = ctx.units || {};
  const traces = [];
  const curveSeries = [];
  (series || []).forEach((s, i) => {
    const y = values1d(s.data.y);
    if (!y.length) return;
    traces.push({
      x: y.map((_, k) => k), y, type: 'scatter', mode: 'lines',
      line: { color: seriesColor(i), width: 1.5 },
      name: s.name || '',
    });
    curveSeries.push(i);
  });
  const layout = withLegend(baseLayout(), traces.length);
  layout.xaxis.title = { ...layout.xaxis.title, text: axisTitle(spec.x_label, u.x) || 'Configuration index' };
  layout.yaxis.title = { ...layout.yaxis.title, text: axisTitle(spec.y_label, u.y) };
  // With one series, its five worst frames are labelled; several would overlap.
  let peaks = [];
  let extraHeight = 0;
  if (traces.length === 1 && ctx.peaks !== false) {
    peaks = pickPeaks(traces[0].y);
    if (peaks.length) {
      const s = series[curveSeries[0]];
      const seen = ctx.visited;
      layout.annotations = peakAnnotations(peaks, u.y,
        (frame) => !!seen && seen.has(visitedKey(s.datasetFp, s.modelFp, frame)));
      // The labels stand above the plot, in the top margin, whatever the plot's size.
      layout.margin.t = peakMarginTop(peaks, u.y);
      // Plotly would count each label's far end in the axis range and flatten
      // the curve, so the range is set from the data alone.
      let lo = 0, hi = -Infinity;
      for (const v of traces[0].y) if (Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
      // 4 % headroom over the span: hi * 1.04 fell below hi for negative data.
      if (hi > lo) layout.yaxis.range = [lo, hi + 0.04 * (hi - lo)];
      extraHeight = layout.margin.t - 8;   // and the plot grows by as much, so the curve keeps its size
    }
  }
  return { traces, layout, peaks, extraHeight, peakUnit: u.y, peaksEligible: traces.length === 1, subInfo: { perFrame: true, curveSeries }, unitAxes: { x: 'x', y: 'y' } };
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
      ...(fill ? { fill: 'tozeroy', fillcolor: hexAlpha(seriesColor(0), 0.15) } : {}),
      line: { color: seriesColor(i), width: 1.5 },
      name: s.name || '',
    };
  });
  const layout = withLegend(baseLayout(), traces.length);
  layout.xaxis.title = { ...layout.xaxis.title, text: axisTitle(spec.x_label, u.value) };
  layout.yaxis.title = { ...layout.yaxis.title, text: axisTitle(spec.y_label, u.y) || 'Density' };
  return { traces, layout, subInfo: null, unitAxes: { x: 'value', y: 'y' } };
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
      line: { color: plotTheme().ink3, width: 1, dash: 'dash' },
      hoverinfo: 'skip', showlegend: false,
    });
  }
  const layout = withLegend(baseLayout(), dataCurveCount);
  layout.xaxis.title = { ...layout.xaxis.title, text: axisTitle(spec.x_label, u.x) };
  layout.yaxis.title = { ...layout.yaxis.title, text: axisTitle(spec.y_label, u.y) };
  // Subbable only when the x metric is per-frame: within any one curve the
  // point index *is* the configuration index, whichever series it belongs to.
  return {
    traces, layout,
    subInfo: ctx.perFrame
      ? { perFrame: true, dataCurveCount, curveSeries }
      : null,
    unitAxes: { x: 'x', y: 'y' },
  };
}

export function buildOverlayTimeline(spec, series, ctx) {
  const labels = (spec.options && spec.options.series_labels) || [];
  const traces = [];
  const curveSeries = [];
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
  layout.xaxis.title = { ...layout.xaxis.title, text: axisTitle(spec.x_label, null) || 'Configuration index' };
  layout.yaxis.title = { ...layout.yaxis.title, text: axisTitle(spec.y_label, null) };
  return { traces, layout, subInfo: { perFrame: true, curveSeries } };
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
        line: { color: multiElement ? plotTheme().ink2 : seriesColor(si), width: 1.5 },
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
  layout.xaxis.title = { ...layout.xaxis.title, text: axisTitle(spec.x_label, u.value) };
  layout.yaxis.title = { ...layout.yaxis.title, text: axisTitle(spec.y_label, u.y) || 'Density' };
  return { traces, layout, subInfo: null, unitAxes: { x: 'value', y: 'y' } };
}

/**
 * One value per element as bars (ADR 0064 amendment): a group per element in
 * order of atomic number, a bar per series in its colour. The element picker,
 * when the tab has one, chooses the groups. An element with no value (NaN) gets
 * no bar, and "no value" when no series has one, never a bar at 0.
 */
export function buildBars(spec, series, ctx) {
  const u = ctx.units || {};
  const order = ctx.elementOrder || [];
  const selected = ctx.selectedElements || ['All'];
  const shown = selected.includes('All') ? order : order.filter((z) => selected.includes(z));
  const labels = shown.map(elementSymbol);
  const traces = [];
  const curveSeries = [];
  (series || []).forEach((s, i) => {
    const v = values1d(s.data.value);
    if (!v.length) return;
    traces.push({
      x: labels, y: shown.map((z) => v[order.indexOf(z)] ?? NaN), type: 'bar',
      marker: { color: seriesColor(i) }, name: s.name || '',
    });
    curveSeries.push(i);
  });
  const layout = withLegend(baseLayout(), traces.length);
  layout.barmode = 'group';
  layout.xaxis.title = { ...layout.xaxis.title, text: axisTitle(spec.x_label, null) || 'Element' };
  layout.yaxis.title = { ...layout.yaxis.title, text: axisTitle(spec.y_label, u.value) };
  const t = plotTheme();
  layout.annotations = labels
    .filter((_, k) => traces.length && traces.every((tr) => !Number.isFinite(tr.y[k])))
    .map((label) => ({ x: label, y: 0, yanchor: 'bottom', text: 'no value', showarrow: false,
      font: { color: t.ink3, size: t.fsPlot } }));
  return { traces, layout, subInfo: null, unitAxes: { y: 'value' } };
}

// ── table-kind builders (pure, return HTML) ─────────────────────────────────

/**
 * `v` with `digits` significant digits in plain notation, never scientific
 * (`0.000921`, `0.0908`, `12.3`, `1235`). The desktop's `format_significant`
 * matches.
 */
export function formatSignificant(v, digits = 3) {
  const n = Number(v);
  if (!Number.isFinite(n)) return String(n);
  if (n === 0) return n.toFixed(digits - 1);
  return n.toFixed(Math.max(0, digits - 1 - Math.floor(Math.log10(Math.abs(n)))));
}

/** `v` in scientific notation with `digits` significant digits: `1.23e-2`,
 * `-4.50e3`, and `0` for zero (the colour legend's limits). */
export function formatScientific(v, digits = 3) {
  const n = Number(v);
  if (!Number.isFinite(n)) return String(n);
  if (n === 0) return '0';
  return n.toExponential(digits - 1).replace('e+', 'e');
}

/**
 * A table cell: `precision` decimal places, but never a false zero — a
 * non-zero value that would round to zero shows three significant digits
 * (`0.00213`, not `0.00`). With `precision` unset the cell shows three
 * significant digits throughout, so an error of 0.0134 is not cut to 0.01.
 * The desktop's `format_table_value` matches (ADR 0055).
 */
export function formatTableValue(v, precision = null) {
  if (v == null || Number.isNaN(v)) return '—';
  if (precision == null) return formatSignificant(v);
  const n = Number(v);
  const text = n.toFixed(precision);
  return n !== 0 && Number(text) === 0 ? n.toPrecision(3) : text;
}
const fmt = formatTableValue;

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

  const unit = (ctx && ctx.units && ctx.units.value) || '';
  const head = `<thead><tr><th${unitAttr(ctx, 'value')}>${esc(withUnit(spec.title || 'Value', unit))}</th>`
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
  const u = ctx.units || {};
  const mae = withUnit('MAE', u.mae);
  const rmse = withUnit('RMSE', u.rmse);
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
    const head = `<thead><tr><th>Element</th><th${unitAttr(ctx, 'mae')}>${esc(mae)}</th>`
      + `<th${unitAttr(ctx, 'rmse')}>${esc(rmse)}</th></tr></thead>`;
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
  const head = `<thead><tr><th>Object</th><th${unitAttr(ctx, 'mae')}>${esc(label)} ${esc(mae)}</th>`
    + `<th${unitAttr(ctx, 'rmse')}>${esc(label)} ${esc(rmse)}</th></tr></thead>`;
  const body = '<tbody>' + list.map((s) =>
    `<tr><td>${esc(s.name || 'Reference')}</td>`
    + `<td>${cell(s, z, 'mae', 'mae_all')}</td>`
    + `<td>${cell(s, z, 'rmse', 'rmse_all')}</td></tr>`).join('') + '</tbody>';
  return `<table class="panel-table">${head}${body}</table>`;
}

// ── dispatch ────────────────────────────────────────────────────────────────

export const PLOT_BUILDERS = {
  timeline: buildTimeline,
  density: buildDensity,
  scatter: buildScatter,
  overlay_timeline: buildOverlayTimeline,
  grouped_density: buildGroupedDensity,
  bars: buildBars,
};

export const TABLE_BUILDERS = {
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
    el._unitAxes = null;
    el._figureInfo = null;
    el._peaks = null;
    el._drawnRange = null;
    el._peaksEligible = false;
    el._extraHeight = 0;
    el.style.minHeight = '';
    return;
  }
  el._unitAxes = built.unitAxes || null;
  // A labelled timeline is taller than the 180 px every plot has (index.html).
  el._extraHeight = built.extraHeight || 0;
  el.style.minHeight = built.extraHeight ? `calc(180px + ${built.extraHeight}px)` : '';
  el._peaks = built.peaks || null;
  el._peaksEligible = !!built.peaksEligible;
  el._peakUnit = built.peakUnit;
  // The ranges it is drawn with, so a plot showing them is not taken for zoomed.
  el._drawnRange = { x: built.layout.xaxis?.range?.map(Number) || null,
                     y: built.layout.yaxis?.range?.map(Number) || null };
  el._figureInfo = {
    xTitle: built.layout.xaxis?.title?.text || '',
    yTitle: built.layout.yaxis?.title?.text || '',
    nSeries: built.traces.filter((t) => t.showlegend !== false).length,
    peaks: built.peaks || [],
  };
  el.querySelectorAll('.main-svg').forEach((svg) => { svg.style.transform = ''; });   // see followSize
  const start = performance.now();
  Plotly().react(el, built.traces, built.layout, PLOT_CONFIG);
  el._drawMs = performance.now() - start;
  el._subInfo = built.subInfo;
  followSize(el);
}

/** A plot that takes longer than this to draw is stretched while its size
 * changes, and drawn again once the size settles. */
const SLOW_DRAW_MS = 30;
const SETTLE_MS = 150;

/**
 * Keep a plot the size of its element as that changes: the window, the 3D
 * sidebar, or a divider being dragged in Edit mode. `responsive` follows
 * only the window, and Plotly's own resize waits 100 ms after the last
 * change, so a plot would jump when the drag stops. Instead a plot is drawn
 * again every frame while its size changes; one too slow for that (a big
 * scatter) is stretched meanwhile and drawn once the size settles.
 */
function followSize(el) {
  if (el._sizeObserver) return;
  let frame = 0, settle = 0;
  const svgs = () => el.querySelectorAll('.main-svg');
  const redraw = () => {
    svgs().forEach((svg) => { svg.style.transform = ''; });
    const start = performance.now();
    Plotly().relayout(el, { autosize: true })
      .then(() => { el._drawMs = performance.now() - start; })
      .catch(() => {});   // hidden or replaced meanwhile
  };
  el._sizeObserver = new ResizeObserver(() => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      const layout = el._fullLayout;
      if (!layout || !el.isConnected || !el.clientWidth || !el.clientHeight) return;
      const sx = el.clientWidth / layout.width, sy = el.clientHeight / layout.height;
      if (Math.abs(sx - 1) * layout.width < 1 && Math.abs(sy - 1) * layout.height < 1) return;
      clearTimeout(settle);
      if ((el._drawMs || 0) < SLOW_DRAW_MS) {
        redraw();
        return;
      }
      svgs().forEach((svg) => {
        svg.style.transformOrigin = '0 0';
        svg.style.transform = `scale(${sx}, ${sy})`;
      });
      settle = setTimeout(redraw, SETTLE_MS);
    });
  });
  el._sizeObserver.observe(el);
}

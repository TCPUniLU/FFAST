/**
 * Download a panel's picture or data (web parity plan 2026-10-29). The CSV helpers are pure, for the tests.
 */
import { safeName } from './session_ops.js';

/** Text with HTML tags removed (axis titles may carry <sub> etc.). */
const plain = (s) => String(s ?? '').replace(/<[^>]*>/g, '');

/** One CSV cell: quoted when it holds a comma, quote or line break;
 * a number that is not finite is an empty cell. */
function csvCell(v) {
  if (typeof v === 'number' && !Number.isFinite(v)) return '';
  const s = v == null ? '' : String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Rows of cells as CSV text, one line per row. */
export function csvText(rows) {
  return rows.map((r) => r.map(csvCell).join(',')).join('\n') + '\n';
}

/**
 * A plot's points as CSV, one row per point: series, x, y. The header
 * names the axes as drawn. Helper traces (`hoverinfo: 'skip'`, the
 * scatter's diagonal) and a live run's marks and lines (`meta: 'overlay'`)
 * are left out.
 * @param {Array<{x?: ArrayLike<any>, y?: ArrayLike<any>, name?: string, hoverinfo?: string}>} traces
 * @param {{xTitle?: string, yTitle?: string}} [titles]
 */
export function plotCsv(traces, { xTitle = '', yTitle = '' } = {}) {
  const rows = [['series', plain(xTitle) || 'x', plain(yTitle) || 'y']];
  for (const tr of traces || []) {
    if (tr.hoverinfo === 'skip' || tr.meta === 'overlay') continue;
    const xs = Array.from(tr.x || []), ys = Array.from(tr.y || []);
    for (let i = 0; i < Math.max(xs.length, ys.length); i++) rows.push([tr.name || '', xs[i] ?? '', ys[i] ?? '']);
  }
  return csvText(rows);
}

/** A panel table as CSV: a cell's `data-value` (the full number) when it
 * has one, else its text. */
export function tableCsv(table) {
  if (!table) return '';
  return csvText([...table.querySelectorAll('tr')].map((tr) =>
    [...tr.children].map((c) => c.dataset.value ?? c.textContent.trim())));
}

/** The file name stem of a panel. */
export function panelFileName(spec) {
  return safeName(spec.title || spec.kind, 'panel');
}

/** Save `text` as a file in the browser's downloads. */
export function downloadText(text, filename, type = 'text/csv') {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/** The colour the page paints behind `el`: its own background or, while that
 * is transparent, the nearest ancestor's; white when none paints one. */
export function backgroundBehind(el) {
  for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
    const c = getComputedStyle(n).backgroundColor;
    if (c && c !== 'transparent' && !/^rgba\(.*,\s*0\)$/.test(c)) return c;
  }
  return '#ffffff';
}

/**
 * The layout a picture of a plot is drawn with: the plot's own, on
 * `background` (a CSS colour) or, when it is null, on nothing (a PNG with a
 * transparent background). The plot on screen is not changed.
 */
export function pictureLayout(layout, background) {
  const bg = background || 'rgba(0,0,0,0)';
  return { ...layout, paper_bgcolor: bg, plot_bgcolor: bg };
}

/** Save a drawn plot as PNG or SVG; `background` as in pictureLayout. Its
 * size is the one on screen unless `width`/`height` (px) are given; `scale`
 * multiplies a PNG's pixels (×3 for a sharp printed figure). */
export async function downloadPlot(el, format, stem,
  { background = null, width = null, height = null, scale = 1 } = {}) {
  const Plotly = globalThis.Plotly;
  const url = await Plotly.toImage({ data: el.data, layout: pictureLayout(el.layout, background) },
    { format, width: width || el.clientWidth, height: height || el.clientHeight,
      scale: format === 'png' ? scale : 1 });
  const a = document.createElement('a');
  a.href = url;
  a.download = `${stem}.${format}`;
  a.click();
}

// ── the picture's size (gap 5 of the Qt-removal audit, 2026-11-13) ──────────

const PICTURE_KEY = 'ffast.pictureChoice';
export const SHARPNESS = Object.freeze([1, 2, 3, 4]);

/** The size, sharpness and background a picture is saved with: the last
 * choice in this browser, else the plot's size on screen. Pure.
 * @param {{width?: number, height?: number, sharpness?: number, transparent?: boolean}|null} saved
 * @param {{width: number, height: number}} screen */
export function pictureChoice(saved, screen) {
  const px = (v, fallback) => (Number.isInteger(v) && v >= 50 && v <= 10000 ? v : fallback);
  return {
    width: px(saved?.width, screen.width),
    height: px(saved?.height, screen.height),
    sharpness: SHARPNESS.includes(saved?.sharpness) ? saved.sharpness : 1,
    transparent: saved?.transparent ?? true,
  };
}

/** Why a typed size cannot be used, or ''. Pure. */
export function pictureProblem(width, height) {
  const ok = (v) => Number.isInteger(v) && v >= 50 && v <= 10000;
  return ok(width) && ok(height) ? '' : 'Width and height are whole pixels, from 50 to 10000.';
}

/** The choice remembered in this browser, or null (storage may be off). */
export function loadPictureChoice() {
  try { return JSON.parse(localStorage.getItem(PICTURE_KEY) || 'null'); } catch (_) { return null; }
}

export function savePictureChoice(choice) {
  try { localStorage.setItem(PICTURE_KEY, JSON.stringify(choice)); } catch (_) { /* not kept */ }
}

/**
 * Draw only the points that show (2026-11-13).
 *
 * A plot 700 px wide drew all 1.77M frames of a dataset: Plotly took 250 ms
 * (lines) to 700 ms (WebGL dots) for each redraw, while ~2,500 points shared
 * each pixel column. The page keeps every value; Plotly gets a few thousand:
 *
 * - a line keeps, in each pixel column, its first, lowest, highest and last
 *   point, so every peak and dip is still drawn;
 * - dots keep one point in each cell of a 2 px grid, so a lone outlier stays.
 *
 * Both thin the visible range finely and the whole data coarsely, so a pan
 * shows the rest at once, and they thin again when the view changes. Each
 * kept point's place in the full data is returned, so a click still opens
 * the frame it shows. Pure: no DOM, no Plotly.
 */

/** A trace with no more points than this is drawn whole. */
export const THIN_ABOVE = 20000;

/** First index with xs[i] >= v (xs ascending). */
function lowerBound(xs, v) {
  let lo = 0, hi = xs.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (xs[mid] < v) lo = mid + 1; else hi = mid;
  }
  return lo;
}

/** Whether xs never decreases (a line drawn in order). */
function ascending(xs) {
  for (let i = 1; i < xs.length; i++) if (xs[i] < xs[i - 1]) return false;
  return true;
}

/** The points of a line in [from, to) to keep for `columns` pixel columns
 * over x from `x0` to `x1`, added to `keep`. A run with a gap (a value that
 * is not a number) keeps the gap, so the line still breaks there. */
function lineColumns(xs, ys, from, to, x0, x1, columns, keep) {
  const span = x1 - x0;
  let col = -1, first = -1, last = -1, lo = -1, hi = -1, gap = -1;
  const flush = () => {
    if (first < 0) return;
    keep.add(first); keep.add(last);
    if (lo >= 0) { keep.add(lo); keep.add(hi); }
    if (gap >= 0) keep.add(gap);
  };
  for (let i = from; i < to; i++) {
    const c = span > 0 ? Math.min(columns - 1, Math.floor(((xs[i] - x0) / span) * columns)) : 0;
    if (c !== col) {
      flush();
      col = c; first = i; lo = -1; hi = -1; gap = -1;
    }
    last = i;
    const y = ys[i];
    if (!Number.isFinite(y)) { if (gap < 0) gap = i; continue; }
    if (lo < 0 || y < ys[lo]) lo = i;
    if (hi < 0 || y > ys[hi]) hi = i;
  }
  flush();
}

/**
 * The indices of a line's points to draw, ascending, or null to draw all.
 * @param {ArrayLike<number>} xs ascending x
 * @param {ArrayLike<number>} ys
 * @param {[number, number]|null} range the x range shown; null for all
 * @param {number} columns the plot's width in pixels
 * @returns {Int32Array|null}
 */
export function thinLine(xs, ys, range, columns) {
  const n = Math.min(xs.length, ys.length);
  columns = Math.max(1, Math.round(columns));
  if (n <= THIN_ABOVE || n <= 4 * columns || !ascending(xs)) return null;
  const keep = new Set();
  // The whole line, coarsely: what a pan or a zoom out shows straight away.
  lineColumns(xs, ys, 0, n, xs[0], xs[n - 1], columns, keep);
  if (range) {
    const [a, b] = range[0] <= range[1] ? range : [range[1], range[0]];
    // One point beyond each edge, so the line runs to the plot's border.
    const from = Math.max(0, lowerBound(xs, a) - 1);
    const to = Math.min(n, lowerBound(xs, b) + 1 + 1);
    if (to - from <= 4 * columns) for (let i = from; i < to; i++) keep.add(i);
    else lineColumns(xs, ys, from, to, a, b, columns, keep);
  }
  return Int32Array.from(keep).sort();
}

/** One point per cell of a `cols` × `rows` grid over the box, added to
 * `keep`. Points outside the box are left out. */
function gridCells(xs, ys, n, [x0, x1], [y0, y1], cols, rows, keep) {
  const sx = x1 - x0, sy = y1 - y0;
  const seen = new Uint8Array(cols * rows);
  for (let i = 0; i < n; i++) {
    const x = xs[i], y = ys[i];
    if (!(x >= x0 && x <= x1 && y >= y0 && y <= y1)) continue;   // NaN too
    const c = sx > 0 ? Math.min(cols - 1, Math.floor(((x - x0) / sx) * cols)) : 0;
    const r = sy > 0 ? Math.min(rows - 1, Math.floor(((y - y0) / sy) * rows)) : 0;
    const k = r * cols + c;
    if (!seen[k]) { seen[k] = 1; keep.add(i); }
  }
}

/** Lowest and highest finite value. */
function extent(vs, n) {
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < n; i++) {
    const v = vs[i];
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  return lo <= hi ? [lo, hi] : null;
}

/**
 * The indices of dots to draw, ascending, or null to draw all: one per cell
 * of `cell` px over the plot (`width` × `height` px).
 * @param {ArrayLike<number>} xs
 * @param {ArrayLike<number>} ys
 * @param {{x: [number, number]|null, y: [number, number]|null}|null} range shown; null for all
 * @returns {Int32Array|null}
 */
export function thinPoints(xs, ys, range, width, height, cell = 2) {
  const n = Math.min(xs.length, ys.length);
  if (n <= THIN_ABOVE) return null;
  const cols = Math.max(1, Math.round(width / cell));
  const rows = Math.max(1, Math.round(height / cell));
  const ex = extent(xs, n), ey = extent(ys, n);
  if (!ex || !ey) return null;
  const keep = new Set();
  gridCells(xs, ys, n, ex, ey, cols, rows, keep);
  if (range && (range.x || range.y)) {
    const order = (r, e) => (r ? (r[0] <= r[1] ? r : [r[1], r[0]]) : e);
    gridCells(xs, ys, n, order(range.x, ex), order(range.y, ey), cols, rows, keep);
  }
  return Int32Array.from(keep).sort();
}

/** `values` at `idx`, as a plain array. */
export function pick(values, idx) {
  const out = new Array(idx.length);
  for (let k = 0; k < idx.length; k++) out[k] = values[idx[k]];
  return out;
}

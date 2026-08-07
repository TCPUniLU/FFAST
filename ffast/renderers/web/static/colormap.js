/**
 * Value-driven atom coloring (ADR 0016) — the browser twin of the vispy
 * adapter's `_map_color_by` (ffast/renderers/vispy/adapter.py:217).
 *
 * Server-resolved `vmin`/`vmax` keep both renderers' numeric range in
 * agreement; the colormap curve itself is a capability-parity approximation,
 * not a pixel-identical LUT (ADR 0045 does not require the two renderers to
 * render identical pixels, only the same task — see the ADR's "capability
 * parity, not pixel parity" framing).
 */

// Named colormap stops (RGB 0..1) at evenly spaced positions along [0, 1].
// viridis/plasma are compact public approximations of the matplotlib
// colormaps of the same name; force_error mirrors the adapter's exact custom
// 5-stop gradient (adapter.py `_get_colormap`).
const COLORMAP_STOPS = {
  viridis: [
    [0.267, 0.005, 0.329], [0.282, 0.157, 0.471], [0.243, 0.290, 0.539],
    [0.192, 0.406, 0.556], [0.149, 0.510, 0.557], [0.122, 0.617, 0.537],
    [0.208, 0.718, 0.472], [0.431, 0.808, 0.345], [0.992, 0.906, 0.145],
  ],
  plasma: [
    [0.051, 0.031, 0.529], [0.278, 0.012, 0.624], [0.451, 0.008, 0.659],
    [0.612, 0.094, 0.620], [0.741, 0.216, 0.526], [0.847, 0.341, 0.420],
    [0.929, 0.475, 0.325], [0.984, 0.702, 0.184], [0.941, 0.976, 0.129],
  ],
  force_error: [
    [0.1, 0.1, 0.9], [0.1, 0.9, 0.1], [0.9, 0.9, 0.1],
    [0.5, 0.1, 0.1], [0.9, 0.1, 0.1],
  ],
  // Compact approximations of the remaining server colormaps
  // (ffast/visualization/stages/builtin/color_stages.py `_COLORMAPS`) — same
  // "public approximation, not pixel-identical" status as viridis/plasma above.
  inferno: [
    [0.000, 0.000, 0.016], [0.259, 0.039, 0.408], [0.576, 0.149, 0.404],
    [0.867, 0.318, 0.227], [0.988, 0.647, 0.039], [0.988, 1.000, 0.643],
  ],
  coolwarm: [
    [0.231, 0.298, 0.753], [0.451, 0.588, 0.961], [0.863, 0.863, 0.863],
    [0.949, 0.573, 0.455], [0.706, 0.016, 0.149],
  ],
  hot: [
    [0.043, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [1.0, 1.0, 1.0],
  ],
  bwr: [
    [0.0, 0.0, 1.0], [1.0, 1.0, 1.0], [1.0, 0.0, 0.0],
  ],
};

function lerp(a, b, t) { return a + (b - a) * t; }

function sampleStops(stops, t) {
  const n = stops.length;
  const scaled = t * (n - 1);
  const i0 = Math.min(Math.floor(scaled), n - 2);
  const i1 = i0 + 1;
  const frac = scaled - i0;
  const [r0, g0, b0] = stops[i0];
  const [r1, g1, b1] = stops[i1];
  return [lerp(r0, r1, frac), lerp(g0, g1, frac), lerp(b0, b1, frac)];
}

/**
 * Map an AtomColorBy descriptor's per-atom `values` through its named
 * colormap between `vmin`/`vmax` to per-atom [r, g, b] (0..1 each).
 *
 * Returns null on an unrecognized colormap name — the caller falls back to
 * the server's baked element colors, exactly the vispy adapter's contract
 * (`_map_color_by` returns None on failure).
 * @param {import('./protocol.js').AtomColorBy} colorBy
 * @returns {number[][]|null}
 */
export function mapColorBy(colorBy) {
  const stops = COLORMAP_STOPS[colorBy.colormap];
  if (!stops) return null;

  const { values, vmin = 0, vmax = 1 } = colorBy;
  const lo = vmin, hi = vmax;
  const flat = hi <= lo;

  const out = new Array(values.length);
  for (let i = 0; i < values.length; i++) {
    const t = flat ? 0 : Math.min(1, Math.max(0, (values[i] - lo) / (hi - lo)));
    out[i] = sampleStops(stops, t);
  }
  return out;
}

/**
 * CSS colour-stop list for a `linear-gradient(...)` colourbar, derived from the
 * same stops `mapColorBy` draws atoms with.
 *
 * The colourbar used to carry its own hand-written hex table in
 * `panes/colorby.js` — the true matplotlib hexes, while the atoms were drawn
 * from the compact approximations above, so the bar and the molecule disagreed
 * on what a value looked like. One table means the bar cannot drift from the
 * atoms (ADR 0052).
 *
 * Unknown names fall back to viridis, matching `setColorBy`'s old behaviour —
 * a missing colourbar is worse than a wrong-palette one.
 * @param {string} colormap
 * @returns {string}
 */
export function gradientCss(colormap) {
  const stops = COLORMAP_STOPS[colormap] || COLORMAP_STOPS.viridis;
  return stops.map(rgbToHex).join(', ');
}

/**
 * `[r, g, b]` (0..1 each) → `#rrggbb`. Hex rather than `rgb(...)` so a stop
 * never contains the `, ` that separates stops.
 * @param {number[]} rgb
 * @returns {string}
 */
export function rgbToHex([r, g, b]) {
  return '#' + [r, g, b].map((v) => {
    const byte = Math.max(0, Math.min(255, Math.round(v * 255)));
    return byte.toString(16).padStart(2, '0');
  }).join('');
}

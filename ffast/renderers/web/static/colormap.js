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

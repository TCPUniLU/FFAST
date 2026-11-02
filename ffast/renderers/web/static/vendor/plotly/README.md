# Vendored Plotly.js (full bundle, v2.35.2, MIT)

Copied verbatim from the CDN, no build step:

- `plotly.min.js` — https://cdn.plot.ly/plotly-2.35.2.min.js
  (sha256 `6d21266ce1bd7d9e5ab4e115989c70c20de0382fd973a8f26ab58619eba4d603`)

Vendored (ADR 0045 decision #2) so the client works offline and on a cluster
with no outbound network — a client that *is* the app cannot depend on a CDN.

## Why the *full* bundle (~4.6 MB)

The panels draw `scatter`, `scattergl` (the WebGL scatter: thousands of
points without one page element each) and `bar` traces.
`tests/ffast/renderers/web/test_vendored_plotly.py` checks that every trace
type the page asks for is in this file.

- Until 2026-11-02 this was the *basic* bundle (~1 MB: `scatter`, `bar`,
  `pie` only). Plotly draws a missing trace type as an SVG scatter without an
  error, so every `scattergl` plot was SVG, one page element per point:
  50,000 points took 0.42 s to draw and 0.32 s per zoom, and the force
  scatter froze the page for 15 min at 55k structures.
- No smaller official bundle has `scattergl` and `bar` together: `gl2d`
  (~2 MB) has no `bar`. A custom bundle needs Plotly's npm build.
- The file is served from the user's own machine (the launcher), also when
  the server runs on a cluster, so its size costs local disk and parse time,
  not tunnel bandwidth.

## UMD, not ESM

Unlike vendored Three.js (native ESM, resolved through `index.html`'s import
map), Plotly's dist is **UMD** — it sets the `window.Plotly` global. So it is
loaded via a classic `<script src="vendor/plotly/plotly.min.js">` tag in
`index.html` (before the `type="module"` app script), and the ES modules read it
off `globalThis.Plotly` (see `static/panels.js`). No import-map entry is needed.

To upgrade: re-download at a new pinned version, update this file's version
and checksum — no `npm install`, no `package.json`.

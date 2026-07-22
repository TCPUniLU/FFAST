# Vendored Plotly.js (basic bundle, v2.35.2, MIT)

Copied verbatim from the CDN, no build step:

- `plotly-basic.min.js` — https://cdn.plot.ly/plotly-basic-2.35.2.min.js

Vendored (ADR 0045 decision #2) so the client works offline and on a cluster
with no outbound network — a client that *is* the app cannot depend on a CDN.

## Why the *basic* bundle (~1 MB, not the ~4.4 MB full one)

The analysis panels only draw **scatter/line traces** (timeline, density,
scatter, overlay_timeline, grouped_density are all `scatter` traces; `table` /
`grouped_table` render as plain HTML, not Plotly tables). The `plotly-basic`
partial bundle ships exactly the `scatter`/`bar`/`pie` trace types, which covers
every Panel Kind at a quarter of the download.

## UMD, not ESM

Unlike vendored Three.js (native ESM, resolved through `index.html`'s import
map), Plotly's dist is **UMD** — it sets the `window.Plotly` global. So it is
loaded via a classic `<script src="vendor/plotly/plotly-basic.min.js">` tag in
`index.html` (before the `type="module"` app script), and the ES modules read it
off `globalThis.Plotly` (see `static/panels.js`). No import-map entry is needed.

To upgrade: re-download at a new pinned version and update this file's version
note — no `npm install`, no `package.json`.

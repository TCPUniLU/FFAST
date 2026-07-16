# Vendored Three.js (v0.165.0, MIT)

Copied verbatim from the npm package, no build step:

- `three.module.min.js` — https://cdn.jsdelivr.net/npm/three@0.165.0/build/three.module.min.js
- `OrbitControls.js` — https://cdn.jsdelivr.net/npm/three@0.165.0/examples/jsm/controls/OrbitControls.js

Vendored (ADR 0045 decision #2) so the client works offline and on a cluster
with no outbound network — the previous CDN import map made every load depend
on jsdelivr being reachable. `index.html`'s import map points the bare
`"three"` specifier at `three.module.min.js`; `OrbitControls.js`'s own
`from 'three'` import resolves through that same map.

To upgrade: re-download both files at a new pinned version and update this
file's version note — no `npm install`, no `package.json`.

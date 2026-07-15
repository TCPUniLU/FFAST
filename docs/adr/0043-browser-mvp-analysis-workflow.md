Status: Superseded by ADR 0045 (2026-07-15) — scope (was "not Qt parity") and plotting (was hand-drawn canvas / no charting lib). Working load + 3D-inspect workflows and the color_by fix carry forward.

# Browser MVP — wire the analysis workflow into the web client

FFAST already has a working browser client. `ffast/renderers/web/static/`
(`index.html` + a ~1100-line hand-written `ffast-viewer.js`, Three.js from a CDN
import-map, zero build step) connects to `ffast-server` over the same WebSocket
protocol the Qt desktop uses, does the ping/HELLO handshake, browses the server
filesystem, loads a dataset and a prediction, opens a view, and renders atoms,
bonds, forces, unit cell, labels, and selection overlays in WebGL — scrubbing
frames and orbiting the camera. Served via `serve.py` when `ffast-server` is
launched with `--web-port`. What it does **not** yet do is the analysis loop the
v2.0 release check turns on: 2D plots, using configured metrics, colouring atoms
by a metric, selecting suspicious structures from a plot, and exporting the
result. `FFAST_v2_plan.md` §3 lists exactly these as the "finish the browser MVP
workflow" task.

The load-bearing fact, traced 2026-07-14: **the server side of every missing
workflow already exists and is renderer-neutral.** Finishing the MVP is
overwhelmingly a client-wiring job against protocol events that already ship,
not new server subsystems.

- **Metrics are already requestable.** `ffast/protocol/control.py` defines
  `REQUEST_METRIC` / `REQUEST_METRIC_CATALOG` with `METRIC_RESULT` /
  `METRIC_CATALOG` replies. The full metric engine (`ffast/metrics/`: registry,
  execution context per ADR 0035, worker pool) sits behind them. The browser
  calls neither yet.
- **Atom colouring by metric is a defined scene field the web renderer ignores.**
  `ffast/visualization/scene.py` `AtomScene.color_by` (`AtomColorBy`: per-atom
  `values`, `colormap`, `vmin`/`vmax`) is the ADR 0016 client-maps-the-colormap
  path. The vispy adapter maps it (`adapter.py:217` `_map_color_by`); the web
  renderer reads only the baked `atoms.colors` and drops `color_by` on the floor
  — the gap ADR 0041 already noted ("metric colouring already degrades to element
  colours in the browser").
- **Selection is a command and a scene primitive, both already present.**
  `ffast/visualization/commands.py` defines `SET_SELECTION` / `CLEAR_SELECTION`
  VIEW_COMMANDs; `scene.py` `SelectionOverlay` (`atom_indices` + colour) is a
  scene field the web renderer *already draws* (`_updateSelections`). The browser
  can render a selection it is told about — it just has no UI that originates one.
- **Colour-source switching is a `SET_PARAMETER` away.** The scene is a pure
  function of view parameters (`scene_builder.py`); `SET_PARAMETER` re-drives it.
  Choosing "colour by force error" is a parameter change, not a new endpoint.
- **Save/export events exist.** `SAVE_SESSION` / `LOAD_SESSION` are in the
  protocol and handled server-side.

The **one** genuine server-side gap: the config-driven analysis-tab / panel
layout (ADR 0021) is modelled in the Headless Core (`ffast/config/tabs.py`) but
today only the Qt client consumes it (`UI/panels.py`, `modules/tabs/configTabs.py`).
There is no protocol event that ships a tab's panel specs to a client. The
browser cannot currently discover *what plots to draw*.

## Decision

Treat the browser as a first-class **Renderer Client** peer to the Qt/Vispy
Loupe (per CONTEXT.md and `docs/server-visualization-architecture.md`), and
finish the MVP by **wiring existing protocol events into `ffast-viewer.js`**,
adding exactly one small server-side transport (the panel-spec event) and closing
one renderer bug (the `color_by` gap). No new metric, scene, or session
machinery. The zero-build / CDN posture is preserved: no `package.json`, no
bundler; new code is hand-written ES modules in the existing file(s), the same
discipline that produced the in-file msgpack codec.

Workflows 1, 2, and 6 (load dataset, load prediction, inspect in 3D) already
work and are unchanged. The MVP is the remaining five.

### Plots — built-in and custom TOML tabs (workflows 3 & 4)

The server ships **panel specs plus metric arrays**; the browser draws
**interactive** plots in JS. Plots are *not* server-rasterised images, because
workflow 5 (select from a plot) requires live hit-testing — a PNG cannot return
which points a box-select covers.

- Add a panel-spec transport event (working name `REQUEST_TAB_LAYOUT` →
  `TAB_LAYOUT`), returning the active config's tabs and, per tab, its ordered
  panel specs — each spec a `{panel_kind, metric refs, axis roles}` descriptor
  drawn from the ADR 0021 config already parsed into `ffast/config/tabs.py`. This
  mirrors `REQUEST_METRIC_CATALOG` and is the only new server surface.
- The browser renders a **minimal set of Panel Kinds** for v1: a scatter (true-vs-
  predicted / error scatter) and a 1-D distribution (histogram / KDE of error).
  Data arrives via `REQUEST_METRIC` (`METRIC_RESULT`). The browser is a **dumb
  panel** in the ADR 0021 sense — it draws what the kind + arrays say and owns no
  analysis logic.
- **Custom TOML tabs are the same code path as built-in ones.** Because tabs and
  panels are server-side config and the browser only renders the spec list it is
  handed, a user-authored `[[...]]` tab reaches the browser identically to a
  built-in tab. Workflow 4 costs nothing beyond workflow 3 — that is the point of
  the config-driven, server-plot-ignorant design.

**One config, both renderers.** The browser reuses the *same* TOML files as the
Qt/Vispy client — `ffast/config/builtin_tabs/*.toml` plus the project
`ffast.toml` `[[visualization.tabs]]`, merged by `ffast/config/tabs.py`
`merge_tabs()`. There is no browser-specific config format and no second parser:
`ffast/config/tabs.py` is already pure and Qt-free ("safe to run on the server,
the client, and the in-process headless thread"), so every renderer shares
`load_builtin_tabs` / `merge_tabs` / `resolve_ref`. The only difference from Qt
is transport, not config: Qt reads the parsed layout in-process
(`UI/panels.py`, `modules/tabs/configTabs.py`); the browser, with no local
filesystem, receives the *same server-parsed layout* over the wire via the
panel-spec event. This tightens an existing split rather than forking — today the
server already loads `ffast.toml` (`server.py`) but only compiles/registers each
Panel's metric ("it never reads Panel Kinds or layout"), leaving layout a
client-only read; this ADR has the server expose the layout it already parses, so
the parsed spec becomes single-owned and both renderers consume it identically.
The Qt client can later migrate to the same wire event, but that is not required
by the MVP.

The reuse is 1:1 at the *config* level; the *coverage* limit is Panel Kinds. A
tab whose panels use a kind the browser has not yet implemented (anything beyond
v1's scatter + distribution — e.g. grouped density, tables) renders fully in Qt
and only partially in the browser until that kind is ported. The same TOML file
is valid and unchanged in both; the browser simply skips (and surfaces) a kind it
cannot draw yet, never errors on it.

Plots are hand-drawn on `<canvas>` (scatter points, axes, histogram bars, a
box-select rectangle), consistent with the hand-rolled-msgpack ethos and giving
direct control over the box-select → structure-index mapping that workflow 5
consumes. No charting library is added (see alternatives).

### Atom colouring by metric (workflow 7)

Close the `color_by` gap. The web `MoleculeRenderer` learns to read
`atoms.color_by`: map per-atom `values` through `colormap` between `vmin`/`vmax`
to per-instance RGB (the browser twin of vispy `_map_color_by`), falling back to
`atoms.colors` when `color_by` is absent — exactly the vispy contract. A **Colour
by** selector in the sidebar, populated from `METRIC_CATALOG`, sets the colour
source via `SET_PARAMETER`; the server rebuilds the scene with `color_by`
populated and the renderer draws it. Built-in and configured metrics flow through
the identical path — a configured metric is just another catalog entry — so
workflow 7 covers both by construction. A colourbar readout is a nice-to-have,
not a v1 gate.

### Select suspicious structures from a plot → 3D (workflow 5)

Box-select on a scatter yields the covered points' **structure indices**. Those
indices drive two existing paths, no new protocol:

- **Inspect in 3D:** clicking a selected point issues `SET_FRAME` to that
  structure; the 3D view jumps to it. (Atom-level `SET_SELECTION` /
  `SelectionOverlay` is available for highlighting and already renders, but
  structure-level frame navigation is the MVP interaction.)
- **Carry to export:** the selected index set becomes the export set below.

### Save / export the result (workflow 8)

Files live on the server (or cluster), not in the browser, so export is a
**server-side write the browser triggers**. `SAVE_SESSION` persists the session.
Subset export of the plot-selection set (the v2.0 minimal-subset workflow,
`FFAST_v2_plan.md` §6 — export selected / worst-error / random) writes an
`extxyz` server-side and reports the path; a browser download of that small
artifact over HTTP is an optional follow-on, not a v1 gate.

## Why

- **The 3–4 day estimate is credible only because it is wiring.** Every workflow
  but the panel-spec event maps to a protocol message or scene field that already
  ships and is exercised by the Qt client. The risk is client-side rendering and
  UX, not backend design.
- **Config-driven panels pay off exactly here.** ADR 0021 made panels dumb and
  the server plot-ignorant; that is what lets a second, thinner renderer join with
  a panel-spec event and a canvas draw loop instead of a parallel plotting stack.
- **Interactive-not-rasterised is forced by workflow 5.** Selection is the reason
  the browser draws plots itself rather than showing server PNGs.
- **One renderer contract, two renderers.** Fixing `color_by` in the web renderer
  brings it to the vispy adapter's contract against the same `RenderScene`,
  shrinking the divergence between the two Renderer Clients rather than forking
  behaviour.

## Consequences

- **One new protocol event** (`REQUEST_TAB_LAYOUT` / `TAB_LAYOUT`), added to
  `ffast/protocol/control.py` and served from the already-parsed
  `ffast/config/tabs.py` model. It benefits the Qt client too (a wire form for the
  panel layout it currently reads locally), but the MVP only requires the browser
  consumer.
- **`ffast-viewer.js` grows** a plot module (canvas scatter + histogram +
  box-select), a `color_by` mapping in `MoleculeRenderer`, a metric-catalog-driven
  **Colour by** selector, and export controls. It stays a hand-written, zero-build
  ES module; no bundler enters.
- **The web renderer stops silently degrading metric colour.** After this,
  `color_by` renders in the browser; the ADR 0041 caveat ("degrades to element
  colours in the browser") is retired for the scalar-metric case.
- **Role gating is unchanged.** Only the CONTROLLING client's messages mutate
  state (`server.py`); a READ_ONLY browser can still receive scenes and draw plots
  but its selections/parameter changes are ignored, as today.
- **Not Qt parity.** The browser gets a *minimal* Panel Kind set and *basic* 3D
  controls. Panel Kinds beyond scatter + distribution (grouped density, tables,
  the full ADR 0021 catalog), publication-quality camera/lighting, and per-atom
  metric overlays on the trajectory cloud (ADR 0041) remain Qt-only in v1.
- **Selection is structure-level in v1.** Atom-level lasso and multi-panel linked
  selection are deferred; the MVP selects structures (points) and navigates frames.

## Considered alternatives

- **Server-rasterised plots (matplotlib/pyqtgraph offscreen → PNG).** Rejected:
  kills workflow 5 (no client-side hit-testing on a bitmap), and re-introduces a
  Qt/plot dependency onto the server render path that ADR 0026's headless-core
  boundary works to keep off. Interactive JS plots keep the server plot-ignorant.
- **Add a charting library (Plotly-from-CDN, uPlot).** Plotly gives box/lasso
  select for free and is CDN-loadable, but adds a ~3 MB heavyweight imperative
  dependency for two plot kinds; uPlot is tiny but its selection model is x-range,
  not 2-D point box-select. Rejected for v1 in favour of a hand-drawn canvas
  scatter/histogram, matching the project's hand-rolled-msgpack, zero-build ethos
  and giving direct box-select → index control. Revisitable if the Panel Kind set
  grows past what hand-drawing sustains.
- **A REST/HTTP API for datasets, metrics, and export.** Rejected: the WebSocket
  protocol already carries all of it and the Qt client proves it; a parallel REST
  surface would be a second contract to keep in sync. `serve.py` stays a dumb
  static-file server.
- **Port the Qt panel widgets / full Panel Kind catalog now.** Rejected as
  scope: the MVP proves the loop end-to-end with the two plot kinds the release
  check actually needs; the rest follows the same spec transport once the loop is
  real.
- **Read the TOML config in the browser directly.** Rejected: the config file
  lives on the server; the browser has no filesystem access to it, and the server
  is the source of truth for what is loaded. The panel-spec event is the correct
  seam.

## Tests

- A Playwright runtime test (extending `tests/ffast/renderers/web/test_web_runtime.py`)
  that, against an example dataset + prediction: opens a built-in tab, asserts a
  scatter renders non-background pixels; box-selects a region and asserts the 3D
  view navigates to a selected structure's frame; switches **Colour by** to a
  metric and asserts atom instance colours change (the `color_by` path, not baked
  element colours); and triggers an export and asserts the server reports a written
  path.
- A protocol test that `REQUEST_TAB_LAYOUT` returns the config's tabs and panel
  specs, and that a TOML-defined custom tab appears identically to a built-in one.
- A renderer unit assertion (source-level, per the existing
  `test_web_server.py` style, pending a JS test harness) that the viewer maps
  `atoms.color_by` and falls back to `atoms.colors` when it is absent.

## Refs

ADR 0015 (client-side raycast picking — web via native raycaster), ADR 0016
(atom colour values + client-maps-colormap), ADR 0021 (config-driven dumb panels,
server plot-ignorant), ADR 0026 (headless-core boundary — keep Qt/plot off the
server render path), ADR 0035 (metric execution context), ADR 0041 (Three.js
alpha + the noted web `color_by` gap). Code: `ffast/renderers/web/`
(`static/ffast-viewer.js`, `serve.py`), `ffast/protocol/control.py`,
`ffast/visualization/scene.py` (`AtomColorBy`, `SelectionOverlay`),
`ffast/visualization/commands.py` (`SET_PARAMETER`, `SET_SELECTION`),
`ffast/visualization/scene_builder.py`, `ffast/config/tabs.py`,
`ffast/renderers/vispy/adapter.py:217` (`_map_color_by`), `server.py`
(`--web-port`, role gating). Plan: `FFAST_v2_plan.md` §3 (browser MVP), §6
(minimal subset export).

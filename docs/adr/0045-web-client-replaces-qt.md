Status: Accepted — implemented (Phases 0–6 complete 2026-07-23)

# Web client replaces the Qt/Vispy desktop — full capability parity

The Qt/Vispy desktop is unreliable on some Linux distributions. The failure
mode, pinned during the design interview, is **install / won't-launch** — not a
rendering or display fault. The `pip` PySide6 wheels bundle Qt itself but still
require host **system libraries** (`libxcb-*`, `libGL`/`libEGL`, `fontconfig`,
…); minimal or unusual distros lack them, so either install breaks or Qt's
`xcb` platform plugin refuses to start. This is the classic PySide6-on-Linux
packaging failure and has nothing to do with vispy/OpenGL or the browser.

A **much cheaper fix exists and was deliberately rejected.** Because
`pyproject.toml` already isolates the desktop behind a `ffast[gui]` extra (ADR
0026's headless-core boundary), shipping that extra as a self-contained bundle —
conda-forge / `pixi`, AppImage, or PyInstaller — would carry the native libs the
wheels assume and fix "breaks on distro X" in days, with zero code change and no
JavaScript. It solves *this instance*. It does **not** solve the *class*: native
GL drivers, Qt platform plugins, and per-OS packaging will keep generating
incidents (the codebase already carries scars — `useOpenGL=False` to stop Apple
Metal mis-rasterising scatter plots, PySide6 6.4→6.8 init regressions).

This ADR takes the strategic bet instead: **make the browser — the most uniform
runtime available — the primary FFAST client, and retire Qt/Vispy.** The ground
is already laid. The server is renderer-neutral (ADR 0010/0014/0016): it builds a
`RenderScene` and streams `SCENE_SNAPSHOT`/`SCENE_PATCH` over WebSocket, with the
Vispy adapter and the web `MoleculeRenderer` as peer consumers. A working browser
Renderer Client already exists (ADR 0043) — load dataset/prediction, 3D inspect —
but ADR 0043 **explicitly scoped itself "not Qt parity."** This ADR reverses that
scope decision and supersedes it.

## Decision

The web client becomes the **primary** FFAST client; the Qt/Vispy desktop is
retired once the web client reaches **capability parity**. Parity means every
*task* a Qt user can perform is performable in the browser — **not** pixel- or
interaction-identical chrome. There *is* a colorbar (static gradient + vmin/vmax
+ label), axes *are* labelled, selection *works* (click + box); the draggable/
rotatable colorbar, inline-editable axis labels, and the five separately-named
pick-tool buttons are not reproduced. Full capability, pragmatic chrome.

Concrete decisions, each settled in the interview:

1. **Local files → localhost server launcher.** A new `ffast` launch command
   starts `ffast-server` bound to `127.0.0.1` and opens the user's already-
   installed browser (optionally Chrome/Edge `--app=` for a chromeless window).
   Local files are read **server-side**, exactly as the existing file browser
   already lists the server filesystem. A cluster session is the same client
   pointed at a remote server. **One** loading path. No browser file-upload, no
   native-webview shell.

2. **Zero-build posture, retained and hardened.** The single ~1331-line
   `ffast-viewer.js` is split into **native ES modules** (browsers `import`
   between local `.js` files with no bundler). Three.js and Plotly.js are
   **vendored** into `static/` — the CDN import-map is dropped, because a client
   that *is* the app cannot depend on a CDN offline or on a cluster. Wire-protocol
   shapes get **JSDoc** types (VS Code type-checks and autocompletes plain `.js`,
   no compile). No `npm`, no bundler, no TypeScript-compile; `serve.py` stays a
   dumb static server. This preserves "edit → refresh," which is load-bearing for
   a maintainer with no JS experience driving AI-written code.

3. **Plotly.js for all 2D panel kinds.** Its box/lasso select returns the covered
   points' indices — which is exactly what **subbing** (viewport/selection →
   `SubDataset`) consumes. Tables render as plain HTML. This **supersedes ADR
   0043's hand-drawn-canvas / no-charting-library** decision, which was correct
   for a two-kind MVP but is reinventing a charting engine at seven kinds +
   subbing, under a non-JS-author reality where leaning on a mature declarative
   library minimises bespoke code.

4. **Multi-window parity by finishing ADR 0044 (Phases 2–4) in v1.** Qt opens
   several independent Loupe windows over one Environment (`UIHandler.newLoupe`);
   the web equivalent needs the server to be multi-client so each browser tab is
   its own controller. ADR 0044 Phase 1 (transport split) is done; its Phases
   2–4 (role model, concurrency hardening, per-connection views, web wiring) are
   pulled into this scope, not deferred.

## Why

- **It defeats the category, not the instance.** Packaging fixes one distro; the
  browser removes native GL drivers, Qt platform plugins, and per-OS packaging
  from the client entirely. The runtime becomes "a recent Chrome/Firefox," which
  is far more uniform across machines than a native OpenGL-through-vispy stack.
- **Distribution becomes trivial — the reliability payoff lands for free.** The
  launcher app has *no* Qt/OpenGL native libraries to bundle: a Python server +
  static files + the browser the user already has. The self-contained-bundle
  effort the packaging option required is largely obviated.
- **It finishes an existing intent.** The server is already renderer-neutral and
  a browser Renderer Client already exists; this grows a peer consumer to parity
  rather than inventing a parallel stack.
- **It fits a non-JS author.** Zero-build + vendored mature libs (Plotly draws,
  Three.js renders) means the least possible hand-written JS and a plain
  edit→refresh loop.

## Consequences

- **Multi-month build**, phased (below); Qt and web coexist until parity gates
  retirement. Qt retirement is a *later* consequence, not an immediate deletion.
- **Supersedes ADR 0043** on two counts: its "not Qt parity" scope and its
  hand-drawn-canvas / no-charting-library plot decision. ADR 0043's already-
  working workflows (load, 3D inspect) and its `color_by` fix carry forward.
- **ADR 0044 is now v1-critical**, including the parts that touch the cluster
  reconnect/recovery lifecycle (ADR 0024/0012) — the one place that must be
  re-verified end-to-end before merge.
- **Plotly.js (~3.5 MB) vendored** — irrelevant on localhost/cluster; no CDN.
- **The `color_by` renderer gap is closed early** (Phase 0), retiring the ADR
  0041/0043 "degrades to element colours in the browser" caveat.
- **Not multi-tenant.** Per ADR 0044, all connections share one Environment; this
  is a shared workspace, not user isolation.

## Considered alternatives

- **Package `ffast[gui]` as a self-contained bundle (conda-forge/`pixi`,
  AppImage, PyInstaller).** The cheapest fix for the *reported* failure — days,
  no code change, no JS, and it keeps the full desktop app. Rejected **as
  strategy**: it preserves the native-GUI class of problems that will keep
  recurring. It remains the correct fallback if the web bet is ever abandoned.
- **pywebview / Tauri native-window shell.** Gives a real desktop window, but on
  Linux depends on system WebKitGTK — reintroducing the exact native-lib
  fragility being fled. Rejected.
- **Electron.** Bundles its own Chromium (reliable, native-feeling) but ~150 MB
  and requires the `npm`/build toolchain the zero-build decision rejects.
  Rejected.
- **A build toolchain (Vite + TypeScript).** Strongest engineering at scale, but
  a second ecosystem for a maintainer with no JS experience; its one decisive win
  — wire-protocol type-safety — is ~80% recoverable via JSDoc with no build.
  Rejected for now, revisitable if the codebase outgrows hand-managed modules.
- **Browser file-upload for local files.** Painful for GB-scale extxyz
  trajectories and duplicates data; the localhost server reads local files
  directly. Rejected.
- **Keep ADR 0043's MVP scope (not parity).** This is precisely the decision
  being reversed.

## Implementation phases

The first daily-driver milestone lands at the end of Phase 3 (analysis is
possible in the browser); Phases 4–6 complete the replacement.

0. **Foundation.** Split `ffast-viewer.js` into ES modules; vendor Three.js +
   Plotly.js (drop CDN); JSDoc protocol types; the `ffast` launcher; close the
   `color_by` renderer gap.
1. **Inspection parity (single view).** `color_by` (element/metric/displacement,
   colormaps, static colorbar); camera (XY/XZ/YZ presets, ortho, manual
   az/el/dist, COM-track, axis gizmo, background colour); DISPLAY / BONDS /
   UNIT CELL / FORCE VECTORS panes; playback (play-pause / FPS / skip).
2. **Selection & picking.** Client raycast (ADR 0015) + box-select; the five pick
   *functions* (Info distance/angle/dihedral, Bonds edit, Forces filter, Extract,
   Align); EXTRACT SUBSET + ALIGNMENT panes.
3. **Analysis plots — daily-driver milestone.** `REQUEST_TAB_LAYOUT`/`TAB_LAYOUT`
   event; metric channel (`REQUEST_METRIC`); Plotly panels for all seven kinds;
   four built-in + custom TOML tabs; auto-generated param controls; **subbing**
   (box → `declareSubDataset`); energy-shift / smoothing / atomic selectors.
4. **Export & session.** PNG export (opaque/transparent via WebGL `readPixels` /
   `toBlob`); subset export (server-side extxyz); `SAVE_SESSION`/`LOAD_SESSION`
   UI; load config TOML.
5. **Multi-window.** ADR 0044 Phases 2–4: role model, concurrency hardening,
   delete-races, per-connection views, multi-tab controllers; retire or gate the
   BroadcastChannel satellite.
6. **Distribution.** Package the launcher app (pip/pipx/conda) — trivial versus
   Qt: no native GL/Qt libraries, just the Python server + static files + the
   user's own browser.

## Tests

- Per-phase gates extending the Playwright runtime test
  (`tests/ffast/renderers/web/test_web_runtime.py`): Phase 1 asserts `color_by`
  changes instance colours and camera presets reorient; Phase 2 asserts a box
  pick yields structure indices; Phase 3 asserts a Plotly scatter renders, a
  box-select declares a subset, and a custom TOML tab appears identically to a
  built-in; Phase 4 asserts an export reports a written path; Phase 5 the ADR
  0044 two-client tests.
- The `REQUEST_TAB_LAYOUT` protocol test and the `color_by` source-level renderer
  assertion carried from ADR 0043.

## Refs

ADR 0010 (server-owned visualization state), 0012/0024 (reconnect/recovery —
re-verify under ADR 0044), 0014/0016 (renderer-neutral scene, client-maps-
colormap), 0015 (client raycast picking — web native raycaster), 0021 (config-
driven dumb panels, server plot-ignorant), 0026 (headless-core boundary,
`ffast[gui]` extra), 0028 (cluster auto-bootstrap), 0035 (metric execution
context), 0041 (web `color_by` gap), **0043 (browser MVP — superseded by this
ADR on scope + plotting)**, 0044 (multi-client view controllers — Phases 2–4
pulled into this scope). Code: `ffast/renderers/web/` (`static/ffast-viewer.js`,
`serve.py`), `ffast/protocol/control.py`, `ffast/visualization/`
(`scene.py`, `scene_builder.py`, `commands.py`), `ffast/config/tabs.py`,
`ffast/renderers/vispy/adapter.py`, `server.py`, `pyproject.toml` (`[gui]` extra,
`[project.scripts]`).

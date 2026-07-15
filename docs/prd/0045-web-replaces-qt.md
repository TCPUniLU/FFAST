# PRD — Web client replaces the Qt/Vispy desktop (full capability parity)

Tracks ADR 0045. Supersedes the ADR 0043 browser-MVP scope. Triage: `ready-for-agent`.

## Problem Statement

As a researcher, I rely on the FFAST desktop (Qt/Vispy) to inspect trajectories
and analyse force-field prediction errors, but it fails to **install or launch**
on several of my Linux machines — it needs system libraries the Python wheels
assume, and on the wrong distro it simply won't start. That blocks my work on
exactly the machines I most need it on (lab boxes, shared workstations,
clusters). I want a client that just runs, anywhere, with the same interface.

## Solution

FFAST's **browser client becomes the primary client**. I run one command
(`ffast`); it starts a local server and opens my browser, and I get the whole
desktop workflow there — browse and load datasets and predictions, inspect
structures in 3D, colour atoms by a metric, run the analysis tabs with
interactive plots, select and extract subsets, and export results — in a runtime
(a recent browser) that is uniform across machines. Pointed at a remote
`ffast-server`, the same client drives a cluster session with no local display.
The Qt/Vispy desktop is retired once the browser reaches **capability parity**:
every task I can do in Qt, I can do in the browser (the chrome may be simpler).

## User Stories

**Launch & connect**

1. As a researcher, I want to launch FFAST with one command that opens my browser, so that I never fight a native-GUI install again.
2. As a researcher, I want the local server to start automatically on launch, so that I don't manage processes by hand.
3. As a researcher, I want an optional chromeless "app" window, so that it feels like a desktop application, not a browser tab.
4. As a cluster user, I want to point the same client at a remote `ffast-server`, so that I can analyse remote data with no local display.
5. As a returning user, I want a dropped connection to re-admit me and replay shared state, so that a blip doesn't lose my session (ADR 0012/0024).

**Load objects**

6. As a researcher, I want to browse the server filesystem and load a dataset, so that I can open my trajectory.
7. As a researcher, I want to choose the dataset loader/type, so that non-default formats load correctly.
8. As a researcher, I want to pick which file keys map to energy/forces for ASE files, so that arbitrary extxyz columns are usable.
9. As a researcher, I want to load a prediction for a dataset (choosing energy/force keys), so that I can compare model to reference.
10. As a researcher, I want a stride option for very large datasets, so that I can open them without exhausting memory.
11. As a researcher, I want load progress surfaced in the UI, so that I know a long load is working, not hung.
12. As a researcher, I want to load a project TOML config, so that my configured metrics and analysis tabs appear.
13. As a researcher, I want to see the dataset and prediction lists and select among them, so that I can switch what a view shows.
14. As a researcher, I want to rename, recolour, freeze and delete objects, so that I can keep the workspace tidy.
15. As a researcher, I want predictions that don't apply to the selected dataset shown as unavailable, so that I don't mis-pair them.

**3D inspection**

16. As a researcher, I want atoms, bonds, forces, unit cell and labels rendered, so that I can see the structure.
17. As a researcher, I want to orbit, pan and zoom the camera, so that I can look around the molecule.
18. As a researcher, I want XY/XZ/YZ camera presets, so that I can snap to standard projections.
19. As a researcher, I want an orthographic toggle, so that I can remove perspective distortion.
20. As a researcher, I want to type an exact azimuth/elevation/distance, so that I can reproduce a view.
21. As a researcher, I want origin-on-centre-of-mass tracking, so that the molecule stays centred across frames.
22. As a researcher, I want an axis gizmo toggle, so that I can read orientation.
23. As a researcher, I want to set the background colour, so that I can prepare figures.
24. As a researcher, I want an atom-size scale, so that I can trade clarity against overlap.
25. As a researcher, I want to hide atoms by index or element (including exclude), view-only, so that I can declutter without altering the dataset.
26. As a researcher, I want to highlight specific atoms, so that I can point at them.
27. As a researcher, I want to set the pick radius, so that picking suits my zoom level.
28. As a researcher, I want bond width and colour controls, so that bonds read clearly.
29. As a researcher, I want fixed vs dynamic bond modes and editable fixed-bond indices, so that I control topology.
30. As a researcher, I want to seed a fixed bond set from the current dynamic bonds, so that I start from a sensible default.
31. As a researcher, I want to show/hide the unit cell, so that periodic context is optional.
32. As a researcher, I want force vectors with normalise, length-scale, and source (ground truth or a model), so that I can inspect force fields and errors.
33. As a researcher, I want to filter force vectors to a selection, so that a dense cloud stays legible.

**Colouring**

34. As a researcher, I want atoms coloured by element by default, so that species are obvious.
35. As a researcher, I want to colour atoms by any atom-colourable metric (choosing the prediction), so that I can see where error concentrates.
36. As a researcher, I want to colour by RMS displacement, so that I can see mobile regions.
37. As a researcher, I want to choose the colormap, so that the scale suits the property.
38. As a researcher, I want a colourbar (gradient + vmin/vmax + name), so that I can read the scale.
39. As a researcher, I want per-metric colouring parameters exposed, so that I can tune the metric the colouring uses.

**Playback**

40. As a researcher, I want a frame slider, so that I can scrub the trajectory.
41. As a researcher, I want previous/next and play/pause with an FPS setting, so that I can animate.
42. As a researcher, I want a skip-frames setting, so that long trajectories play at a useful speed.
43. As a researcher, I want playback settings remembered per dataset, so that switching datasets keeps my choices.

**Selection & picking**

44. As a researcher, I want to click-pick the nearest atom (occlusion-correct), so that I select what I see.
45. As a researcher, I want to box-select atoms by dragging, so that I select groups quickly.
46. As a researcher, I want single-atom position, pair distance, triple angle and quad dihedral read-outs, so that I can measure geometry.
47. As a researcher, I want to add/remove fixed bond pairs by picking, so that I edit topology directly.
48. As a researcher, I want to pick which atoms show force vectors, so that I focus the display.
49. As a researcher, I want to pick atoms and extract them as a new subset dataset, so that I can study a fragment.
50. As a researcher, I want to pick three atoms for frame alignment, so that I can align on a chosen plane.
51. As a researcher, I want Kabsch alignment (optionally heavy-atoms-only) across frames, so that rigid motion is removed.

**Analysis plots (daily-driver milestone)**

52. As a researcher, I want the built-in analysis tabs (Basic Errors, Subsystem Errors, Atomic Errors, Gyration) discovered from server config, so that I get the standard analyses without setup.
53. As a researcher, I want my custom TOML-defined tabs to appear identically to built-in ones, so that my configured analyses work in the browser too.
54. As a researcher, I want every Panel Kind to render (timeline, density, scatter, table, grouped_density, grouped_table, overlay_timeline), so that no configured panel is blank.
55. As a researcher, I want panels to fetch their data over the metric channel, so that plots reflect the loaded objects.
56. As a researcher, I want to pan and zoom plots, so that I can inspect regions.
57. As a researcher, I want a diagonal reference on true-vs-predicted scatter, so that I can judge bias at a glance.
58. As a researcher, I want per-panel compute controls generated from each metric's parameters, so that I can retune without editing config.
59. As a researcher, I want an energy-shift toggle and a smoothing control, so that I can normalise comparisons.
60. As a researcher, I want the atomic element-picker (single/multi element), so that per-element analyses are selectable.
61. As a researcher, I want to box-select a plot region and declare a live SubDataset from the covered configuration indices (subbing), so that I can isolate suspicious structures.
62. As a researcher, I want that SubDataset usable by the 3D view and other tabs, so that a selection flows through my whole analysis.
63. As a researcher, I want clicking a selected scatter point to jump the 3D view to that structure's frame, so that I can inspect an outlier immediately.

**Export & session**

64. As a researcher, I want to export the current frame as an opaque PNG with a chosen background, so that I can put figures in papers.
65. As a researcher, I want a transparent-background PNG option, so that figures composite cleanly.
66. As a researcher, I want to export a selected subset as extxyz (written server-side, path reported, optional download), so that I can reuse a fragment elsewhere.
67. As a researcher, I want to save and load a session, so that I can resume analysis later.

**Multiple windows**

68. As a researcher, I want to open a second independent window controlling its own view, so that I can compare two datasets side by side.
69. As a researcher, I want two windows to show different frames/datasets at once, so that comparison is real, not mirrored.
70. As a researcher, I want loading or deleting an object in one window to appear in the other, so that the shared workspace stays consistent.
71. As a researcher, I want a metric computed in one window served from cache in the other, so that I never wait twice for the same result.
72. As a researcher, I want deleting an object another window is viewing to degrade that view gracefully, so that nothing crashes.
73. As a viewer, I want an explicit read-only mode, so that I can mirror a view without controlling it.

**Maintainer & distribution**

74. As a maintainer with no JS experience, I want plain `.js` modules with an edit→refresh loop, so that I can maintain the client with AI assistance.
75. As a maintainer, I want JSDoc types on the wire protocol, so that my editor catches shape mistakes without a build step.
76. As a maintainer, I want Three.js and Plotly.js vendored (no CDN), so that the client works offline and on a cluster.
77. As a maintainer, I want the launcher app packaged simply (pip/pipx/conda) with no native GL/Qt libraries, so that it installs and launches reliably on any distro — the reliability the desktop lacked.

## Implementation Decisions

- **The web client is the primary Renderer Client**; Qt/Vispy is retired at
  capability parity. Both coexist during the build (ADR 0045).
- **Capability parity, not pixel parity.** Every task is reachable; Qt-idiomatic
  chrome (draggable colourbar, inline-editable axis labels, five named pick-tool
  buttons) is not reproduced.
- **Localhost server launcher.** A new package entry point starts `ffast-server`
  on loopback and opens the system browser (optional app-mode window). Local
  files are read **server-side** through the existing filesystem-browsing
  protocol — one loading path. Cluster = the same client at a remote server.
- **Zero-build client.** The single viewer module is split into **native ES
  modules** loaded directly by the browser. Three.js and Plotly.js are
  **vendored** into the static assets; the CDN import-map is removed. Wire-message
  shapes carry **JSDoc** types. No `npm`, bundler, or TypeScript-compile; the
  static server stays a dumb file server.
- **Reuse the renderer-neutral protocol.** View lifecycle, scene snapshot/patch,
  view commands (frame, camera, parameter, feature toggle, selection),
  metric-catalog and metric requests, and session save/load already exist and are
  exercised by the Qt client. The web client wires to them; it does not add
  renderer-specific endpoints.
- **One new server surface:** a tab-layout request/response event, served from
  the already-parsed, Qt-free config-tabs model (mirroring the metric-catalog
  request). Both renderers consume the *same* server-parsed layout from the same
  TOML — no browser-specific config or second parser.
- **`color_by` in the renderer.** The web `MoleculeRenderer` maps per-atom values
  through the named colormap between server-resolved `vmin`/`vmax` to per-instance
  colour, falling back to baked colours when absent — the vispy adapter's
  contract. A metric-catalogue-driven "Colour by" selector sets the source via a
  parameter change; the server rebuilds the scene.
- **Client-side picking.** A native raycaster does occlusion-correct click-pick
  and box-select (per ADR 0015); the five pick *functions* ride on that one
  mechanism. Selections commit via the existing selection command; subset
  extraction and plot-driven subsets reuse the existing subset/declare paths.
- **All Panel Kinds via Plotly.js.** Panels are dumb (ADR 0021): they render what
  the kind + metric arrays say. Plotly's box/lasso select returns point indices,
  which drives subbing and point→frame navigation. Tables render as HTML.
- **Multiple windows via ADR 0044 Phases 2–4.** Per-connection session + outbound
  queue and the connection hub exist (Phase 1). This delivers the role model
  (every connection controls its own views; read-only opt-in), concurrency
  hardening (serialize/lock shared-Environment mutations, graceful degrade on
  broadcast delete), per-connection view namespaces, and web wiring; the
  BroadcastChannel satellite is retired or gated behind a single-client server.
- **Cluster reconnect/recovery re-verified.** ADR 0024/0012 recovery-window and
  token semantics change under ADR 0044 (last-client trigger, re-admit not
  reclaim); the SLURM path is exercised end-to-end before merge.
- **Distribution.** The launcher app is packaged (pip/pipx/conda) with no native
  GL/Qt libraries — Python server + static files + the user's own browser.
- **Phasing.** 0 Foundation → 1 Inspection → 2 Selection → 3 Plots (**daily-driver
  milestone**) → 4 Export/session → 5 Multi-window → 6 Distribution.

## Testing Decisions

- **Test external behaviour, not implementation.** Assert what the user sees in
  the browser or what the server emits on the wire — never internal renderer or
  panel structure.
- **Primary seam — the Playwright runtime test.** One end-to-end seam drives a
  real browser client against a real `ffast-server` on the example dataset +
  prediction. Each phase adds behavioural assertions here: metric colouring
  changes atom colours; a camera preset reorients; a box pick yields structure
  indices; a Plotly scatter renders and a box-select declares a subset that
  appears as a dataset; a custom TOML tab appears identically to a built-in;
  export reports a written path.
- **Supporting seams — two existing in-process seams.** The tab-layout event is
  tested at the server-session handler seam (returns the config's tabs and panel
  specs; a custom TOML tab matches a built-in). Multiple windows are tested at the
  socket-free connection-hub seam (two client queues: independent view scenes,
  broadcast object events, cache reuse, delete-race graceful degrade,
  recovery-window on last-client).
- **Pure mapping logic** (e.g. `color_by`) may be pinned with a source-level
  assertion in the existing web-server-test style where a browser test would be
  overkill.
- **Prior art:** the Playwright runtime test, the connection-hub test, and the
  source-level web-server test already in the suite.
- **No JS unit harness** is introduced — the zero-build stance holds; JS
  behaviour is covered through Playwright.

## Out of Scope

- **Pixel/UX-parity chrome:** draggable/rotatable colourbar, inline-editable axis
  labels and legend, the five pick tools as distinct named toolbar buttons.
- **Browser file-upload** of local files (the localhost server reads them
  directly).
- **Native-webview shell** (pywebview/Tauri) and **Electron** — both undercut the
  reliability rationale or the zero-build stance.
- **A build toolchain** (Vite/TypeScript/npm).
- **Multi-tenant isolation** (per-connection Environments) — this is a shared
  workspace, not user isolation (ADR 0044).
- **Immediate deletion of the Qt client** — it coexists until parity; retirement
  is a subsequent step, not part of this work.
- **Zero-install collaboration/sharing features** beyond the ADR 0044
  multiple-window capability.
- **Server-rasterised (PNG) plots** — rejected; interactive plots are required by
  subbing.

## Further Notes

- The cheaper alternative — packaging `ffast[gui]` as a self-contained bundle
  (conda-forge/`pixi`/AppImage) — was identified and rejected **as strategy** (it
  keeps the native-GUI class of failures); it remains the correct fallback if the
  web bet is abandoned. See ADR 0045 "Considered alternatives".
- The **daily-driver milestone is the end of Phase 3**: once analysis plots and
  subbing work, the browser is usable for real work even before export, multiple
  windows, and packaging are complete.
- The riskiest integration is Phase 5's interaction with the cluster reconnect
  lifecycle; it must be verified end-to-end before merge to avoid orphaning SLURM
  jobs.

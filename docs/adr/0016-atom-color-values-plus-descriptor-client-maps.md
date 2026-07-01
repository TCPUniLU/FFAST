# Atom coloring travels as values + a colormap descriptor; the client maps

**Status:** Accepted / Implemented (`AtomColorBy` descriptor + client-side mapping in `VispySceneAdapter`)

For value-driven atom coloring (force error, displacement, any per-atom
metric), the **server emits per-atom scalar values plus a presentation
descriptor**; each Renderer Client maps values → RGBA and draws the colorbar.
The server does not bake colors and does not import a colormap/render library.

## Decision

`AtomScene` carries two color channels:

- `colors` — resolved element/default RGBA, **always present** (the fallback).
- `color_by` — optional descriptor, present when a non-element source is active:
  `{values: list[float] (per atom), colormap: str, vmin: float, vmax: float,
  label: str, unit: str}`.

Contract (normative for all Renderer Clients):

- The **server resolves concrete `vmin`/`vmax`** into the descriptor
  (presentation-configured when set, else auto on a stable, frame-independent
  basis). The client never auto-ranges.
- The **client maps** `values` → RGBA with the named colormap and draws the
  colorbar from the descriptor. Value→color mapping is a **baseline Renderer
  Capability**; a renderer lacking it falls back to `colors`.
- Changing `colormap`/`vmin`/`vmax` is a **presentation change** — applied
  client-side without recompute where the values are unchanged. (`SET_PARAMETER`
  may also update the descriptor server-side; the cached metric values are
  reused, never recomputed, for a colormap change.)
- A single **`atom_color` source selector** — `element` (default) /
  `displacement` / `metric:<id>` — chooses the value source. `element`/none
  omits `color_by`, so the client uses `colors`.

Value sources:

- **displacement** — the `ffast.displacement_stats` stage (geometry-only,
  frame-independent); no metric executor needed.
- **metric** — `build_scene` runs the chosen `per_structure_per_atom` metric
  through an `InProcessExecutor(_default_registry)`. A symbolic-input resolver
  maps the metric's declared refs (`reference.forces`, `prediction.forces`,
  `frame.positions`, `frame.elements`, metric-to-metric deps) to current-frame
  arrays; the per-atom result fills `color_by.values`.

## Considered alternatives

- **Server bakes RGBA into `colors`** (the Milestone-1 `MetricColorPresenter`
  shape, which calls `vispy.color...rgba`). Rejected: it couples the headless
  server to a render library, and a colormap change — a pure presentation
  change — would force a `SET_PARAMETER` round-trip + scene rebuild + color
  re-send, contradicting the documented rule that colormap changes reuse cached
  values and never recompute. It was acceptable at M1 only because server and
  client were the *same in-process* object; it is the wrong shape after the
  client/server split.
- **Client auto-ranges per frame.** Rejected: per-frame range flickers and
  breaks cross-frame comparability during playback, and the colorbar drifts.
- **Separate per-source toggles** (`color_displacement` + `color_metric`).
  Rejected: coloring is pick-one; a single selector avoids the "both on"
  ambiguity and mirrors the legacy "Coloring" combo.

## Consequences

- `MetricColorPresenter` (returns RGBA + `ColorBarSpec` via `vispy.color`) and
  the `ffast.value_colors` stage are reshaped/relocated: the value→RGBA mapping
  moves to the **renderer client**; the server side yields values + descriptor.
  Both are currently called only by the **dying legacy render path**
  (`modules/loupeForceError.py`, `modules/loupeAtoms.py`), so no live server
  code changes shape.
- `build_scene` gains a metric executor + input-resolver; `server.py`
  constructs `InProcessExecutor(_default_registry)` and threads it in alongside
  `getDataset`/`getPrediction`.
- `color_by.values` are subset by the atom-filter keep-mask exactly like
  `colors`/`sizes` (ADR 0014 gate 3).
- The web renderer maps natively (WebGL/JS) — no shared mapping code across
  renderers; both honor the same `color_by` contract.
- A truly comparable **per-frame** metric range must be computed over the whole
  trajectory (or presentation-fixed), not the current frame — flagged for when
  force-error coloring is wired.

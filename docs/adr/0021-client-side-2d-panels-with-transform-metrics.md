# Client-side 2D Panels with reductions as Transform Metrics

2D analysis plots are a client-side declarative composition: an **Analysis Tab** holds
**Panels**, each a **Panel Kind** (timeline / density / scatter / table) bound to
**Metric IDs**. Panels never compute — every reduction (KDE, smoothing, downsampling,
per-structure reduction) is a **Transform Metric** computed server-side and compiled
(Vega-Lite style) from a Panel's `{metric, transform, params}` into a deterministically
named concrete **Metric** with a static **Metric Graph** edge to its source. The server
stays unaware of Panel Kinds and layout, exposing only **Metric Results**. A Panel's
interactive controls are generated from the **Parameter Schemas** of the Metrics it binds.

## Why not server-owned (like the 3D scene)?

The rest of the architecture is migrating *to* server-owned **Visualization State**, so a
reader will reasonably ask why 2D plots deliberately are *not*:

- pyqtgraph zoom / pan / hover is inherently client-local — the same constraint that keeps
  Loupe camera client-side. The server could not own the interactive parts anyway.
- The web renderer (Milestone 6) is far off; a renderer-neutral config plus server-computed
  Metrics already give web parity without inventing a 2D scene protocol now.
- The transfer win — large arrays never cross the wire — comes from reductions being
  **Transform Metrics** (server-side, shipped small), **not** from server-owning the layout.

This split (declarative spec unchanged, data transforms relocated off the renderer) is
exactly VegaFusion's server/client division for interactive Vega visualizations.

## Considered alternatives

- **Server-owned 2D presentation.** Rejected for now: large speculative build (plot-scene
  representation + protocol + state) ahead of the web renderer, for little gain over the
  client model given interaction must stay client-local.
- **Dynamic source-parameterized Transform Metric** (`ffast.kde(source=<metric id>)` with a
  runtime resolver edge). Rejected: heavier change to InputResolver / Metric Graph / cache
  identity, and no precedent. Compile-to-concrete keeps the graph static and every metric ID
  normal to the selection / cache / Session-persistence stack.
- **Explicit per-combo registration of every derived metric.** Acceptable as the bootstrap
  for the engine-first round, but too verbose as the end state; the compiler replaces it.

## Consequences

- Interactive transform params are **Compute Parameters** → debounced recompute on release;
  no live-drag preview (acceptable: reduced arrays are small, cache-keyed, instant locally).
- **Subbing** reads the auto-bound *indexed source* Metric (the Transform Metric's declared
  input), never the drawn reduced array; downsampling stays visual-only and box-filters the
  full source.
- A new `curve` / `density` **Metric Shape** (x-grid + y) is required; the shape set is
  already declared extensible.
- Downsampling is an optional Transform Metric with adaptive point-count auto-on, computed
  server-side (LTTB / M4).
- **Compiled metric functions must be picklable.** A compiled Transform Metric is computed on
  the server scene path through the M4 `WorkerProcessExecutor`, which pickles the whole registry
  to its worker subprocess. So the compiler must register **module-level** callables, never lambdas
  or local closures: transform bodies are module-level `_tb_*` functions and `_make_fn` returns a
  module-level `_TransformFn` instance (`ffast/metrics/transforms.py`). A closure here silently
  breaks *all* metric coloring with `Can't pickle local object` (fixed 2026-06-24). See the
  **Metric Worker Pool** pickling requirement in CONTEXT.md.

# Metric Adapter Bridge for legacy plot widgets (transitional)

**Status: Superseded by ADR 0019**

> **Note (2026-07-01):** the transitional `client/metric_bridge.py` proposed below
> was never built. Plot widgets migrated directly to the MetricWatcher path
> (ADR 0019), which made the bridge unnecessary. Kept for historical context.

Legacy plot widgets (`UI/Plots.py`, `modules/basicErrors.py`, `modules/atomicErrors.py`) pull
data via `env.getData("energy_error", model=..., dataset=...)`.  The new metrics subsystem
(`ffast/metrics/`) computes the same values through `MetricExecutor.run(...)`.

Rather than rewriting all plot widgets and the metrics subsystem simultaneously, a thin
**Metric Adapter Bridge** satisfies the legacy `env.getData()` API by routing calls through
`MetricExecutor`.  The adapter lives in `client/metric_bridge.py` and is injected into
`Environment` at startup.

**Why not rewrite plot widgets first?**  
Rewriting plots requires defining the RPC protocol for Metric Results, adapting every
`DataWatcher`, and updating every plot widget — all before any migration benefit is visible.
The adapter delivers the new metric logic into the existing UI without touching plots.

**Why not keep legacy DataTypes permanently?**  
Legacy `DataType` classes in `modules/basicErrors.py` duplicate metric logic already present
in `ffast/metrics/builtin/`.  Keeping both creates two sources of truth for scientific
calculations.  The adapter is a bridge, not a destination.

**Deletion condition:**  
This ADR and `client/metric_bridge.py` are deleted when all plot widgets read Metric Results
directly via RPC.  Each legacy `DataType` is deleted individually as its metric counterpart
is confirmed equivalent.  The adapter is explicitly `# TRANSITIONAL` in code.

**What the adapter does NOT do:**  
- It does not re-implement caching (delegates to `MetricCache`).
- It does not introduce new public API — only satisfies existing `env.getData()` calls.
- It does not bridge VisualizationState or scene building — that is a separate migration step.

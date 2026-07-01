# ADR 0019: MetricWatcher + InputResolver replace the DataType system

**Status:** Accepted  
**Supersedes:** ADR 0018 (metric adapter bridge — deleted when this migration completes)

## Context

ADR 0018 introduced a transitional bridge (`client/metric_bridge.py`) so that legacy plot
widgets could consume metric results without being rewritten.  The bridge is explicitly
temporary: its deletion condition is "all plot widgets read MetricResults directly."

This ADR defines what "directly" means and specifies the full migration architecture.

## Decisions

### D1 — DataWatcher is generalized, not replaced

DataWatcher handles fan-out across (model x dataset) pairs, automatic invalidation on
`DATASET_LOADED`/`MODEL_LOADED`/`DATASET_DELETED`, and missing-dependency tracking for the
Load button.  These features are correct and would need to be re-implemented in any
replacement.  DataWatcher is instead generalized to accept **metric IDs** alongside legacy
DataType keys.  It becomes the single watcher for all computation results.

### D2 — InputResolver lives in Environment (server-side)

Metrics declare symbolic input refs (`I.reference_energies`, `I.prediction_forces`, etc.).
Resolving those refs to actual arrays requires calling `dataset.getEnergies()`,
`env.getData("energy", model=model, dataset=dataset)`, etc. — operations that require the
arrays to be present locally.

In remote mode the arrays live on the server (Environment runs on the cluster).  Putting
InputResolver on the client would require transferring arrays to the client before metric
execution, which defeats the purpose of remote compute.

InputResolver therefore lives in Environment and maps each symbolic ref to an
`(env, model, dataset) → np.ndarray | None` lambda.  It is the single place that knows
how to source each input type, including optional inputs (e.g. `"offsets"` for variable
datasets).

### D3 — generateData is unified; dispatches on DataType vs metric ID

`env.generateData(key, model, dataset)` checks whether `key` is a registered DataType or a
registered metric ID and dispatches accordingly.

- **DataType path:** existing behaviour unchanged.
- **Metric path:** InputResolver sources inputs, `metric_executor.run_batch()` computes
  the metric and its transitive dependencies, result stored in env cache, `DATA_UPDATED`
  fired with metric cache key `metric_id__params_hash__modelFp__datasetFp`.

This unification means the generationQueue, Load button, and task management work
identically for both types.  Legacy DataType path is deleted when all DataTypes are gone.

### D4 — Flat generationQueue; MetricCache handles deduplication

Metric cache keys are queued individually (same flat `set` as today).  When
`ffast.energy_shift` is generated and internally needs `ffast.energy_difference`, the
executor checks MetricCache before computing.  Cache hit cost is one array hash — negligible
vs computation cost.  No per-plot batching logic is needed.

### D5 — getWatchedData groups by (model, dataset); dataEntry is a dict

A plot watching multiple metric IDs gets one entry per (model, dataset) pair:

```python
{
    "model": model,
    "dataset": dataset,
    "dataEntry": {
        "ffast.energy_difference": MetricResult,
        "ffast.energy_shift":      MetricResult,
    }
}
```

For legacy DataType-backed plots, `dataEntry` is the existing `DataEntity` (dict-like,
accessed via `.get("diff")`).  The common access pattern is `dataEntry[key]` in both cases.
Plot widgets that watch metric IDs use `dataEntry["ffast.energy_difference"].values`.

### D6 — Presentation logic lives in plot widgets, not in metrics

Metrics return a single `values` array (pure math).  KDE computation, smoothing,
axis formatting — all presentation — move into the plot widget.  Multi-field DataTypes
(e.g. `EnergyErrorDist` storing `distX`, `distY`, `shiftedDistX`, `shiftedDistY`) dissolve:
the plot watches `["ffast.energy_difference", "ffast.energy_shift"]` and computes the
distributions inline.

### D7 — Parameters declared with metric dependencies (P1)

```python
self.setMetricDependencies({
    "ffast.force_mae": {"norm": "l2"},
})
```

Parameters are part of the computation identity.  Cache key includes a hash of parameters.
Changing a parameter (e.g. slider moves `norm` from `"l2"` to `"l1"`) triggers a cache miss
and recomputation naturally, without special invalidation logic.

### D8 — Remote mode slots in without a parallel implementation

MetricWatcher calls `env.generateData(metric_id, model, dataset, params)` — the same
interface regardless of local or remote mode.  In local mode Environment is in-process.  In
remote mode `generateData` sends an RPC message carrying `(metric_id, params, model_fp,
dataset_fp)`; the server's Environment runs InputResolver + executor and pushes
`DATA_UPDATED` back over the wire.  InputResolver is server-side code in both modes.

Remote metric RPC is a separate ADR (out of scope here).

## Migration path

1. Generalize `DataWatcher` to accept metric IDs alongside DataType keys.
2. Add `InputResolver` to Environment (one entry per `inputs.*` constant).
3. Extend `generateData` with the metric dispatch path.
4. Migrate plot widgets one at a time:
   - Replace `setDataDependencies("energyErrorDist")` with `setMetricDependencies({...})`
   - Replace `de.get("diff")` with `dataEntry["ffast.energy_difference"].values`
   - Move presentation logic (KDE, smoothing) inline
5. Delete each legacy DataType as its plot widget is migrated.
6. Delete `client/metric_bridge.py`, `modules/metricBridge.py`, ADR 0018.
7. Delete `generateData` DataType branch when last DataType is gone.

## What this ADR does NOT cover

- Remote metric RPC protocol (new ADR when remote mode is tackled).
- SubDataset / atom filtering for metric-backed plots (handled automatically: SubDataset has
  its own fingerprint, InputResolver resolves against it via `dataset.getForces()` etc.).
- VisualizationState or scene building (separate migration track).

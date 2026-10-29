Status: Proposed

# DataWatcher: configure once, refresh as an implementation detail

`client/dataWatcher.py` (~384 lines, no direct tests) exposes its own state machine as its
interface: four setters (`setDataDependencies`, `setModelDependencies`, `setDatasetDependencies`,
`setMetricDependencies`), each of which must be followed by an internal `refreshDependencyList()` —
called from 6 sites within the file — before `getWatchedData()` returns anything current. A missed
refresh is a silently stale watcher. It is also the only module that knows how to expand a Metric ID
plus parameters into **Cache Keys** (`_getWatchedDataMetric`), so every **Panel**'s correctness
rests on load-bearing, test-dark glue.

**Decision (proposed):** collapse the setters into one
`configure(metrics=…, models=…, datasets=…)` call; refresh becomes an implementation detail that
`configure` and the event handlers own internally. The public interface shrinks to configure + query
(`getWatchedData`), and the Metric-ID→Cache-Key expansion gets direct unit tests against a fake
environment.

## Why

- The "call setter, remember refresh happens" protocol is interface complexity that buys callers
  nothing — it exists because the implementation leaks its update discipline.
- Eliminates the stale-watcher bug class structurally instead of by vigilance.
- The dependency-expansion logic is the natural test surface for the client metric spine (ADR 0019
  consumers); today it is only exercised through full Panel refresh.

## Consequences

- Call sites (Panels via `DataDependentObject`, the Loupe coloring path) migrate from N setter calls
  to one configure call — mechanical, but touches every Panel construction path.
- `_getWatchedDataMetric` rebuilding its (model, dataset) grouping on every call can be memoized
  behind the same interface later; that is an implementation concern this ADR deliberately leaves
  open.

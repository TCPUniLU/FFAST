Status: Accepted — implemented (2026-07-24)

# Finish the Metric Execution Context: one InputSource seam, an injected executor per Environment

ADR 0035 made `ffast/metrics/execution.py` a deep module — `build_execution_plan` +
`run_plan` own input resolution, dependency ordering, Compute Parameter filtering, and caching in
one place. But the **MetricExecutor** seam in front of it was never widened to speak the Context's
vocabulary, so the depth win was only banked halfway:

- `MetricExecutor.run(id, inputs: dict, parameters)` still takes a *flat pre-resolved dict*. The
  panel path therefore builds a plan once (`InputResolver.build_metric_inputs`) only to harvest a
  flat dict, hands it to `run`, and the executor re-wraps it in `FlatInputSource` and walks the
  **Metric Graph** a second time. One walk, paid for twice.
- Atom coloring bypasses the seam entirely: `color_values._collect_leaf_inputs` is a fourth
  independent walk, feeding a **module-global** `WorkerProcessExecutor`. Its failure model diverges
  too — it raises `KeyError` for an unavailable input, where the Context uses `(found, value)` and a
  pre-recorded `MetricFailure`.
- A whole ordering interface behind the registry is dead but tested: `registry.compute_plan`,
  `registry.dependencies_of`, `MetricGraph.compute_plan`/`dependencies_of`, and
  `InProcessExecutor.run_batch` have no production callers after 0035 derived ordering from the walk
  itself. Two topological engines and two cycle detectors coexist, kept in agreement by a comment.

The colour_values gap is the highest-severity one: `WorkerProcessExecutor` is the **only**
production consumer of the **Metric Worker Pool**, yet every atom-coloring test swaps in
`InProcessExecutor`, so the pickling / shared-memory / timeout paths for the real coloring metrics
never run in the suite — the exact trap CONTEXT.md's Metric Worker Pool note warns about.

## Decision

1. **Widen the seam.** `MetricExecutor.run(id, source: InputSource, parameters)`. The executor owns
   the single walk (build the plan with its registry → drive `run_plan` with its cache and its
   transport). `ExecutionPlan` stays internal to the executor. `run_batch` is dropped (no callers;
   `build_execution_plan` already accepts a root-id list if batch execution returns).
2. **Panel path passes a source, not a flattened dict.** `DataService.generateMetric` hands
   `_ResolverSource(...)` straight to `run`. **`build_metric_inputs` is deleted** — the double walk
   is gone.
3. **Coloring is injected, not global.** `build_scene` gains an `executor` parameter, threaded to
   `resolve_atom_color_values` exactly as `get_dataset`/`get_prediction`/`get_forces` already are.
   The `color_values` module-global is deleted. A new **frame-scoped `InputSource`** mirrors
   `_ResolverSource`: `get` returns `(True, value-or-None)` and never raises; an unavailable input
   flows through the Context's `MetricFailure` path, which `resolve_atom_color_values` maps to `None`
   → element-color fallback (ADR 0016 behaviour, preserved). **`_collect_leaf_inputs` and the
   `KeyError` dance are deleted.** The per-element→per-atom broadcast, the shape-check, and the
   non-metric `displacement` source stay in `resolve_atom_color_values` — they are coloring
   presentation, not input resolution.
4. **The `DataService` executor is injected at Environment construction**, not a hardcoded lazy
   `InProcessExecutor`. The **headless/server Environment injects a `WorkerProcessExecutor`**; the
   **desktop/client Environment injects an `InProcessExecutor`**. The one server instance serves
   *both* server-side metric paths — atom coloring and `REQUEST_METRIC` — so they stop diverging in
   isolation.
5. **Delete the dead ordering surface** — `compute_plan` / `dependencies_of` from both `registry`
   and `MetricGraph`, and `run_batch`. `MetricGraph.freeze` stays as the ref/shape/cycle **validator**
   (its live role at server startup and CLI validate).

## Relationship to prior ADRs

- **Amends ADR 0016.** 0016 specified that the server constructs an `InProcessExecutor` and threads
  it into `build_scene`. This overrides the *executor kind*: the server injects a
  `WorkerProcessExecutor`, per the Key Constraint that *"Metrics execute through a recyclable Metric
  Worker Pool rather than directly inside the long-lived server process."* The *injection mechanism*
  0016 introduced (thread the executor through `build_scene` alongside the data accessors) is
  retained and finally realised — the current code diverged from 0016 by hiding a `WorkerProcessExecutor`
  as a module-global instead of injecting it.
- **Completes ADR 0035.** 0035 deferred folding `color_values._collect_leaf_inputs` into the Context
  because its `KeyError` failure model had to be reconciled with whole-dataset `(found, value)`
  semantics. Decision 3 does that reconciliation, so the frame-scoped walk becomes a second adapter
  of the same `InputSource` seam.

## Why

- **Locality** — one walk, one failure model, one server executor. A change to resolution or
  isolation lives in one place instead of four.
- **Leverage** — one interface serves both paths (panel + coloring) and both transports (in-process
  + pool). `_ResolverSource` and the frame-scoped source are two adapters, so the seam is real, not
  indirection.
- **The interface is the test surface** — no monkeypatched module-global; callers name their source
  (`FlatInputSource` for pre-resolved inputs, the resolver/frame sources for the live paths). One
  end-to-end test injecting a real `WorkerProcessExecutor` closes the pool-never-tested trap.

## Consequences

- Cache-first execution means the server's Worker Pool is hit only on the **first** compute of each
  unique `(metric, model, dataset)`; redraws hit `env.cache`. `REQUEST_METRIC` cold compute already
  runs off the event loop in a thread pool, so moving it to a subprocess is bounded, not per-frame.
- ~30 flat-input `run(id, {...}, params)` call sites (tests, CLI metric-test, `data_service`) wrap
  their dict in `FlatInputSource`. Mechanical, single-shape.
- `dependencies_of` has two *test* callers (`test_transform_compiler`, `test_expr_metrics`) that
  assert compiled graph edges; those assertions retarget to observable structure (e.g. plan step
  order) so the dead method truly goes.
- Server-side `REQUEST_METRIC` gains the Worker Pool's crash/timeout isolation, and its metrics now
  face the pool's picklability constraint (module-level functions only) — already satisfied by the
  built-in and compiled Transform/Expression metrics.

## Test strategy (replace, don't layer)

- Delete tests on deleted code: `compute_plan`/`dependencies_of`/`requires_freeze`/`run_batch` cases
  in `test_metric_graph`; the three `monkeypatch(cv, "_executor", …)` cases in `test_atom_coloring`;
  `build_metric_inputs` tests. Keep `MetricGraph.freeze` validator tests.
- `test_execution_context` and `test_pool` survive (the Context is unchanged; only pool `run()`'s
  call shape updates).
- Rewrite coloring tests to **inject** an `InProcessExecutor` (fast everyday coverage).
- Add **one** end-to-end test that injects a real `WorkerProcessExecutor` and drives a metric-color
  (or `REQUEST_METRIC`) through it, asserting values return — transitively covering registry
  picklability, plan shipping, and shared-memory packing for the real coloring path.

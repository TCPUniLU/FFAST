Status: Accepted

# Metric Execution Context: resolve metric inputs once, not three times

Three modules independently walk a `MetricSchema.inputs` declaration and re-implement the same
resolution rules: `ffast/metrics/input_resolver.py` (`build_metric_inputs`, the panel path),
`ffast/metrics/executor.py` (`InProcessExecutor._execute_one`), and `ffast/metrics/pool.py`
(`WorkerProcessExecutor.run`, whose ~16-line resolution block is a near-copy of the executor's).
Each re-decides optional-input semantics (**Metric Input** missing → `None`), re-filters **Compute
Parameters** from presentation ones, and re-derives dependency order from the **Metric Graph**. This
glue is exactly where real bugs have already lived — the registry-pickling failure and the
spawn-timeout kill were both pool-glue defects — and the in-process executor masking pool behaviour
in tests is a documented trap (see the **Metric Worker Pool** picklability note in CONTEXT.md).

**Decision:** introduce a Metric Execution Context — one module that, given a Metric ID,
parameters, and a data source, resolves inputs, dependencies, and Compute Parameters once and yields
a transport-ready execution plan. The two executors and the panel path become adapters that differ
only in transport: direct call, or pickle-to-worker over the pool. Optional-input and
missing-dependency semantics are defined (and tested) in exactly one place.

## Why

- The MetricExecutor seam already exists by design (milestone constraint: in-process now, worker
  pool later, same contracts). This deepens what sits behind it instead of letting three callers
  each re-own the resolution rules.
- Divergence between the in-process and pool paths is a standing bug class; a shared context makes
  the two paths differ only where they must.
- Resolution becomes unit-testable in isolation, without an Environment or a live pool.

## Consequences

- The pool's worker-side unpacking must consume the same plan shape the context produces, so the
  context's output has to stay picklable — same constraint the registry already carries.
- `input_resolver`'s duck-typed env contract (the DataService) is unchanged; the context sits above
  it, not instead of it.

## What shipped

`ffast/metrics/execution.py` holds the context: `build_execution_plan(registry, roots, parameters,
source)` yields an `ExecutionPlan` (dependency-ordered `PlanStep`s, each with its inputs classified
as `RawInput`/`DepInput`, Compute Parameters filtered, and any missing-required-input failure
pre-recorded), and `run_plan(plan, registry, cache, run_fn)` is the shared driver. `run_fn` is the
only per-transport difference: `InProcessExecutor` calls the metric function directly;
`WorkerProcessExecutor._ship_to_worker` ships it to a recycled worker (all the pool's cold-start /
timeout / crash / shared-memory logic stays there). The panel path
(`InputResolver.build_metric_inputs`) now builds a plan through a `_ResolverSource` and harvests its
raw bindings, so the same walk drives both env-sourcing and execution.

Consolidating the three paths also closed a standing divergence: the pool path previously ignored
`optional_inputs` and never guarded a present-but-`None` required input — both are now handled
identically to the in-process path. `tests/ffast/test_execution_context.py` exercises the semantics
directly, with no Environment and no live pool.

Deliberately out of scope: `ffast/visualization/color_values._collect_leaf_inputs`, the *frame-scoped*
atom-coloring sourcing walk. It resolves refs against the currently displayed frame and signals an
unavailable input by raising `KeyError` (fall back to element colors) rather than returning `None` —
a different contract from whole-dataset resolution. Folding it in would require reconciling that
failure model and is left as a follow-up.

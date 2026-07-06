"""Metric Execution Context — resolve a metric's inputs, dependencies, and
Compute Parameters once (ADR 0035).

Three call sites used to each re-walk a ``MetricSchema.inputs`` declaration and
re-implement the same resolution rules: ``input_resolver.build_metric_inputs``
(the panel path), ``InProcessExecutor`` (direct call), and
``WorkerProcessExecutor`` (pickle-to-worker over the pool).  Each independently
re-decided optional-input semantics (a missing/None **Metric Input** → ``None``),
re-filtered **Compute Parameters** from presentation ones, and re-derived
dependency order from the **Metric Graph**.  That glue is exactly where real
bugs have lived (the registry-pickling failure, the spawn-timeout kill).

This module owns those decisions in one place:

- ``build_execution_plan`` walks the dependency tree once and yields an
  ``ExecutionPlan`` — topologically ordered steps, each with its inputs
  pre-classified (raw value vs dependency output), its Compute Parameters
  pre-filtered, and any missing-required-input failure pre-recorded.
- ``run_plan`` is the single execution driver: it wires dependency outputs,
  consults the result cache, and builds every ``MetricResult``.  Its ``run_fn``
  argument is the *only* thing that differs between transports — call the metric
  function directly, or ship it to a worker subprocess.

The ``InputSource`` abstraction is how the panel path (env-backed
``DataService`` resolution, keyed by symbolic ref) and the executors
(pre-resolved arrays, keyed by each metric's local input name) plug into the
same walk.

The plan is picklable — raw inputs are numpy arrays / ``None``, dependency
bindings are ``DepInput`` markers, and Compute Parameters are plain values — so
a worker can consume it without an Environment (ADR 0035 consequence).
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from ffast.metrics.cache import MetricCache, function_hash
from ffast.metrics.dims import shape_to_str
from ffast.metrics.models import MetricFailure, MetricResult


# ── Input bindings ──────────────────────────────────────────────────────────

@dataclass
class DepInput:
    """A metric input bound to another metric's output, resolved at run time."""

    metric_id: str


@dataclass
class RawInput:
    """A metric input bound to a concrete sourced value.

    ``value`` is a numpy array, or ``None`` for an optional or unavailable input.
    """

    value: Any


Binding = DepInput | RawInput


@dataclass
class PlanStep:
    """One metric in an execution plan, fully resolved except its own compute.

    ``bindings`` maps each local input name to a ``DepInput`` (bind to another
    step's output) or a ``RawInput`` (a concrete value).  ``failure`` is set when
    a *required* raw input was missing or ``None`` — the step then short-circuits
    to a ``MetricFailure`` instead of running.
    """

    metric_id: str
    bindings: dict[str, Binding]
    compute_params: dict[str, Any]
    failure: str | None = None


@dataclass
class ExecutionPlan:
    """Topologically-ordered steps for a metric (and its transitive deps).

    ``steps`` lists dependencies before dependents, each metric exactly once.
    """

    root_ids: list[str]
    steps: list[PlanStep] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)


# ── Input sources ───────────────────────────────────────────────────────────

class InputSource(ABC):
    """Resolves a metric's raw (non-metric) input to a value.

    Implementations differ in where the value comes from: a flat dict of
    pre-resolved arrays (executors) or env-backed symbolic-ref resolution (the
    panel path).  ``get`` returns ``(found, value)`` — ``found`` distinguishes an
    input that was never supplied from one explicitly supplied as ``None``, which
    the plan builder needs to pick the right missing-input failure message.
    """

    @abstractmethod
    def get(self, metric_id: str, local_key: str, ref: str) -> tuple[bool, Any]:
        ...


class FlatInputSource(InputSource):
    """Raw inputs read from a flat dict keyed by each metric's local input name.

    The dict is shared across the whole dependency tree: a leaf metric's input
    (e.g. ``predicted``) is looked up by its local name regardless of which
    metric declared it.  This is the shape both executors already receive.
    """

    def __init__(self, inputs: dict[str, Any]) -> None:
        self._inputs = inputs

    def get(self, metric_id: str, local_key: str, ref: str) -> tuple[bool, Any]:
        if local_key in self._inputs:
            return True, self._inputs[local_key]
        return False, None


# ── Plan building ───────────────────────────────────────────────────────────

def build_execution_plan(
    registry,
    root_ids: str | list[str],
    parameters: dict[str, Any],
    source: InputSource,
) -> ExecutionPlan:
    """Walk a metric's dependency tree once → an ordered, resolved plan.

    For each metric reached from ``root_ids``:
    - inputs whose ref is a registered metric become ``DepInput`` markers;
    - other inputs are sourced via ``source`` and classified with the shared
      optional/missing semantics (a missing or ``None`` *required* input records
      a step failure; an optional one becomes ``RawInput(None)``);
    - Compute Parameters (``role == "compute"``) are filtered from presentation
      ones, defaulting to each parameter's declared default.

    Dependencies are ordered before their dependents (post-order DFS); each
    metric appears exactly once even when shared.  No frozen graph is required —
    ordering comes from the walk itself.
    """
    if isinstance(root_ids, str):
        root_ids = [root_ids]

    steps: list[PlanStep] = []
    seen: set[str] = set()

    def visit(metric_id: str) -> None:
        if metric_id in seen:
            return
        seen.add(metric_id)
        schema, _ = registry.get(metric_id)

        bindings: dict[str, Binding] = {}
        failure: str | None = None

        for local_key, ref in schema.inputs.items():
            if registry.has(ref):
                visit(ref)  # dependency first → post-order gives a valid run order
                bindings[local_key] = DepInput(ref)
                continue

            found, value = source.get(metric_id, local_key, ref)
            optional = local_key in schema.optional_inputs
            if not found:
                bindings[local_key] = RawInput(None)
                if not optional and failure is None:
                    failure = f"Missing raw input '{local_key}' (symbolic ref '{ref}')"
            elif value is None:
                bindings[local_key] = RawInput(None)
                if not optional and failure is None:
                    failure = f"Input '{local_key}' (ref '{ref}') is unavailable for this dataset"
            else:
                bindings[local_key] = RawInput(np.asarray(value))

        # Bare optional inputs — declared in optional_inputs but not in inputs.
        for opt_key in schema.optional_inputs:
            if opt_key not in bindings:
                found, value = source.get(metric_id, opt_key, opt_key)
                bindings[opt_key] = RawInput(
                    np.asarray(value) if (found and value is not None) else None
                )

        compute_params = {
            key: parameters.get(key, p.default)
            for key, p in schema.parameters.items()
            if p.role == "compute"
        }

        steps.append(
            PlanStep(
                metric_id=metric_id,
                bindings=bindings,
                compute_params=compute_params,
                failure=failure,
            )
        )

    for rid in root_ids:
        visit(rid)

    return ExecutionPlan(root_ids=list(root_ids), steps=steps, parameters=parameters)


# ── Plan execution ──────────────────────────────────────────────────────────

# run_fn(metric_id, schema, fn, kwargs, compute_params) -> raw value | MetricFailure
RunFn = Callable[[str, Any, Callable, dict, dict], Any]


def run_plan(
    plan: ExecutionPlan,
    registry,
    cache: MetricCache,
    run_fn: RunFn,
) -> dict[str, MetricResult | MetricFailure]:
    """Execute a plan's steps in order → ``{metric_id: MetricResult | MetricFailure}``.

    Everything that is identical across transports lives here: dependency
    wiring, the result cache (checked before ``run_fn`` so cached metrics are
    never re-run or shipped to a worker), missing/failed-dependency propagation,
    and ``MetricResult`` construction.  ``run_fn`` is the transport: it either
    calls the metric function directly or ships it to a worker, returning the raw
    function output or a ``MetricFailure``.
    """
    results: dict[str, MetricResult | MetricFailure] = {}

    for step in plan.steps:
        metric_id = step.metric_id
        schema, fn = registry.get(metric_id)

        # A required raw input was missing/None → don't run, just fail this step.
        if step.failure is not None:
            results[metric_id] = MetricFailure(
                metric_id=metric_id, traceback=step.failure, parameters=plan.parameters
            )
            continue

        # Assemble kwargs from raw values + already-computed dependency outputs.
        kwargs: dict[str, Any] = {}
        dep_failure: tuple[str, MetricFailure] | None = None
        for key, binding in step.bindings.items():
            if isinstance(binding, DepInput):
                dep = results.get(binding.metric_id)
                if isinstance(dep, MetricFailure):
                    dep_failure = (binding.metric_id, dep)
                    break
                kwargs[key] = dep.values
            else:
                kwargs[key] = binding.value

        if dep_failure is not None:
            dep_id, dep = dep_failure
            results[metric_id] = MetricFailure(
                metric_id=metric_id,
                traceback=f"Dependency '{dep_id}' failed:\n{dep.traceback}",
                parameters=plan.parameters,
            )
            continue

        cache_key = cache.make_key(metric_id, fn, step.compute_params, kwargs)
        cached = cache.get(cache_key)
        if cached is not None:
            results[metric_id] = cached
            continue

        raw = run_fn(metric_id, schema, fn, kwargs, step.compute_params)
        if isinstance(raw, MetricFailure):
            results[metric_id] = raw
            continue

        arr = np.asarray(raw)
        checksum = hashlib.sha256(np.ascontiguousarray(arr).tobytes()).hexdigest()
        result = MetricResult(
            metric_id=metric_id,
            shape=shape_to_str(schema.shape),
            dtype=str(arr.dtype),
            unit=schema.unit,
            compute_parameters=step.compute_params,
            implementation_hash=function_hash(fn),
            checksum=checksum,
            values=arr,
        )
        cache.put(cache_key, result)
        results[metric_id] = result

    return results

"""MetricGraph — DAG of metric dependencies (M1).

Built once at registry.freeze(). Provides topological compute plans
so InProcessExecutor.run_batch() shares intermediates rather than
recomputing them per dependent.
"""
from __future__ import annotations

from graphlib import TopologicalSorter, CycleError
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ffast.metrics.models import MetricSchema


class MetricGraph:
    """Immutable DAG built from a frozen registry snapshot."""

    def __init__(self) -> None:
        self._deps: dict[str, set[str]] = {}   # metric_id → {direct metric-ID deps}
        self._frozen = False

    def freeze(
        self,
        metrics: dict[str, tuple],  # {id: (MetricSchema, fn)}
        valid_refs: frozenset | None = None,
    ) -> list[tuple[str, str]]:
        """Build DAG and validate. Returns [(metric_id, error_msg), ...].

        valid_refs: set of valid symbolic ref strings (from inputs.ALL_VALID_REFS).
                    If None, symbolic ref validation is skipped.
        """
        from ffast.metrics.dims import Dim
        from ffast.metrics.inputs import is_field_ref

        errors: list[tuple[str, str]] = []
        graph: dict[str, set[str]] = {}

        for mid, (schema, _fn) in metrics.items():
            metric_deps: set[str] = set()

            for input_key, input_ref in schema.inputs.items():
                if input_ref in metrics:
                    metric_deps.add(input_ref)
                else:
                    # Valid if in the closed set OR a Dataset Field ref (ADR 0023).
                    if valid_refs is not None and input_ref not in valid_refs and not is_field_ref(input_ref):
                        errors.append((mid, f"Unknown symbolic ref '{input_ref}' for input '{input_key}'"))

            # shape validation
            shape = schema.shape
            if isinstance(shape, str):
                errors.append((mid, f"shape='{shape}' is a legacy string — use dims.* constants"))
            elif isinstance(shape, Dim):
                pass  # valid
            elif isinstance(shape, tuple):
                for d in shape:
                    if not isinstance(d, Dim):
                        errors.append((mid, f"shape tuple contains non-Dim element: {d!r}"))
            else:
                errors.append((mid, f"shape has unexpected type: {type(shape).__name__}"))

            graph[mid] = metric_deps

        # cycle detection
        try:
            ts = TopologicalSorter(graph)
            ts.prepare()
            while ts.is_active():
                ready = list(ts.get_ready())
                for node in ready:
                    ts.done(node)
        except CycleError as e:
            errors.append(("__cycle__", f"Dependency cycle detected: {e}"))

        self._deps = graph
        self._frozen = True
        return errors

    def compute_plan(self, metric_ids: list[str]) -> list[str]:
        """Return ordered execution list for the given metrics + all transitive deps.

        Only metrics needed for the requested IDs are included.
        """
        if not self._frozen:
            raise RuntimeError("MetricGraph.freeze() must be called before compute_plan()")

        needed: set[str] = set()
        stack = list(metric_ids)
        while stack:
            mid = stack.pop()
            if mid in needed:
                continue
            needed.add(mid)
            stack.extend(self._deps.get(mid, ()))

        subgraph = {mid: self._deps[mid] & needed for mid in needed}
        ts = TopologicalSorter(subgraph)
        return list(ts.static_order())

    def dependencies_of(self, metric_id: str) -> set[str]:
        """Full transitive dependency set for one metric."""
        if not self._frozen:
            raise RuntimeError("MetricGraph.freeze() must be called before dependencies_of()")
        result: set[str] = set()
        stack = list(self._deps.get(metric_id, ()))
        while stack:
            mid = stack.pop()
            if mid in result:
                continue
            result.add(mid)
            stack.extend(self._deps.get(mid, ()))
        return result

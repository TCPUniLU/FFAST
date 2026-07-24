from __future__ import annotations

import traceback as tb
from abc import ABC, abstractmethod
from typing import Any

from ffast.metrics.cache import MetricCache
from ffast.metrics.execution import (
    ExecutionPlan,
    InputSource,
    build_execution_plan,
    run_plan,
)
from ffast.metrics.models import MetricFailure, MetricResult
from ffast.metrics.registry import MetricRegistry


class MetricExecutor(ABC):
    @abstractmethod
    def run(self, id: str, source: InputSource, parameters: dict[str, Any]) -> MetricResult | MetricFailure:
        ...


class InProcessExecutor(MetricExecutor):
    """Runs metrics directly in the calling process.

    Input resolution, dependency ordering, Compute Parameter filtering, and
    caching all live in the Metric Execution Context (ADR 0035); this executor
    only supplies the transport — calling each metric function in-process.
    """

    def __init__(self, registry: MetricRegistry, cache: MetricCache | None = None) -> None:
        self._registry = registry
        self._cache = cache if cache is not None else MetricCache()

    def _call(self, id: str, schema: Any, fn: Any, kwargs: dict, compute_params: dict, parameters: dict):
        """Transport: run the metric function here, wrapping failures."""
        try:
            return fn(**kwargs, **compute_params)
        except Exception:
            return MetricFailure(metric_id=id, traceback=tb.format_exc(), parameters=parameters)

    def _execute(self, plan: ExecutionPlan) -> dict[str, MetricResult | MetricFailure]:
        return run_plan(
            plan,
            self._registry,
            self._cache,
            lambda mid, schema, fn, kwargs, cparams: self._call(
                mid, schema, fn, kwargs, cparams, plan.parameters
            ),
        )

    def run(self, id: str, source: InputSource, parameters: dict[str, Any]) -> MetricResult | MetricFailure:
        """Run a single metric and its dependencies."""
        plan = build_execution_plan(self._registry, id, parameters, source)
        return self._execute(plan)[id]

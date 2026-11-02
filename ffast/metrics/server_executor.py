"""MetricExecutor of the server (ADR 0079).

FFAST's own metric code (built-ins, transforms, formulas) runs in this
process. Outside code, Python that did not ship with FFAST, runs in worker
processes. A server with no outside code never starts a worker.
"""

from __future__ import annotations

import threading
import traceback as tb
from typing import Any, Callable

from ffast.metrics.cache import MetricCache
from ffast.metrics.execution import InputSource, build_execution_plan, run_plan
from ffast.metrics.executor import MetricExecutor
from ffast.metrics.models import MetricFailure, MetricResult
from ffast.metrics.placement import is_outside_code
from ffast.metrics.registry import MetricRegistry


class ServerExecutor(MetricExecutor):
    """Runs FFAST's code here and outside code in a ``WorkerProcessExecutor``.

    ``outside`` decides which is which; it is injectable for tests only, since
    test metric functions live in ``tests.*`` and so are outside code by the
    real rule.
    """

    def __init__(
        self,
        registry: MetricRegistry,
        cache: MetricCache | None = None,
        *,
        outside: Callable[[Any], bool] = is_outside_code,
        workers: Any = None,
    ) -> None:
        self._registry = registry
        self._cache = cache if cache is not None else MetricCache()
        self._outside = outside
        self._workers_executor = workers
        self._workers_lock = threading.Lock()

    @property
    def _workers_built(self) -> bool:
        return self._workers_executor is not None

    def _workers(self):
        with self._workers_lock:
            if self._workers_executor is None:
                from ffast.metrics.pool import WorkerProcessExecutor
                self._workers_executor = WorkerProcessExecutor(self._registry, cache=self._cache)
            return self._workers_executor

    def start(self) -> None:
        """Warm the slow imports on a thread and boot a worker if the registry
        holds outside code. Returns at once."""
        def warm() -> None:
            from ffast.metrics.pool import _preload
            _preload()

        threading.Thread(target=warm, name="ffast-metric-warm", daemon=True).start()
        if any(self._outside(self._registry.get(m)[1]) for m in self._registry.list_metrics()):
            self._workers().start()

    def shutdown(self) -> None:
        if self._workers_executor is not None:
            self._workers_executor.shutdown()

    def _step(self, id, schema, fn, kwargs, compute_params, parameters):
        """Transport: one step here, or in a worker when ``fn`` is outside code."""
        if self._outside(fn):
            return self._workers()._ship_to_worker(id, schema, fn, kwargs, compute_params, parameters)
        try:
            return fn(**kwargs, **compute_params)
        except Exception:
            return MetricFailure(metric_id=id, traceback=tb.format_exc(), parameters=parameters)

    def run(self, id: str, source: InputSource, parameters: dict[str, Any]) -> MetricResult | MetricFailure:
        plan = build_execution_plan(self._registry, id, parameters, source)
        results = run_plan(
            plan,
            self._registry,
            self._cache,
            lambda mid, schema, fn, kwargs, cparams: self._step(
                mid, schema, fn, kwargs, cparams, parameters
            ),
        )
        return results[id]

    def call(self, fn: Any, kwargs: dict[str, Any], label: str = "formula") -> Any:
        if self._outside(fn):
            return self._workers().call(fn, kwargs, label)
        return fn(**kwargs)

"""MetricExecutor of the server (ADR 0079).

FFAST's own metric code (built-ins, transforms, formulas) runs in this
process. Outside code, Python that did not ship with FFAST, runs in worker
processes. A server with no outside code never starts a worker.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback as tb
from typing import Any, Callable

import numpy as np

from ffast.metrics.cache import MetricCache
from ffast.metrics.execution import InputSource, build_execution_plan, run_plan
from ffast.metrics.executor import MetricExecutor
from ffast.metrics.models import MetricFailure, MetricResult
from ffast.metrics.placement import is_outside_code
from ffast.metrics.registry import MetricRegistry
from ffast.metrics.resources import MemoryGate
from ffast.metrics import stop

log = logging.getLogger(__name__)

_MIB = 2**20


def _estimate(schema: Any, fn: Any, kwargs: dict[str, Any]) -> int:
    """Bytes a step is expected to need (ADR 0079 decision 2): its
    ``memory_mb`` hint; else, for a formula, one full-size array per
    operation plus the result; else twice its array inputs. At least 1 MiB."""
    hint = schema.hints.memory_mb if schema is not None else None
    if hint is not None:
        return max(hint * _MIB, _MIB)
    arrays = [v.nbytes for v in kwargs.values() if isinstance(v, np.ndarray)]
    from ffast.metrics.expr import _ExprFn
    if isinstance(fn, _ExprFn):
        return max((fn.operations + 1) * max(arrays, default=0), _MIB)
    return max(2 * sum(arrays), _MIB)


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
        gate: MemoryGate | None = None,
    ) -> None:
        self._registry = registry
        self._cache = cache if cache is not None else MetricCache()
        self._outside = outside
        self._workers_executor = workers
        self._workers_lock = threading.Lock()
        # One gate for in-server steps and steps shipped to a worker.
        self._gate = gate if gate is not None else MemoryGate()

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
        """Hold BLAS/OpenMP to one thread (the chunk pool is the server's
        parallelism), warm the slow imports on a thread and boot a worker if
        the registry holds outside code. Returns at once."""
        try:
            import threadpoolctl
            threadpoolctl.threadpool_limits(1)
        except Exception:
            log.warning("threadpoolctl is not available; BLAS threads are not limited")

        def warm() -> None:
            from ffast.metrics.pool import _preload
            _preload()

        threading.Thread(target=warm, name="ffast-metric-warm", daemon=True).start()
        if any(self._outside(self._registry.get(m)[1]) for m in self._registry.list_metrics()):
            self._workers().start()

    def shutdown(self) -> None:
        if self._workers_executor is not None:
            self._workers_executor.shutdown()

    def _step(self, id, schema, fn, kwargs, compute_params, parameters, stopped=lambda: False):
        """Transport: one step here, or in a worker when ``fn`` is outside code."""
        outside = self._outside(fn)
        want = _estimate(schema, fn, kwargs)
        asked = time.monotonic()
        reservation = self._gate.reserve(want, stopped)
        if reservation is None:
            return MetricFailure(metric_id=id, traceback=stop.STOPPED, parameters=parameters)
        with reservation:
            started = time.monotonic()
            token = stop.running_under(stopped)
            try:
                if outside:
                    return self._workers()._ship_to_worker(
                        id, schema, fn, kwargs, compute_params, parameters)
                try:
                    return fn(**kwargs, **compute_params)
                except stop.Stopped:
                    return MetricFailure(metric_id=id, traceback=stop.STOPPED, parameters=parameters)
                except Exception:
                    return MetricFailure(metric_id=id, traceback=tb.format_exc(), parameters=parameters)
            finally:
                stop.reset(token)
                log.info(
                    "metric step %s: waited %d ms for memory, computed in %d ms "
                    "(reserved ~%d MB, in %s)",
                    id, (started - asked) * 1000, (time.monotonic() - started) * 1000,
                    want // _MIB, "worker" if outside else "server")

    def run(self, id: str, source: InputSource, parameters: dict[str, Any]) -> MetricResult | MetricFailure:
        plan = build_execution_plan(self._registry, id, parameters, source)
        results = run_plan(
            plan,
            self._registry,
            self._cache,
            lambda mid, schema, fn, kwargs, cparams: self._step(
                mid, schema, fn, kwargs, cparams, parameters, source.stopped
            ),
            stopped=source.stopped,
        )
        return results[id]

    def call(self, fn: Any, kwargs: dict[str, Any], label: str = "formula") -> Any:
        with self._gate.reserve(_estimate(None, fn, kwargs)):
            if self._outside(fn):
                return self._workers().call(fn, kwargs, label)
            return fn(**kwargs)

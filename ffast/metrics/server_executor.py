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
from contextlib import contextmanager
from typing import Any, Callable

import numpy as np
from ffast.io.batched import BatchedArray
from ffast.io.extxyz_arrays import NotEnoughMemory

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
        # Streaming bypasses the full-array gate, but its small workspaces
        # must still queue when several dashboards request scans together.
        self._batch_gate = MemoryGate(available=self._batch_headroom, share=0.5)
        self._colour_cache = MetricCache(max_bytes=128 * 1024, spill_max_bytes=0)
        from ffast.metrics.force_scan import ForceScans
        self._force_scans = ForceScans(self._batch_work)

    @staticmethod
    def _batch_headroom():
        from ffast.io.budget import budget
        from ffast.metrics.resources import available_memory

        owner = budget()
        return min(available_memory(), max(0, owner.limit - owner.used))

    @contextmanager
    def _batch_work(self, stopped, *, blocks=4):
        from ffast.io.budget import budget

        # Arithmetic workspace plus source/headroom slots. The shared force
        # producer reserves more blocks once; its waiting consumers reserve none.
        reservation = self._batch_gate.reserve(
            (blocks + 2) * budget().block_bytes, stopped
        )
        if reservation is None:
            raise stop.Stopped(stop.STOPPED)
        with reservation, budget().reserve(blocks * budget().block_bytes):
            stop.check()
            yield

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
        self._force_scans.forget()
        if self._workers_executor is not None:
            self._workers_executor.shutdown()

    def _step(self, id, schema, fn, kwargs, compute_params, parameters, stopped=lambda: False,
              *, defer_result=False, force_scan=None):
        """Transport: one step here, or in a worker when ``fn`` is outside code."""
        outside = self._outside(fn)
        from ffast.metrics.colouring import deferring_results
        if force_scan is not None and not outside and not deferring_results():
            from ffast.metrics.force_scan import NOT_HANDLED
            try:
                shared = force_scan.select(id, fn)
                if shared is not NOT_HANDLED:
                    return shared
            except (NotEnoughMemory, MemoryError, stop.Stopped) as exc:
                return MetricFailure(metric_id=id, traceback=str(exc) or stop.STOPPED,
                                     parameters=parameters, kind="temporary")
            except Exception:
                return MetricFailure(metric_id=id, traceback=tb.format_exc(), parameters=parameters)
        if deferring_results() and not outside:
            # Even RAM-backed datasets should calculate colours a batch at
            # a time. Offsets remain small indexing metadata.
            kwargs = {k: BatchedArray(v.shape, v.dtype, lambda a, b, v=v: v[a:b])
                      if isinstance(v, np.ndarray) and v.ndim and k not in ("offsets", "_offsets")
                      else v for k, v in kwargs.items()}
        if any(isinstance(v, BatchedArray) for v in kwargs.values()):
            from ffast.io.budget import budget
            from ffast.io.result_array import store_result
            from ffast.metrics.streaming import execute, NOT_HANDLED
            token = stop.running_under(stopped)
            try:
                with self._batch_work(stopped):
                    if not outside:
                        raw = execute(fn, kwargs, compute_params)
                        if raw is not NOT_HANDLED:
                            return raw if defer_result and isinstance(raw, BatchedArray) else store_result(raw)
                    # Arbitrary code keeps its ndarray contract, but only
                    # after the full input and working-set estimate fits.
                    want = max(_estimate(schema, fn, kwargs),
                               2 * sum(v.nbytes for v in kwargs.values() if hasattr(v, "nbytes")))
                    budget().check(want)
                    materialized = {k: np.asarray(v) if isinstance(v, BatchedArray) else v
                                    for k, v in kwargs.items()}
                    if outside:
                        return self._workers()._ship_to_worker(
                            id, schema, fn, materialized, compute_params, parameters)
                    return fn(**materialized, **compute_params)
            except (MemoryError, NotEnoughMemory, stop.Stopped) as exc:
                return MetricFailure(metric_id=id, traceback=str(exc) or stop.STOPPED,
                                     parameters=parameters, kind="temporary")
            except Exception:
                return MetricFailure(metric_id=id, traceback=tb.format_exc(), parameters=parameters)
            finally:
                stop.reset(token)
        want = _estimate(schema, fn, kwargs)
        asked = time.monotonic()
        reservation = self._gate.reserve(want, stopped)
        if reservation is None:
            return MetricFailure(metric_id=id, traceback=stop.STOPPED, parameters=parameters, kind="temporary")
        with reservation:
            started = time.monotonic()
            token = stop.running_under(stopped)
            try:
                from ffast.io.budget import budget
                # Reserve what these inputs need. A schema's memory_mb hint
                # is a fixed guess for large jobs; the gate above already
                # uses it to make heavy steps take turns. Reserving it here
                # refused small inputs whenever the budget was below it.
                arrays = [v.nbytes for v in kwargs.values() if isinstance(v, np.ndarray)]
                working = max(1024, 2 * sum(arrays))
                with budget().reserve(working):
                    if outside:
                        return self._workers()._ship_to_worker(
                            id, schema, fn, kwargs, compute_params, parameters)
                    return fn(**kwargs, **compute_params)
            except stop.Stopped:
                return MetricFailure(metric_id=id, traceback=stop.STOPPED,
                                     parameters=parameters, kind="temporary")
            except (MemoryError, NotEnoughMemory) as exc:
                return MetricFailure(metric_id=id, traceback=str(exc),
                                     parameters=parameters, kind="temporary")
            except Exception:
                return MetricFailure(metric_id=id, traceback=tb.format_exc(), parameters=parameters)
            finally:
                stop.reset(token)
                log.info(
                    "metric step %s: waited %d ms for memory, computed in %d ms "
                    "(reserved ~%d MB, in %s)",
                    id, (started - asked) * 1000, (time.monotonic() - started) * 1000,
                    want // _MIB, "worker" if outside else "server")

    def run(self, id: str, source: InputSource, parameters: dict[str, Any], *,
            key_out: dict | None = None) -> MetricResult | MetricFailure:
        plan = build_execution_plan(self._registry, id, parameters, source)
        final_key = {} if key_out is None else key_out
        shared = self._force_scans.acquire(plan, source, self._registry)
        try:
            results = run_plan(
                plan,
                self._registry,
                self._cache,
                lambda mid, schema, fn, kwargs, cparams: self._step(
                    mid, schema, fn, kwargs, cparams, parameters, source.stopped,
                    defer_result=mid not in plan.root_ids, force_scan=shared,
                ),
                stopped=source.stopped,
                key_out=final_key,
                uses=getattr(source, "uses", frozenset)(),
                stream_intermediates=True,
            )
        finally:
            if shared is not None:
                shared.close()
        result = results[id]
        # A dependency cached as a virtual array may later be requested as a
        # root. Admit its complete output then, keeping the same size checks.
        if (isinstance(result, MetricResult) and isinstance(result.values, BatchedArray)
                and not hasattr(result.values, "result_path")):
            from ffast.io.budget import budget
            from ffast.io.result_array import store_result
            token = stop.running_under(source.stopped)
            try:
                with self._batch_work(source.stopped):
                    values = store_result(result.values)
                result = result.model_copy(update={"values": values})
                if "key" in final_key:
                    self._cache.put(final_key["key"], result)
            except (NotEnoughMemory, MemoryError, stop.Stopped) as exc:
                return MetricFailure(metric_id=id, traceback=str(exc) or stop.STOPPED,
                                     parameters=parameters, kind="temporary")
            finally:
                stop.reset(token)
        return result

    def run_colouring(self, id, source, parameters, *, key_out=None):
        """Cache exact bounds and a recipe for frame reads, never a full array."""
        from ffast.io.batched import blocks
        from ffast.io.budget import budget
        from ffast.metrics.colouring import defer_results

        token = stop.running_under(source.stopped)
        try:
            with defer_results():
                plan = build_execution_plan(
                    self._registry, id, parameters, source
                )
                final_key = {}
                results = run_plan(
                    plan,
                    self._registry,
                    self._colour_cache,
                    lambda mid, schema, fn, kwargs, params: self._step(
                        mid,
                        schema,
                        fn,
                        kwargs,
                        params,
                        parameters,
                        source.stopped,
                        defer_result=True,
                    ),
                    stopped=source.stopped,
                    key_out=final_key,
                    uses=getattr(source, "uses", frozenset)(),
                    stream_intermediates=True,
                )
                result = results[id]
                if isinstance(result, MetricFailure):
                    return result
                if not result._colour_only:
                    lo, hi = np.inf, -np.inf
                    values = result.values
                    with self._batch_work(source.stopped):
                        if isinstance(values, BatchedArray):
                            chunks = (v for _, _, v in blocks(values))
                        else:
                            chunks = [np.asarray(values)]
                        for chunk in chunks:
                            stop.check()
                            finite = chunk[np.isfinite(chunk)]
                            if finite.size:
                                lo, hi = min(lo, float(finite.min())), max(
                                    hi, float(finite.max())
                                )
                    result._colour_span = (
                        (lo, hi)
                        if np.isfinite(lo) and np.isfinite(hi)
                        else None
                    )
                    if result.shape in (
                        "N_elements",
                        "(N_frames, N_elements)",
                    ):
                        found, elements = source.get(
                            id, "_elements", "reference.elements"
                        )
                        if found and elements is not None:
                            result._colour_elements = np.unique(elements)
                    notes = list(result.warnings)
                    for step in results.values():
                        if isinstance(step, MetricResult):
                            provider = getattr(step.values, "_colour_warnings", None)
                            if provider is not None:
                                notes.extend(provider())
                    result.warnings = list(dict.fromkeys(notes))
                    result._colour_only = True
                    self._colour_cache.put(final_key["key"], result)
                if key_out is not None:
                    key_out["key"] = ("colour", final_key["key"])
                return result
        except (NotEnoughMemory, MemoryError, stop.Stopped) as exc:
            return MetricFailure(
                metric_id=id,
                traceback=str(exc) or stop.STOPPED,
                parameters=parameters,
                kind="temporary",
            )
        except Exception:
            return MetricFailure(
                metric_id=id, traceback=tb.format_exc(), parameters=parameters
            )
        finally:
            stop.reset(token)

    def cached(self, key):
        if isinstance(key, tuple) and len(key) == 2 and key[0] == "colour":
            result = self._colour_cache.get(key[1])
            return (
                result if result is not None and result._colour_only else None
            )
        return super().cached(key)

    def stored_for(self, fingerprints):
        main = super().stored_for(fingerprints)
        colours = self._colour_cache.stored_for(fingerprints)
        return {key: main[key] + colours[key] for key in main}

    def forget_objects(self, fingerprints):
        fingerprints = tuple(fingerprints)
        self._force_scans.forget(fingerprints)
        main = super().forget_objects(fingerprints)
        colours = self._colour_cache.forget_objects(fingerprints)
        return {key: main[key] + colours[key] for key in main}

    def call(self, fn: Any, kwargs: dict[str, Any], label: str = "formula") -> Any:
        with self._gate.reserve(_estimate(None, fn, kwargs)):
            if self._outside(fn):
                return self._workers().call(fn, kwargs, label)
            return fn(**kwargs)

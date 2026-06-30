"""Worker-process MetricExecutor with shared-memory inputs and recycling.

Architecture
------------
WorkerProcessExecutor wraps one lazily-spawned worker subprocess at a time.

Large numpy inputs (>= policy.shm_threshold_bytes) are passed via
multiprocessing.shared_memory.SharedMemory (Worker Buffers) to avoid
inter-process array copies.

Worker lifecycle
- Self-terminates after policy.max_tasks_per_worker tasks.
- Parent detects crash (BrokenPipeError / EOFError) and spawns a replacement.
- If the hard time limit (effective_timeout) is exceeded, the parent
  terminates the worker process and returns a MetricFailure.
  effective_timeout = min(schema.hints.max_runtime_s, policy.max_runtime_s)
  when the metric declares a hint; otherwise policy.max_runtime_s.
- A freshly-spawned worker signals readiness (after import + registry unpickle)
  via the _READY sentinel; the parent consumes it before arming the deadline,
  so worker cold-start (bounded separately by policy.spawn_timeout_s) is never
  charged to a metric's runtime.

Cancellation
- Cooperative: parent sends shutdown sentinel (None), waits grace_period_s.
- Forced: parent calls process.terminate() after the grace period.

Dependency resolution
- Metric-to-metric dependencies are resolved in the parent process (recursive
  run() calls, same as InProcessExecutor).  The worker receives only resolved
  scalar / array inputs and pre-computed compute_params.
"""

from __future__ import annotations

import hashlib
import multiprocessing as mp
import pickle
import time
import traceback as tb
from dataclasses import dataclass
from multiprocessing.shared_memory import SharedMemory
from typing import Any

import numpy as np

from ffast.metrics.cache import MetricCache, function_hash
from ffast.metrics.dims import shape_to_str
from ffast.metrics.executor import MetricExecutor
from ffast.metrics.models import MetricFailure, MetricResult
from ffast.metrics.registry import MetricRegistry


# ── Policy ─────────────────────────────────────────────────────────────────────

@dataclass
class PoolPolicy:
    """Hard server limits enforced on every metric worker."""

    max_tasks_per_worker: int = 100
    max_runtime_s: float = 300.0
    grace_period_s: float = 5.0
    shm_threshold_bytes: int = 1_048_576  # arrays >= 1 MiB use SharedMemory
    # Budget for a fresh worker to boot (interpreter start + imports + registry
    # unpickle) and reach its recv loop. Cold-start is NOT a metric's runtime,
    # so it is bounded separately and excluded from max_runtime_s.
    spawn_timeout_s: float = 30.0


# Sentinel a freshly-booted worker sends once it has unpickled the registry and
# is about to block on recv — see _await_ready.
_READY = "__worker_ready__"


# ── Shared-memory helpers (Worker Buffers) ────────────────────────────────────

@dataclass
class _ShmDescriptor:
    """Lightweight descriptor for a numpy array in shared memory."""

    shm_name: str
    dtype: str
    shape: tuple[int, ...]


def _pack_inputs(
    inputs: dict[str, Any],
    threshold: int,
) -> tuple[dict[str, Any], list[SharedMemory]]:
    """Replace large numpy arrays with ShmDescriptors; return created SharedMemory blocks."""
    packed: dict[str, Any] = {}
    shm_list: list[SharedMemory] = []
    for key, value in inputs.items():
        if isinstance(value, np.ndarray) and value.nbytes >= threshold:
            arr = np.ascontiguousarray(value)
            shm = SharedMemory(create=True, size=arr.nbytes)
            shared = np.ndarray(arr.shape, dtype=arr.dtype, buffer=shm.buf)
            shared[:] = arr
            packed[key] = _ShmDescriptor(
                shm_name=shm.name, dtype=arr.dtype.str, shape=tuple(arr.shape)
            )
            shm_list.append(shm)
        else:
            packed[key] = value
    return packed, shm_list


def _unpack_inputs(packed: dict[str, Any]) -> tuple[dict[str, Any], list[SharedMemory]]:
    """Materialise ShmDescriptors to numpy arrays; return opened SharedMemory blocks."""
    inputs: dict[str, Any] = {}
    shm_list: list[SharedMemory] = []
    for key, value in packed.items():
        if isinstance(value, _ShmDescriptor):
            shm = SharedMemory(name=value.shm_name, create=False)
            arr = np.ndarray(value.shape, dtype=np.dtype(value.dtype), buffer=shm.buf).copy()
            inputs[key] = arr
            shm_list.append(shm)
        else:
            inputs[key] = value
    return inputs, shm_list


# ── Worker subprocess entry point ─────────────────────────────────────────────

def _worker_main(
    conn: "mp.connection.Connection",
    registry_bytes: bytes,
    policy: PoolPolicy,
) -> None:
    """Entry point for each worker subprocess.

    Receives pre-resolved inputs and pre-computed compute_params from the
    parent — no dependency resolution or parameter filtering needed here.

    Runs until:
    - parent sends the shutdown sentinel (None msg);
    - pipe is closed (EOFError);
    - max_tasks_per_worker reached (clean self-exit).
    """
    registry: MetricRegistry = pickle.loads(registry_bytes)
    task_count = 0

    # Announce readiness: the parent's per-metric deadline only starts once it
    # has consumed this, so interpreter boot + imports + unpickle above don't
    # eat into a metric's hard time limit.
    conn.send(_READY)

    try:
        while True:
            try:
                msg = conn.recv()
            except EOFError:
                break

            if msg is None:
                break  # Cooperative shutdown sentinel

            task_id: int = msg["task_id"]
            metric_id: str = msg["metric_id"]
            packed_inputs: dict = msg["packed_inputs"]
            compute_params: dict = msg["compute_params"]  # pre-filtered by parent

            inputs, shm_list = _unpack_inputs(packed_inputs)
            try:
                _, fn = registry.get(metric_id)
                value = fn(**inputs, **compute_params)
                conn.send({"task_id": task_id, "value": value})
            except Exception:
                conn.send({
                    "task_id": task_id,
                    "value": MetricFailure(
                        metric_id=metric_id,
                        traceback=tb.format_exc(),
                        parameters=compute_params,
                    ),
                })
            finally:
                for shm in shm_list:
                    shm.close()

            task_count += 1
            if task_count >= policy.max_tasks_per_worker:
                break  # Self-terminate; parent spawns a replacement on next call

    finally:
        conn.close()


# ── Worker handle ─────────────────────────────────────────────────────────────

class _Worker:
    def __init__(self, conn: "mp.connection.Connection", process: mp.Process) -> None:
        self.conn = conn
        self.process = process
        self.task_count = 0
        self.ready = False  # set once the _READY sentinel has been consumed

    @property
    def alive(self) -> bool:
        return self.process.is_alive()

    def terminate(self) -> None:
        try:
            self.process.terminate()
            self.process.join(timeout=1)
        except Exception:
            pass
        try:
            self.conn.close()
        except Exception:
            pass


# ── WorkerProcessExecutor ─────────────────────────────────────────────────────

class WorkerProcessExecutor(MetricExecutor):
    """MetricExecutor that runs metrics in recycled worker subprocesses.

    Drop-in replacement for InProcessExecutor: implements the same ABC and
    returns MetricResult / MetricFailure.

    Dependency resolution, parameter filtering, caching, and MetricResult
    construction all happen in the parent process.  The worker subprocess
    receives only resolved inputs and pre-computed compute_params and returns
    only the raw function output.

    Large numpy inputs use shared memory (Worker Buffers).
    Workers self-recycle after policy.max_tasks_per_worker tasks.
    Crashed workers are transparently replaced on the next run() call.

    Effective timeout per metric:
        min(schema.hints.max_runtime_s, policy.max_runtime_s)
    when the metric declares a max_runtime_s hint; otherwise policy.max_runtime_s.
    """

    def __init__(
        self,
        registry: MetricRegistry,
        policy: PoolPolicy | None = None,
        cache: MetricCache | None = None,
    ) -> None:
        self._registry = registry
        self._policy = policy or PoolPolicy()
        self._cache = cache if cache is not None else MetricCache()
        self._registry_bytes = pickle.dumps(registry)
        self._worker: _Worker | None = None
        self._task_counter = 0

    def _spawn_worker(self) -> _Worker:
        parent_conn, child_conn = mp.Pipe(duplex=True)
        process = mp.Process(
            target=_worker_main,
            args=(child_conn, self._registry_bytes, self._policy),
            daemon=True,
        )
        process.start()
        child_conn.close()  # Parent only uses the parent end
        return _Worker(conn=parent_conn, process=process)

    def _get_worker(self) -> _Worker:
        if self._worker is None or not self._worker.alive:
            self._worker = self._spawn_worker()
        return self._worker

    def _await_ready(self, worker: _Worker) -> bool:
        """Block until a freshly-spawned worker has booted and is in its recv
        loop. Returns False if it never signals ready within spawn_timeout_s
        (slow boot under load, or a crash during import/unpickle).

        Consuming the readiness sentinel here — before the per-metric deadline
        starts — keeps worker cold-start out of the metric's hard time limit.
        A warm (already-ready) worker returns immediately.
        """
        if worker.ready:
            return True
        try:
            if worker.conn.poll(self._policy.spawn_timeout_s):
                if worker.conn.recv() == _READY:
                    worker.ready = True
                    return True
        except (EOFError, OSError):
            pass
        return False

    def run(self, id: str, inputs: dict[str, Any], parameters: dict[str, Any]) -> MetricResult | MetricFailure:
        schema, fn = self._registry.get(id)

        # Resolve metric-to-metric dependencies in parent process.
        resolved: dict[str, Any] = {}
        for input_key, input_ref in schema.inputs.items():
            if self._registry.has(input_ref):
                dep = self.run(input_ref, inputs, parameters)
                if isinstance(dep, MetricFailure):
                    return MetricFailure(
                        metric_id=id,
                        traceback=f"Dependency '{input_ref}' failed:\n{dep.traceback}",
                        parameters=parameters,
                    )
                resolved[input_key] = dep.values
            else:
                if input_key not in inputs:
                    return MetricFailure(
                        metric_id=id,
                        traceback=f"Missing raw input '{input_key}' (symbolic ref '{input_ref}')",
                        parameters=parameters,
                    )
                resolved[input_key] = inputs[input_key]

        compute_params = {
            k: parameters.get(k, p.default)
            for k, p in schema.parameters.items()
            if p.role == "compute"
        }

        cache_key = self._cache.make_key(id, fn, compute_params, resolved)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        # Effective timeout: metric hint clamped by policy hard limit.
        hint = schema.hints.max_runtime_s
        effective_timeout = (
            min(hint, self._policy.max_runtime_s)
            if hint is not None
            else self._policy.max_runtime_s
        )

        self._task_counter += 1
        task_id = self._task_counter

        worker = self._get_worker()

        # Wait for the worker to finish booting BEFORE arming the deadline, so a
        # slow cold-start (common under load) can't falsely time out a fast metric.
        if not self._await_ready(worker):
            worker.terminate()
            self._worker = None
            return MetricFailure(
                metric_id=id,
                traceback=(
                    f"Worker failed to start within "
                    f"{self._policy.spawn_timeout_s:.1f}s"
                ),
                parameters=parameters,
            )

        packed_inputs, shm_list = _pack_inputs(resolved, self._policy.shm_threshold_bytes)

        try:
            worker.conn.send({
                "task_id": task_id,
                "metric_id": id,
                "packed_inputs": packed_inputs,
                "compute_params": compute_params,
            })

            deadline = time.monotonic() + effective_timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    worker.terminate()
                    self._worker = None
                    return MetricFailure(
                        metric_id=id,
                        traceback=(
                            f"Metric exceeded hard time limit "
                            f"({effective_timeout:.1f}s)"
                        ),
                        parameters=parameters,
                    )

                if not worker.conn.poll(min(remaining, 1.0)):
                    continue

                msg = worker.conn.recv()
                if msg["task_id"] == task_id:
                    worker.task_count += 1
                    if worker.task_count >= self._policy.max_tasks_per_worker:
                        self._worker = None
                    raw = msg["value"]
                    break

        except (BrokenPipeError, EOFError, OSError):
            worker.terminate()
            self._worker = None
            return MetricFailure(
                metric_id=id,
                traceback="Worker process died unexpectedly",
                parameters=parameters,
            )
        finally:
            for shm in shm_list:
                try:
                    shm.close()
                    shm.unlink()
                except Exception:
                    pass

        if isinstance(raw, MetricFailure):
            return raw

        arr = np.asarray(raw)
        checksum = hashlib.sha256(np.ascontiguousarray(arr).tobytes()).hexdigest()
        result = MetricResult(
            metric_id=id,
            shape=shape_to_str(schema.shape),
            dtype=str(arr.dtype),
            unit=schema.unit,
            compute_parameters=compute_params,
            implementation_hash=function_hash(fn),
            checksum=checksum,
            values=arr,
        )
        self._cache.put(cache_key, result)
        return result

    def shutdown(self) -> None:
        """Gracefully stop the worker: cooperative sentinel then forced terminate."""
        if self._worker is not None:
            try:
                self._worker.conn.send(None)
            except Exception:
                pass
            self._worker.process.join(timeout=self._policy.grace_period_s)
            self._worker.terminate()
            self._worker = None

"""Worker-process MetricExecutor with shared-memory inputs and recycling.

Architecture
------------
WorkerProcessExecutor owns a pool of lazily-spawned worker subprocesses, at most
policy.max_workers of them.  A worker is checked out for the whole send/recv
exchange, so its pipe is only ever touched by the thread holding it — callers
(the TaskManager runs each metric via asyncio.to_thread) therefore run in
parallel rather than queueing behind one another.

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
- Metric-to-metric dependencies are resolved in the parent process by the Metric
  Execution Context (ADR 0035): each metric in the plan is a separate worker
  task, and a dependency's output is wired into its dependent's inputs before
  that dependent ships.  The worker receives only resolved scalar / array inputs
  and pre-computed compute_params.
"""

from __future__ import annotations

import itertools
import multiprocessing as mp
import os
import pickle
import threading
import time
import traceback as tb
from dataclasses import dataclass
from multiprocessing.shared_memory import SharedMemory
from typing import Any

import numpy as np

from ffast.metrics.cache import MetricCache
from ffast.metrics.execution import InputSource, build_execution_plan, run_plan
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
    # How many metrics may run at once. None leaves cores for the parent process
    # and whatever else the machine is doing.
    max_workers: int | None = None


def _default_worker_count() -> int:
    """Concurrency budget: the cores this process may actually use, less two.

    ``os.cpu_count()`` reports the machine, not the allocation, so on a batch
    node (SLURM and friends) it would oversubscribe a job pinned to a handful
    of cores.  Affinity is the truthful number where the platform exposes it.
    """
    try:
        available = len(os.sched_getaffinity(0))
    except AttributeError:  # macOS / Windows have no affinity mask
        available = os.cpu_count() or 4
    return max(1, available - 2)


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
    construction all happen in the parent process via the Metric Execution
    Context (ADR 0035) — the same plan/driver the in-process executor uses.  The
    worker subprocess receives only resolved inputs and pre-computed
    compute_params and returns only the raw function output.

    Large numpy inputs use shared memory (Worker Buffers).
    Workers self-recycle after policy.max_tasks_per_worker tasks.
    Crashed workers are transparently replaced on the next run() call.

    Thread-safe: concurrent run() calls each check out their own worker and
    execute in parallel, up to policy.max_workers.  Beyond that they queue.

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
        self._max_workers = self._policy.max_workers or _default_worker_count()
        # Workers are checked out for the whole send/recv exchange, so a pipe is
        # only ever used by the one thread holding it. The lock guards the two
        # bookkeeping collections, never the exchange itself.
        self._idle: list[_Worker] = []
        self._live: set[_Worker] = set()
        self._pool_lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(self._max_workers)
        self._closed = False
        self._task_ids = itertools.count(1)

    def worker_pids(self) -> list[int]:
        """PIDs of the workers this executor currently owns (idle or busy)."""
        with self._pool_lock:
            return sorted(w.process.pid for w in self._live if w.process.pid is not None)

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

    def _checkout(self) -> _Worker:
        """Take exclusive ownership of a worker, blocking while all are busy.

        Reuses a warm idle worker when one is free; otherwise spawns, up to
        policy.max_workers. The caller must always return it via _checkin.
        """
        self._slots.acquire()
        try:
            if self._closed:
                raise RuntimeError("WorkerProcessExecutor has been shut down")
            while True:
                with self._pool_lock:
                    if not self._idle:
                        break
                    worker = self._idle.pop()
                if worker.alive:
                    return worker
                self._retire(worker)  # Died while sitting idle
            worker = self._spawn_worker()
            with self._pool_lock:
                self._live.add(worker)
            return worker
        except BaseException:
            self._slots.release()
            raise

    def _checkin(self, worker: _Worker, reuse: bool) -> None:
        """Hand a worker back: park it for the next caller, or retire it."""
        try:
            reusable = (
                reuse
                and not self._closed  # Never park a worker into a pool being torn down
                and worker.alive
                and worker.task_count < self._policy.max_tasks_per_worker
            )
            if reusable:
                with self._pool_lock:
                    self._idle.append(worker)
            else:
                self._retire(worker)
        finally:
            self._slots.release()

    def _retire(self, worker: _Worker) -> None:
        worker.terminate()
        with self._pool_lock:
            self._live.discard(worker)

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

    def run(self, id: str, source: InputSource, parameters: dict[str, Any]) -> MetricResult | MetricFailure:
        """Run a single metric and its dependencies.

        Input resolution, dependency ordering, Compute Parameter filtering, and
        caching all live in the Metric Execution Context (ADR 0035); this
        executor only supplies the transport — see ``_ship_to_worker``.  Each
        step is a separate worker task (dependencies resolved in the parent, same
        as before), and cached results never reach the worker.
        """
        plan = build_execution_plan(self._registry, id, parameters, source)
        results = run_plan(
            plan,
            self._registry,
            self._cache,
            lambda mid, schema, fn, kwargs, cparams: self._ship_to_worker(
                mid, schema, kwargs, cparams, parameters
            ),
        )
        return results[id]

    def _ship_to_worker(
        self,
        id: str,
        schema: Any,
        resolved: dict[str, Any],
        compute_params: dict[str, Any],
        parameters: dict[str, Any],
    ) -> Any:
        """Transport: run one metric in a worker subprocess, returning its raw
        output (or a ``MetricFailure`` on timeout / crash / worker-side error).

        The worker is checked out for the whole exchange, so concurrent callers
        (the TaskManager runs each metric via ``asyncio.to_thread``) each get
        their own worker and pipe and run in parallel, up to policy.max_workers.

        Large numpy inputs travel via shared memory (Worker Buffers).  The worker
        cold-start deadline (``spawn_timeout_s``) is consumed before the metric's
        hard time limit is armed, so boot time is never charged to the metric.
        """
        # Effective timeout: metric hint clamped by policy hard limit.
        hint = schema.hints.max_runtime_s
        effective_timeout = (
            min(hint, self._policy.max_runtime_s)
            if hint is not None
            else self._policy.max_runtime_s
        )

        task_id = next(self._task_ids)
        worker = self._checkout()
        reuse = False  # Only a clean round-trip earns the worker its place back
        shm_list: list[SharedMemory] = []

        try:
            # Wait for the worker to finish booting BEFORE arming the deadline, so a
            # slow cold-start (common under load) can't falsely time out a fast metric.
            if not self._await_ready(worker):
                return MetricFailure(
                    metric_id=id,
                    traceback=(
                        f"Worker failed to start within "
                        f"{self._policy.spawn_timeout_s:.1f}s"
                    ),
                    parameters=parameters,
                )

            packed_inputs, shm_list = _pack_inputs(resolved, self._policy.shm_threshold_bytes)

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
                    reuse = True
                    return msg["value"]

        except (BrokenPipeError, EOFError, OSError):
            return MetricFailure(
                metric_id=id,
                traceback="Worker process died unexpectedly",
                parameters=parameters,
            )
        finally:
            self._checkin(worker, reuse)
            for shm in shm_list:
                try:
                    shm.close()
                    shm.unlink()
                except Exception:
                    pass

    def shutdown(self) -> None:
        """Gracefully stop every worker: cooperative sentinel then forced terminate.

        Idempotent, and safe while calls are in flight: the executor is closed
        first, so a worker handed back afterwards is retired rather than parked
        for a reuse that can never come.
        """
        with self._pool_lock:
            self._closed = True
            workers = list(self._live)
            self._live.clear()
            self._idle.clear()

        for worker in workers:
            try:
                worker.conn.send(None)
            except Exception:
                pass
        for worker in workers:
            worker.process.join(timeout=self._policy.grace_period_s)
            worker.terminate()

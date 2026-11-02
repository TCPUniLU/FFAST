"""Worker-process MetricExecutor with shared-memory inputs and recycling.

Since ADR 0079 only outside code reaches this pool; FFAST's own metric code
runs in the server (``ffast.metrics.server_executor``).

Architecture
------------
WorkerProcessExecutor runs tasks on a small set of worker subprocesses, one
task per worker at a time. A worker boots when every worker is busy (up to
``policy.max_workers``); extra workers idle for ``policy.idle_retire_s`` exit,
and one stays. Each worker is held by a _Slot.

Large numpy inputs (>= policy.shm_threshold_bytes) are passed via
multiprocessing.shared_memory.SharedMemory (Worker Buffers) to avoid
inter-process array copies.

Worker lifecycle
- Retires after policy.max_tasks_per_worker tasks: the parent boots a
  replacement, and the old worker keeps serving until the replacement says it
  is ready, so no task waits for a worker to boot.
- Parent detects crash (BrokenPipeError / EOFError) and spawns a replacement.
- If the hard time limit (effective_timeout) is exceeded, the parent
  terminates the worker process and returns a MetricFailure.
  effective_timeout = min(schema.hints.max_runtime_s, policy.max_runtime_s)
  when the metric declares a hint; otherwise policy.max_runtime_s.
- A freshly-spawned worker signals readiness (after import)
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

import multiprocessing as mp
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
from ffast.metrics.resources import cores


# ── Policy ─────────────────────────────────────────────────────────────────────

@dataclass
class PoolPolicy:
    """Hard server limits enforced on every metric worker."""

    max_tasks_per_worker: int = 100
    max_runtime_s: float = 300.0
    grace_period_s: float = 5.0
    shm_threshold_bytes: int = 1_048_576  # arrays >= 1 MiB use SharedMemory
    # Budget for a fresh worker to boot (interpreter start + imports)
    # and reach its recv loop. Cold-start is NOT a metric's runtime,
    # so it is bounded separately and excluded from max_runtime_s.
    spawn_timeout_s: float = 30.0
    # Most workers at once: when every worker is busy another boots, up to
    # this many (ADR 0079). None: max(2, min(8, cores // 4)).
    max_workers: int | None = None
    # An extra worker idle this long exits; one always stays.
    idle_retire_s: float = 300.0


# Sentinel a freshly-booted worker sends once it is
# about to block on recv — see _await_ready.
_READY = "__worker_ready__"


# The failure a task gets when its worker process ends without answering.
WORKER_DIED = "Worker process died unexpectedly"


class WorkerCallError(RuntimeError):
    """A function run with ``WorkerProcessExecutor.call`` failed; the message
    says why in a sentence."""


def _failure_message(traceback: str) -> str:
    """One line for a failed call: the error it raised, or what became of
    the worker."""
    if traceback == WORKER_DIED:
        return "the worker process stopped (it may have run out of memory); it restarts on its own"
    lines = [ln for ln in traceback.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else traceback


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
    """Numpy arrays reading the ShmDescriptors' blocks in place (no copy: the
    parent made the blocks for this task alone); return the opened blocks."""
    inputs: dict[str, Any] = {}
    shm_list: list[SharedMemory] = []
    for key, value in packed.items():
        if isinstance(value, _ShmDescriptor):
            shm = SharedMemory(name=value.shm_name, create=False)
            inputs[key] = np.ndarray(value.shape, dtype=np.dtype(value.dtype), buffer=shm.buf)
            shm_list.append(shm)
        else:
            inputs[key] = value
    return inputs, shm_list


def _take_result(value: Any) -> Any:
    """A result the worker left in shared memory, copied out, its block freed."""
    if not isinstance(value, _ShmDescriptor):
        return value
    shm = SharedMemory(name=value.shm_name, create=False)
    try:
        return np.array(np.ndarray(value.shape, dtype=np.dtype(value.dtype), buffer=shm.buf))
    finally:
        shm.close()
        shm.unlink()


def _close_all(shm_list: list[SharedMemory]) -> None:
    for shm in shm_list:
        try:
            shm.close()
        except BufferError:     # a metric kept a view of its input; the
            pass                # mapping goes when the worker retires


# ── Worker subprocess entry point ─────────────────────────────────────────────

# Modules a worker imports while it boots, before it says it is ready, so the
# first metric that needs one does not wait for the import (scipy.stats alone
# took ~500 ms on the first tab opened).
_PRELOAD = ("scipy.stats", "ffast.metrics.builtin")


def _preload() -> None:
    """Import ``_PRELOAD``. One that fails is left for the metric that needs
    it to report."""
    import importlib

    for name in _PRELOAD:
        try:
            importlib.import_module(name)
        except Exception:
            pass


def _worker_main(
    conn: "mp.connection.Connection",
    policy: PoolPolicy,
) -> None:
    """Entry point for each worker subprocess.

    Receives the function to run, pre-resolved inputs and pre-computed
    compute_params from the parent — no registry, no dependency resolution or
    parameter filtering needed here (ADR 0061).

    Runs until:
    - parent sends the shutdown sentinel (None msg), as it does when the
      worker retires;
    - pipe is closed (EOFError).
    """
    _preload()

    # Announce readiness: the parent's per-metric deadline only starts once it
    # has consumed this, so interpreter boot + imports above don't
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
                value = msg["fn"](**inputs, **compute_params)
                # A large result goes back through shared memory, not pickled
                # through the pipe (~90% of a force metric's time, 2026-11-02).
                # Copied into a block of its own: it may be a view of an input.
                packed, made = _pack_inputs({"value": value}, policy.shm_threshold_bytes)
                inputs = value = None
                _close_all(made)        # the parent frees them
                conn.send({"task_id": task_id, "value": packed["value"]})
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
                inputs = value = None
                _close_all(shm_list)

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


class _Slot:
    """One place for a worker: the worker, the replacement booting for it, and
    whether a task holds it."""

    def __init__(self) -> None:
        self.worker: _Worker | None = None
        self.next: _Worker | None = None
        self.busy = False
        self.last_used = time.monotonic()


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
    Workers retire after policy.max_tasks_per_worker tasks, handing over to a
    replacement once it has booted.
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
        self._slots: list[_Slot] = []
        self._task_counter = 0
        # Guards the slot list and each slot's ``busy``; held only briefly, never
        # while a task runs. A task owns its busy slot alone.
        self._lock = threading.RLock()
        self._free = threading.Condition(self._lock)

    @property
    def _worker(self) -> "_Worker | None":
        """The first slot's worker."""
        return self._slots[0].worker if self._slots else None

    @property
    def _next(self) -> "_Worker | None":
        """The first slot's replacement while it boots."""
        return self._slots[0].next if self._slots else None

    def _max_workers(self) -> int:
        limit = self._policy.max_workers
        return limit if limit is not None else max(2, min(8, cores() // 4))

    def _spawn_worker(self) -> _Worker:
        parent_conn, child_conn = mp.Pipe(duplex=True)
        process = mp.Process(
            target=_worker_main,
            args=(child_conn, self._policy),
            daemon=True,
        )
        process.start()
        child_conn.close()  # Parent only uses the parent end
        return _Worker(conn=parent_conn, process=process)

    def start(self) -> None:
        """Boot the first worker now and return without waiting for it, so the
        first metric finds it ready instead of paying its start-up (~700 ms)."""
        with self._lock:
            if not self._slots:
                self._slots.append(_Slot())
            if not self._slots[0].busy:
                self._get_worker(self._slots[0])

    def _get_worker(self, slot: _Slot | None = None) -> _Worker:
        """The worker for the next task on ``slot`` (default: the first). The
        caller owns the slot."""
        if slot is None:
            with self._lock:
                if not self._slots:
                    self._slots.append(_Slot())
                slot = self._slots[0]
        if slot.next is not None:
            if self._await_ready(slot.next, timeout=0):
                self._retire(slot.worker)
                slot.worker, slot.next = slot.next, None
            elif not slot.next.alive:
                slot.next = None  # died booting; the next task asks for another
        if slot.worker is None or not slot.worker.alive:
            if slot.next is not None:
                slot.worker, slot.next = slot.next, None
            else:
                slot.worker = self._spawn_worker()
        return slot.worker

    @staticmethod
    def _retire(worker: _Worker | None) -> None:
        """Ask a worker to exit once it has finished; does not wait for it."""
        if worker is None:
            return
        try:
            worker.conn.send(None)
            worker.conn.close()
        except Exception:
            pass

    def _await_ready(self, worker: _Worker, timeout: float | None = None) -> bool:
        """Block until a freshly-spawned worker has booted and is in its recv
        loop. Returns False if it does not signal ready within ``timeout``
        (default spawn_timeout_s: slow boot under load, or a crash during
        import); ``timeout=0`` only checks.

        Consuming the readiness sentinel here — before the per-metric deadline
        starts — keeps worker cold-start out of the metric's hard time limit.
        A warm (already-ready) worker returns immediately.
        """
        if worker.ready:
            return True
        if timeout is None:
            timeout = self._policy.spawn_timeout_s
        try:
            if worker.conn.poll(timeout):
                if worker.conn.recv() == _READY:
                    worker.ready = True
                    return True
        except (EOFError, OSError):
            pass
        return False

    def _acquire_slot(self) -> _Slot:
        """A slot for one task, held until ``_release_slot``: an idle one, else
        a new one while under ``max_workers``, else wait for a release. Extra
        workers idle past ``idle_retire_s`` exit first."""
        with self._free:
            self._retire_idle()
            while True:
                for slot in self._slots:
                    if not slot.busy:
                        slot.busy = True
                        return slot
                if len(self._slots) < self._max_workers():
                    slot = _Slot()
                    slot.busy = True
                    self._slots.append(slot)
                    return slot
                self._free.wait()

    def _release_slot(self, slot: _Slot) -> None:
        with self._free:
            slot.busy = False
            slot.last_used = time.monotonic()
            self._free.notify()

    def _retire_idle(self) -> None:
        """Let extra idle workers go, stalest first, keeping at least one. The
        caller holds ``self._lock``."""
        limit = time.monotonic() - self._policy.idle_retire_s
        while len(self._slots) > 1:
            stale = [sl for sl in self._slots if not sl.busy and sl.last_used < limit]
            if not stale:
                return
            slot = min(stale, key=lambda sl: sl.last_used)
            self._slots.remove(slot)
            if slot.next is not None:
                slot.next.terminate()
            self._retire(slot.worker)

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
                mid, schema, fn, kwargs, cparams, parameters
            ),
            stopped=source.stopped,
        )
        return results[id]

    def _ship_to_worker(
        self,
        id: str,
        schema: Any,
        fn: Any,
        resolved: dict[str, Any],
        compute_params: dict[str, Any],
        parameters: dict[str, Any],
    ) -> Any:
        """Transport: run one metric in a worker subprocess, returning its raw
        output (or a ``MetricFailure`` on timeout / crash / worker-side error).

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
        return self._send(id, fn, resolved, compute_params, parameters, effective_timeout)

    def call(self, fn: Any, kwargs: dict[str, Any], label: str = "formula") -> Any:
        """``fn(**kwargs)`` in the worker, under the policy's time limit. A
        failure raises ``WorkerCallError``; a worker that dies (e.g. out of
        memory) is replaced on the next call, and this process carries on."""
        out = self._send(label, fn, kwargs, {}, {}, self._policy.max_runtime_s)
        if isinstance(out, MetricFailure):
            raise WorkerCallError(_failure_message(out.traceback))
        return out

    def _send(self, id, fn, resolved, compute_params, parameters, effective_timeout):
        """One task on a worker; its raw output, or a ``MetricFailure``."""
        slot = self._acquire_slot()
        try:
            return self._send_on(slot, id, fn, resolved, compute_params, parameters,
                                 effective_timeout)
        finally:
            self._release_slot(slot)

    def _send_on(self, slot, id, fn, resolved, compute_params, parameters, effective_timeout):
        with self._lock:
            self._task_counter += 1
            task_id = self._task_counter

        worker = self._get_worker(slot)

        # Wait for the worker to finish booting BEFORE arming the deadline, so a
        # slow cold-start (common under load) can't falsely time out a fast metric.
        if not self._await_ready(worker):
            worker.terminate()
            slot.worker = None
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
            try:
                worker.conn.send({
                    "task_id": task_id,
                    "metric_id": id,
                    "fn": fn,
                    "packed_inputs": packed_inputs,
                    "compute_params": compute_params,
                })
            except (pickle.PicklingError, AttributeError, TypeError) as exc:
                # Pickling happens before anything is written, so the pipe
                # is still clean and the worker keeps serving (ADR 0061).
                return MetricFailure(
                    metric_id=id,
                    traceback=f"The metric's task cannot be sent to the worker: {exc!r}",
                    parameters=parameters,
                )

            deadline = time.monotonic() + effective_timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    worker.terminate()
                    slot.worker = None
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
                    if (worker.task_count >= self._policy.max_tasks_per_worker
                            and slot.next is None):
                        slot.next = self._spawn_worker()
                    return _take_result(msg["value"])

        except (BrokenPipeError, EOFError, OSError):
            worker.terminate()
            slot.worker = None
            return MetricFailure(
                metric_id=id,
                traceback=WORKER_DIED,
                parameters=parameters,
            )
        finally:
            for shm in shm_list:
                try:
                    shm.close()
                    shm.unlink()
                except Exception:
                    pass

    def shutdown(self) -> None:
        """Gracefully stop every worker: cooperative sentinel then forced terminate."""
        with self._lock:
            slots, self._slots = self._slots, []
        for slot in slots:
            if slot.next is not None:
                slot.next.terminate()
                slot.next = None
            if slot.worker is not None:
                try:
                    slot.worker.conn.send(None)
                except Exception:
                    pass
        for slot in slots:
            if slot.worker is not None:
                slot.worker.process.join(timeout=self._policy.grace_period_s)
                slot.worker.terminate()
                slot.worker = None

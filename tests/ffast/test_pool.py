"""Tests for metrics.pool (M4: worker-process MetricExecutor).

Metric functions are defined at module level (not inside fixtures) because
Python's spawn start method pickles functions by (module, qualname) reference.
Nested / closure functions are NOT picklable with spawn.
"""

from __future__ import annotations

import os
import time

import numpy as np
import pytest

from ffast.metrics.execution import FlatInputSource
from ffast.metrics.models import MetricFailure, MetricResult
from ffast.metrics.pool import PoolPolicy, WorkerProcessExecutor
from ffast.metrics.registry import MetricRegistry


# ── Module-level metric functions (spawn-picklable) ───────────────────────────

def _pool_identity(x):
    return x


def _pool_double(x):
    return x * 2


def _pool_sum_arr(arr):
    return float(arr.sum())


def _pool_slow(x):
    time.sleep(60)  # Far exceeds any test policy time limit
    return x


def _pool_raise_error(x):
    raise ValueError("intentional test failure")


def _pool_crash(x):
    os._exit(1)  # Hard process crash — no result sent


# Dependency resolution: inner computes x*2; outer adds 1 to inner's result.
def _pool_dep_inner(x):
    return x * 2


def _pool_dep_outer(y):
    return y + 1.0


# Source for a compiled transform metric (returns a per-atom-style vector).
def _pool_vec(x):
    return np.asarray(x, dtype=float)


# ── Registry fixture ──────────────────────────────────────────────────────────

@pytest.fixture
def registry():
    reg = MetricRegistry()
    reg.metric(id="test.identity",    inputs={"x":   "ref.x"},         shape="scalar", unit="energy")(_pool_identity)
    reg.metric(id="test.double",      inputs={"x":   "ref.x"},         shape="scalar", unit="energy")(_pool_double)
    reg.metric(id="test.sum_arr",     inputs={"arr": "ref.arr"},        shape="scalar", unit="energy")(_pool_sum_arr)
    reg.metric(id="test.slow",        inputs={"x":   "ref.x"},         shape="scalar", unit="energy")(_pool_slow)
    reg.metric(id="test.raise_error", inputs={"x":   "ref.x"},         shape="scalar", unit="energy")(_pool_raise_error)
    reg.metric(id="test.crash",       inputs={"x":   "ref.x"},         shape="scalar", unit="energy")(_pool_crash)
    reg.metric(id="test.dep_inner",   inputs={"x":   "ref.x"},         shape="scalar", unit="energy")(_pool_dep_inner)
    reg.metric(id="test.dep_outer",   inputs={"y":   "test.dep_inner"}, shape="scalar", unit="energy")(_pool_dep_outer)
    return reg


@pytest.fixture
def executor(registry):
    exc = WorkerProcessExecutor(registry)
    yield exc
    exc.shutdown()


# ── Basic execution ───────────────────────────────────────────────────────────

def test_run_returns_metric_result(executor):
    result = executor.run("test.double", FlatInputSource({"x": 3.0}), {})
    assert isinstance(result, MetricResult)
    assert result.metric_id == "test.double"


def test_run_returns_correct_output(executor):
    result = executor.run("test.double", FlatInputSource({"x": 3.0}), {})
    assert isinstance(result, MetricResult)
    assert np.isclose(result.values, 6.0)


def test_run_returns_metric_failure_on_exception(executor):
    result = executor.run("test.raise_error", FlatInputSource({"x": 1.0}), {})
    assert isinstance(result, MetricFailure)
    assert result.metric_id == "test.raise_error"
    assert "ValueError" in result.traceback
    assert "intentional test failure" in result.traceback


def test_failure_does_not_affect_subsequent_calls(executor):
    bad = executor.run("test.raise_error", FlatInputSource({"x": 1.0}), {})
    assert isinstance(bad, MetricFailure)
    good = executor.run("test.identity", FlatInputSource({"x": 42.0}), {})
    assert isinstance(good, MetricResult)
    assert np.isclose(good.values, 42.0)


# ── Dependency resolution ─────────────────────────────────────────────────────

def test_worker_resolves_metric_dependency(executor):
    # dep_inner(x=5) = 10; dep_outer(y=dep_inner) = 11
    result = executor.run("test.dep_outer", FlatInputSource({"x": 5.0}), {})
    assert isinstance(result, MetricResult)
    assert np.isclose(result.values, 11.0)


def test_worker_missing_raw_input_returns_failure(executor):
    result = executor.run("test.identity", FlatInputSource({}), {})  # "x" not provided
    assert isinstance(result, MetricFailure)
    assert "Missing raw input" in result.traceback


def test_cached_dependency_is_not_shipped_to_worker(registry):
    """run_plan checks the shared cache BEFORE calling the worker transport, so a
    dependency whose result is already cached is served from cache and never
    shipped to a worker subprocess (only the still-uncomputed dependent ships).

    Verified with a recording wrapper over the executor's ``_ship_to_worker``
    transport (the ``run_fn`` the shared driver invokes).
    """
    from ffast.metrics.cache import MetricCache

    cache = MetricCache()
    executor = WorkerProcessExecutor(registry, cache=cache)
    try:
        # Prime the cache: compute the dependency alone. dep_inner(x=5) = 10.
        primed = executor.run("test.dep_inner", FlatInputSource({"x": 5.0}), {})
        assert isinstance(primed, MetricResult)
        assert np.isclose(primed.values, 10.0)

        # Record every metric_id that reaches the worker transport.
        shipped: list[str] = []
        original = executor._ship_to_worker

        def recording(id, schema, resolved, compute_params, parameters):
            shipped.append(id)
            return original(id, schema, resolved, compute_params, parameters)

        executor._ship_to_worker = recording

        # dep_outer depends on dep_inner (already cached with the same x=5 input).
        result = executor.run("test.dep_outer", FlatInputSource({"x": 5.0}), {})
        assert isinstance(result, MetricResult)
        assert np.isclose(result.values, 11.0)  # dep_inner(10) + 1

        # The cached dependency was served from cache — its compute was skipped;
        # only the uncomputed dependent was shipped to a worker.
        assert "test.dep_inner" not in shipped
        assert shipped == ["test.dep_outer"]
    finally:
        executor.shutdown()


# ── MetricResult fields ───────────────────────────────────────────────────────

def test_result_has_correct_shape_and_unit(executor, registry):
    result = executor.run("test.double", FlatInputSource({"x": 2.0}), {})
    schema, _ = registry.get("test.double")
    assert result.shape == schema.shape
    assert result.unit == schema.unit


def test_result_checksum_is_deterministic(executor):
    r1 = executor.run("test.identity", FlatInputSource({"x": 7.0}), {})
    r2 = executor.run("test.identity", FlatInputSource({"x": 7.0}), {})
    assert isinstance(r1, MetricResult)
    assert isinstance(r2, MetricResult)
    assert r1.checksum == r2.checksum


# ── Shared-memory (Worker Buffers) ────────────────────────────────────────────

def test_large_array_via_shared_memory(registry):
    policy = PoolPolicy(shm_threshold_bytes=1)  # Force all arrays through shm
    executor = WorkerProcessExecutor(registry, policy)
    try:
        arr = np.arange(100, dtype=np.float64)
        result = executor.run("test.sum_arr", FlatInputSource({"arr": arr}), {})
        assert isinstance(result, MetricResult)
        assert np.isclose(result.values, arr.sum())
    finally:
        executor.shutdown()


def test_array_below_threshold_not_shared_memory(registry):
    policy = PoolPolicy(shm_threshold_bytes=10 * 1024 * 1024)  # Large threshold
    executor = WorkerProcessExecutor(registry, policy)
    try:
        arr = np.array([1.0, 2.0, 3.0])
        result = executor.run("test.sum_arr", FlatInputSource({"arr": arr}), {})
        assert isinstance(result, MetricResult)
        assert np.isclose(result.values, 6.0)
    finally:
        executor.shutdown()


# ── Worker recycling ──────────────────────────────────────────────────────────

def test_worker_recycles_after_max_tasks(registry):
    policy = PoolPolicy(max_tasks_per_worker=2, max_workers=1)
    executor = WorkerProcessExecutor(registry, policy)
    try:
        executor.run("test.identity", FlatInputSource({"x": 1.0}), {})
        pid_before = executor.worker_pids()

        executor.run("test.identity", FlatInputSource({"x": 2.0}), {})
        # After 2 tasks the worker is retired rather than parked

        executor.run("test.identity", FlatInputSource({"x": 3.0}), {})  # Spawns fresh worker
        pid_after = executor.worker_pids()

        assert len(pid_before) == len(pid_after) == 1
        assert pid_before != pid_after
    finally:
        executor.shutdown()


def test_worker_spawns_replacement_after_crash(registry):
    executor = WorkerProcessExecutor(registry)
    try:
        result = executor.run("test.crash", FlatInputSource({"x": 1.0}), {})
        assert isinstance(result, MetricFailure)
        assert "died unexpectedly" in result.traceback

        result2 = executor.run("test.identity", FlatInputSource({"x": 7.0}), {})
        assert isinstance(result2, MetricResult)
        assert np.isclose(result2.values, 7.0)
    finally:
        executor.shutdown()


# ── Hard time limit ───────────────────────────────────────────────────────────

def test_hard_time_limit_returns_metric_failure(registry):
    policy = PoolPolicy(max_runtime_s=0.3, grace_period_s=0.1)
    executor = WorkerProcessExecutor(registry, policy)
    try:
        result = executor.run("test.slow", FlatInputSource({"x": 1.0}), {})
        assert isinstance(result, MetricFailure)
        assert result.metric_id == "test.slow"
        assert "time limit" in result.traceback.lower()
    finally:
        executor.shutdown()


def test_executor_recovers_after_time_limit(registry):
    policy = PoolPolicy(max_runtime_s=0.3, grace_period_s=0.1)
    executor = WorkerProcessExecutor(registry, policy)
    try:
        result1 = executor.run("test.slow", FlatInputSource({"x": 0.0}), {})
        assert isinstance(result1, MetricFailure)

        result2 = executor.run("test.identity", FlatInputSource({"x": 99.0}), {})
        assert isinstance(result2, MetricResult)
        assert np.isclose(result2.values, 99.0)
    finally:
        executor.shutdown()


# ── Scheduling hints ──────────────────────────────────────────────────────────

def test_scheduling_hint_tightens_timeout():
    # hint declares 0.2s max; policy allows 300s; hint should win
    reg = MetricRegistry()
    reg.metric(
        id="test.slow_hinted",
        inputs={"x": "ref.x"},
        shape="scalar",
        unit="energy",
        hints={"max_runtime_s": 0.2},
    )(_pool_slow)

    executor = WorkerProcessExecutor(reg, PoolPolicy(max_runtime_s=300.0, grace_period_s=0.1))
    try:
        result = executor.run("test.slow_hinted", FlatInputSource({"x": 1.0}), {})
        assert isinstance(result, MetricFailure)
        assert "time limit" in result.traceback.lower()
        # Failure message must mention the hint-derived timeout, not 300s
        assert "0.2s" in result.traceback
    finally:
        executor.shutdown()


def test_scheduling_hint_cannot_exceed_policy():
    # hint wants 600s; policy caps at 0.3s; policy should win
    reg = MetricRegistry()
    reg.metric(
        id="test.slow_long_hint",
        inputs={"x": "ref.x"},
        shape="scalar",
        unit="energy",
        hints={"max_runtime_s": 600.0},
    )(_pool_slow)

    executor = WorkerProcessExecutor(reg, PoolPolicy(max_runtime_s=0.3, grace_period_s=0.1))
    try:
        result = executor.run("test.slow_long_hint", FlatInputSource({"x": 1.0}), {})
        assert isinstance(result, MetricFailure)
        assert "time limit" in result.traceback.lower()
        # Effective timeout clamped to policy 0.3s
        assert "0.3s" in result.traceback
    finally:
        executor.shutdown()


def test_metric_schema_stores_hints(registry):
    schema, _ = registry.get("test.identity")
    assert schema.hints.max_runtime_s is None
    assert schema.hints.cpu_intensive is False


def test_metric_schema_stores_declared_hints():
    reg = MetricRegistry()
    reg.metric(
        id="test.hinted",
        inputs={"x": "ref.x"},
        shape="scalar",
        unit="energy",
        hints={"max_runtime_s": 10.0, "cpu_intensive": True, "memory_mb": 512},
    )(_pool_identity)
    schema, _ = reg.get("test.hinted")
    assert schema.hints.max_runtime_s == 10.0
    assert schema.hints.cpu_intensive is True
    assert schema.hints.memory_mb == 512


# ── Shutdown ──────────────────────────────────────────────────────────────────

def test_shutdown_is_idempotent(registry):
    executor = WorkerProcessExecutor(registry)
    executor.run("test.identity", FlatInputSource({"x": 1.0}), {})
    executor.shutdown()
    executor.shutdown()  # Must not raise


def test_shutdown_before_any_run(registry):
    executor = WorkerProcessExecutor(registry)
    executor.shutdown()  # No worker spawned — must not raise


# ── Regression: compiled transform metrics must survive the worker pool ───────
# A compiled transform metric (ADR 0021 Phase 5) registers a _TransformFn wrapper
# over a Transform. WorkerProcessExecutor pickles the WHOLE registry to its
# subprocess; when the wrapper or a Transform body was a lambda / local closure,
# pickling raised "Can't pickle local object '_make_fn.<locals>._impl'", and every
# metric atom-coloring silently fell back to element colors (server-side
# build_scene caught it). These lock both layers (wrapper + transform body) as
# picklable.

def _compiled_reg():
    import ffast.metrics.transforms as T
    reg = MetricRegistry()
    reg.metric(id="test.vec", inputs={"x": "ref.x"}, shape="scalar", unit="force")(_pool_vec)
    cid = T.compile_transform("test.vec", "mean_abs", registry=reg)
    return reg, cid


def test_registry_with_compiled_transform_is_picklable():
    import pickle
    reg, _ = _compiled_reg()
    # Pre-fix: AttributeError "Can't pickle local object '_make_fn.<locals>._impl'".
    pickle.dumps(reg)


def test_compiled_transform_metric_runs_in_worker():
    reg, cid = _compiled_reg()
    executor = WorkerProcessExecutor(reg)  # pre-fix: raised in __init__ (pickle.dumps)
    try:
        result = executor.run(cid, FlatInputSource({"x": np.array([-1.0, 2.0, -3.0])}), {})
    finally:
        executor.shutdown()
    assert isinstance(result, MetricResult), getattr(result, "traceback", result)
    assert np.isclose(result.values, 2.0)  # mean(|-1|,|2|,|-3|) = 2.0


# ── Thread safety ─────────────────────────────────────────────────────────────

def _pool_sleep_double(x):
    time.sleep(0.5)  # Wide enough for the calls to genuinely overlap
    return x * 2


def _threaded_runs(executor, metric_id, inputs, timeout=120):
    import concurrent.futures as cf

    with cf.ThreadPoolExecutor(max_workers=len(inputs)) as tp:
        futures = {x: tp.submit(executor.run, metric_id, FlatInputSource({"x": x}), {})
                   for x in inputs}
        return {x: f.result(timeout=timeout) for x, f in futures.items()}


def _sleep_registry():
    reg = MetricRegistry()
    reg.metric(id="test.sleep_double", inputs={"x": "ref.x"}, shape="scalar", unit="energy")(_pool_sleep_double)
    return reg


def test_concurrent_runs_on_a_warm_pool_each_get_their_own_answer():
    """Threads sharing one executor must not cross wires.

    The TaskManager runs every metric via ``asyncio.to_thread``, so real OS
    threads drive one executor at once.  Before workers were checked out per
    call they shared a single pipe: a thread could receive another thread's
    result while waiting for the _READY sentinel (reported as "worker failed to
    start", killing the worker for everyone), and the reply loop silently
    dropped any message whose task_id was not its own.

    The pool is warmed first on purpose: from cold, each thread spawns its own
    worker and the pipes never cross, which hid the bug.
    """
    executor = WorkerProcessExecutor(_sleep_registry(), PoolPolicy(max_runtime_s=15.0))
    try:
        warm = executor.run("test.sleep_double", FlatInputSource({"x": 1.0}), {})
        assert isinstance(warm, MetricResult), getattr(warm, "traceback", warm)

        results = _threaded_runs(executor, "test.sleep_double", [float(i) for i in range(4)])
    finally:
        executor.shutdown()

    for x, result in results.items():
        assert isinstance(result, MetricResult), getattr(result, "traceback", result)
        assert np.isclose(result.values, x * 2), f"input {x} got {result.values}"


def test_concurrent_runs_actually_run_in_parallel():
    """Four 0.5s metrics on four workers must not take four times 0.5s."""
    executor = WorkerProcessExecutor(_sleep_registry(), PoolPolicy(max_workers=4))
    try:
        executor.run("test.sleep_double", FlatInputSource({"x": 1.0}), {})  # Pay worker boot once
        start = time.monotonic()
        results = _threaded_runs(executor, "test.sleep_double", [float(i) for i in range(4)])
        elapsed = time.monotonic() - start
    finally:
        executor.shutdown()

    assert all(isinstance(r, MetricResult) for r in results.values())
    # Serialised would be >= 2.0s; parallel is ~0.5s plus three worker boots.
    assert elapsed < 1.8, f"4 x 0.5s metrics took {elapsed:.2f}s — not running in parallel"


def test_pool_never_exceeds_max_workers():
    executor = WorkerProcessExecutor(_sleep_registry(), PoolPolicy(max_workers=2))
    try:
        results = _threaded_runs(executor, "test.sleep_double", [float(i) for i in range(6)])
        assert len(executor.worker_pids()) <= 2
    finally:
        executor.shutdown()

    for x, result in results.items():
        assert isinstance(result, MetricResult), getattr(result, "traceback", result)
        assert np.isclose(result.values, x * 2)


def test_shutdown_terminates_every_worker():
    executor = WorkerProcessExecutor(_sleep_registry(), PoolPolicy(max_workers=3))
    try:
        _threaded_runs(executor, "test.sleep_double", [1.0, 2.0, 3.0])
        pids = executor.worker_pids()
        assert len(pids) >= 2, "expected the pool to have spawned several workers"
    finally:
        executor.shutdown()  # Never leak workers into the rest of the suite
    assert executor.worker_pids() == []
    time.sleep(0.3)  # Give the OS a moment to reap the terminated children
    for pid in pids:
        with pytest.raises(OSError):
            os.kill(pid, 0)  # Raises once the process is gone


def test_shutdown_while_busy_leaves_no_orphan_worker():
    """A worker checked out during shutdown must not be parked for reuse.

    shutdown() empties the pool's bookkeeping; a call still in flight then
    handed its worker back, which sat in _idle unreferenced by _live and
    survived the next shutdown as an orphan process.
    """
    import concurrent.futures as cf

    executor = WorkerProcessExecutor(_sleep_registry(), PoolPolicy(max_workers=2))
    with cf.ThreadPoolExecutor(max_workers=1) as tp:
        busy = tp.submit(executor.run, "test.sleep_double", FlatInputSource({"x": 2.0}), {})
        time.sleep(0.2)          # Let the worker start the metric
        pids = executor.worker_pids()
        assert pids, "expected a worker to be checked out"

        executor.shutdown()
        busy.result(timeout=30)  # The in-flight call still returns

    executor.shutdown()          # Idempotent, and must not leave the worker behind
    assert executor.worker_pids() == []
    time.sleep(0.3)
    for pid in pids:
        with pytest.raises(OSError):
            os.kill(pid, 0)


def test_run_after_shutdown_is_refused():
    """A shut-down executor must not quietly spawn a fresh pool."""
    executor = WorkerProcessExecutor(_sleep_registry())
    executor.shutdown()
    with pytest.raises(RuntimeError):
        executor.run("test.sleep_double", FlatInputSource({"x": 1.0}), {})

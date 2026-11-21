"""Related force requests share reads/arithmetic, preserving metric results."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
import gc
import time
import subprocess
import sys

import numpy as np
import pytest

from ffast.config.tabs import compile_project_metrics
from ffast.io.batched import BatchedArray
from ffast.io import budget as resources
from ffast.metrics.execution import InputSource
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.models import MetricFailure
from ffast.metrics.registry import default_registry
from ffast.metrics.server_executor import ServerExecutor

METRICS = [
    "ffast.force_difference[dataset;component;mae]",
    "ffast.force_difference[dataset;component;rmse]",
    "ffast.force_difference[structure;component;mae]",
    "ffast.force_difference[structure;component;rmse]",
    "ffast.force_parity_outliers",
    "ffast.force_parity_density",
]


class Source(InputSource):
    def __init__(self, true, pred, offsets, *, token="data", stopped=lambda: False):
        self.values = {"reference.forces": true, "prediction.forces": pred,
                       "offsets": offsets, "reference.elements": np.ones(len(true), dtype=int)}
        self.token = token
        self._stop = stopped

    def get(self, metric_id, local_key, ref):
        return ref in self.values, self.values.get(ref)

    def identity(self, ref):
        return self.token + "|" + ref

    def stopped(self):
        return self._stop()

    def uses(self):
        return frozenset({(self.token, "model")})


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    from ffast.metrics import resources as execution_resources

    owner = resources.Budget(ram_bytes=8 * 2**20, parsed_bytes=0,
                             result_bytes=8 * 2**20)
    monkeypatch.setattr(resources, "_budget", owner)
    # CPU availability is varied by these tests; do not reuse a pool built
    # with another test's CPU count or leave its pool behind for other suites.
    monkeypatch.setattr(execution_resources, "_CHUNK_POOL", None)
    monkeypatch.setenv("FFAST_RESULT_CACHE_DIR", str(tmp_path / "results"))
    compile_project_metrics(None)
    yield owner
    if execution_resources._CHUNK_POOL is not None:
        execution_resources._CHUNK_POOL.shutdown(wait=True, cancel_futures=True)
    gc.collect()


def inputs(n=250):
    counts = np.arange(n) % 11  # variable frames, including empty frames
    offsets = np.concatenate(([0], np.cumsum(counts)))
    true = np.random.default_rng(42).normal(size=(offsets[-1], 3))
    pred = true + (1 + np.arange(true.size).reshape(true.shape) / (true.size + 1))
    return true, pred, offsets


@pytest.mark.parametrize("frames", [250, 10000])
@pytest.mark.parametrize("lanes", [1, 4, 8])
def test_concurrent_force_requests_make_two_input_passes(workspace, frames, lanes, monkeypatch):
    from ffast.metrics import force_scan, resources as execution_resources

    monkeypatch.setattr(execution_resources, "cores", lambda: lanes)
    selector = force_scan._parallelism
    monkeypatch.setattr(force_scan, "_parallelism", lambda: selector(maximum=lanes))
    if lanes > 1:
        monkeypatch.setattr(type(workspace), "block_bytes", property(lambda self: 32 * 1024))
    true, pred, offsets = inputs(frames)
    reads = [0, 0]
    lock = Lock()

    def counted(which, values):
        def read(a, b):
            with lock:
                reads[which] += b - a
            return values[a:b]
        return BatchedArray(values.shape, values.dtype, read)

    source = Source(counted(0, true), counted(1, pred), offsets)
    dense = Source(true, pred, offsets)
    baseline = InProcessExecutor(default_registry)
    expected = {}
    for name in METRICS:
        result = baseline.run(name, dense, {})
        assert not isinstance(result, MetricFailure), result
        expected[name] = np.array(result.values, copy=True)
    del result
    baseline._cache.clear()
    gc.collect()
    executor = ServerExecutor(default_registry)
    start = Barrier(len(METRICS))

    def run(name):
        start.wait(timeout=10)
        return executor.run(name, source, {})

    try:
        with ThreadPoolExecutor(max_workers=len(METRICS)) as pool:
            got = list(pool.map(run, METRICS))
        for name, result in zip(METRICS, got):
            assert not isinstance(result, MetricFailure), (name, result)
            np.testing.assert_allclose(np.asarray(result.values), expected[name],
                                       rtol=1e-12, atol=1e-12, equal_nan=True)
        # Pass 1: all error summaries + worst components + parity bounds.
        # Pass 2: parity histogram only; no scattered outlier-value rereads.
        assert reads == [2 * len(true), 2 * len(true)]
        assert workspace.used + workspace.cache_bytes <= workspace.limit
    finally:
        executor.shutdown()


def test_cancelled_producer_keeps_work_for_another_consumer(workspace, monkeypatch):
    from ffast.metrics import force_scan

    true, pred, offsets = inputs()
    true = BatchedArray(true.shape, true.dtype, lambda a, b, v=true: v[a:b])
    pred = BatchedArray(pred.shape, pred.dtype, lambda a, b, v=pred: v[a:b])
    stopped, started, resume = Event(), Event(), Event()
    first = Source(true, pred, offsets, stopped=stopped.is_set)
    second = Source(true, pred, offsets)
    original = force_scan.scan
    scans = []

    def blocked(*args):
        scans.append(1)
        started.set()
        assert resume.wait(5)
        return original(*args)

    monkeypatch.setattr(force_scan, "scan", blocked)
    executor = ServerExecutor(default_registry)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(executor.run, METRICS[0], first, {})
            assert started.wait(5)
            b = pool.submit(executor.run, METRICS[1], second, {})
            deadline = time.monotonic() + 5
            while True:
                with executor._force_scans.lock:
                    joined = any(len(g.members) == 2 for g in executor._force_scans.groups.values())
                if joined:
                    break
                assert time.monotonic() < deadline
                time.sleep(.005)
            stopped.set()
            resume.set()
            cancelled, valid = a.result(10), b.result(10)
        assert isinstance(cancelled, MetricFailure) and cancelled.kind == "temporary"
        assert not isinstance(valid, MetricFailure), valid
        retry = executor.run(METRICS[0], second, {})
        assert not isinstance(retry, MetricFailure), retry
        assert scans == [1]
    finally:
        resume.set()
        executor.shutdown()


def test_all_cancelled_scan_is_not_published_and_can_retry(workspace, monkeypatch):
    from ffast.metrics import force_scan

    true, pred, offsets = inputs(10000)
    cancelled = Event()
    scans = []
    original = force_scan.scan

    def counted(*args):
        scans.append(1)
        return original(*args)

    monkeypatch.setattr(force_scan, "scan", counted)

    def read(a, b):
        cancelled.set()
        return true[a:b]

    r = BatchedArray(true.shape, true.dtype, read)
    p = BatchedArray(pred.shape, pred.dtype, lambda a, b: pred[a:b])
    executor = ServerExecutor(default_registry)
    try:
        failure = executor.run(METRICS[0], Source(r, p, offsets, stopped=cancelled.is_set), {})
        assert isinstance(failure, MetricFailure) and failure.kind == "temporary"
        r = BatchedArray(true.shape, true.dtype, lambda a, b: true[a:b])
        valid = executor.run(METRICS[0], Source(r, p, offsets), {})
        assert not isinstance(valid, MetricFailure), valid
        np.testing.assert_allclose(valid.values, np.abs(pred - true).mean(), rtol=1e-12)
        assert scans == [1, 1]
    finally:
        executor.shutdown()


@pytest.mark.parametrize("bad", [np.nan, np.inf, 1e200])
def test_nonfinite_rules_match_regular_metrics(workspace, bad):
    true, pred, offsets = inputs()
    pred[2, 1] = bad
    wrapped = [BatchedArray(v.shape, v.dtype, lambda a, b, v=v: v[a:b]) for v in (true, pred)]
    executor = ServerExecutor(default_registry)
    baseline = InProcessExecutor(default_registry)
    try:
        for name in METRICS:
            expected = baseline.run(name, Source(true, pred, offsets), {})
            got = executor.run(name, Source(*wrapped, offsets), {})
            assert isinstance(got, MetricFailure) == isinstance(expected, MetricFailure), (name, got, expected)
            if not isinstance(got, MetricFailure):
                np.testing.assert_allclose(np.asarray(got.values), expected.values,
                                           rtol=1e-12, atol=1e-12, equal_nan=True)
    finally:
        executor.shutdown()


def test_invalidation_and_changed_prediction_do_not_reuse_summaries(workspace):
    true, pred, offsets = inputs()
    wrapped = [BatchedArray(v.shape, v.dtype, lambda a, b, v=v: v[a:b]) for v in (true, pred)]
    executor = ServerExecutor(default_registry)
    try:
        initial = executor.run(METRICS[0], Source(*wrapped, offsets), {})
        assert not isinstance(initial, MetricFailure), initial
        executor.forget_objects({"data"})
        pred += 2
        updated = executor.run(METRICS[0], Source(*wrapped, offsets), {})
        np.testing.assert_allclose(updated.values, np.abs(pred - true).mean(), rtol=1e-12)
        assert updated.values > initial.values
        third = Source(wrapped[0], wrapped[0], offsets, token="other-prediction")
        np.testing.assert_allclose(executor.run(METRICS[0], third, {}).values, 0)
    finally:
        executor.shutdown()


def test_custom_formula_uses_its_own_definition(workspace, monkeypatch):
    from ffast.metrics import force_scan
    from ffast.metrics.expr import _ExprFn

    true, pred, offsets = inputs()
    original = default_registry.get
    schema, _ = original(METRICS[0])
    custom = _ExprFn("mean(mean_parts(D), by=all)", {"D": "ffast.force_difference"}, {"D": 1})
    monkeypatch.setattr(default_registry, "get", lambda name: (schema, custom) if name == METRICS[0] else original(name))
    monkeypatch.setattr(force_scan, "scan", lambda *args: pytest.fail("A custom formula must not be replaced"))
    pred = -pred
    wrapped = [BatchedArray(v.shape, v.dtype, lambda a, b, v=v: v[a:b]) for v in (true, pred)]
    executor = ServerExecutor(default_registry)
    try:
        got = executor.run(METRICS[0], Source(*wrapped, offsets), {})
        assert not isinstance(got, MetricFailure), got
        np.testing.assert_allclose(got.values, np.mean(pred - true), rtol=1e-12)
    finally:
        executor.shutdown()


def test_large_per_frame_summaries_spill_without_storing_delta(workspace, tmp_path):
    true, pred, offsets = inputs(100000)
    reads = [0, 0]

    def wrapped(which, value):
        def read(a, b):
            reads[which] += b - a
            return value[a:b]
        return BatchedArray(value.shape, value.dtype, read)

    executor = ServerExecutor(default_registry)
    try:
        result = executor.run(METRICS[0], Source(wrapped(0, true), wrapped(1, pred), offsets), {})
        assert not isinstance(result, MetricFailure), result
        np.testing.assert_allclose(result.values, np.abs(pred - true).mean(), rtol=1e-12)
        assert reads == [len(true), len(true)]
        # Only O(frames) arrays: structure MAE and RMSE, and the MAE of each
        # structure's atoms of each element (one element here). The much
        # larger O(atoms x xyz) delta is absent.
        files = list((tmp_path / "results").glob("*.npy"))
        assert len(files) == 3
        assert sum(p.stat().st_size for p in files) < 100000 * 24 + 3 * 4096
        assert workspace.used + workspace.cache_bytes <= workspace.limit
    finally:
        executor.shutdown()


def test_timelines_and_distributions_match_the_unfused_engine(workspace, monkeypatch):
    true, pred, offsets = inputs(100000)
    wrapped = [BatchedArray(v.shape, v.dtype, lambda a, b, v=v: v[a:b]) for v in (true, pred)]
    source = Source(*wrapped, offsets)
    metrics = ["ffast.force_mae_per_structure_smoothed", "ffast.force_rmse_smoothed",
               "ffast.force_mae_per_structure_density", "ffast.force_rmse_density"]
    baseline = ServerExecutor(default_registry)
    monkeypatch.setattr(baseline._force_scans, "acquire", lambda *args: None)
    expected = {}
    try:
        for name in metrics:
            result = baseline.run(name, source, {"window": 11})
            assert not isinstance(result, MetricFailure), result
            expected[name] = np.array(result.values, copy=True)
        del result
        baseline._cache.clear()
    finally:
        baseline.shutdown()
    gc.collect()
    executor = ServerExecutor(default_registry)
    try:
        for name in metrics:
            result = executor.run(name, source, {"window": 11})
            assert not isinstance(result, MetricFailure), result
            np.testing.assert_allclose(np.asarray(result.values), expected[name],
                                       rtol=1e-11, atol=1e-12, equal_nan=True)
    finally:
        executor.shutdown()


def test_empty_frames_remain_no_value(workspace):
    true, pred = np.empty((0, 3)), np.empty((0, 3))
    wrapped = [BatchedArray(v.shape, v.dtype, lambda a, b, v=v: v[a:b]) for v in (true, pred)]
    source = Source(*wrapped, np.array([0, 0, 0]))
    executor = ServerExecutor(default_registry)
    try:
        for name in METRICS[:4]:
            result = executor.run(name, source, {})
            assert not isinstance(result, MetricFailure), result
            assert np.isnan(np.asarray(result.values)).all()
    finally:
        executor.shutdown()


def test_unavailable_shared_storage_falls_back_for_all_waiters(workspace, monkeypatch):
    from ffast.metrics import force_scan
    from ffast.io.extxyz_arrays import NotEnoughMemory

    true, pred, offsets = inputs()
    wrapped = [BatchedArray(v.shape, v.dtype, lambda a, b, v=v: v[a:b]) for v in (true, pred)]
    source = Source(*wrapped, offsets)
    started, resume = Event(), Event()

    def refuse(*args):
        started.set()
        assert resume.wait(5)
        raise NotEnoughMemory("No storage for additional shared summaries")

    monkeypatch.setattr(force_scan, "scan", refuse)
    executor = ServerExecutor(default_registry)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(executor.run, METRICS[0], source, {})
            assert started.wait(5)
            second = pool.submit(executor.run, METRICS[1], source, {})
            deadline = time.monotonic() + 5
            while True:
                with executor._force_scans.lock:
                    joined = any(len(g.members) == 2 for g in executor._force_scans.groups.values())
                if joined:
                    break
                assert time.monotonic() < deadline
                time.sleep(.005)
            resume.set()
            mae, rmse = first.result(10), second.result(10)
        assert not isinstance(mae, MetricFailure), mae
        assert not isinstance(rmse, MetricFailure), rmse
        np.testing.assert_allclose(mae.values, np.abs(pred - true).mean(), rtol=1e-12)
        np.testing.assert_allclose(rmse.values, np.sqrt(np.square(pred - true).mean()), rtol=1e-12)
    finally:
        resume.set()
        executor.shutdown()


def test_unrelated_executor_does_not_import_builtins():
    code = '''
import sys
import numpy as np
from ffast.metrics.registry import MetricRegistry, default_registry
from ffast.metrics.server_executor import ServerExecutor
from ffast.metrics.execution import FlatInputSource
from ffast.metrics.models import MetricFailure

assert 'ffast.metrics.builtin' not in sys.modules
default_registry.freeze()
registry = MetricRegistry()
@registry.metric(id='custom.double', inputs={'x': 'raw.x'}, shape='per_structure', unit='energy')
def double(x):
    return x * 2
executor = ServerExecutor(registry, outside=lambda fn: False)
try:
    result = executor.run('custom.double', FlatInputSource({'x': np.arange(3.)}), {})
    assert not isinstance(result, MetricFailure), result
    np.testing.assert_array_equal(result.values, [0, 2, 4])
    assert 'ffast.metrics.builtin' not in sys.modules
finally:
    executor.shutdown()
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_batches_overlap_without_extra_force_reads(workspace, monkeypatch):
    """Real executor: overlap batches, retain shared reads and ordered results."""
    from ffast.metrics import force_scan, resources as execution_resources

    monkeypatch.setattr(execution_resources, "cores", lambda: 4)
    monkeypatch.setattr(type(workspace), "block_bytes", property(lambda self: 32 * 1024))
    true, pred, offsets = inputs(10000)
    lock, meet = Lock(), Barrier(2)
    active = peak = differences = 0
    reads = [0, 0]
    original = force_scan._difference

    def difference(p, t):
        nonlocal active, peak, differences
        with lock:
            active += 1
            peak = max(peak, active)
            differences += 1
            first_pair = differences <= 2
        try:
            if first_pair:
                meet.wait(timeout=3)
            return original(p, t)
        finally:
            with lock:
                active -= 1

    def counted(which, values):
        def read(a, b):
            with lock:
                reads[which] += b - a
            return values[a:b]
        return BatchedArray(values.shape, values.dtype, read)

    monkeypatch.setattr(force_scan, "_difference", difference)
    executor = ServerExecutor(default_registry)
    try:
        source = Source(counted(0, true), counted(1, pred), offsets)
        result = executor.run(METRICS[0], source, {})
        assert not isinstance(result, MetricFailure), result
        np.testing.assert_allclose(result.values, np.abs(pred - true).mean(), rtol=1e-12)
        assert 2 <= peak <= 4
        assert reads == [len(true), len(true)]
        assert workspace.used + workspace.cache_bytes <= workspace.limit
        assert executor._batch_gate._reserved == 0
    finally:
        executor.shutdown()


@pytest.mark.parametrize("cancel", [False, True])
def test_parallel_failure_drains_reads_before_releasing_workspace(workspace, monkeypatch, cancel):
    from ffast.metrics import resources as execution_resources

    monkeypatch.setattr(execution_resources, "cores", lambda: 4)
    monkeypatch.setattr(type(workspace), "block_bytes", property(lambda self: 32 * 1024))
    true, pred, offsets = inputs(10000)
    lock, meet, cancelled = Lock(), Barrier(2), Event()
    active = starts = 0

    def read(a, b):
        nonlocal active, starts
        with lock:
            active += 1
            starts += 1
            first_pair = starts <= 2
        try:
            if first_pair:
                meet.wait(timeout=3)
            if a == 0:
                if cancel:
                    cancelled.set()
                else:
                    raise OSError("injected force read failure")
            return true[a:b]
        finally:
            with lock:
                active -= 1

    source = Source(BatchedArray(true.shape, true.dtype, read),
                    BatchedArray(pred.shape, pred.dtype, lambda a, b: pred[a:b]),
                    offsets, stopped=cancelled.is_set)
    executor = ServerExecutor(default_registry)
    try:
        result = executor.run(METRICS[0], source, {})
        assert isinstance(result, MetricFailure), result
        assert result.kind == ("temporary" if cancel else "failed")
        if not cancel:
            assert "injected force read failure" in result.traceback
        assert active == 0
        assert 2 <= starts <= 4  # bounded read-ahead, never enqueue the whole dataset
        assert executor._batch_gate._reserved == 0
        assert not executor._force_scans.groups
        good = [BatchedArray(v.shape, v.dtype, lambda a, b, v=v: v[a:b]) for v in (true, pred)]
        retry = executor.run(METRICS[0], Source(*good, offsets), {})
        assert not isinstance(retry, MetricFailure), retry
        np.testing.assert_allclose(retry.values, np.abs(pred - true).mean(), rtol=1e-12)
    finally:
        executor.shutdown()


def _wrapped(values):
    return BatchedArray(values.shape, values.dtype, lambda a, b, v=values: v[a:b])


def test_plan_reading_two_predictions_is_not_answered_by_one_scan(workspace):
    from ffast.metrics.execution import ExecutionPlan, PlanStep, RawInput
    from ffast.metrics.force_scan import ForceScans

    true, pred, offsets = inputs()

    def plan(first, second):
        steps = [PlanStep(metric_id, {"reference": RawInput(_wrapped(true), "data|reference.forces"),
                                      "predicted": RawInput(_wrapped(values), name)}, {})
                 for metric_id, (name, values) in zip(
                     ("ffast.force_difference", "ffast.force_parity_outliers"), (first, second))]
        return ExecutionPlan(root_ids=["ffast.force_parity_outliers"], steps=steps)

    scans = ForceScans(lambda *a, **k: None)
    source = Source(true, pred, offsets)
    assert scans.acquire(plan(("pred-A", pred), ("pred-B", pred + 3)), source, default_registry) is None
    lease = scans.acquire(plan(("pred-A", pred), ("pred-A", pred)), source, default_registry)
    assert lease is not None
    lease.close()


def test_two_predictions_in_one_source_each_get_their_own_value(workspace):
    true, pred, offsets = inputs()
    other = pred + 3.0
    outliers = "ffast.force_parity_outliers"

    class TwoPredictions(Source):
        """Parity outliers read prediction B; every other metric reads A."""
        asked = None

        def get(self, metric_id, local_key, ref):
            TwoPredictions.asked = metric_id
            if ref == "prediction.forces" and metric_id == outliers:
                return True, _wrapped(other)
            return super().get(metric_id, local_key, ref)

        def identity(self, ref):
            if ref == "prediction.forces" and TwoPredictions.asked == outliers:
                return "pred-B"
            return super().identity(ref)

    executor = ServerExecutor(default_registry)
    try:
        source = TwoPredictions(_wrapped(true), _wrapped(pred), offsets)
        mae = executor.run(METRICS[0], source, {})
        assert not isinstance(mae, MetricFailure), mae
        np.testing.assert_allclose(mae.values, np.abs(pred - true).mean(), rtol=1e-12)
        got = executor.run(outliers, source, {})
        assert not isinstance(got, MetricFailure), got
        want = InProcessExecutor(default_registry).run(outliers, Source(true, other, offsets), {})
        np.testing.assert_allclose(np.asarray(got.values), np.asarray(want.values), rtol=1e-12)
    finally:
        executor.shutdown()


COLOURS = [
    "ffast.force_difference[atom+structure;component;mae]",
    "ffast.force_difference[atom+structure;component;rmse]",
    "ffast.force_difference[atom+structure;length]",
]


def _counted(values, reads):
    def read(a, b):
        reads[0] += b - a
        return values[a:b]
    return BatchedArray(values.shape, values.dtype, read)


@pytest.mark.parametrize("colour", COLOURS)
def test_colour_range_after_the_plots_reads_no_forces(workspace, colour):
    """Plots first: the shared pass already saw every atom's error, so the 3D
    colour range comes from it without reading the forces again."""
    default_registry.ensure(colour)
    true, pred, offsets = inputs()
    fresh = ServerExecutor(default_registry)
    executor = ServerExecutor(default_registry)
    try:
        want = fresh.run_colouring(colour, Source(_wrapped(true), _wrapped(pred), offsets), {})
        assert not isinstance(want, MetricFailure), want
        reads = [0]
        source = Source(_counted(true, reads), _counted(pred, reads), offsets)
        assert not isinstance(executor.run(METRICS[2], source, {}), MetricFailure)
        reads[0] = 0
        got = executor.run_colouring(colour, source, {})
        assert not isinstance(got, MetricFailure), got
        assert reads == [0]
        assert got._colour_span == want._colour_span
        # Values of one frame are still read on demand, and right.
        frame = slice(int(offsets[7]), int(offsets[8]))
        np.testing.assert_array_equal(np.asarray(got.values)[frame],
                                      np.asarray(want.values)[frame])
    finally:
        fresh.shutdown()
        executor.shutdown()


def test_colour_range_without_the_plots_still_reads_the_forces(workspace):
    colour = COLOURS[0]
    default_registry.ensure(colour)
    true, pred, offsets = inputs()
    executor = ServerExecutor(default_registry)
    try:
        reads = [0]
        got = executor.run_colouring(
            colour, Source(_counted(true, reads), _counted(pred, reads), offsets), {})
        assert not isinstance(got, MetricFailure), got
        assert reads[0] >= 2 * len(true)
        np.testing.assert_allclose(got._colour_span,
                                   (np.abs(pred - true).mean(axis=1).min(),
                                    np.abs(pred - true).mean(axis=1).max()))
    finally:
        executor.shutdown()


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_colour_with_bad_forces_behaves_as_without_the_plots(workspace, bad):
    """A non-finite error is the colour pass's own question: no shortcut."""
    colour = COLOURS[0]
    default_registry.ensure(colour)
    true, pred, offsets = inputs()
    pred[2, 1] = bad
    fresh = ServerExecutor(default_registry)
    executor = ServerExecutor(default_registry)
    try:
        want = fresh.run_colouring(colour, Source(_wrapped(true), _wrapped(pred), offsets), {})
        source = Source(_wrapped(true), _wrapped(pred), offsets)
        executor.run(METRICS[2], source, {})
        got = executor.run_colouring(colour, source, {})
        assert isinstance(got, MetricFailure) == isinstance(want, MetricFailure), (got, want)
        if not isinstance(got, MetricFailure):
            assert got._colour_span == want._colour_span
    finally:
        fresh.shutdown()
        executor.shutdown()


def test_colour_range_is_not_taken_from_another_prediction(workspace):
    colour = COLOURS[0]
    default_registry.ensure(colour)
    true, pred, offsets = inputs()
    other = pred + 3.0
    executor = ServerExecutor(default_registry)
    try:
        executor.run(METRICS[2], Source(_wrapped(true), _wrapped(pred), offsets), {})
        got = executor.run_colouring(
            colour, Source(_wrapped(true), _wrapped(other), offsets, token="other"), {})
        errors = np.abs(other - true).mean(axis=1)
        np.testing.assert_allclose(got._colour_span, (errors.min(), errors.max()))
    finally:
        executor.shutdown()


MAE_PLOTS = [
    "ffast.force_difference[dataset;component;mae]",
    "ffast.force_difference[structure;component;mae]",
    "ffast.force_mae_per_structure_density",
    "ffast.force_mae_per_structure_smoothed",
]


@pytest.mark.parametrize("frames", [250, 3000])
def test_colouring_first_gives_the_mae_plots_without_a_read(workspace, frames, monkeypatch):
    """Colouring by per-atom MAE sums each structure's MAE on the way; the MAE
    table cell, distribution and timeline then read no forces."""
    # Small blocks, so blocks cut structures and are added in parts.
    monkeypatch.setattr(type(workspace), "block_bytes", property(lambda self: 512))
    colour = COLOURS[0]
    default_registry.ensure(colour)
    true, pred, offsets = inputs(frames)
    baseline = InProcessExecutor(default_registry)
    executor = ServerExecutor(default_registry)
    try:
        reads = [0]
        source = Source(_counted(true, reads), _counted(pred, reads), offsets)
        coloured = executor.run_colouring(colour, source, {})
        assert not isinstance(coloured, MetricFailure), coloured
        reads[0] = 0
        for name in MAE_PLOTS:
            got = executor.run(name, source, {})
            want = baseline.run(name, Source(true, pred, offsets), {})
            assert not isinstance(got, MetricFailure), (name, got)
            np.testing.assert_allclose(np.asarray(got.values), np.asarray(want.values),
                                       rtol=1e-12, atol=1e-12, equal_nan=True)
        assert reads == [0]
        # RMSE was not collected: it still makes its own pass, and is right.
        got = executor.run(METRICS[3], source, {})
        assert reads[0] >= 2 * len(true)
        want = baseline.run(METRICS[3], Source(true, pred, offsets), {})
        np.testing.assert_allclose(np.asarray(got.values), np.asarray(want.values),
                                   rtol=1e-12, atol=1e-12, equal_nan=True)
    finally:
        executor.shutdown()


@pytest.mark.parametrize("colour", COLOURS[1:])
def test_other_colourings_collect_nothing(workspace, colour):
    default_registry.ensure(colour)
    true, pred, offsets = inputs()
    executor = ServerExecutor(default_registry)
    try:
        reads = [0]
        source = Source(_counted(true, reads), _counted(pred, reads), offsets)
        executor.run_colouring(colour, source, {})
        reads[0] = 0
        executor.run(METRICS[2], source, {})
        assert reads[0] >= 2 * len(true)
    finally:
        executor.shutdown()


def test_colour_collected_mae_is_forgotten_with_its_objects(workspace):
    colour = COLOURS[0]
    default_registry.ensure(colour)
    true, pred, offsets = inputs()
    executor = ServerExecutor(default_registry)
    try:
        executor.run_colouring(colour, Source(_wrapped(true), _wrapped(pred), offsets), {})
        executor.forget_objects({"data"})
        pred += 2
        got = executor.run(METRICS[0], Source(_wrapped(true), _wrapped(pred), offsets), {})
        np.testing.assert_allclose(got.values, np.abs(pred - true).mean(), rtol=1e-12)
    finally:
        executor.shutdown()


def test_colour_collected_mae_is_not_given_to_another_prediction(workspace):
    colour = COLOURS[0]
    default_registry.ensure(colour)
    true, pred, offsets = inputs()
    other = pred + 3.0
    executor = ServerExecutor(default_registry)
    try:
        executor.run_colouring(colour, Source(_wrapped(true), _wrapped(pred), offsets), {})
        got = executor.run(METRICS[0], Source(_wrapped(true), _wrapped(other), offsets,
                                              token="other"), {})
        np.testing.assert_allclose(got.values, np.abs(other - true).mean(), rtol=1e-12)
    finally:
        executor.shutdown()


def test_stopped_colouring_publishes_nothing(workspace, monkeypatch):
    monkeypatch.setattr(type(workspace), "block_bytes", property(lambda self: 512))
    colour = COLOURS[0]
    default_registry.ensure(colour)
    true, pred, offsets = inputs()
    asked = [False]

    def read(a, b):
        asked[0] = True   # stop once the first block was read
        return true[a:b]

    executor = ServerExecutor(default_registry)
    try:
        got = executor.run_colouring(
            colour, Source(BatchedArray(true.shape, true.dtype, read), _wrapped(pred),
                           offsets, stopped=lambda: asked[0]), {})
        assert isinstance(got, MetricFailure)
        assert not any(g.from_colour for g in executor._force_scans.groups.values())
    finally:
        executor.shutdown()


@pytest.mark.parametrize("frames", [300, 25_000])
def test_per_element_mae_distribution_reads_the_forces_once(workspace, frames):
    """Every element's curve comes from one pass over the forces, however
    many elements; before, each element read them once (twice above the
    exact-KDE size)."""
    from ffast.metrics.builtin.atomic_metrics import _per_structure_element_mae
    from ffast.metrics.streaming import kde

    name = "ffast.force_mae_per_structure_per_element_kde"
    true, pred, offsets = inputs(frames)
    elements = np.random.default_rng(7).choice([1, 6, 7, 8, 16], size=len(true))
    reads = [0]
    source = Source(_counted(true, reads), _counted(pred, reads), offsets)
    source.values["reference.elements"] = elements
    executor = ServerExecutor(default_registry)
    try:
        got = executor.run(name, source, {})
        assert not isinstance(got, MetricFailure), got
        assert reads == [2 * len(true)]
        fd = pred - true
        want = np.stack([kde(_per_structure_element_mae(fd, elements, offsets, z), "abs")
                         for z in np.unique(elements)])
        np.testing.assert_allclose(np.asarray(got.values), want, rtol=1e-9, atol=1e-12)
    finally:
        executor.shutdown()


ATOMIC = [
    "ffast.force_difference[element;component;mae]",
    "ffast.force_difference[element;component;rmse]",
    "ffast.force_mae_per_structure_per_element_kde",
]


def _with_elements(source, elements):
    source.values["reference.elements"] = elements
    return source


def _atomic_inputs(frames, choices=(1, 6, 7, 8, 16)):
    true, pred, offsets = inputs(frames)
    elements = np.random.default_rng(7).choice(choices, size=len(true))
    return true, pred, offsets, elements


def _expected(name, true, pred, offsets, elements):
    for z in ATOMIC[:2]:
        default_registry.ensure(z)
    baseline = InProcessExecutor(default_registry)
    return baseline.run(name, _with_elements(Source(true, pred, offsets), elements), {})


@pytest.mark.parametrize("frames", [250, 25_000])
def test_atomic_errors_after_basic_errors_read_no_forces(workspace, frames, monkeypatch):
    """The Basic Errors pass also sums the errors per element and keeps each
    structure's MAE per element: the Atomic Errors table and distribution
    after it read no forces. 25,000 structures keep the table in files."""
    monkeypatch.setattr(type(workspace), "block_bytes", property(lambda self: 4096))
    true, pred, offsets, elements = _atomic_inputs(frames)
    executor = ServerExecutor(default_registry)
    try:
        reads = [0]
        source = _with_elements(Source(_counted(true, reads), _counted(pred, reads), offsets), elements)
        assert not isinstance(executor.run(METRICS[2], source, {}), MetricFailure)
        assert reads == [2 * len(true)]
        reads[0] = 0
        for name in ATOMIC:
            want = _expected(name, true, pred, offsets, elements)
            got = executor.run(name, source, {})
            assert not isinstance(got, MetricFailure), (name, got)
            np.testing.assert_allclose(np.asarray(got.values), np.asarray(want.values),
                                       rtol=1e-9, atol=1e-12, equal_nan=True)
        assert reads == [0]
    finally:
        executor.shutdown()


def test_atomic_errors_first_give_basic_errors_without_a_read(workspace):
    true, pred, offsets, elements = _atomic_inputs(250)
    executor = ServerExecutor(default_registry)
    baseline = InProcessExecutor(default_registry)
    try:
        reads = [0]
        source = _with_elements(Source(_counted(true, reads), _counted(pred, reads), offsets), elements)
        assert not isinstance(executor.run(ATOMIC[2], source, {}), MetricFailure)
        assert reads == [2 * len(true)]
        reads[0] = 0
        for name in METRICS[:5]:
            got = executor.run(name, source, {})
            want = baseline.run(name, Source(true, pred, offsets), {})
            np.testing.assert_allclose(np.asarray(got.values), np.asarray(want.values),
                                       rtol=1e-12, atol=1e-12, equal_nan=True)
        assert reads == [0]
    finally:
        executor.shutdown()


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_atomic_errors_with_bad_forces_match_regular_metrics(workspace, bad):
    true, pred, offsets, elements = _atomic_inputs(250)
    pred[2, 1] = bad
    executor = ServerExecutor(default_registry)
    try:
        source = _with_elements(Source(_wrapped(true), _wrapped(pred), offsets), elements)
        executor.run(METRICS[2], source, {})
        for name in ATOMIC:
            want = _expected(name, true, pred, offsets, elements)
            got = executor.run(name, source, {})
            assert isinstance(got, MetricFailure) == isinstance(want, MetricFailure), (name, got, want)
            if not isinstance(got, MetricFailure):
                np.testing.assert_allclose(np.asarray(got.values), np.asarray(want.values),
                                           rtol=1e-9, atol=1e-12, equal_nan=True)
    finally:
        executor.shutdown()


def test_many_elements_keep_sums_but_not_the_table(workspace):
    """Past 32 elements each structure's per-element MAE is not kept; the
    distribution makes its own pass and is still right."""
    true, pred, offsets, elements = _atomic_inputs(250, choices=np.arange(1, 41))
    executor = ServerExecutor(default_registry)
    try:
        reads = [0]
        source = _with_elements(Source(_counted(true, reads), _counted(pred, reads), offsets), elements)
        executor.run(METRICS[2], source, {})
        reads[0] = 0
        got = executor.run(ATOMIC[0], source, {})
        assert reads == [0]
        np.testing.assert_allclose(np.asarray(got.values),
                                   np.asarray(_expected(ATOMIC[0], true, pred, offsets, elements).values),
                                   rtol=1e-12)
        got = executor.run(ATOMIC[2], source, {})
        assert reads[0] >= 2 * len(true)
        np.testing.assert_allclose(np.asarray(got.values),
                                   np.asarray(_expected(ATOMIC[2], true, pred, offsets, elements).values),
                                   rtol=1e-9, atol=1e-12)
    finally:
        executor.shutdown()


def test_atomic_errors_are_not_taken_from_other_elements(workspace):
    true, pred, offsets, elements = _atomic_inputs(250)
    other = elements.copy()
    other[::2] = 1

    class Renamed(Source):
        def identity(self, ref):
            return super().identity(ref) + ("|other" if ref == "reference.elements" else "")

    executor = ServerExecutor(default_registry)
    try:
        executor.run(METRICS[2], _with_elements(Source(_wrapped(true), _wrapped(pred), offsets), elements), {})
        source = _with_elements(Renamed(_wrapped(true), _wrapped(pred), offsets), other)
        for name in ATOMIC:
            got = executor.run(name, source, {})
            want = _expected(name, true, pred, offsets, other)
            np.testing.assert_allclose(np.asarray(got.values), np.asarray(want.values),
                                       rtol=1e-9, atol=1e-12)
    finally:
        executor.shutdown()

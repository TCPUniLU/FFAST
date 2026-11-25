"""Forces held in RAM (plain arrays) share one force pass too, split into
pieces on many cores, and give the same bits as each metric run on its own."""

import gc

import numpy as np
import pytest

from ffast.metrics.models import MetricFailure
from ffast.metrics.registry import default_registry
from ffast.metrics.server_executor import ServerExecutor

from tests.ffast.test_force_scan import ATOMIC, METRICS, SUBSYSTEM, Source, workspace  # noqa: F401

NET = "ffast.force_net_residual"


def _frames(kind, n):
    if kind == "uneven":            # sizes 0-10, so empty structures too
        counts = np.arange(n) % 11
    elif kind == "equal":           # every structure the same size
        counts = np.full(n, 7)
    else:                           # mostly equal, one different
        counts = np.full(n, 7)
        counts[n // 2] = 3
    return np.concatenate(([0], np.cumsum(counts)))


def _forces(offsets, seed=3):
    rng = np.random.default_rng(seed)
    true = rng.normal(size=(offsets[-1], 3)) * np.exp(rng.normal(size=(offsets[-1], 1)) * 3)
    pred = true + rng.normal(size=true.shape) * 0.1
    return true, pred


def _alone(names, source, parameters=None):
    """Each metric run on its own path, as before the shared pass served RAM."""
    executor = ServerExecutor(default_registry)
    executor._force_scans.acquire = lambda *args: None
    try:
        out = {}
        for name in names:
            result = executor.run(name, source, parameters or {})
            assert not isinstance(result, MetricFailure), (name, result)
            out[name] = np.array(result.values, copy=True)
        return out
    finally:
        executor.shutdown()
        gc.collect()


@pytest.mark.parametrize("kind", ["uneven", "equal", "one_odd"])
@pytest.mark.parametrize("lanes", [1, 8])
def test_forces_in_ram_share_one_pass_and_keep_every_bit(workspace, kind, lanes, monkeypatch):
    from ffast.metrics import force_scan, resources as execution_resources

    monkeypatch.setattr(execution_resources, "cores", lambda: lanes)
    # Small pieces: the dataset sums are split many times.
    monkeypatch.setattr(type(workspace), "block_bytes", property(lambda self: 32 * 1024))
    offsets = _frames(kind, 6000)
    true, pred = _forces(offsets)
    names = [*METRICS, NET]
    want = _alone(names, Source(true, pred, offsets))
    passes = []
    scan = force_scan.scan
    monkeypatch.setattr(force_scan, "scan", lambda *a, **k: passes.append(1) or scan(*a, **k))
    executor = ServerExecutor(default_registry)
    try:
        source = Source(true, pred, offsets)
        for name in names:
            got = executor.run(name, source, {})
            assert not isinstance(got, MetricFailure), (name, got)
            np.testing.assert_array_equal(np.asarray(got.values), want[name], err_msg=name)
        assert passes == [1]
    finally:
        executor.shutdown()


def test_plots_from_forces_in_ram_keep_every_bit(workspace, monkeypatch):
    monkeypatch.setattr(type(workspace), "block_bytes", property(lambda self: 32 * 1024))
    offsets = _frames("uneven", 6000)
    true, pred = _forces(offsets)
    names = ["ffast.force_mae_per_structure_smoothed", "ffast.force_rmse_smoothed",
             "ffast.force_mae_per_structure_density", "ffast.force_rmse_density"]
    want = _alone(names, Source(true, pred, offsets), {"window": 11})
    executor = ServerExecutor(default_registry)
    try:
        for name in names:
            got = executor.run(name, Source(true, pred, offsets), {"window": 11})
            assert not isinstance(got, MetricFailure), (name, got)
            np.testing.assert_array_equal(np.asarray(got.values), want[name], err_msg=name)
    finally:
        executor.shutdown()


def test_element_and_net_metrics_from_forces_in_ram_keep_every_bit(workspace, monkeypatch):
    """The per-element sums add each atom in turn, which pieces cannot
    repeat exactly: they keep their own path. The rest come from the pass."""
    monkeypatch.setattr(type(workspace), "block_bytes", property(lambda self: 32 * 1024))
    offsets = _frames("uneven", 3000)
    true, pred = _forces(offsets)
    elements = np.random.default_rng(7).choice((1, 6, 8), size=len(true))

    def source():
        s = Source(true, pred, offsets)
        s.values["reference.elements"] = elements
        return s

    for z in ATOMIC[:2]:
        default_registry.ensure(z)
    names = [*ATOMIC, *SUBSYSTEM, METRICS[0]]
    want = _alone(names, source())
    executor = ServerExecutor(default_registry)
    try:
        for name in names:
            got = executor.run(name, source(), {})
            assert not isinstance(got, MetricFailure), (name, got)
            np.testing.assert_array_equal(np.asarray(got.values), want[name], err_msg=name)
    finally:
        executor.shutdown()


def test_bad_forces_in_ram_fail_as_on_their_own(workspace):
    offsets = _frames("uneven", 300)
    true, pred = _forces(offsets)
    pred[5, 1] = np.nan
    executor = ServerExecutor(default_registry)
    try:
        for name in METRICS[:4]:
            got = executor.run(name, Source(true, pred, offsets), {})
            assert isinstance(got, MetricFailure) and "non-finite" in got.traceback, name
    finally:
        executor.shutdown()


@pytest.mark.parametrize("bad", [np.inf, -np.inf, np.nan])
def test_parity_density_with_bad_forces_in_ram_keeps_every_bit(workspace, monkeypatch, bad):
    """The lowest and highest force skip NaN and infinite values, as the
    density run on its own does."""
    monkeypatch.setattr(type(workspace), "block_bytes", property(lambda self: 32 * 1024))
    offsets = _frames("uneven", 3000)
    true, pred = _forces(offsets)
    true[7, 2] = bad
    pred[4000, 0] = bad
    name = "ffast.force_parity_density"
    want = _alone([name], Source(true, pred, offsets))[name]
    executor = ServerExecutor(default_registry)
    try:
        got = executor.run(name, Source(true, pred, offsets), {})
        assert not isinstance(got, MetricFailure), got
        np.testing.assert_array_equal(np.asarray(got.values), want)
    finally:
        executor.shutdown()


def test_the_pass_starts_before_the_difference_table_is_built(workspace, monkeypatch):
    """The shared pass reads the reference and predicted forces, not the
    difference table: it does not wait for the table (built alongside)."""
    import threading

    from ffast.metrics import force_scan
    from ffast.metrics.builtin import force_metrics

    offsets = _frames("uneven", 3000)
    true, pred = _forces(offsets)
    started = threading.Event()
    seen = []
    scan = force_scan.scan
    subtract = force_metrics._subtract

    def scan_(*a, **k):
        started.set()
        return scan(*a, **k)

    def subtract_(*a):
        seen.append(started.wait(10))
        return subtract(*a)

    monkeypatch.setattr(force_scan, "scan", scan_)
    monkeypatch.setattr(force_metrics, "_subtract", subtract_)
    executor = ServerExecutor(default_registry)
    try:
        name = METRICS[0]
        got = executor.run(name, Source(true, pred, offsets), {})
        assert not isinstance(got, MetricFailure), got
        assert seen == [True]
    finally:
        executor.shutdown()


def test_the_pass_over_few_forces_in_ram_reserves_little_memory(workspace, monkeypatch):
    """The pass's workspace follows the forces it holds: a small dataset
    under a small budget must leave room for the replies (it took 10 blocks
    a lane, 640 KiB of a 1 MiB budget for 360 atoms, and a reply was refused)."""
    offsets = _frames("uneven", 60)
    true, pred = _forces(offsets)
    executor = ServerExecutor(default_registry)
    asked = []
    work = executor._force_scans.workspace
    executor._force_scans.workspace = lambda stopped, blocks=4: asked.append(blocks) or work(stopped, blocks=blocks)
    try:
        got = executor.run(METRICS[0], Source(true, pred, offsets), {})
        assert not isinstance(got, MetricFailure), got
        assert asked and asked[0] * workspace.block_bytes <= max(workspace.block_bytes, 4 * 48 * len(true) * 8)
        assert asked[0] < 10
    finally:
        executor.shutdown()


def test_results_from_the_cache_start_no_pass(workspace, monkeypatch):
    from ffast.metrics import force_scan

    offsets = _frames("uneven", 300)
    true, pred = _forces(offsets)
    starts = []
    start = force_scan._Group.start
    monkeypatch.setattr(force_scan._Group, "start", lambda self, lease: starts.append(1) or start(self, lease))
    executor = ServerExecutor(default_registry)
    try:
        source = Source(true, pred, offsets)
        first = executor.run(METRICS[0], source, {})
        assert starts == [1]
        again = executor.run(METRICS[0], source, {})
        assert starts == [1]
        np.testing.assert_array_equal(np.asarray(again.values), np.asarray(first.values))
    finally:
        executor.shutdown()


def test_a_colouring_of_forces_in_ram_collects_nothing(workspace):
    from ffast.metrics.execution import build_execution_plan

    offsets = _frames("uneven", 300)
    true, pred = _forces(offsets)
    executor = ServerExecutor(default_registry)
    try:
        source = Source(true, pred, offsets)
        name = "ffast.force_difference[atom+structure;component;mae]"
        plan = build_execution_plan(default_registry, name, {}, source)
        assert executor._force_scans.colour_collector(plan, source, default_registry, name) is None
    finally:
        executor.shutdown()


@pytest.mark.parametrize("n", [0, 1, 7, 8, 129, 1000, 8193, 100_003, 3_000_017])
@pytest.mark.parametrize("size", [1, 100, 4096, 50_000])
def test_dataset_sums_in_pieces_equal_numpys_sum(n, size):
    from ffast.metrics.force_scan import _leaves, _pairwise, _tree_total

    assert _pairwise(), "this NumPy adds in an order the pass does not know"
    values = np.random.default_rng(n).random(n) * np.exp(np.random.default_rng(1).normal(size=n) * 5)
    pieces = _leaves(n, size)
    assert pieces[0][0] == 0 and pieces[-1][1] == n
    assert all(a[1] == b[0] for a, b in zip(pieces, pieces[1:]))
    total = _tree_total([values[a:b].sum() for a, b in pieces], n, size)
    assert total == values.sum() == values.reshape(1, -1).sum(axis=1)[0]


def test_a_numpy_that_adds_in_another_order_takes_each_metrics_own_path(workspace, monkeypatch):
    from ffast.metrics import force_scan

    monkeypatch.setattr(force_scan, "_PAIRWISE", [False])
    offsets = _frames("uneven", 300)
    true, pred = _forces(offsets)
    passes = []
    scan = force_scan.scan
    monkeypatch.setattr(force_scan, "scan", lambda *a, **k: passes.append(1) or scan(*a, **k))
    want = _alone(METRICS[:4], Source(true, pred, offsets))
    executor = ServerExecutor(default_registry)
    try:
        for name in METRICS[:4]:
            got = executor.run(name, Source(true, pred, offsets), {})
            np.testing.assert_array_equal(np.asarray(got.values), want[name], err_msg=name)
        assert passes == []
    finally:
        executor.shutdown()


@pytest.mark.parametrize("dtype", [np.float64, np.float32])
def test_force_difference_in_chunks_equals_the_subtraction(monkeypatch, dtype):
    from ffast.metrics.builtin import force_metrics

    monkeypatch.setattr(force_metrics, "_CHUNK", 999)
    rng = np.random.default_rng(5)
    true = rng.normal(size=(10_001, 3)).astype(dtype)
    pred = rng.normal(size=(10_001, 3)).astype(dtype)
    got = force_metrics.force_difference(true, pred)
    want = pred - true
    assert got.dtype == want.dtype
    np.testing.assert_array_equal(got, want)

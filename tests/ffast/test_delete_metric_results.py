"""Deleting a dataset or prediction can delete its computed metric results.

The metric result cache keys results by input content, so it could not tell
which results a dataset or prediction owned; a delete left them in memory and,
for large ones, in ``.npy`` files on disk until the server stopped. Each result
now records the (dataset, prediction) pairs that used it, and the delete dialog
asks whether to delete them too.
"""

import asyncio

import numpy as np
import pytest

from ffast.core.environment import Environment
from ffast.metrics.cache import MetricCache
from ffast.metrics.execution import FlatInputSource
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.models import MetricResult
from ffast.metrics.registry import MetricRegistry
from ffast.protocol import control
from ffast.protocol.rpc import unpack


def _result(n, metric_id="m"):
    return MetricResult(metric_id=metric_id, shape="(N,)", dtype="float64", unit="",
                        compute_parameters={}, implementation_hash="h",
                        values=np.zeros(n), warnings=[])


def _key(cache, name):
    return cache.make_key(name, len, {}, {}, {})


# ── the cache ───────────────────────────────────────────────────────────────

def test_a_result_used_only_by_a_deleted_object_goes_from_memory():
    cache = MetricCache()
    a, b = _key(cache, "a"), _key(cache, "b")
    cache.put(a, _result(10))
    cache.use(a, {("ds", "m")})
    cache.put(b, _result(10))
    cache.use(b, {("other", None)})

    assert cache.stored_for(["ds"]) == {"count": 1, "memory_bytes": 80, "disk_bytes": 0}
    assert cache.forget_objects(["ds"])["count"] == 1

    assert cache.get(a) is None
    assert cache.get(b) is not None


def test_a_result_another_object_also_used_stays():
    cache = MetricCache()
    k = _key(cache, "shared")
    cache.put(k, _result(10))
    cache.use(k, {("ds", "m"), ("ds2", "m")})

    assert cache.stored_for(["ds"])["count"] == 0
    cache.forget_objects(["ds"])
    assert cache.get(k) is not None

    assert cache.stored_for(["ds2"])["count"] == 1     # now only ds2 uses it
    cache.forget_objects(["ds2"])
    assert cache.get(k) is None


def test_a_prediction_takes_its_results_on_every_dataset():
    cache = MetricCache()
    k = _key(cache, "k")
    cache.put(k, _result(10))
    cache.use(k, {("ds", "m"), ("ds2", "m")})

    cache.forget_objects(["m"])

    assert cache.get(k) is None


def test_a_result_on_disk_has_its_file_deleted(tmp_path):
    cache = MetricCache(max_bytes=100, spill_dir=tmp_path, spill_min_bytes=0,
                        spill_max_bytes=1 << 20)
    k = _key(cache, "big")
    cache.use(k, {("ds", None)})
    cache.put(k, _result(1000))                 # 8000 bytes > 100: written to disk
    [file] = list(tmp_path.glob("*.npy"))

    assert cache.stored_for(["ds"]) == {"count": 1, "memory_bytes": 0, "disk_bytes": 8000}
    assert cache.forget_objects(["ds"])["disk_bytes"] == 8000

    assert not file.exists()
    assert cache.get(k) is None


def test_running_a_metric_records_what_its_source_reads():
    registry = MetricRegistry()

    @registry.metric(id="t.double", inputs={"x": "reference.energies"}, shape=("N",), unit="")
    def double(x):
        return 2 * x

    class _Source(FlatInputSource):
        def uses(self):
            return frozenset({("ds", "m")})

    cache = MetricCache()
    executor = InProcessExecutor(registry, cache=cache)
    executor.run("t.double", _Source({"x": np.arange(3.0)}), {})

    assert executor.stored_for(["ds"])["count"] == 1
    assert executor.stored_for(["m"])["count"] == 1
    executor.forget_objects(["ds"])
    assert executor.stored_for(["ds"])["count"] == 0


# ── the Environment's delete ────────────────────────────────────────────────

class _Dataset:
    isSubDataset = False
    active = True
    parent = None

    def __init__(self, fingerprint, parent=None):
        self.fingerprint = fingerprint
        if parent is not None:
            self.parent = parent
            self.isSubDataset = True

    def getN(self):
        return 2

    def getName(self):
        return self.fingerprint

    def onDelete(self):
        pass


@pytest.fixture
def env():
    e = Environment()
    e.data._metricExecutor = InProcessExecutor(MetricRegistry(), cache=MetricCache())
    parent = _Dataset("ds")
    e.datasets.add(parent)
    e.datasets.add(_Dataset("sub", parent=parent))
    e.datasets.add(_Dataset("other"))
    cache = e.data._metricExecutor._cache
    for name, use in [("on_ds", ("ds", None)), ("on_sub", ("sub", None)), ("on_other", ("other", None))]:
        k = _key(cache, name)
        cache.put(k, _result(10))
        cache.use(k, {use})
    return e


def test_deleting_a_dataset_counts_the_results_of_its_subsets(env):
    assert env.storedMetricResults("ds")["count"] == 2


def test_deleting_with_results_deletes_its_and_its_subsets(env):
    env.deleteObject("ds", delete_results=True)

    assert env.storedMetricResults("other")["count"] == 1
    assert env.data._metricExecutor._cache.stored_for(["ds", "sub"])["count"] == 0


def test_deleting_without_results_keeps_them(env):
    env.deleteObject("ds")

    assert env.data._metricExecutor._cache.stored_for(["ds", "sub"])["count"] == 2


# ── the server session ──────────────────────────────────────────────────────

def _dispatch(env, event, kwargs):
    from ffast.session.server_session import ServerSession

    async def scenario():
        s = ServerSession(env, asyncio.Queue(), broadcast=lambda *_: None)
        await s.dispatch(event, [], kwargs)
        out = []
        while not s.outbound.empty():
            out.append(unpack(s.outbound.get_nowait()))
        return out

    return asyncio.run(scenario())


def test_the_session_answers_what_a_delete_could_delete(env):
    out = _dispatch(env, control.STORED_RESULTS, {"fingerprint": "ds"})
    [kw] = [k for e, _, k in out if e == control.STORED_RESULTS_INFO]
    assert kw == {"fingerprint": "ds", "count": 2, "memory_bytes": 160, "disk_bytes": 0}


def test_the_session_deletes_results_only_when_asked(env):
    _dispatch(env, control.DELETE_OBJECT, {"fingerprint": "other"})
    assert env.data._metricExecutor._cache.stored_for(["other"])["count"] == 1

    _dispatch(env, control.DELETE_OBJECT, {"fingerprint": "ds", "delete_results": True})
    assert env.data._metricExecutor._cache.stored_for(["ds", "sub"])["count"] == 0


def test_stored_results_is_a_read_a_viewer_may_send():
    assert control.STORED_RESULTS in control.CLIENT_TO_SERVER
    assert control.STORED_RESULTS not in control.MUTATING_CLIENT_EVENTS

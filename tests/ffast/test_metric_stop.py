"""A metric stops early when its dataset or prediction is deleted (ADR 0079)."""

from __future__ import annotations

import threading
import time

from ffast.metrics import stop
from ffast.metrics.cache import MetricCache
from ffast.metrics.execution import InputSource, build_execution_plan, run_plan
from ffast.metrics.models import MetricFailure, MetricResult
from ffast.metrics.registry import MetricRegistry
from ffast.metrics.server_executor import ServerExecutor


class _Source(InputSource):
    def __init__(self):
        self.flag = False

    def get(self, metric_id, local_key, ref):
        return True, 2.0

    def stopped(self):
        return self.flag


def _inner(x):
    return x + 1.0


def _outer(y):
    return y * 10.0


def _loop(x):
    for _ in range(100):
        stop.check()
        time.sleep(0.05)
    return 1.0


def _registry():
    reg = MetricRegistry()
    reg.metric(id="t.inner", inputs={"x": "ref.x"}, shape="scalar", unit="energy")(_inner)
    reg.metric(id="t.outer", inputs={"y": "t.inner"}, shape="scalar", unit="energy")(_outer)
    reg.metric(id="t.loop", inputs={"x": "ref.x"}, shape="scalar", unit="energy")(_loop)
    return reg


def _plan_run(source):
    reg = _registry()
    cache = MetricCache()
    plan = build_execution_plan(reg, "t.outer", {}, source)

    def run_fn(mid, schema, fn, kwargs, cparams):
        out = fn(**kwargs, **cparams)
        source.flag = True          # the data is deleted after the first step
        return out

    return run_plan(plan, reg, cache, run_fn, stopped=source.stopped), cache, reg, plan


def test_steps_after_the_stop_fail_and_earlier_ones_keep_their_result():
    results, *_ = _plan_run(_Source())
    assert isinstance(results["t.inner"], MetricResult)
    assert isinstance(results["t.outer"], MetricFailure)
    assert "Stopped" in results["t.outer"].traceback


def test_a_stopped_step_is_not_cached():
    source = _Source()
    _results, cache, reg, plan = _plan_run(source)
    source.flag = False
    again = run_plan(plan, reg, cache, lambda mid, schema, fn, kwargs, cparams: fn(**kwargs, **cparams),
                     stopped=source.stopped)
    assert isinstance(again["t.outer"], MetricResult)
    assert float(again["t.outer"].values) == 30.0


def test_a_running_step_stops_at_its_next_check():
    source = _Source()
    ex = ServerExecutor(_registry(), outside=lambda fn: False)
    threading.Timer(0.3, lambda: setattr(source, "flag", True)).start()
    start = time.monotonic()
    result = ex.run("t.loop", source, {})
    assert isinstance(result, MetricFailure) and "Stopped" in result.traceback
    assert time.monotonic() - start < 1.0


def test_deleting_a_dataset_marks_it_and_its_subset():
    from types import SimpleNamespace

    from ffast.cache.store import DataCache
    from ffast.core.dataset_registry import DatasetRegistry
    from ffast.core.object_catalog import ObjectCatalog

    events = SimpleNamespace(objects=ObjectCatalog(), eventPush=lambda *a, **k: None)
    reg = DatasetRegistry(DataCache(), events)
    parent = SimpleNamespace(fingerprint="D", parent=None, active=True, isSubDataset=False,
                             onDelete=lambda: None, getN=lambda: 1)
    child = SimpleNamespace(fingerprint="sub", parent=parent, active=True, isSubDataset=True,
                            onDelete=lambda: None, getN=lambda: 1)
    reg._datasets.update(D=parent, sub=child)
    reg.delete("D")
    assert parent.deleted and child.deleted


def test_deleting_a_model_marks_it():
    from types import SimpleNamespace

    from ffast.cache.store import DataCache
    from ffast.core.model_registry import ModelRegistry
    from ffast.core.object_catalog import ObjectCatalog

    events = SimpleNamespace(objects=ObjectCatalog(), eventPush=lambda *a, **k: None)
    reg = ModelRegistry(DataCache(), events)
    model = SimpleNamespace(fingerprint="M", onDelete=lambda: None)
    reg._models["M"] = model
    reg.delete("M")
    assert model.deleted

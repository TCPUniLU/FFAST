"""ServerExecutor: FFAST's code runs here, outside code in a worker (ADR 0079).

Metric functions are module-level so the worker can unpickle them (spawn).
"""

from __future__ import annotations

import functools
import os

import numpy as np
import pytest

from ffast.metrics import dims
from ffast.metrics.execution import FlatInputSource
from ffast.metrics.expr import _ExprFn, compile_expr_metric
from ffast.metrics.models import MetricFailure, MetricResult
from ffast.metrics.placement import is_outside_code
from ffast.metrics.pool import WorkerProcessExecutor
from ffast.metrics.registry import MetricRegistry
from ffast.metrics.server_executor import ServerExecutor


def _pid(x):
    return os.getpid()


def _crash(x):
    os._exit(1)


def _double(x):
    return x * 2


@pytest.fixture
def registry():
    reg = MetricRegistry()
    reg.metric(id="t.pid", inputs={"x": "ref.x"}, shape="scalar", unit="energy")(_pid)
    reg.metric(id="t.crash", inputs={"x": "ref.x"}, shape="scalar", unit="energy")(_crash)
    reg.metric(id="t.double", inputs={"x": "ref.x"}, shape="scalar", unit="energy")(_double)
    return reg


@pytest.fixture
def made():
    built = []

    def make(registry, **kw):
        ex = ServerExecutor(registry, **kw)
        built.append(ex)
        return ex

    yield make
    for ex in built:
        ex.shutdown()


SRC = FlatInputSource({"x": 3.0})


def test_what_is_outside_code():
    from ffast.metrics.builtin import force_metrics

    reg = MetricRegistry()
    mid = compile_expr_metric("t.f", "a * 2", {"a": "reference.energies"}, registry=reg)
    formula = reg.get(mid)[1]
    assert isinstance(formula, _ExprFn)

    assert not is_outside_code(force_metrics.parity_density)
    assert not is_outside_code(formula)
    assert not is_outside_code(functools.partial(force_metrics.parity_density, bins=4))
    assert is_outside_code(_pid)


def test_ffast_code_runs_in_the_server_without_a_worker(registry, made):
    ex = made(registry, outside=lambda fn: False)
    result = ex.run("t.pid", SRC, {})
    assert isinstance(result, MetricResult)
    assert int(result.values) == os.getpid()
    assert ex._workers_executor is None


def test_outside_code_runs_in_a_worker(registry, made):
    ex = made(registry, outside=lambda fn: True)
    result = ex.run("t.pid", SRC, {})
    assert int(result.values) != os.getpid()


def test_a_crashing_outside_metric_only_kills_its_worker(registry, made):
    ex = made(registry, outside=lambda fn: fn is _crash)
    assert isinstance(ex.run("t.crash", SRC, {}), MetricFailure)
    result = ex.run("t.double", SRC, {})
    assert isinstance(result, MetricResult)
    assert float(result.values) == 6.0


def test_try_computes_a_formula_in_the_server(made):
    reg = MetricRegistry()

    @reg.metric(id="b.e", inputs={"x": "reference.energies"}, shape=(dims.N_frames,), unit="energy")
    def e(x):
        return x

    mid = compile_expr_metric("t.f", "e * 10", {"e": "b.e"}, registry=reg)
    ex = made(reg)
    out = ex.call(reg.get(mid)[1], {"e": np.array([1.0, 2.0])})
    assert list(out[0] if isinstance(out, tuple) else out) == [10.0, 20.0]
    assert ex._workers_executor is None


def test_start_boots_a_worker_only_when_there_is_outside_code(registry, made, monkeypatch):
    started = []
    monkeypatch.setattr(WorkerProcessExecutor, "start", lambda self: started.append(self))

    made(registry, outside=lambda fn: False).start()
    assert started == []

    made(registry, outside=lambda fn: fn is _double).start()
    assert len(started) == 1


def test_the_server_executor_names_the_key_of_its_result(registry, made):
    ex = made(registry)
    k = {}
    result = ex.run("t.double", SRC, {}, key_out=k)
    assert ex.cached(k["key"]) is result


def _stops(x):
    from ffast.metrics import stop
    raise stop.Stopped()


def _no_memory(x):
    raise MemoryError()


def test_a_metric_stopped_while_it_runs_fails_as_temporary(made):
    reg = MetricRegistry()
    reg.metric(id="t.stops", inputs={"x": "ref.x"}, shape="scalar", unit="energy")(_stops)
    result = made(reg, outside=lambda fn: False).run("t.stops", SRC, {})
    assert isinstance(result, MetricFailure) and result.kind == "temporary"


def test_running_out_of_memory_is_temporary_not_the_metrics_fault(made):
    """ADR 0085 kind list: "no memory" is temporary, so it is computed again,
    not remembered as a failure until the dataset is deleted."""
    reg = MetricRegistry()
    reg.metric(id="t.oom", inputs={"x": "ref.x"}, shape="scalar", unit="energy")(_no_memory)
    for where in (lambda fn: False, lambda fn: True):     # in the server, in a worker
        result = made(reg, outside=where).run("t.oom", SRC, {})
        assert isinstance(result, MetricFailure) and result.kind == "temporary"

"""A step two metrics need at the same time is computed once.

MeluXina 2026-11-03: Basic Errors' "smoothed" and "density" panels both need
the per-structure force difference. Each found the cache empty, because the
other had not finished, and both computed it: ~11 s twice, at once.
"""
import threading
import time

import numpy as np

from ffast.metrics.cache import MetricCache
from ffast.metrics.execution import FlatInputSource
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.models import MetricFailure, MetricResult
from ffast.metrics.registry import MetricRegistry


def _registry(calls, *, fail_first=False):
    reg = MetricRegistry()

    @reg.metric(id="t.shared", inputs={"x": "raw.x"}, shape="per_structure", unit="energy")
    def shared(x):
        calls.append(threading.get_ident())
        time.sleep(0.3)
        if fail_first and len(calls) == 1:
            raise RuntimeError("first run fails")
        return x * 2

    @reg.metric(id="t.plus", inputs={"s": "t.shared"}, shape="per_structure", unit="energy")
    def plus(s):
        return s + 1

    @reg.metric(id="t.minus", inputs={"s": "t.shared"}, shape="per_structure", unit="energy")
    def minus(s):
        return s - 1

    return reg


def _run_both(reg, cache):
    executor = InProcessExecutor(reg, cache=cache)
    source = FlatInputSource({"x": np.arange(4.0)})
    out = {}

    def run(metric_id):
        out[metric_id] = executor.run(metric_id, source, {})

    threads = [threading.Thread(target=run, args=(m,)) for m in ("t.plus", "t.minus")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return out


def test_a_step_needed_twice_at_once_is_computed_once():
    calls = []
    out = _run_both(_registry(calls), MetricCache())
    assert len(calls) == 1
    np.testing.assert_array_equal(out["t.plus"].values, np.arange(4.0) * 2 + 1)
    np.testing.assert_array_equal(out["t.minus"].values, np.arange(4.0) * 2 - 1)


def test_when_the_first_run_fails_the_waiting_one_computes_it_itself():
    calls = []
    out = _run_both(_registry(calls, fail_first=True), MetricCache())
    assert len(calls) == 2
    results = [r for r in out.values() if isinstance(r, MetricResult)]
    failures = [r for r in out.values() if isinstance(r, MetricFailure)]
    assert len(results) == 1 and len(failures) == 1


def test_nothing_is_left_claimed_after_a_failure():
    calls = []
    reg, cache = _registry(calls, fail_first=True), MetricCache()
    _run_both(reg, cache)
    calls.clear()
    out = _run_both(reg, cache)     # cached now: nothing waits, nothing runs
    assert calls == []
    assert all(isinstance(r, MetricResult) for r in out.values())

"""ADR 0061 decision 3: a registry revision, and a metric run that overlapped a
registry change is thrown away and computed again."""

from __future__ import annotations

import numpy as np
import pytest

import ffast.metrics.builtin  # noqa: F401 — registers the built-ins
from ffast.core.data_service import DataService
from ffast.metrics.models import MetricResult
from ffast.metrics.registry import MetricRegistry, default_registry


def _fn(x):
    return x


def _reg_with(id="t61.a"):
    reg = MetricRegistry()
    reg.metric(id=id, inputs={"x": "reference.energy"}, shape="scalar", unit="energy")(_fn)
    return reg


# ── revision ──────────────────────────────────────────────────────────────────

def test_revision_starts_at_zero():
    assert MetricRegistry().revision == 0


def test_register_unregister_and_restore_each_raise_the_revision():
    reg = _reg_with()
    assert reg.revision == 1
    entry = reg.unregister("t61.a")
    assert reg.revision == 2
    reg.restore("t61.a", entry)
    assert reg.revision == 3


def test_a_failed_register_does_not_raise_the_revision():
    reg = _reg_with()
    with pytest.raises(ValueError):
        reg.metric(id="t61.a", inputs={"x": "reference.energy"}, shape="scalar",
                   unit="energy")(_fn)
    assert reg.revision == 1


# ── generateMetric retry ──────────────────────────────────────────────────────

class _Events:
    def __init__(self):
        self.pushed = []

    def eventPush(self, *args, **kwargs):
        self.pushed.append(args)


class _Dataset:
    fingerprint = "ds-fp"


def _result(value):
    return MetricResult(
        metric_id="t61.m", shape="scalar", dtype="float64", unit="energy",
        compute_parameters={}, implementation_hash="h", checksum="c",
        values=np.array(value),
    )


class _ChangingExecutor:
    """Runs return ``values`` in turn; before run n, ``on_run[n]`` (if any) is
    called, standing in for a metric save that lands mid-computation."""

    def __init__(self, values, on_run=None):
        self.values = list(values)
        self.on_run = on_run or {}
        self.runs = 0

    def run(self, id, source, parameters):
        n = self.runs
        self.runs += 1
        result = _result(self.values[min(n, len(self.values) - 1)])
        if n in self.on_run:
            self.on_run[n]()     # the change lands while the run is in flight
        return result


def _touch():
    """Raise default_registry.revision the way a metric save does."""
    default_registry.metric(id="t61.tmp", inputs={"x": "reference.energy"},
                            shape="scalar", unit="energy")(_fn)
    default_registry.unregister("t61.tmp")


@pytest.fixture
def make():
    def build(executor):
        events = _Events()
        data = DataService(cache={}, models={}, datasets={}, tm=None,
                           events=events, executor=executor)
        return data, events
    yield build
    if default_registry.has("t61.tmp"):
        default_registry.unregister("t61.tmp")


def test_a_run_that_overlaps_a_change_is_discarded_and_run_again(make):
    data, _ = make(_ChangingExecutor([1.0, 2.0], on_run={0: _touch}))
    key = data.generateMetric("ffast.energy_reference", {}, None, _Dataset())
    assert isinstance(key, str) and key
    assert float(data.cache[key].values) == 2.0
    assert len(data.cache) == 1                      # the first run left nothing behind


def test_it_gives_up_after_three_attempts_and_stores_nothing(make):
    ex = _ChangingExecutor([1.0], on_run={0: _touch, 1: _touch, 2: _touch})
    data, _ = make(ex)
    assert data.generateMetric("ffast.energy_reference", {}, None, _Dataset()) is None
    assert ex.runs == 3
    assert data.cache == {}


def test_a_given_key_and_a_change_during_the_run_stores_nothing(make):
    ex = _ChangingExecutor([1.0], on_run={0: _touch})
    data, _ = make(ex)
    assert data.generateMetric("ffast.energy_reference", {}, None, _Dataset(), key="k") is None
    assert ex.runs == 1
    assert data.cache == {}


def test_an_unchanged_run_stores_and_returns_the_key(make):
    data, events = make(_ChangingExecutor([5.0]))
    key = data.generateMetric("ffast.energy_reference", {}, None, _Dataset())
    assert float(data.cache[key].values) == 5.0
    assert ("DATA_UPDATED", key) in events.pushed

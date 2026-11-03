"""The server log says where a metric request spends its time outside the
metric's own compute (ADR 0079 Consequences: a slow step stays visible)."""

from __future__ import annotations

import logging

import numpy as np

from ffast.metrics.cache import MetricCache
from ffast.metrics.execution import FlatInputSource, build_execution_plan, run_plan
from ffast.metrics.registry import MetricRegistry


def _double(x):
    return x * 2


def _registry():
    reg = MetricRegistry()
    reg.metric(id="t.double", inputs={"x": "ref.x"}, shape="scalar", unit="energy")(_double)
    return reg


def _run(reg, cache):
    plan = build_execution_plan(reg, "t.double", {}, FlatInputSource({"x": np.ones(4)}))
    return run_plan(plan, reg, cache, lambda mid, schema, fn, kw, cp: fn(**kw, **cp))


def test_a_computed_step_logs_its_key_cache_checksum_and_store_times(caplog):
    caplog.set_level(logging.INFO, logger="ffast.metrics.execution")
    _run(_registry(), MetricCache())
    line = next(r.getMessage() for r in caplog.records if "around compute" in r.getMessage())
    for part in ("t.double", "key", "cache read", "checksum", "cache write"):
        assert part in line


def test_a_cache_hit_logs_how_long_it_took(caplog):
    reg, cache = _registry(), MetricCache()
    _run(reg, cache)
    caplog.set_level(logging.INFO, logger="ffast.metrics.execution")
    _run(reg, cache)
    assert any("t.double" in r.getMessage() and "from cache" in r.getMessage()
               for r in caplog.records)


def test_building_a_plan_logs_how_long_its_inputs_took(caplog):
    caplog.set_level(logging.INFO, logger="ffast.metrics.execution")
    build_execution_plan(_registry(), "t.double", {}, FlatInputSource({"x": np.ones(4)}))
    assert any("t.double" in r.getMessage() and "inputs read" in r.getMessage()
               for r in caplog.records)

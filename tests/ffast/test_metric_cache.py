"""ADR 0061 decision 6: the inner MetricCache is bounded by memory, LRU."""

from __future__ import annotations

import numpy as np

from ffast.metrics.cache import CacheKey, MetricCache
from ffast.metrics.models import MetricResult


def _key(name: str) -> CacheKey:
    return CacheKey(name, "h", frozenset(), frozenset())


def _result(n_floats: int) -> MetricResult:
    return MetricResult(
        metric_id="t.m", shape="N_frames", dtype="float64", unit="energy",
        compute_parameters={}, implementation_hash="h", checksum="c",
        values=np.zeros(n_floats),      # 8 bytes each
    )


def test_adding_past_the_limit_drops_the_oldest_entry():
    cache = MetricCache(max_bytes=200)          # room for two 80-byte entries
    for name in "abc":
        cache.put(_key(name), _result(10))
    assert cache.get(_key("a")) is None
    assert cache.get(_key("b")) is not None and cache.get(_key("c")) is not None


def test_get_makes_an_entry_the_newest():
    cache = MetricCache(max_bytes=200)
    cache.put(_key("a"), _result(10))
    cache.put(_key("b"), _result(10))
    cache.get(_key("a"))                         # a is now newer than b
    cache.put(_key("c"), _result(10))
    assert cache.get(_key("b")) is None
    assert cache.get(_key("a")) is not None


def test_an_entry_larger_than_the_limit_is_not_stored():
    cache = MetricCache(max_bytes=100)
    cache.put(_key("small"), _result(5))
    cache.put(_key("big"), _result(100))        # 800 bytes
    assert cache.get(_key("big")) is None
    assert cache.get(_key("small")) is not None  # and it evicted nothing


def test_replacing_a_key_does_not_double_count_it():
    cache = MetricCache(max_bytes=100)
    for _ in range(5):
        cache.put(_key("a"), _result(10))        # 80 bytes each time
    assert cache.get(_key("a")) is not None


def test_clear_resets_the_total():
    cache = MetricCache(max_bytes=100)
    cache.put(_key("a"), _result(10))
    cache.clear()
    cache.put(_key("b"), _result(10))            # would not fit if the total stayed 80
    assert cache.get(_key("b")) is not None and cache.get(_key("a")) is None


def test_the_default_limit_is_a_quarter_of_memory_and_at_least_one_gibibyte():
    """A 1 GiB limit could not keep the 4 GB force difference of 1.77M
    structures, so every force metric recomputed it (2026-11-02)."""
    from ffast.metrics.cache import DEFAULT_MAX_BYTES, _memory_bytes
    total = _memory_bytes()
    assert DEFAULT_MAX_BYTES == max(1 << 30, total // 4 if total else 0)
    assert MetricCache()._max_bytes == DEFAULT_MAX_BYTES

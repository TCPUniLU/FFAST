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


# ── results that do not fit in memory go to disk (2026-11-02) ────────────────
# A result dropped from memory used to be computed again: ~25 s for the
# force difference of 1.77M structures; reading it back from disk takes
# seconds. The user is told once per metric.

def _spilling(tmp_path, max_bytes=200, spill_max_bytes=10_000):
    return MetricCache(max_bytes=max_bytes, spill_dir=tmp_path,
                       spill_min_bytes=1, spill_max_bytes=spill_max_bytes)


def _filled(n, value):
    r = _result(n)
    r.values[:] = value
    return r


def test_a_result_pushed_out_of_memory_is_read_back_from_disk(tmp_path, caplog):
    cache = _spilling(tmp_path)
    cache.put(_key("a"), _filled(10, 1.0))
    cache.put(_key("b"), _filled(10, 2.0))
    cache.put(_key("c"), _filled(10, 3.0))       # a no longer fits in memory
    back = cache.get(_key("a"))
    assert back is not None and back.metric_id == "t.m"
    np.testing.assert_array_equal(back.values, np.full(10, 1.0))
    assert len(list(tmp_path.iterdir())) == 1
    assert "moved to disk" in caplog.text and "t.m" in caplog.text


def test_a_result_larger_than_memory_goes_straight_to_disk(tmp_path):
    cache = _spilling(tmp_path, max_bytes=100)
    cache.put(_key("big"), _filled(100, 7.0))    # 800 bytes
    np.testing.assert_array_equal(cache.get(_key("big")).values, np.full(100, 7.0))


def test_each_metric_is_warned_about_once(tmp_path, caplog):
    cache = _spilling(tmp_path, max_bytes=100)
    for name in "abc":
        cache.put(_key(name), _filled(100, 1.0))   # all the same metric id
    assert caplog.text.count("moved to disk") == 1


def test_when_the_disk_share_is_full_the_oldest_file_goes(tmp_path, caplog):
    cache = _spilling(tmp_path, max_bytes=100, spill_max_bytes=1000)
    cache.put(_key("a"), _filled(100, 1.0))      # 800 bytes on disk
    cache.put(_key("b"), _filled(100, 2.0))      # 800 more: a is deleted
    assert cache.get(_key("a")) is None
    np.testing.assert_array_equal(cache.get(_key("b")).values, np.full(100, 2.0))
    assert len(list(tmp_path.iterdir())) == 1
    assert "compute it again" in caplog.text


def test_clear_deletes_the_files(tmp_path):
    cache = _spilling(tmp_path, max_bytes=100)
    cache.put(_key("big"), _filled(100, 7.0))
    cache.clear()
    assert cache.get(_key("big")) is None
    assert list(tmp_path.iterdir()) == []


def test_small_results_are_not_written_to_disk(tmp_path):
    cache = MetricCache(max_bytes=100, spill_dir=tmp_path)   # default: 100 MB and up
    cache.put(_key("big"), _result(100))
    assert cache.get(_key("big")) is None and list(tmp_path.iterdir()) == []


def test_a_memory_backed_folder_is_not_spilled_to(tmp_path, monkeypatch, caplog):
    """On a MeluXina node /tmp is tmpfs (2026-11-02): a file there takes the
    same memory the cache let go of."""
    import ffast.metrics.cache as cache_module
    monkeypatch.setattr(cache_module, "_memory_backed", lambda path: True)
    cache = _spilling(tmp_path, max_bytes=100)
    cache.put(_key("big"), _filled(100, 7.0))
    assert cache.get(_key("big")) is None and list(tmp_path.iterdir()) == []
    assert "memory-backed" in caplog.text and "FFAST_METRIC_CACHE_DIR" in caplog.text


def test_tmpfs_and_ramfs_are_memory_backed():
    from ffast.metrics.cache import _memory_backed
    mounts = ("rootfs / tmpfs rw 0 0\n"
              "/dev/nvme0n1 /scratch xfs rw 0 0\n"
              "none /dev/shm ramfs rw 0 0\n")
    assert _memory_backed("/tmp/ffast-metric-cache-1", mounts) is True
    assert _memory_backed("/scratch/me/cache", mounts) is False
    assert _memory_backed("/dev/shm/x", mounts) is True
    assert _memory_backed("/anywhere", "") is False      # no mount table (macOS)


def test_the_folder_can_be_chosen_with_an_environment_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("FFAST_METRIC_CACHE_DIR", str(tmp_path / "spill"))
    cache = MetricCache(max_bytes=100, spill_min_bytes=1, spill_max_bytes=10_000)
    cache.put(_key("big"), _filled(100, 7.0))
    assert len(list((tmp_path / "spill").iterdir())) == 1

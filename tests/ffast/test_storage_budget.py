"""Shared admission and distinct workstation/job resource policies."""

import gc

import numpy as np
import pytest

from ffast.io import budget as resources
from ffast.io.extxyz_arrays import NotEnoughMemory
from ffast.config.models import ProjectConfig


def test_laptop_and_slurm_defaults_use_different_limits(monkeypatch):
    gib = 2**30
    monkeypatch.delenv("FFAST_MEMORY_BYTES", raising=False)
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    monkeypatch.setattr(resources, "total_memory", lambda: 16 * gib)
    monkeypatch.setattr(resources, "available_memory", lambda: 16 * gib)
    monkeypatch.setattr(resources, "cgroup_headroom", lambda: None)
    monkeypatch.setattr(resources, "slurm_headroom", lambda: None)
    assert resources.Budget().limit == int(16 * gib * 0.80)
    monkeypatch.setattr(resources, "total_memory", lambda: 2 * gib)
    assert resources.Budget().limit == int(1.5 * gib)  # preserve 512 MiB headroom
    monkeypatch.setattr(resources, "total_memory", lambda: 16 * gib)
    monkeypatch.setattr(resources, "cgroup_headroom", lambda: 8 * gib)
    assert resources.Budget().limit == int(8 * gib * 0.85)
    monkeypatch.setattr(resources, "cgroup_headroom", lambda: 2 * gib)
    assert resources.Budget().limit == int(1.5 * gib)
    assert resources.Budget(ram_bytes=100 * gib).limit == 2 * gib


def test_workstation_limit_follows_total_memory_not_free_memory(monkeypatch):
    gib = 2**30
    monkeypatch.delenv("FFAST_MEMORY_BYTES", raising=False)
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    monkeypatch.setattr(resources, "total_memory", lambda: 16 * gib)
    monkeypatch.setattr(resources, "cgroup_headroom", lambda: None)
    monkeypatch.setattr(resources, "slurm_headroom", lambda: None)
    limits = set()
    for free in (1 * gib, 8 * gib, 15 * gib):
        monkeypatch.setattr(resources, "available_memory", lambda free=free: free)
        limits.add(resources.Budget().limit)
    assert limits == {int(16 * gib * 0.80)}


def test_explicit_settings_are_used_as_given_on_a_workstation(monkeypatch):
    gib = 2**30
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    monkeypatch.setattr(resources, "total_memory", lambda: 16 * gib)
    monkeypatch.setattr(resources, "available_memory", lambda: 1 * gib)
    monkeypatch.setattr(resources, "cgroup_headroom", lambda: None)
    monkeypatch.setattr(resources, "slurm_headroom", lambda: None)
    assert resources.Budget(ram_bytes=3 * gib).limit == 3 * gib
    monkeypatch.setenv("FFAST_MEMORY_BYTES", str(5 * gib))
    assert resources.Budget().limit == 5 * gib


def test_fits_compares_with_the_limit_and_ignores_free_memory(monkeypatch):
    owner = resources.Budget(ram_bytes=10_000)
    with owner.reserve(4000):
        monkeypatch.setattr(resources, "available_memory", lambda: 1)
        assert owner.fits(6000)
        assert not owner.fits(6001)
    assert owner.fits(10_000)
    assert not owner.fits(10_001)


def test_allocations_and_active_work_share_one_limit():
    owner = resources.Budget(ram_bytes=10_000)
    array = owner.retain(np.zeros(1000))
    with owner.reserve(1000):
        with pytest.raises(NotEnoughMemory):
            owner.check(2000)
    assert owner.used == 8000
    view = array[:5]
    del array
    gc.collect()
    assert owner.used == 8000
    del view
    gc.collect()
    assert owner.used == 0


def test_owned_arrays_take_over_their_allocation_reservation():
    owner = resources.Budget(ram_bytes=10_000)
    with owner.reserve(9000) as lease:
        array = owner.retain(np.zeros(1000), lease)
        assert owner.used == 9000 and owner.pending == 1000
    assert owner.used == 8000 and owner.pending == 0


def test_project_budgets_allow_disabling_disk_but_not_ram():
    config = ProjectConfig.model_validate(
        {
            "resources": {
                "ram_bytes": 2**30,
                "parsed_cache_bytes": 0,
                "result_cache_bytes": 0,
            }
        }
    )
    assert config.resources.parsed_cache_bytes == 0
    with pytest.raises(ValueError):
        ProjectConfig.model_validate({"resources": {"ram_bytes": 0}})


def test_full_array_and_batch_caches_share_the_parsed_disk_limit(
    tmp_path, monkeypatch
):
    from ffast.io import frame_cache

    owner = resources.Budget(ram_bytes=2**20, parsed_bytes=10_000)
    monkeypatch.setattr(resources, "_budget", owner)
    batches = tmp_path / "batches"
    batches.mkdir()
    (batches / "one.npz").write_bytes(b"x" * 8000)
    assert frame_cache.max_bytes(tmp_path) == 2000


def test_a_slurm_job_without_a_visible_limit_falls_back_to_free_memory(monkeypatch, caplog):
    """Whole-node jobs (--mem=0) expose no limit; the server must still start."""
    from ffast.io import budget as shared

    monkeypatch.setenv("SLURM_JOB_ID", "1")
    monkeypatch.delenv("FFAST_MEMORY_BYTES", raising=False)
    monkeypatch.setattr(shared, "cgroup_headroom", lambda: None)
    monkeypatch.setattr(shared, "slurm_headroom", lambda: None)
    monkeypatch.setattr(shared, "total_memory", lambda: 10 * 2**30)
    with caplog.at_level("WARNING"):
        owner = shared.Budget()
    assert owner.limit == 8 * 2**30          # the workstation rule: 80% of total memory
    assert "FFAST_MEMORY_BYTES" in caplog.text


def test_free_memory_is_asked_outside_the_lock_and_only_for_large_requests(monkeypatch):
    """Asking took seconds on a busy cluster node; every thread that wanted
    memory waited behind the lock for it (MeluXina, 2026-11-24)."""
    import threading

    owner = resources.Budget(ram_bytes=64 * 2**20)
    asked = []

    def free():
        other = []

        def probe():
            other.append(owner.lock.acquire(blocking=False))
            if other[0]:
                owner.lock.release()

        t = threading.Thread(target=probe)
        t.start()
        t.join()
        asked.append(other[0])
        return 2**20

    monkeypatch.setattr(resources, "available_memory", free)
    small = owner.block_bytes
    with owner.reserve(small):
        pass
    owner.check(small)
    owner.retain(np.zeros(small // 8))
    assert asked == []
    with pytest.raises(NotEnoughMemory):
        owner.check(small + 1)
    with pytest.raises(NotEnoughMemory):
        with owner.reserve(small + 1):
            pass
    with pytest.raises(NotEnoughMemory):
        owner.retain(np.zeros(small // 8 + 1))
    assert asked == [True, True, True]

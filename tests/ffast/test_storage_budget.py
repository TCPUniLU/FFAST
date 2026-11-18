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
    monkeypatch.setattr(resources, "available_memory", lambda: 16 * gib)
    monkeypatch.setattr(resources, "cgroup_headroom", lambda: None)
    monkeypatch.setattr(resources, "slurm_headroom", lambda: None)
    assert resources.Budget().limit == int(16 * gib * 0.80)
    monkeypatch.setattr(resources, "available_memory", lambda: 2 * gib)
    assert resources.Budget().limit == int(1.5 * gib)  # preserve 512 MiB headroom
    monkeypatch.setattr(resources, "available_memory", lambda: 16 * gib)
    monkeypatch.setattr(resources, "cgroup_headroom", lambda: 8 * gib)
    assert resources.Budget().limit == int(8 * gib * 0.85)
    monkeypatch.setattr(resources, "cgroup_headroom", lambda: 2 * gib)
    assert resources.Budget().limit == int(1.5 * gib)
    assert resources.Budget(ram_bytes=100 * gib).limit == 2 * gib


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

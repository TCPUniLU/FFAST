"""The Slurm memory check reuses its process-tree walk for a moment.

On a MeluXina node psutil found this process's children by reading every
process under /proc: 44 ms a call, before each of 180 groups of a 48 GB
parse = 7.9 s of 31 s (2026-11-21)."""
import psutil
import pytest

from ffast.metrics import resources


@pytest.fixture
def walks(monkeypatch):
    """Each walk of the process tree, and a clock the test moves."""
    calls = []
    real = psutil.Process.children
    monkeypatch.setattr(psutil.Process, "children",
                        lambda self, recursive=False: calls.append(1) or real(self, recursive=recursive))
    now = [1000.0]
    monkeypatch.setattr(resources, "_clock", lambda: now[0])
    monkeypatch.setattr(resources, "_headroom_memo", None)
    monkeypatch.setenv("SLURM_MEM_PER_NODE", str(64 * 1024))      # MiB
    return calls, now


def test_calls_within_the_reuse_time_walk_the_tree_once(walks):
    calls, now = walks
    first = resources.slurm_headroom()
    now[0] += resources.HEADROOM_REUSE_S / 2
    assert resources.slurm_headroom() == first
    assert len(calls) == 1


def test_a_call_after_the_reuse_time_walks_again(walks):
    calls, now = walks
    resources.slurm_headroom()
    now[0] += resources.HEADROOM_REUSE_S + 0.01
    resources.slurm_headroom()
    assert len(calls) == 2


def test_a_changed_allocation_is_not_answered_from_the_last_walk(walks, monkeypatch):
    calls, now = walks
    small = resources.slurm_headroom()
    monkeypatch.setenv("SLURM_MEM_PER_NODE", str(128 * 1024))
    assert resources.slurm_headroom() > small
    assert len(calls) == 2


def test_the_reuse_time_is_short():
    assert 0 < resources.HEADROOM_REUSE_S <= 1.0

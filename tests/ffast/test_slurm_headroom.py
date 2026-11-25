"""The Slurm memory check reuses its process-tree walk for a moment.

On a MeluXina node psutil found this process's children by reading every
process under /proc: 44 ms a call, before each of 180 groups of a 48 GB
parse = 7.9 s of 31 s (2026-11-21). While Basic Errors computed on 32
threads one walk took 7.5-9.9 s, and 16-18 threads walked at once: 55-67 s
of thread time (2026-11-25). An outdated answer is now refreshed by one
walk in the background while callers use the last answer."""
import threading

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
    monkeypatch.setattr(resources, "_refreshing", None)
    monkeypatch.setenv("SLURM_MEM_PER_NODE", str(64 * 1024))      # MiB
    return calls, now


def test_calls_within_the_reuse_time_walk_the_tree_once(walks):
    calls, now = walks
    first = resources.slurm_headroom()
    now[0] += resources.HEADROOM_REUSE_S / 2
    assert resources.slurm_headroom() == first
    assert len(calls) == 1


def test_a_call_after_the_reuse_time_walks_again_in_the_background(walks):
    calls, now = walks
    first = resources.slurm_headroom()
    now[0] += resources.HEADROOM_REUSE_S + 0.01
    assert resources.slurm_headroom() == first      # the last answer, at once
    resources._refreshing.join(10)
    assert len(calls) == 2
    assert resources._headroom_memo[0] == now[0]   # the walk's own answer


def test_callers_during_a_slow_walk_use_the_last_answer_and_start_no_other_walk(walks, monkeypatch):
    calls, now = walks
    first = resources.slurm_headroom()
    started, release = threading.Event(), threading.Event()
    real = resources._walk_headroom

    def slow(amount):
        started.set()
        release.wait(10)
        return real(amount)

    monkeypatch.setattr(resources, "_walk_headroom", slow)
    now[0] += resources.HEADROOM_REUSE_S + 0.01
    answers = [resources.slurm_headroom() for _ in range(20)]
    assert started.wait(10)
    assert answers == [first] * 20
    assert resources._refreshing.is_alive()
    release.set()
    resources._refreshing.join(10)
    assert len(calls) == 2


def test_a_changed_allocation_is_not_answered_from_the_last_walk(walks, monkeypatch):
    calls, now = walks
    small = resources.slurm_headroom()
    monkeypatch.setenv("SLURM_MEM_PER_NODE", str(128 * 1024))
    assert resources.slurm_headroom() > small
    assert len(calls) == 2


def test_the_reuse_time_is_short():
    assert 0 < resources.HEADROOM_REUSE_S <= 1.0

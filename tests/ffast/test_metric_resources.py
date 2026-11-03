"""Cores, memory gate and memory estimates (ADR 0079 decision 2)."""

from __future__ import annotations

import os
import threading
import time

import numpy as np
import pytest

from ffast.metrics import dims, resources
from ffast.metrics.execution import FlatInputSource
from ffast.metrics.expr import compile_expr_metric
from ffast.metrics.registry import MetricRegistry
from ffast.metrics.resources import MemoryGate, cores
from ffast.metrics.server_executor import ServerExecutor, _estimate


def test_cores():
    assert cores() >= 1


@pytest.mark.skipif(not hasattr(os, "sched_getaffinity"), reason="Linux only")
def test_cores_follow_the_job_cpus(monkeypatch):
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: {0, 1, 2})
    assert cores() == 3


def test_cores_without_affinity(monkeypatch):
    monkeypatch.delattr(os, "sched_getaffinity", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 7)
    assert cores() == 7


def _gate(available=1000):
    return MemoryGate(available=lambda: available, share=1.0)


def _reserve_in_thread(gate, n, out):
    def go():
        res = gate.reserve(n)
        out.append(time.monotonic())
        res.release()
    t = threading.Thread(target=go)
    t.start()
    return t


def test_two_steps_that_fit_start_together():
    gate = _gate(2 * 2**20 * 1000)
    a = gate.reserve(600 * 2**20)
    b = gate.reserve(300 * 2**20)
    assert a is not None and b is not None


def test_a_step_that_does_not_fit_waits_for_a_release():
    gate = _gate(2**30)
    a = gate.reserve(600 * 2**20)
    out = []
    t = _reserve_in_thread(gate, 600 * 2**20, out)
    time.sleep(0.3)
    assert out == []
    released = time.monotonic()
    a.release()
    t.join(2)
    assert out and out[0] >= released


def test_a_step_bigger_than_the_cap_runs_alone():
    gate = _gate(1000)
    big = gate.reserve(5000)
    assert big is not None
    out = []
    t = _reserve_in_thread(gate, 2**20, out)
    time.sleep(0.3)
    assert out == []
    big.release()
    t.join(2)
    assert out


def test_the_cap_is_measured_when_the_gate_goes_from_empty_to_busy():
    seen = {"value": 10 * 2**20}
    gate = MemoryGate(available=lambda: seen["value"], share=1.0)
    a = gate.reserve(6 * 2**20)
    seen["value"] = 100 * 2**20             # not looked at while busy
    out = []
    t = _reserve_in_thread(gate, 6 * 2**20, out)
    time.sleep(0.3)
    assert out == []
    a.release()                              # empty again: the new value is used
    t.join(2)
    assert out


def test_a_waiter_that_is_stopped_gives_up():
    gate = _gate(2**20)
    held = gate.reserve(2**20)
    flag = {"stop": False}
    threading.Timer(0.2, lambda: flag.update(stop=True)).start()
    start = time.monotonic()
    assert gate.reserve(2**20, stopped=lambda: flag["stop"]) is None
    assert time.monotonic() - start < 1.5
    held.release()


# ── steps ────────────────────────────────────────────────────────────────

def _nap(x):
    time.sleep(1.0)
    return 1.0


def _registry(memory_mb=None):
    reg = MetricRegistry()
    hints = {"memory_mb": memory_mb} if memory_mb else None
    reg.metric(id="t.nap", inputs={"x": "ref.x"}, shape="scalar", unit="energy", hints=hints)(_nap)
    return reg


def _run_two(executor):
    start = time.monotonic()
    threads = [threading.Thread(target=executor.run, args=("t.nap", FlatInputSource({"x": float(i)}), {}))
               for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return time.monotonic() - start


def test_two_in_server_steps_run_at_the_same_time():
    ex = ServerExecutor(_registry(), outside=lambda fn: False)
    assert _run_two(ex) < 1.8


def test_a_big_memory_hint_makes_steps_take_turns():
    gate = MemoryGate(available=lambda: 100 * 2**20, share=1.0)
    ex = ServerExecutor(_registry(memory_mb=60), outside=lambda fn: False, gate=gate)
    assert _run_two(ex) >= 2.0


# ── estimates ────────────────────────────────────────────────────────────

def _formula(expr):
    reg = MetricRegistry()
    for name in ("a", "b"):
        reg.metric(id=f"t.{name}", inputs={"x": f"reference.{name}"},
                   shape=(dims.N_frames,), unit="energy")(lambda x: x)
    mid = compile_expr_metric("t.f", expr, {"a": "t.a", "b": "t.b"}, registry=reg)
    return reg.get(mid)


def _plain(a, b):
    return a


def test_formula_estimate_counts_its_operations():
    mb8 = np.zeros(2**20)               # 8 MiB
    kw = {"a": mb8, "b": mb8}
    schema, fn = _formula("abs(a - b) * 2")
    assert fn.operations == 3
    assert _estimate(schema, fn, kw) == 4 * mb8.nbytes
    schema, fn = _formula("a")
    assert _estimate(schema, fn, kw) == 1 * mb8.nbytes


def test_other_estimates():
    mb8 = np.zeros(2**20)
    kw = {"a": mb8, "b": mb8}
    reg = MetricRegistry()
    reg.metric(id="t.p", inputs={"a": "ref.a", "b": "ref.b"}, shape="scalar", unit="energy")(_plain)
    assert _estimate(reg.get("t.p")[0], _plain, kw) == 2 * 2 * mb8.nbytes
    reg2 = MetricRegistry()
    reg2.metric(id="t.p", inputs={"a": "ref.a", "b": "ref.b"}, shape="scalar", unit="energy",
                hints={"memory_mb": 5})(_plain)
    assert _estimate(reg2.get("t.p")[0], _plain, kw) == 5 * 2**20
    assert _estimate(None, _plain, {}) == 2**20


# ── cgroup limits (ADR 0083) ────────────────────────────────────────────────

def _tree(tmp_path, cgroup, files):
    proc = tmp_path / "cgroup"
    proc.write_text(cgroup)
    root = tmp_path / "fs"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return str(proc), str(root)


def test_a_slurm_job_limit_under_cgroup_v2(tmp_path):
    proc, root = _tree(tmp_path, "0::/job_1/step_0\n", {
        "job_1/memory.max": str(8 * 2**30),
        "job_1/memory.current": str(6 * 2**30),
        "job_1/memory.stat": f"anon 1\ninactive_file {2**30}\n",
        "job_1/step_0/memory.max": "max",
        "job_1/step_0/memory.current": "5",
    })
    assert resources.cgroup_headroom(proc, root) == 3 * 2**30   # 8 - 6 + 1 of droppable file cache


def test_the_tightest_level_wins(tmp_path):
    proc, root = _tree(tmp_path, "0::/a/b\n", {
        "a/memory.max": "1000", "a/memory.current": "100",
        "a/b/memory.max": "500", "a/b/memory.current": "100",
    })
    assert resources.cgroup_headroom(proc, root) == 400


def test_a_limit_under_cgroup_v1(tmp_path):
    proc, root = _tree(tmp_path, "4:memory:/slurm/uid_1/job_1\n3:cpu,cpuacct:/x\n", {
        "memory/slurm/uid_1/job_1/memory.limit_in_bytes": "1000",
        "memory/slurm/uid_1/job_1/memory.usage_in_bytes": "300",
        "memory/slurm/uid_1/job_1/memory.stat": "total_inactive_file 50\n",
        "memory/memory.limit_in_bytes": "9223372036854771712",   # v1's "no limit"
        "memory/memory.usage_in_bytes": "5",
    })
    assert resources.cgroup_headroom(proc, root) == 750


def test_no_limit_or_no_cgroup_is_none(tmp_path):
    proc, root = _tree(tmp_path, "0::/\n", {"memory.max": "max", "memory.current": "1"})
    assert resources.cgroup_headroom(proc, root) is None
    assert resources.cgroup_headroom(str(tmp_path / "absent"), root) is None

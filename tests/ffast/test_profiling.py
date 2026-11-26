"""Profiling must retain failure evidence and exercise the real dashboard."""

import json
import os
import subprocess
import sys

import pytest

from ffast.profiling import Profile


def test_failure_retains_trace_and_restores_hooks(tmp_path):
    from ffast.metrics.resources import MemoryGate
    from ffast.readers import indexed

    original = MemoryGate.reserve
    source_lock = indexed._parse_lock
    folder = tmp_path / "failure"
    with pytest.raises(RuntimeError, match="probe failed"):
        with Profile(folder, interval=0.01) as profile:
            assert MemoryGate.reserve is not original
            assert indexed._parse_lock is not source_lock
            with indexed._parse_lock:
                with indexed._parse_lock:  # retain the source lock's reentrancy
                    pass
            with profile.span("probe"):
                raise RuntimeError("probe failed")
    assert MemoryGate.reserve is original
    assert indexed._parse_lock is source_lock
    summary = json.loads((folder / "summary.json").read_text())
    assert not summary["completed"]
    assert summary["error"] == "probe failed"
    assert next(a for a in summary["phases"] if a["name"] == "probe")["failures"] == 1
    assert next(a for a in summary["phases"]
                if a["name"] == "readers.indexed.source_lock_wait")["calls"] == 2
    assert any(json.loads(line)["type"] == "sample"
               for line in (folder / "events.jsonl").read_text().splitlines())


@pytest.mark.parametrize("mode, budget, expected_mode, refused", [
    # Eager load and metrics fit 4 MiB. Memory hints (4096 MB on force parity)
    # once refused these; they must not reserve the shared budget. Not 1 MiB:
    # one dashboard result is 0.7 MB (a 300 x 301 grid) and its reply needs
    # it plus a block, so on a busy machine (4 cores, GitHub CI) a reply that
    # overlapped other work holding 0.8 MiB was refused every run.
    ("auto", 4 * 2**20, "eager", False),
    ("saving", 16 * 2**20, "saving", False),
    # Too small for the replies: the profiler must report the refusals and
    # still measure the next open.
    ("auto", 64 * 2**10, "saving", True),
])
def test_real_dashboard_first_and_repeated_open(tmp_path, mode, budget, expected_mode, refused):
    source = tmp_path / "dashboard.extxyz"
    with source.open("w") as f:
        for i in range(120):
            f.write("3\nProperties=species:S:1:pos:R:3:forces:R:3:forces_so3lr:R:3 "
                    f"energy={i * .01} energy_so3lr={i * .011 + .1}\n")
            for j in range(3):
                delta = 0.01 * (1 + (i + j) % 9)
                f.write(f"C {j} 0 {i*.1} 0.1 0.2 0.3 "
                        f"{.1+delta} {.2+delta} {.3+delta}\n")
    original = source.read_bytes()
    env = os.environ.copy()
    # A small shared budget confined to the child.
    env["FFAST_MEMORY_BYTES"] = str(budget)
    output = tmp_path / "profile"
    completed = subprocess.run([
        sys.executable, "-m", "ffast.profiling", str(source),
        "--output", str(output), "--interval", "0.02", "--timeout", "90",
        "--memory-mode", mode],
        env=env, capture_output=True, text=True, timeout=120)
    if completed.returncode:
        logs = "\n".join(p.read_text() for p in output.glob("*.log"))
        pytest.fail(completed.stdout + completed.stderr + logs)
    assert source.read_bytes() == original
    for i in (1, 2):
        folder = output / f"run-{i}"
        summary = json.loads((folder / "summary.json").read_text())
        assert summary["completed"]
        assert summary["metadata"]["request_count"] == 14
        events = [json.loads(s) for s in (folder / "events.jsonl").read_text().splitlines()]
        phases = {e["phase"]: e for e in events if e.get("name", "").startswith("workload.")}
        assert phases["load"]["frames"] == 120
        assert phases["load"]["memory_mode"] == expected_mode
        for name in ("basic_errors", "basic_errors_cached"):
            assert phases[name]["reply_count"] == 14
            assert phases[name]["dashboard_ok"] == (not phases[name]["errors"])
            if refused:
                assert not phases[name]["dashboard_ok"]
                assert any("MiB of working memory" in e for e in phases[name]["errors"])
            else:
                assert phases[name]["errors"] == []
        if mode == "saving":
            assert any(e.get("streamed") for e in events)
            assert any(e.get("name", "").endswith("MemoryGate.reserve") for e in events)
    assert (output / "report.md").is_file()
    from ffast.profiling_workload import check_targets
    assert check_targets(output, 60)["passed"] is not refused
    assert not check_targets(output, 1e-9)["passed"]


def test_timeout_is_visible_even_without_a_summary(tmp_path):
    source = tmp_path / "never-read.extxyz"
    source.write_text("1\nProperties=species:S:1:pos:R:3\nC 0 0 0\n")
    output = tmp_path / "timeout"
    result = subprocess.run([
        sys.executable, "-m", "ffast.profiling", str(source),
        "--output", str(output), "--timeout", "0.001"],
        capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert json.loads((output / "run-1-status.json").read_text())["state"] == "timed_out"
    assert "Incomplete run: run-1-status: timed_out" in (output / "report.md").read_text()

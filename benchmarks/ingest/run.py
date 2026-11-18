"""Run the profiler once for one side and one data file (ADR 0105 §2)."""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _tree_bytes(folder):
    return sum(p.stat().st_size for p in Path(folder).rglob("*") if p.is_file())


def run_once(side, env, data_file, out_dir, timeout, meta=None):
    """Profile ``data_file`` twice (first open, reopen) into ``out_dir``.

    ``out_dir`` must not exist yet; the profiler makes it. Its cache folder is
    measured and removed afterwards, everything else stays. Returns ``out_dir``.
    """
    out_dir = Path(out_dir)
    command = [sys.executable, "-m", "ffast.profiling", str(data_file),
               "--output", str(out_dir), "--runs", "2", "--timeout", str(timeout),
               "--energy-key", "energy", "--force-key", "forces",
               "--pred-energy-key", "energy_pred", "--pred-force-key", "forces_pred"]
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    exit_code, output = None, ""
    try:
        done = subprocess.run(command, cwd=side.root, env=env, capture_output=True, text=True,
                              timeout=2 * timeout + 300)
        exit_code, output = done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired as exc:
        exit_code, output = -1, f"harness timeout after {exc.timeout} s"
    finally:
        out_dir.mkdir(parents=True, exist_ok=True)
        cache = out_dir / "cache"
        cache_bytes = _tree_bytes(cache) if cache.is_dir() else 0
        shutil.rmtree(cache, ignore_errors=True)
        (out_dir / "profiler.log").write_text(output)
        (out_dir / "harness.json").write_text(json.dumps({
            **(meta or {}), "side": side.name, "exit_code": exit_code,
            "cache_bytes": cache_bytes,
            "wall_seconds": time.perf_counter() - started}, indent=2) + "\n")
    return out_dir

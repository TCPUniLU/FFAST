"""Time a portable bundle against the parsed cache (ADR 0110, Validation).

For each case and repeat, on this checkout:

1. export with an empty parsed cache (cold) and again with the cache the first
   export left (warm): time and size of the bundle;
2. open the bundle and run Basic Errors with the profiler (first open, then a
   reopen), with the source file out of reach of the profiler's cache;
3. open the source file the ordinary way, for its first open and its reopen
   from the parsed cache, to compare with.

Everything is deleted after each repeat except the source files. Results go to
``<output>/results.json`` and a table on screen.
"""

import json
import os
import re
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

from benchmarks.ingest import generate, rows
from benchmarks.ingest.run import HARNESS_ROOT, _tree_bytes, run_once
from benchmarks.ingest.sides import Side, side_env

EXPORT = ("import sys; from ffast.cli.main import main; main(sys.argv[1:])")


def _export(env, source, target, cache, force=False):
    """``(seconds the command reports, bundle bytes)`` of one export."""
    env = {**env, "FFAST_FRAME_ARRAYS_DIR": str(cache)}
    command = [sys.executable, "-c", EXPORT, "cache", "export", str(source), "-o", str(target)]
    if force:
        command.append("--force")
    done = subprocess.run(command, cwd=HARNESS_ROOT, env=env, capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"export failed: {done.stdout}{done.stderr}")
    seconds = float(re.search(r"in ([0-9.]+) s", done.stdout).group(1))
    return seconds, _tree_bytes(target)


def _timed(pair):
    first, reopen = pair
    ok = first["completed"] and reopen["completed"]
    return {"completed": ok, "error": None if ok else (first["error"] or reopen["error"]),
            "first_load_s": first["load_s"], "first_basic_errors_s": first["basic_errors_s"],
            "reopen_load_s": reopen["load_s"], "reopen_basic_errors_s": reopen["basic_errors_s"]}


def _summary(results, key):
    values = [r[key] for r in results if r.get(key) is not None]
    if not values:
        return "n/a"
    q = statistics.quantiles(values, n=4) if len(values) > 1 else [values[0]] * 3
    return (f"median {statistics.median(values):.2f}  min {min(values):.2f}  max {max(values):.2f}"
            f"  IQR {q[0]:.2f}-{q[2]:.2f}")


def run_bundle(args):
    output = Path(args.output).expanduser().resolve()
    if output.exists():
        print(f"error: --output {output} already exists", file=sys.stderr)
        return 2
    env = dict(os.environ)
    if args.memory_bytes:
        env["FFAST_MEMORY_BYTES"] = str(args.memory_bytes)
    side = Side("new", HARNESS_ROOT)
    env, info = side_env(side, HARNESS_ROOT, env)
    output.mkdir(parents=True)
    all_results = {}
    for size in args.sizes:
        for atoms in args.classes:
            for counts in args.counts:
                case = generate.Case(size, atoms, counts, 0)
                source, manifest = generate.ensure(case, args.data_dir, workers=4)
                results = []
                for repeat in range(1, args.repeats + 1):
                    work = output / "work" / case.name / f"r{repeat}"
                    cache, bundle_dir = work / "cache", work / "a.ffast"
                    work.mkdir(parents=True)
                    row = {"repeat": repeat}
                    try:
                        row["cold_export_s"], row["bundle_bytes"] = _export(env, source, bundle_dir, cache)
                        row["warm_export_s"], _ = _export(env, source, work / "b.ffast", cache)
                        shutil.rmtree(cache, ignore_errors=True)
                        shutil.rmtree(work / "b.ffast", ignore_errors=True)
                        raw = output / "raw" / case.name
                        row["bundle"] = _timed(rows.rows_from_run(run_once(
                            side, env, bundle_dir / "dataset.ffast.json", raw / f"bundle-r{repeat}",
                            args.timeout, {"case": case.name, "scenario": "bundle"})))
                        shutil.rmtree(bundle_dir, ignore_errors=True)
                        row["parsed_cache"] = _timed(rows.rows_from_run(run_once(
                            side, env, source, raw / f"cache-r{repeat}", args.timeout,
                            {"case": case.name, "scenario": "parsed-cache"})))
                    finally:
                        shutil.rmtree(work, ignore_errors=True)
                    results.append(row)
                    print(f"{case.name} r{repeat}: cold export {row['cold_export_s']:.1f} s, warm "
                          f"{row['warm_export_s']:.1f} s, {row['bundle_bytes'] / 2**30:.2f} GB; "
                          f"bundle open {row['bundle']['first_load_s']:.2f} s + BE "
                          f"{row['bundle']['first_basic_errors_s']:.2f} s; cache reopen "
                          f"{row['parsed_cache']['reopen_load_s']:.2f} s + BE "
                          f"{row['parsed_cache']['reopen_basic_errors_s']:.2f} s", flush=True)
                all_results[case.name] = {"manifest": manifest, "runs": results}
                (output / "results.json").write_text(json.dumps(
                    {"commit": info["commit"], "dirty": info["dirty"], "cases": all_results},
                    indent=2) + "\n")
                print(f"== {case.name} ({source.stat().st_size / 2**30:.2f} GB source), "
                      f"{len(results)} repeats")
                for label, key in (("cold export s", "cold_export_s"), ("warm export s", "warm_export_s"),
                                   ("bundle bytes", "bundle_bytes")):
                    print(f"  {label:34s} {_summary(results, key)}")
                for scenario, name, field in (
                        ("bundle", "bundle open s (first)", "first_load_s"),
                        ("bundle", "bundle Basic Errors s (first)", "first_basic_errors_s"),
                        ("bundle", "bundle open s (reopen)", "reopen_load_s"),
                        ("bundle", "bundle Basic Errors s (reopen)", "reopen_basic_errors_s"),
                        ("parsed_cache", "cache reopen s", "reopen_load_s"),
                        ("parsed_cache", "cache reopen Basic Errors s", "reopen_basic_errors_s")):
                    print(f"  {name:34s} "
                          f"{_summary([r[scenario] for r in results], field)}")
    shutil.rmtree(output / "work", ignore_errors=True)
    return 0

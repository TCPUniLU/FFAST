"""Time the strict check against reading the disk (ADR 0111, Validation).

For each case and repeat, on this checkout:

1. export the file (untimed): this loads it once, leaving the parsed cache,
   source digests included, and a bundle;
2. time ``ffast-cli cache verify`` on the source (its parsed cache), on the
   bundle with its source in place, and on the bundle with the source out of
   reach;
3. time ``cat source > /dev/null`` for comparison. The operating system's
   file cache is warm and not controlled, as in the other scenarios.

Each verify reports wall time, the child's peak memory and the bytes it had to
read (the source plus the batch files it opened). Results go to
``<output>/results.json`` and a table on screen. This is spec section 7.3
scenario 4, "cache reopen, strict".
"""

import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

from benchmarks.ingest import generate
from benchmarks.ingest.run import HARNESS_ROOT, _tree_bytes
from benchmarks.ingest.sides import Side, side_env

CHILD = (
    "import json, resource, sys\n"
    "from ffast.cli.main import main\n"
    "code = 0\n"
    "try:\n"
    "    main(sys.argv[1:])\n"
    "except SystemExit as stop:\n"
    "    code = stop.code or 0\n"
    "peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
    "peak = peak if sys.platform == 'darwin' else peak * 1024\n"
    "sys.stderr.write('STATS ' + json.dumps({'code': code, 'peak_rss': peak}) + '\\n')\n"
)
BATCH = re.compile(r"-\d+-\d+\.npz$")


def _env(env, cache):
    return {**env, "FFAST_FRAME_ARRAYS_DIR": str(cache / "parsed"),
            "FFAST_FRAME_INDEX_DIR": str(cache / "index")}


def _cli(env, *args):
    """``(seconds, stdout, stats)`` of one ``ffast-cli`` call."""
    started = time.perf_counter()
    done = subprocess.run([sys.executable, "-c", CHILD, *args], cwd=HARNESS_ROOT, env=env,
                          capture_output=True, text=True)
    seconds = time.perf_counter() - started
    found = re.search(r"STATS (\{.*\})", done.stderr)
    if not found:
        raise RuntimeError(f"{args}: no stats; {done.stdout}{done.stderr}")
    return seconds, done.stdout, json.loads(found.group(1))


def _cat(path):
    started = time.perf_counter()
    subprocess.run(["cat", str(path)], stdout=subprocess.DEVNULL, check=True)
    return time.perf_counter() - started


def _batch_bytes(folder):
    return sum(p.stat().st_size for p in Path(folder).rglob("*.npz") if BATCH.search(p.name))


def _measure(env, target, source_bytes, batches):
    seconds, out, stats = _cli(env, "cache", "verify", str(target))
    return {"seconds": seconds, "peak_rss": stats["peak_rss"], "exit_code": stats["code"],
            "bytes_read": source_bytes + batches, "closing_line": out.strip().splitlines()[-1]}


def _summary(results, key):
    values = [r[key] for r in results if r.get(key) is not None]
    if not values:
        return "n/a"
    q = statistics.quantiles(values, n=4) if len(values) > 1 else [values[0]] * 3
    return (f"median {statistics.median(values):.2f}  min {min(values):.2f}  max {max(values):.2f}"
            f"  IQR {q[0]:.2f}-{q[2]:.2f}")


def run_verify(args):
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
                source = Path(source).resolve()
                source_bytes = source.stat().st_size
                results = []
                for repeat in range(1, args.repeats + 1):
                    work = output / "work" / case.name / f"r{repeat}"
                    work.mkdir(parents=True)
                    scoped = _env(env, work)
                    row = {"repeat": repeat}
                    try:
                        _cli(scoped, "cache", "export", str(source), "-o", str(work / "a.ffast"))
                        batches = _batch_bytes(work / "parsed")
                        row["bundle_bytes"] = _tree_bytes(work / "a.ffast")
                        row["cat_warm_s"] = _cat(source)
                        row["cache"] = _measure(scoped, source, source_bytes, batches)
                        description = work / "a.ffast" / "dataset.ffast.json"
                        row["bundle_with_source"] = _measure(scoped, description, source_bytes,
                                                             row["bundle_bytes"])
                        hidden = source.with_name(source.name + ".hidden")
                        os.replace(source, hidden)
                        try:
                            row["bundle_alone"] = _measure(scoped, description, 0, row["bundle_bytes"])
                        finally:
                            os.replace(hidden, source)
                        row["cat_warm_after_s"] = _cat(source)
                    finally:
                        shutil.rmtree(work, ignore_errors=True)
                    results.append(row)
                    print(f"{case.name} r{repeat}: cat {row['cat_warm_s']:.1f} s; verify cache "
                          f"{row['cache']['seconds']:.1f} s, bundle+source "
                          f"{row['bundle_with_source']['seconds']:.1f} s, bundle alone "
                          f"{row['bundle_alone']['seconds']:.1f} s; exits "
                          f"{row['cache']['exit_code']}/{row['bundle_with_source']['exit_code']}/"
                          f"{row['bundle_alone']['exit_code']}", flush=True)
                all_results[case.name] = {"manifest": manifest, "runs": results}
                (output / "results.json").write_text(json.dumps(
                    {"commit": info["commit"], "dirty": info["dirty"], "cases": all_results},
                    indent=2) + "\n")
                print(f"== {case.name} ({source_bytes / 2**30:.2f} GB source), {len(results)} repeats")
                flat = [{"cat": r["cat_warm_s"], **{f"{k}_{f}": r[k][f] for k in
                         ("cache", "bundle_with_source", "bundle_alone")
                         for f in ("seconds", "peak_rss")}} for r in results]
                for label, key in (("cat source s (warm)", "cat"),
                                   ("verify source cache s", "cache_seconds"),
                                   ("verify bundle + source s", "bundle_with_source_seconds"),
                                   ("verify bundle alone s", "bundle_alone_seconds"),
                                   ("verify source cache peak B", "cache_peak_rss"),
                                   ("verify bundle alone peak B", "bundle_alone_peak_rss")):
                    print(f"  {label:34s} {_summary(flat, key)}")
    shutil.rmtree(output / "work", ignore_errors=True)
    return 0

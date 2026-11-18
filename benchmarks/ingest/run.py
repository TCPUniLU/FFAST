"""Run the profiler once for one side and one data file (ADR 0105 §2)."""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from benchmarks.ingest import generate, rows
from benchmarks.ingest.sides import SideError, check_side, parse_sides, side_env

HARNESS_ROOT = Path(__file__).resolve().parents[2]


def _tree_bytes(folder):
    return sum(p.stat().st_size for p in Path(folder).rglob("*") if p.is_file())


def run_once(side, env, data_file, out_dir, timeout, meta=None):
    """Profile ``data_file`` twice (first open, reopen) into ``out_dir``.

    ``out_dir`` must not exist yet; the profiler makes it. Its cache folder is
    measured and removed afterwards, everything else stays. Returns ``out_dir``.
    """
    # The profiler runs inside the side's folder, so relative paths must be made absolute here.
    out_dir = Path(out_dir).resolve()
    data_file = Path(data_file).resolve()
    command =[sys.executable, "-m", "ffast.profiling", str(data_file),
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


def _cases(args):
    for name, choices, allowed in (("sizes", args.sizes, generate.SIZES),
                                   ("classes", args.classes, generate.CLASSES),
                                   ("counts", args.counts, generate.COUNTS)):
        bad = [c for c in choices if c not in allowed]
        if bad:
            raise SideError(f"unknown --{name} value(s): {', '.join(bad)}; "
                            f"choose from {', '.join(allowed)}")
    cases = [generate.Case(size, atoms, counts, getattr(args, "seed", 0))
             for size in args.sizes for atoms in args.classes for counts in args.counts]
    # Smallest first, so a mistake shows up quickly.
    return sorted(cases, key=lambda c: (generate.SIZES[c.size], list(generate.CLASSES).index(c.atoms),
                                        generate.COUNTS.index(c.counts)))


def _line(case, side, repeat, pair):
    first, reopen = pair
    if not (first["completed"] and reopen["completed"]):
        error = first["error"] or reopen["error"]
        return f"{case.name} {side} r{repeat}: FAILED {error}"
    return (f"{case.name} {side} r{repeat}: load {first['load_s']:.1f} s, "
            f"BE {first['basic_errors_s']:.1f} s, reopen {reopen['load_s']:.1f} s")


def run_matrix(args):
    """The ``run`` subcommand: alternate the sides, ABBA, one case at a time."""
    output = Path(args.output).expanduser().resolve()
    try:
        sides = parse_sides(args.side)
        cases = _cases(args)
        if args.repeats < 1:
            raise SideError("--repeats must be at least 1")
        if output.exists():
            raise SideError(f"--output {output} already exists")
        envs = {}
        base_env = dict(os.environ)
        memory = getattr(args, "memory_bytes", 0)
        if memory:
            # The same budget for every run: FFAST's own choice follows the RAM
            # free at start, which changes its batching and cache (ADR 0105).
            base_env["FFAST_MEMORY_BYTES"] = str(memory)
        for side in sides:
            env, info = side_env(side, HARNESS_ROOT, base_env)
            check_side(side, env)
            if info["dirty"]:
                print(f"warning: side {side.name!r} has uncommitted changes", flush=True)
            envs[side.name] = (env, info)
    except SideError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.repeats < 5:
        print("warning: the spec asks for at least 5 repeats; medians of fewer are weak", flush=True)

    output.mkdir(parents=True)
    facts = rows.host_facts()
    (output / "run_set.json").write_text(json.dumps({
        "name": output.name, "host_facts": facts,
        "sides": {s.name: {"root": str(s.root), **envs[s.name][1]} for s in sides},
        "arguments": {k: str(v) for k, v in vars(args).items()},
        "started_utc": datetime.now(timezone.utc).isoformat()}, indent=2) + "\n")

    order_index = 0
    try:
        for case in cases:
            path, manifest = generate.ensure(case, args.data_dir, workers=args.workers,
                                             target_bytes=getattr(args, "target_bytes", None))
            for repeat in range(1, args.repeats + 1):
                for side in (sides if repeat % 2 else sides[::-1]):
                    env, info = envs[side.name]
                    out_dir = output / "raw" / case.name / side.name / f"r{repeat}"
                    meta = {"run_set": output.name, "case": case.name, "repeat": repeat,
                            "order_index": order_index, "manifest": manifest, **info}
                    run_once(side, env, path, out_dir, args.timeout, meta)
                    harness = json.loads((out_dir / "harness.json").read_text())
                    pair = rows.rows_from_run(out_dir, facts)
                    rows.write_rows(pair, output / "rows", rows.stem_of(harness))
                    print(_line(case, side.name, repeat, pair), flush=True)
                    budgets = {row["ram_budget_bytes"] for row in pair} - {None}
                    if memory and any(b < memory for b in budgets):
                        print(f"  warning: FFAST's memory budget was {min(budgets) / 2**30:.2f} GiB, "
                              f"below the {memory / 2**30:.2f} GiB asked for: too little free RAM",
                              flush=True)
                    order_index += 1
            if args.delete_data_after:
                path.unlink(missing_ok=True)
    except KeyboardInterrupt:
        print("interrupted; rows already written are kept", file=sys.stderr)
        return 130
    return 0

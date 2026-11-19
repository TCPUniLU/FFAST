"""Run the variants in turns, each timed operation in a fresh process (ADR 0109 §4)."""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from benchmarks.storage import rows, variants
from benchmarks.storage.source import Source

HARNESS_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORES = Path("temp/storage-bench/stores")
TIMED_READS = ("scan", "frames", "reopen")


class RunError(Exception):
    """The run set cannot start, or must stop; the message says why."""


def order_for(names, repeat):
    """Variants take turns: repeat ``r`` starts at variant ``r mod n`` and walks round the
    list, forward on odd repeats and backward on even ones."""
    n = len(names)
    step = 1 if repeat % 2 else -1
    return [names[(repeat % n + step * k) % n] for k in range(n)]


def can_purge():
    """Cold runs need ``sudo purge`` without a password prompt."""
    return subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0


def purge():
    done = subprocess.run(["sudo", "-n", "purge"], capture_output=True, text=True)
    if done.returncode != 0:
        raise RunError(f"sudo -n purge failed: {done.stderr.strip()}")


def warm(store):
    """Read every file of the store once, so it sits in the operating system's file cache."""
    for path in variants.store_files(store):
        with open(path, "rb", buffering=0) as f:
            while f.readinto(_BUFFER):
                pass


_BUFFER = bytearray(8 * 2**20)


def call(op, variant, source, store, timeout):
    """Run one operation in a fresh process; its JSON, or a RunError with the end of stderr."""
    command = [sys.executable, "-m", "benchmarks.storage.measure", op, "--variant", variant,
               "--source", str(source.folder), "--store", str(store)]
    try:
        done = subprocess.run(command, cwd=HARNESS_ROOT, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RunError(f"{op} timed out after {timeout} s") from None
    if done.returncode != 0:
        raise RunError(f"{op} failed: {(done.stderr or done.stdout).strip()[-400:]}")
    return json.loads(done.stdout.strip().splitlines()[-1])


def _check_names(names):
    for name in names:
        try:
            variants.make(name)
        except ValueError as exc:
            raise RunError(str(exc)) from None


def run_matrix(args):
    """The ``run`` subcommand. Returns an exit code."""
    output = Path(args.output).expanduser().resolve()
    try:
        names = variants.all_names() if args.variants == "all" else \
            [v for v in args.variants.split(",") if v]
        _check_names(names)
        if not names:
            raise RunError("no variants")
        if "npz" in names and names[0] != "npz":
            names.remove("npz")
            names.insert(0, "npz")
        if args.repeats < 1:
            raise RunError("--repeats must be at least 1")
        if output.exists():
            raise RunError(f"--output {output} already exists")
        if args.cold and not can_purge():
            raise RunError("--cold needs `sudo -n purge`, and `sudo -n true` fails here (a password "
                           "is needed). Run `sudo -v` in this terminal first, then repeat the command "
                           "within a few minutes.")
        sources = [Source(d) for d in args.source]
    except (RunError, OSError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.repeats < 5:
        print("warning: the spec asks for at least 5 repeats; medians of fewer are weak", flush=True)

    store_root = Path(args.store_dir).expanduser().resolve()
    cache = "cold" if args.cold else "warm"
    output.mkdir(parents=True)
    facts = rows.host_facts()
    versions = _versions()
    (output / "run_set.json").write_text(json.dumps({
        "name": output.name, "host_facts": facts, "versions": versions, "cache": cache,
        "variants": names, "sources": {s.name: str(s.folder) for s in sources},
        "arguments": {k: str(v) for k, v in vars(args).items()},
        "started_utc": datetime.now(timezone.utc).isoformat()}, indent=2) + "\n")

    store_root.mkdir(parents=True, exist_ok=True)
    order_index, checked = 0, {}
    try:
        for source in sources:
            for repeat in range(1, args.repeats + 1):
                for name in order_for(names, repeat):
                    free = shutil.disk_usage(store_root).free
                    if free < 1.3 * source.info["bytes"]:
                        raise RunError(f"only {free / 1e9:.1f} GB free; a store of {source.name} "
                                       f"needs about {1.3 * source.info['bytes'] / 1e9:.1f} GB")
                    if checked.get((source.name, name)) is False:
                        continue  # failed the bit-identical check: never timed again
                    row = _run_one(args, source, name, repeat, order_index, cache, output,
                                   store_root, facts, versions, checked)
                    order_index += 1
                    print(_line(row), flush=True)
    except RunError as exc:
        print(f"error: {exc}; rows already written are kept", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted; rows already written are kept", file=sys.stderr)
        return 130
    return 0


def _versions():
    import numcodecs
    import zarr
    return {"zarr": zarr.__version__, "numcodecs": numcodecs.__version__}


def _run_one(args, source, name, repeat, order_index, cache, output, store_root, facts, versions,
             checked):
    run_dir = output / "raw" / source.name / name / f"r{repeat}"
    run_dir.mkdir(parents=True)
    store = store_root / f"{source.name}--{name}--r{repeat}"
    shutil.rmtree(store, ignore_errors=True)
    store_root.mkdir(parents=True, exist_ok=True)
    harness = {"run_set": output.name, "case": source.name, "real": source.real, "variant": name,
               "repeat": repeat, "order_index": order_index, "cache": cache,
               "source_bytes": source.info["bytes"], "frames": source.frames, "atoms": source.atoms,
               "started_utc": datetime.now(timezone.utc).isoformat(), "error": None}

    def save(op, result):
        (run_dir / f"{op}.json").write_text(json.dumps(result) + "\n")

    try:
        save("write", call("write", name, source, store, args.timeout))
        if (source.name, name) not in checked:
            # Once per variant and file, before any read is timed.
            result = call("check", name, source, store, args.timeout)
            save("check", result)
            checked[(source.name, name)] = result["ok"]
            if not result["ok"]:
                raise RunError("not bit-identical: " + "; ".join(result["bad"][:3]))
        for op in TIMED_READS:
            if args.cold:
                purge()
            else:
                warm(store)
            save(op, call(op, name, source, store, args.timeout))
    except RunError as exc:
        harness["error"] = str(exc)
    finally:
        if not args.keep_stores:
            shutil.rmtree(store, ignore_errors=True)
    (run_dir / "harness.json").write_text(json.dumps(harness, indent=2) + "\n")
    row = rows.row_from_run(run_dir, {**facts, **versions})
    rows.write_row(row, output / "rows")
    return row


def _line(row):
    head = f"{row['case']} {row['variant']} r{row['repeat']} {row['cache']}:"
    if not row["completed"]:
        return f"{head} FAILED {row['error']}"
    return (f"{head} write {row['write_s']:.2f} s, scan {row['scan_s']:.2f} s, "
            f"frame p95 {row['frame_p95_ms']:.2f} ms, reopen {row['reopen_s'] * 1000:.1f} ms, "
            f"{row['disk_bytes'] / 2**20:.0f} MiB")

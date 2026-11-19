"""One timed operation in its own process: ``python -m benchmarks.storage.measure`` (ADR 0109 §4).

Prints one JSON object. Operations: write, scan, frames, reopen, check.
"""

import argparse
import json
import resource
import sys
import time

import numpy as np

from benchmarks.storage import variants
from benchmarks.storage.source import Source, read_restored

FRAME_SEED = 20261119
FRAME_COUNT = 2000


def peak_rss_bytes():
    """Peak resident memory of this process; macOS reports bytes, Linux KiB."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def frame_indices(frames, count=FRAME_COUNT):
    """The random frames, in draw order (not sorted)."""
    return np.random.default_rng(FRAME_SEED).integers(0, frames, size=count)


def fold(acc, array):
    """Fold an array into a checksum so no read can be skipped."""
    flat = array.reshape(-1)
    if not flat.size:
        return acc
    return acc + float(flat[0]) + float(flat[-1]) + float(flat[:: max(1, flat.size // 64)].sum())


def op_write(variant, source, store):
    wall, cpu = variant.write(source, store)
    size, alloc, files = variants.disk_usage(store)
    return {"write_s": wall, "write_cpu_s": cpu, "disk_bytes": size,
            "disk_alloc_bytes": alloc, "n_files": files}


def op_scan(variant, source, store):
    handle = variant.open(source, store)
    acc = 0.0
    w0, c0 = time.perf_counter(), time.process_time()
    for window in range(len(source.batches)):
        for key in source.force_columns:
            acc = fold(acc, handle.scan_window(window, key))
    return {"scan_s": time.perf_counter() - w0, "scan_cpu_s": time.process_time() - c0,
            "checksum": acc}


def op_frames(variant, source, store):
    handle = variant.open(source, store)
    times, acc = [], 0.0
    for i in frame_indices(source.frames):
        t0 = time.perf_counter()
        got = handle.frame(int(i))
        times.append(time.perf_counter() - t0)
        for array in got.values():
            acc = fold(acc, array)
    ms = np.asarray(times) * 1000
    return {"frame_p50_ms": float(np.percentile(ms, 50)), "frame_p95_ms": float(np.percentile(ms, 95)),
            "frames_total_s": float(sum(times)), "checksum": acc}


def op_reopen(variant, source, store):
    w0 = time.perf_counter()
    handle = variant.open(source, store)
    got = handle.reopen()
    seconds = time.perf_counter() - w0
    return {"reopen_s": seconds, "checksum": fold(0.0, got["positions"])}


def equal(a, b):
    if a.dtype != b.dtype or a.shape != b.shape:
        return False
    return bool(np.array_equal(a, b, equal_nan=a.dtype.kind in "fc"))


def check(variant, source, store):
    """Every column of every window from the variant equals the source bit for bit."""
    handle = variant.open(source, store)
    bad = []
    for index in range(len(source.batches)):
        want = read_restored(source.batch_path(index))
        want = {k: v for k, v in want.items() if k in source.columns}
        got = handle.window(index)
        for key in source.columns:
            if key not in got or not equal(got[key], want[key]):
                bad.append(f"window {index} column {key}")
    # Frame reads must agree with the rows of the source window.
    for i in (0, source.frames // 2, source.frames - 1):
        window, l0, l1, _, _ = source.locate(i)
        want = read_restored(source.batch_path(window))
        got = handle.frame(i)
        for key, array in got.items():
            if not equal(array, want[key][l0:l1]):
                bad.append(f"frame {i} column {key}")
    return {"ok": not bad, "bad": bad[:10]}


OPS = {"write": op_write, "scan": op_scan, "frames": op_frames, "reopen": op_reopen}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m benchmarks.storage.measure")
    parser.add_argument("op", choices=[*OPS, "check"])
    parser.add_argument("--variant", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--store", required=True)
    args = parser.parse_args(argv)
    variant, source = variants.make(args.variant), Source(args.source)
    result = {"op": args.op, "variant": args.variant}
    if args.variant.startswith("zarr-"):
        result.update(variants.configure_threads())
    result.update(check(variant, source, args.store) if args.op == "check"
                  else OPS[args.op](variant, source, args.store))
    result["peak_rss_bytes"] = peak_rss_bytes()
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())

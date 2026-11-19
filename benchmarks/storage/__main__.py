"""Storage bench (ADR 0109): NumPy batches against a Zarr sweep."""

import argparse
import sys
from pathlib import Path

from benchmarks.ingest.__main__ import parse_bytes


def build_parser():
    parser = argparse.ArgumentParser(prog="python -m benchmarks.storage", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    src = sub.add_parser("source", help="load a file through FFAST and keep its batch set")
    src.add_argument("file", type=Path)
    src.add_argument("--out", type=Path, required=True)
    src.add_argument("--memory-bytes", type=parse_bytes, default=3 * 2**30)
    src.add_argument("--real", action="store_true", help="mark the file as real data (not synthetic)")
    src.add_argument("--label", help="name used in rows and reports (default: the folder name)")
    src.add_argument("--profiler-arg", action="append", default=[], metavar="ARG",
                     help="extra argument for ffast.profiling, e.g. --profiler-arg=--pred-force-key=forces_pred")

    run = sub.add_parser("run", help="time the variants on one or more sources, in turns")
    run.add_argument("--source", type=Path, action="append", required=True,
                     help="a folder made by `source`; repeat for more files")
    run.add_argument("--variants", default="all",
                     help="all, or comma-separated names such as npz,zarr-lz4-16k")
    run.add_argument("--repeats", type=int, default=5)
    run.add_argument("--cold", action="store_true",
                     help="drop the file cache before each timed read (needs `sudo -n purge`)")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--store-dir", type=Path, default=Path("temp/storage-bench/stores"))
    run.add_argument("--keep-stores", action="store_true")
    run.add_argument("--timeout", type=float, default=7200, help="seconds per operation")

    rep = sub.add_parser("report", help="tables, verdicts and the go/no-go from a run set")
    rep.add_argument("output", type=Path)
    rep.add_argument("--baseline", default="npz")

    cut = sub.add_parser("slice", help="copy the first whole frames of an extxyz file")
    cut.add_argument("file", type=Path)
    cut.add_argument("--bytes", type=parse_bytes, required=True, dest="size",
                     help="size to reach, e.g. 1GB (binary units, like 1G)")
    cut.add_argument("--out", type=Path, required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "source":
        from benchmarks.storage.source import SourceError, make_source
        try:
            info = make_source(args.file, args.out, args.memory_bytes, args.real, args.label,
                               args.profiler_arg)
        except SourceError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"{args.out}: {len(info['batches'])} batches, {info['frames']} frames, "
              f"{info['atoms']} atoms, {info['bytes']} bytes (memory mode {info['memory_mode']}, "
              f"budget {info['memory_bytes']})")
    elif args.command == "run":
        from benchmarks.storage.run import run_matrix
        return run_matrix(args)
    elif args.command == "report":
        from benchmarks.storage.report import report
        return report(args.output, args.baseline)
    elif args.command == "slice":
        from benchmarks.storage.slice import slice_frames
        frames, size = slice_frames(args.file, args.out, args.size)
        print(f"{args.out}: {frames} frames, {size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())

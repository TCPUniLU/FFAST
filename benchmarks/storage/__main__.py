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
    return 0


if __name__ == "__main__":
    sys.exit(main())

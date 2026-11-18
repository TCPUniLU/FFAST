"""Ingestion benchmark harness (ADR 0105): generate, run, compare, rebuild."""

import argparse
import sys
from pathlib import Path


def _csv(text):
    return [part for part in text.split(",") if part]


def parse_bytes(text):
    """``4G``, ``512M``, ``1048576K`` or a plain number of bytes (binary units)."""
    text = text.strip().upper().removesuffix("IB").removesuffix("B")
    scale = {"K": 2**10, "M": 2**20, "G": 2**30}.get(text[-1:], 1)
    number = text[:-1] if scale > 1 else text
    try:
        value = int(float(number) * scale)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a size: {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError("a size cannot be negative")
    return value


def build_parser():
    parser = argparse.ArgumentParser(prog="python -m benchmarks.ingest", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="make one synthetic .extxyz file")
    gen.add_argument("--size", required=True, choices=("100MB", "1GB", "10GB"))
    gen.add_argument("--atoms", required=True, choices=("small", "medium", "large"))
    gen.add_argument("--counts", required=True, choices=("constant", "variable"))
    gen.add_argument("--seed", type=int, default=0)
    gen.add_argument("--data-dir", type=Path, required=True)
    gen.add_argument("--workers", type=int, default=4)

    run = sub.add_parser("run", help="time the cases on one or more checkouts")
    run.add_argument("--side", action="append", required=True, metavar="NAME=PATH")
    run.add_argument("--sizes", type=_csv, default=["100MB"])
    run.add_argument("--classes", type=_csv, default=["small", "medium", "large"])
    run.add_argument("--counts", type=_csv, default=["constant", "variable"])
    run.add_argument("--repeats", type=int, default=5)
    run.add_argument("--data-dir", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--timeout", type=float, default=1800)
    run.add_argument("--workers", type=int, default=4)
    run.add_argument("--delete-data-after", action="store_true")
    run.add_argument("--memory-bytes", type=parse_bytes, default=3 * 2**30,
                     help="FFAST's memory budget for every run (default 3G, below the lowest "
                          "free RAM seen on the laptop; a budget above free RAM is cut "
                          "down and varies again). FFAST otherwise "
                          "takes 80%% of the RAM free at start, which differs between runs and "
                          "changes batching, cache size and load time. 0 lets FFAST choose.")

    cmp_ = sub.add_parser("compare", help="compare two sides of a run set")
    cmp_.add_argument("output", type=Path)
    cmp_.add_argument("--baseline", required=True)
    cmp_.add_argument("--candidate", required=True)
    cmp_.add_argument("--with", dest="extra", type=Path, action="append", default=[],
                      help="another run set's folder whose rows are added")

    rb = sub.add_parser("rebuild", help="regenerate rows/ from the raw run folders")
    rb.add_argument("output", type=Path)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "generate":
        from benchmarks.ingest.generate import Case, ensure
        path, manifest = ensure(Case(args.size, args.atoms, args.counts, args.seed),
                                args.data_dir, workers=args.workers)
        print(f"{path}: {manifest['bytes']} bytes, {manifest['frames']} frames, "
              f"{manifest['seconds']:.1f} s")
    elif args.command == "run":
        from benchmarks.ingest.run import run_matrix
        return run_matrix(args)
    elif args.command == "compare":
        from benchmarks.ingest.compare import compare
        return compare(args.output, args.baseline, args.candidate, args.extra)
    elif args.command == "rebuild":
        from benchmarks.ingest.rows import rebuild
        rebuild(args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())

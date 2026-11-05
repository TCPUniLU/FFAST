"""``ffast bundle`` and ``ffast lammps data`` (ADR 0086).

``launcher.main`` hands these subcommands here before starting any client.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import numpy as np


class CommandError(Exception):
    """A user-facing error: printed without a traceback."""


def _no_space(path: Path, what: str) -> None:
    if any(ch.isspace() for ch in str(path)):
        raise CommandError(f"{what} {str(path)!r} contains a space; LAMMPS cannot use it")


def _parse_elements(text: str) -> list[str]:
    elements = text.replace(",", " ").split()
    if not elements:
        raise CommandError("--elements is empty; give the element of each LAMMPS type, e.g. \"C H O\"")
    if len(set(elements)) != len(elements):
        raise CommandError(f"--elements {text!r} names an element twice")
    return elements


def _read_frames(path: Path):
    from ase.io import read

    try:
        frames = read(str(path), index=":")
    except Exception as exc:
        raise CommandError(f"could not read {path}: {exc}") from exc
    if not frames:
        raise CommandError(f"{path} has no frames")
    return frames


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _positions(frames, symbols: list[str], name: str) -> np.ndarray:
    for k, atoms in enumerate(frames):
        if atoms.get_chemical_symbols() != symbols:
            raise CommandError(
                f"{name} frame {k} has another atom order or element list than the first "
                "training frame; all frames must list the same atoms in the same order"
            )
    return np.stack([a.get_positions() for a in frames]).astype(np.float64)


def bundle_paths(output: str) -> tuple[Path, Path]:
    """``-o PATH`` → (``PATH.bundle`` directory, ``PATH.lmp`` include file)."""
    out = Path(output).expanduser().resolve()
    if out.suffix in (".bundle", ".lmp"):
        out = out.with_suffix("")
    return out.with_name(out.name + ".bundle"), out.with_name(out.name + ".lmp")


def cmd_bundle(args: argparse.Namespace) -> None:
    from ffast.live.detectana_api import require_detectana
    from ffast.live.include_file import include_text

    detectana = require_detectana()
    from detectana.topology import build_topology

    train_path, valid_path = Path(args.train), Path(args.valid)
    if train_path.resolve() == valid_path.resolve() or _file_sha(train_path) == _file_sha(valid_path):
        raise CommandError(
            "--train and --valid are the same data; the thresholds are calibrated on "
            "frames the fit has not seen, so give two different sets"
        )
    elements = _parse_elements(args.elements)
    bundle_dir, include_path = bundle_paths(args.output)
    _no_space(bundle_dir, "bundle path")

    train = _read_frames(train_path)
    valid = _read_frames(valid_path)
    symbols = train[0].get_chemical_symbols()
    missing = sorted(set(symbols) - set(elements))
    if missing:
        raise CommandError(
            f"--elements {' '.join(elements)!r} lacks {' '.join(missing)}, present in {train_path}"
        )
    train_pos = _positions(train, symbols, "training")
    valid_pos = _positions(valid, symbols, "validation")

    topology = build_topology(train[0])
    bundle = detectana.fit(train_pos, valid_pos, topology)
    bundle.save(bundle_dir)
    include_path.write_text(include_text(str(bundle_dir), elements, args.every))

    t = bundle.thresholds(bundle.tracks[0])
    print(f"Reference bundle: {bundle_dir}")
    print(f"  {len(train_pos)} training and {len(valid_pos)} validation frames, "
          f"{bundle.n_atoms} atoms, thresholds (T²) "
          + ", ".join(f"{k} {v:.4g}" for k, v in t.items()))
    print(f"LAMMPS include file: {include_path}")
    print(f"  Add `include {include_path}` to your input after the atoms exist,")
    print("  and run the job as `ffast-lammps srun lmp ...`.")


def cmd_lammps_data(args: argparse.Namespace) -> None:
    from ffast.live.data_file import bundle_sha256, data_file_text, record_data_file
    from ffast.live.detectana_api import require_detectana

    detectana = require_detectana()
    try:
        bundle = detectana.load(args.bundle)
    except detectana.BundleError as exc:
        raise CommandError(str(exc)) from exc
    elements = _parse_elements(args.elements)
    frames = _read_frames(Path(args.source))
    if not -len(frames) <= args.index < len(frames):
        raise CommandError(f"{args.source} has {len(frames)} frames; --index {args.index} is outside")
    atoms = frames[args.index]
    symbols = atoms.get_chemical_symbols()
    if tuple(symbols) != bundle.elements:
        raise CommandError(
            f"{args.source} frame {args.index} does not list its atoms in the bundle's order "
            f"({len(symbols)} atoms vs {bundle.n_atoms}); start from a frame of the "
            "bundle's reference data"
        )
    missing = sorted(set(symbols) - set(elements))
    if missing:
        raise CommandError(f"--elements {' '.join(elements)!r} lacks {' '.join(missing)}")
    out = Path(args.output).expanduser().resolve()
    _no_space(out, "data file path")
    positions = atoms.get_positions().astype(np.float64)
    source = f"{Path(args.source).resolve()}@{args.index}"
    out.write_text(data_file_text(
        positions, symbols, elements,
        title=f"written by ffast lammps data from {source} in reference order",
    ))
    record = record_data_file(positions, bundle_sha256=bundle_sha256(args.bundle),
                              source=source, data_file=out)
    print(f"LAMMPS data file: {out} ({len(symbols)} atoms, types {' '.join(elements)})")
    print("  Use `units metal` (or real), `atom_style atomic` and `read_data` it.")
    print(f"  Recorded in {record}: a run starting from it is proven to be in reference order.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ffast", description="FFAST commands for live LAMMPS runs")
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("bundle", help="Fit a reference bundle and write its LAMMPS include file")
    b.add_argument("--train", required=True, help="training frames (extxyz/xyz)")
    b.add_argument("--valid", required=True, help="validation frames, not used to fit")
    b.add_argument("--elements", required=True,
                   help='element of each LAMMPS atom type, in type order, e.g. "C H O"')
    b.add_argument("-o", "--output", required=True,
                   help="output path without extension: writes PATH.bundle and PATH.lmp")
    b.add_argument("--every", type=int, default=100, help="dump and score every N steps (default 100)")
    b.set_defaults(func=cmd_bundle)

    lmp = sub.add_parser("lammps", help="LAMMPS helpers")
    lmp_sub = lmp.add_subparsers(dest="lammps_command", required=True)
    d = lmp_sub.add_parser("data", help="Write a LAMMPS data file in the bundle's atom order")
    d.add_argument("--from", dest="source", required=True,
                   help="structure file holding the starting frame (a frame of the reference data)")
    d.add_argument("--index", type=int, default=0, help="frame in that file (default 0)")
    d.add_argument("--bundle", required=True, help="the reference bundle the run will be scored with")
    d.add_argument("--elements", required=True,
                   help='element of each LAMMPS atom type, as given to ffast bundle, e.g. "C H O"')
    d.add_argument("-o", "--output", required=True, help="data file to write, e.g. run.data")
    d.set_defaults(func=cmd_lammps_data)
    return parser


def main(argv: list[str] | None = None) -> int:
    from ffast.live.detectana_api import DetectAnaMissing

    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (CommandError, DetectAnaMissing) as exc:
        print(f"ffast {args.command}: {exc}", file=sys.stderr)
        return 1
    return 0

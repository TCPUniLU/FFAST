"""``ffast lammps data``: a LAMMPS data file in the bundle's atom order (ADR 0086).

Atom identity in a live run is proven by a chain: the starting structure is
written by this module in reference order, and the run's first dump frame
equals it bit for bit. ``%.17g`` round-trips a float64, so LAMMPS reads exactly
the numbers written here and dumps them back unchanged. Each file written is
recorded under ``~/.ffast/lammps-data/<coordinate hash>.json``; the scorer
hashes the first frame and looks the record up.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np

RECORDS_ENV = "FFAST_LAMMPS_DATA_RECORDS"
BOX_MARGIN = 10.0  # Å around the molecule


def records_dir() -> Path:
    return Path(os.environ.get(RECORDS_ENV) or "~/.ffast/lammps-data").expanduser()


def coordinate_hash(positions) -> str:
    """sha256 of the coordinates as little-endian float64, atoms in order."""
    pos = np.ascontiguousarray(np.asarray(positions, dtype="<f8"))
    return hashlib.sha256(pos.tobytes()).hexdigest()


def data_file_text(positions, symbols: list[str], elements: list[str], title: str) -> str:
    """LAMMPS ``atom_style atomic`` data; atom ``i`` gets ID ``i + 1``, type from ``elements``."""
    from ase.data import atomic_masses, atomic_numbers

    pos = np.asarray(positions, dtype=np.float64)
    lo, hi = pos.min(axis=0) - BOX_MARGIN, pos.max(axis=0) + BOX_MARGIN
    lines = [title.replace("\n", " "), "",
             f"{len(pos)} atoms", f"{len(elements)} atom types", ""]
    for (a, b), axis in zip(zip(lo, hi), "xyz"):
        lines.append(f"{a!r} {b!r} {axis}lo {axis}hi")
    lines += ["", "Masses", ""]
    for t, e in enumerate(elements, start=1):
        lines.append(f"{t} {atomic_masses[atomic_numbers[e]]:.6f}  # {e}")
    lines += ["", "Atoms # atomic", ""]
    for i, (s, (x, y, z)) in enumerate(zip(symbols, pos), start=1):
        lines.append("%d %d %.17g %.17g %.17g" % (i, elements.index(s) + 1, x, y, z))
    return "\n".join(lines) + "\n"


def record_data_file(positions, *, bundle_sha256: str, source: str, data_file: Path) -> Path:
    """Remember a written data file so a run starting from it counts as proven."""
    from ffast.live.run_folder import utc_now, write_json_atomic

    d = records_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{coordinate_hash(positions)}.json"
    write_json_atomic(path, {
        "bundle_sha256": bundle_sha256,
        "source": source,
        "data_file": str(data_file),
        "written_utc": utc_now(),
    })
    return path


def find_record(positions) -> dict | None:
    """The record of a data file with exactly these coordinates, or None."""
    from ffast.live.run_folder import read_json

    return read_json(records_dir() / f"{coordinate_hash(positions)}.json")


def bundle_sha256(bundle_dir) -> str:
    """Content hash of a bundle directory (its manifest and fitted state)."""
    h = hashlib.sha256()
    for name in ("manifest.json", "state.npz"):
        h.update(hashlib.sha256((Path(bundle_dir) / name).read_bytes()).digest())
    return h.hexdigest()

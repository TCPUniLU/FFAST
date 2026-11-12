"""Writing a dataset through a reader (ADR 0095, decision 11).

Export builds ``ExportItem``s (the reference values and each prediction that
was chosen) and ``write_dataset`` gives the chosen format's writer the frames
in chunks, in the dict shape ``read()`` returns, so a format never sees
FFAST's objects. ``check_writable`` says what a format cannot hold; the dialog
asks it before writing starts, and the server asks it again.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from ffast.readers.contract import ReaderError

CHUNK_FRAMES = 500


@dataclass
class ExportItem:
    """One set of values to write: the reference's or a prediction's.
    ``energy(i)`` and ``forces(i)`` answer for frame ``i`` (a float, an
    ``(atoms, 3)`` array) or are None when the set has none; the names are the
    ones the chunk's ``energy:<name>`` and ``forces:<name>`` carry."""

    energy_name: str
    force_name: str
    energy: Optional[Callable] = None
    forces: Optional[Callable] = None


def _plural(n):
    return "set" if n == 1 else "sets"


def check_writable(format_info: dict, dataset, items) -> None:
    """Raise ReaderError, plainly, when ``format_info``'s format cannot hold
    ``dataset`` with the chosen ``items``."""
    label, can = format_info.get("label", "This format"), format_info.get("can_write") or {}
    if can.get("variable_atoms", True) is False and getattr(dataset, "isVariable", False):
        raise ReaderError(
            f"An {label} file needs the same atoms in the same order in every structure, "
            f"and this dataset's structures differ.")
    frames = can.get("frames")
    if frames is not None and dataset.getN() > frames:
        many = "one structure" if frames == 1 else f"{frames} structures"
        raise ReaderError(f"{label} holds {many}; this dataset has {dataset.getN()}.")
    chosen = [item for item in items if item.energy is not None or item.forces is not None]
    limit = can.get("energies")
    if limit is not None and len(chosen) > limit:
        if limit == 0:
            raise ReaderError(f"{label} holds no energies or forces.")
        raise ReaderError(f"{label} holds {limit} {_plural(limit)} of energies and forces; "
                          f"{len(chosen)} are chosen.")
    with_forces = [item for item in items if item.forces is not None]
    force_limit = can.get("forces")
    if force_limit and len(with_forces) > force_limit:   # 0: write_dataset leaves them out
        raise ReaderError(f"{label} holds {force_limit} {_plural(force_limit)} of forces; "
                          f"{len(with_forces)} are chosen.")


def _without_forces(items):
    """``items`` with their forces left out, for a format that holds none;
    a set that had only forces is dropped."""
    return [ExportItem(item.energy_name, item.force_name, item.energy, None)
            for item in items if item.energy is not None]


def _chunk(dataset, items, frames):
    """The frames ``frames`` as the dict ``read()`` returns."""
    zs = [np.asarray(dataset.getElements(i)) for i in frames]
    out = {
        "n_atoms": np.array([len(z) for z in zs], dtype=np.int64),
        "numbers": np.concatenate(zs) if zs else np.empty(0, dtype=np.int64),
        "positions": np.concatenate([np.asarray(dataset.getCoordinates(i)).reshape(-1, 3)
                                     for i in frames]),
    }
    lattice = dataset.getLattice(frames) if hasattr(dataset, "getLattice") else None
    if lattice is not None:
        lattice = np.asarray(lattice, dtype=np.float64)
        out["cell"] = (np.tile(lattice, (len(frames), 1, 1)) if lattice.ndim == 2
                       else lattice.reshape(len(frames), 3, 3))
    for item in items:
        if item.energy is not None:
            out[f"energy:{item.energy_name}"] = np.array(
                [_value(item.energy, i) for i in frames], dtype=np.float64)
        if item.forces is not None:
            out[f"forces:{item.force_name}"] = np.concatenate([
                _forces(item.forces, i, len(z)) for i, z in zip(frames, zs)]) if zs else np.empty((0, 3))
    return out


def _value(fn, i):
    try:
        value = fn(i)
    except (RuntimeError, KeyError, IndexError):
        return np.nan
    return np.nan if value is None else float(value)


def _forces(fn, i, n):
    try:
        value = fn(i)
    except (RuntimeError, KeyError, IndexError):
        value = None
    return np.full((n, 3), np.nan) if value is None else np.asarray(value, dtype=np.float64).reshape(-1, 3)


def write_dataset(reader_class, path, dataset, items, *, format=None, progress=None,
                  chunk=CHUNK_FRAMES) -> int:
    """Write ``dataset`` with ``items`` through ``reader_class``'s writer, in
    chunks of ``chunk`` frames; ``progress(done, total)`` follows each. Returns
    how many frames were left out (a frame with no atoms: an element subset's
    frame without those elements, ADR 0067)."""
    info = next((f for f in getattr(reader_class, "write_formats", []) if f["format"] == format),
                {"label": reader_class.name, "can_write": {}})
    check_writable(info, dataset, items)
    if int(dataset.getN()) == 0:
        raise ReaderError("This dataset has no structures to write.")
    if (info.get("can_write") or {}).get("forces") == 0:
        items = _without_forces(items)
    total = int(dataset.getN())
    contents = {
        "positions": True, "numbers": True,
        "cell": dataset.getLattice() is not None if hasattr(dataset, "getLattice") else False,
        "energies": [item.energy_name for item in items if item.energy is not None],
        "forces": [item.force_name for item in items if item.forces is not None],
        "frame_fields": [], "atom_fields": [], "units": {},
    }
    parent = os.path.dirname(path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)
    writer = reader_class.open_writer(path, contents, format)
    try:
        for a in range(0, total, chunk):
            b = min(a + chunk, total)
            writer.write(_chunk(dataset, items, list(range(a, b))))
            if progress:
                progress(b, total)
        writer.close()
    except BaseException:
        abort = getattr(writer, "abort", None)
        if abort:
            abort()
        raise
    return int(getattr(writer, "skipped", 0) or 0)

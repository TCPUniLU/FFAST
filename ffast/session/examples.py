"""FFAST's bundled example data: where it is and what it holds.

The examples live in the repository (``examples/data``), one folder per
example. A server installed without the repository has none.
"""
from __future__ import annotations

import os
from pathlib import Path


def example_data_dir() -> Path:
    """The ``examples/data`` folder of the repository this code runs from."""
    return Path(__file__).resolve().parents[2] / "examples" / "data"


def example_listing() -> dict | None:
    """The examples folder and its files, for a window's file dialog.

    ``folder`` is an absolute path in the form a folder listing returns.
    ``files`` are the files in it and in its direct sub-folders, as sorted
    POSIX paths relative to it. ``None`` when the folder does not exist.
    """
    root = example_data_dir()
    if not root.is_dir():
        return None
    files = []
    for entry in root.iterdir():
        if entry.is_file():
            files.append(entry.name)
        elif entry.is_dir():
            files.extend(f"{entry.name}/{sub.name}" for sub in entry.iterdir() if sub.is_file())
    return {"folder": os.path.abspath(root), "files": sorted(files)}


def lammps_example_dir() -> Path:
    """The repository's shipped LAMMPS practice input and recorded run."""
    return Path(__file__).resolve().parents[2] / "examples" / "lammps"

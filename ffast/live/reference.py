"""Make a reference bundle and its LAMMPS include file (ADR 0086, plan decision 22).

Shared by ``ffast bundle`` (files on disk) and the web "Make reference" dialog
(two datasets loaded on the server). Refuses one dataset given twice: the
thresholds are calibrated on frames the fit has not seen.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


class ReferenceRefused(ValueError):
    """A user-facing reason the bundle cannot be made."""


def bundle_paths(output: str | Path) -> tuple[Path, Path]:
    """``PATH`` → (``PATH.bundle`` directory, ``PATH.lmp`` include file)."""
    out = Path(output).expanduser().resolve()
    if out.suffix in (".bundle", ".lmp"):
        out = out.with_suffix("")
    return out.with_name(out.name + ".bundle"), out.with_name(out.name + ".lmp")


def elements_in_order(symbols: list[str]) -> list[str]:
    """Each element once, in order of first appearance: LAMMPS types 1, 2, …"""
    return list(dict.fromkeys(symbols))


def dataset_frames(ds, name: str) -> tuple[list[str], np.ndarray]:
    """(element symbols, positions (n_frames, n_atoms, 3)) of a loaded dataset."""
    from ase.data import chemical_symbols

    if getattr(ds, "isVariable", False):
        raise ReferenceRefused(f"{name} has frames with different atoms; a reference needs one molecule")
    n = int(ds.getN())
    if n < 1:
        raise ReferenceRefused(f"{name} has no frames")
    z = np.asarray(ds.getElements(0))
    for k in range(1, n):
        if not np.array_equal(np.asarray(ds.getElements(k)), z):
            raise ReferenceRefused(f"{name} frame {k} lists other atoms than frame 0")
    pos = np.asarray(ds.getCoordinates(np.arange(n)), dtype=np.float64).reshape(n, len(z), 3)
    return [chemical_symbols[int(v)] for v in z], pos


def make_reference(train: np.ndarray, valid: np.ndarray, symbols: list[str], output: str | Path, *,
                   elements: list[str] | None = None, every: int = 100,
                   valid_symbols: list[str] | None = None) -> dict:
    """Fit the bundle on ``train``, calibrate on ``valid``, save it and its include.

    ``output`` is the path without suffix; ``elements`` the LAMMPS type order
    (default: order of first appearance). Returns what was written and the
    lines the user adds to the input and the job script.
    """
    from ffast.live.detectana_api import require_detectana
    from ffast.live.include_file import include_text

    detectana = require_detectana()
    from ase import Atoms
    from detectana.topology import build_topology

    train = np.asarray(train, dtype=np.float64)
    valid = np.asarray(valid, dtype=np.float64)
    if valid_symbols is not None and list(valid_symbols) != list(symbols):
        raise ReferenceRefused("the validation frames list other atoms, or the same atoms in another "
                               "order, than the training frames")
    if train.shape[1:] != valid.shape[1:]:
        raise ReferenceRefused(f"training frames have {train.shape[1]} atoms, validation {valid.shape[1]}")
    if train.shape == valid.shape and np.array_equal(train, valid):
        raise ReferenceRefused("training and validation are the same frames; the thresholds are "
                               "calibrated on frames the fit has not seen, so give two different sets")
    elements = list(elements) if elements else elements_in_order(list(symbols))
    missing = sorted(set(symbols) - set(elements))
    if missing:
        raise ReferenceRefused(f"the element list {' '.join(elements)!r} lacks {' '.join(missing)}")
    bundle_dir, include_path = bundle_paths(output)
    if any(ch.isspace() for ch in str(bundle_dir)):
        raise ReferenceRefused(f"{bundle_dir} contains a space; LAMMPS cannot read it")

    topology = build_topology(Atoms(symbols=list(symbols), positions=train[0]))
    bundle = detectana.fit(train, valid, topology)
    bundle_dir.parent.mkdir(parents=True, exist_ok=True)
    bundle.save(bundle_dir)
    include_path.write_text(include_text(str(bundle_dir), elements, every))
    return {
        "bundle": str(bundle_dir), "include": str(include_path),
        "n_train": int(len(train)), "n_valid": int(len(valid)), "n_atoms": int(train.shape[1]),
        "elements": elements, "tests": [list(t) for t in bundle.tests],
        "lines": [f"include {include_path}", "ffast-lammps srun lmp -in <your input>"],
    }

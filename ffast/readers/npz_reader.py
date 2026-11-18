"""sGDML ``.npz`` files as a reader (ADR 0095, step 3), Level 1.

A dataset file holds ``R`` (frames, atoms, 3), ``z`` (the atoms' elements, the
same in every frame), ``E``, ``F`` and optionally ``lattice``. A prediction
file holds only ``E`` and ``F``. Keys are lower-cased on reading, as the
old loader did; the names FFAST shows stay ``E`` and ``F``.
"""
from __future__ import annotations

import os

import numpy as np

from ffast.readers.contract import ReaderError

_KEYS = ("r", "z", "e", "f", "lattice")


class NpzReader:
    name = "sGDML"
    extensions = ["*.npz"]
    package = None            # bundled
    write_formats = [
        {"order": 6, "format": "sgdml", "label": "sGDML", "ext": ".npz", "can_write": {
            "energies": 1, "forces": 1, "variable_atoms": False,
            "frame_fields": False, "atom_fields": False}},
    ]

    def __init__(self, path):
        self.path = path
        data = np.load(path, allow_pickle=True)
        self._data = {key.lower(): np.asarray(data[key]) for key in data.files if key.lower() in _KEYS}
        data.close()
        lattice = self._data.get("lattice")
        if lattice is not None and (lattice.dtype == object or lattice.ndim == 0):
            del self._data["lattice"]          # an old export wrote None for "no cell"
        file = os.path.basename(path)
        per_frame = [self._data[k] for k in ("r", "e", "f") if k in self._data]
        if not per_frame:
            raise ReaderError(f"{file} has none of R, E or F, so it holds no frames")
        self._n = int(per_frame[0].shape[0])
        atoms = next((self._data[k].shape[1] for k in ("r", "f")
                      if k in self._data and self._data[k].ndim == 3), None)
        if atoms is None and "z" in self._data:
            atoms = len(self._data["z"])
        if atoms is None:
            raise ReaderError(f"{file} has no R or F, so its atoms per frame are unknown")
        self._atoms = int(atoms)

    def n_frames(self):
        return self._n

    def contents(self):
        has = self._data.__contains__
        return {
            "positions": has("r"),
            "numbers": has("z"),
            "cell": has("lattice"),
            "energies": ["E"] if has("e") else [],
            "forces": ["F"] if has("f") else [],
            "frame_fields": [],
            "atom_fields": [],
            "units": {},
        }

    def read(self, frames):
        d, idx = self._data, np.asarray(frames, dtype=np.intp)
        out = {"n_atoms": np.full(len(idx), self._atoms, dtype=np.int64)}
        if "z" in d:
            out["numbers"] = np.tile(d["z"], len(idx))
        if "r" in d:
            out["positions"] = d["r"][idx].reshape(-1, 3)
        if "lattice" in d:
            lattice = d["lattice"]
            out["cell"] = (lattice[idx] if lattice.ndim == 3
                           else np.tile(lattice, (len(idx), 1, 1))).astype(lattice.dtype, copy=False)
        if "e" in d:
            out["energy:E"] = d["e"][idx].reshape(-1)
        if "f" in d:
            out["forces:F"] = d["f"][idx].reshape(-1, 3)
        return out


class _NpzWriter:
    """An sGDML file: ``R``, ``E``, ``F``, ``z`` (the elements of one structure)
    and the cell, with the md5 of the arrays. The arrays are joined when closed."""

    skipped = 0

    def __init__(self, path):
        self.path, self.chunks = path, []

    def write(self, chunk):
        self.chunks.append(chunk)

    def close(self):
        joined = {key: np.concatenate([c[key] for c in self.chunks]) for key in self.chunks[0]}
        counts = joined["n_atoms"]
        if len(set(counts.tolist())) != 1:
            raise ReaderError("An sGDML file needs the same atoms in the same order in every "
                              "structure, and this dataset's structures differ.")
        atoms = int(counts[0])
        data = {"R": joined["positions"].reshape(-1, atoms, 3), "z": joined["numbers"][:atoms]}
        energy = next((k for k in joined if k.startswith("energy:")), None)
        forces = next((k for k in joined if k.startswith("forces:")), None)
        if energy:
            data["E"] = joined[energy]
        if forces:
            data["F"] = joined[forces].reshape(-1, atoms, 3)
        if "cell" in joined:
            data["lattice"] = joined["cell"][0]
        data["name"] = self.path
        from ffast.cache.fingerprint import sgdml_md5
        data["md5"] = sgdml_md5(*(data[k] for k in ("R", "E", "F") if k in data))
        np.savez_compressed(self.path, **data)


def _open_writer(path, contents, format=None):
    return _NpzWriter(path)


NpzReader.open_writer = staticmethod(_open_writer)

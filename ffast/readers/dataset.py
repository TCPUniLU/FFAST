"""FFAST's dataset, built from the arrays a reader returned (ADR 0095).

The reader never sees this class: it returns plain numpy arrays, and the
rename of any field in here cannot break an outside reader.
"""
from __future__ import annotations

import os

import numpy as np

from ffast.loaders.dataset import FlatDatasetLoader
from ffast.readers.contract import ReaderError


class ReaderDataset(FlatDatasetLoader):
    """A file a reader read, in the flat storage every file dataset uses
    (ADR 0058): all atoms in one run, ``molecule_offsets`` saying where each
    frame starts. ``isVariable`` is true unless every frame has the same atoms
    in the same order.

    ``energy_key`` and ``force_key`` name the energy and force set that become
    the dataset's reference values; the first of each when None. Values a
    frame lacks are NaN, a set the file never has is None (ADR 0058, ADR 0064).
    """

    datasetName = "Reader Dataset"
    datasetFileExtension = "*"

    def __init__(self, path, loaded, reader_name, energy_key=None, force_key=None):
        super().__init__(path)
        file = os.path.basename(path)
        arrays, contents = loaded.arrays, loaded.contents
        self.reader_name = reader_name
        self.sourceFrameCount = loaded.n_file_frames   # the file's frames, before the stride
        self.sourceFrames = list(loaded.frames)        # which of them this dataset kept
        if "positions" not in arrays:
            raise ReaderError(f"{file} has no positions, so it cannot load as a dataset")
        if "numbers" not in arrays:
            raise ReaderError(f"{file} has no atomic numbers, so it cannot load as a dataset")

        counts = np.asarray(arrays["n_atoms"], dtype=np.int64)
        self.N = len(counts)
        self.molecule_offsets = np.concatenate([[0], np.cumsum(counts)]).astype(int)
        self.R_flat = np.asarray(arrays["positions"])
        self.z_flat = np.asarray(arrays["numbers"])

        self.energy_key = self._pick(energy_key, contents.get("energies") or [], "energy", file)
        self.force_key = self._pick(force_key, contents.get("forces") or [], "force", file)
        energy = arrays.get(f"energy:{self.energy_key}") if self.energy_key else None
        self.E = None if energy is None else np.asarray(energy, dtype=np.float64).reshape(-1)
        forces = arrays.get(f"forces:{self.force_key}") if self.force_key else None
        self.F_flat = None if forces is None else np.asarray(forces)
        cell = arrays.get("cell")
        self.lattice = None if cell is None else np.asarray(cell)

        n0 = int(counts[0])
        same = bool((counts == n0).all()) and (
            n0 == 0 or bool((self.z_flat.reshape(self.N, n0) == self.z_flat[:n0]).all()))
        self.isVariable = not same
        self.chem = self.getChemicalFormula()

    @staticmethod
    def _pick(chosen, names, what, file):
        """The set the user chose, else the first; an unknown name is refused."""
        if not chosen:
            return names[0] if names else None
        if chosen not in names:
            listed = ", ".join(names) or "none"
            raise ReaderError(f"{file} has no {what} named '{chosen}' (it has: {listed})")
        return chosen

    def getLattice(self, indices=None):
        if self.lattice is None:
            return None
        return self.lattice if indices is None else self.lattice[indices]

    def toMetaDict(self) -> dict:
        meta = super().toMetaDict()
        meta["source_type"] = self.reader_name     # a saved session finds the reader by it
        return meta

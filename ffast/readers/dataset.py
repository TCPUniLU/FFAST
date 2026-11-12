"""FFAST's dataset, built from the arrays a reader returned (ADR 0095).

The reader never sees this class: it returns plain numpy arrays, and the
rename of any field in here cannot break an outside reader.
"""
from __future__ import annotations

import os

import numpy as np

from ffast.cache.fingerprint import md5FromArraysAndStrings
from ffast.loaders.ase import _same_formula_on_sample
from ffast.loaders.dataset import FlatDatasetLoader
from ffast.readers.contract import ReaderError
from ffast.readers.pairing import pair_names


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

        self.arrays = arrays          # all the file's arrays, until the predictions in it are loaded
        self.contents = contents
        self.energy_key, self.force_key = pair_names(
            contents.get("energies") or [], contents.get("forces") or [],
            energy_key, force_key, file=file)
        energy = arrays.get(f"energy:{self.energy_key}") if self.energy_key else None
        self.E = None if energy is None else np.asarray(energy).reshape(-1)
        forces = arrays.get(f"forces:{self.force_key}") if self.force_key else None
        self.F_flat = None if forces is None else np.asarray(forces)
        cell = arrays.get("cell")
        self.lattice = None if cell is None else np.asarray(cell)

        n0 = int(counts[0])
        same = bool((counts == n0).all()) and (
            n0 == 0 or bool((self.z_flat.reshape(self.N, n0) == self.z_flat[:n0]).all()))
        self.isVariable = not same
        self.chem = self.getChemicalFormula()

    def getFingerprint(self):
        """The formula the ASE and sGDML loaders used, unchanged, so saved
        sessions and caches still match (ADR 0058, section 3): frame 0's
        elements when every frame has the same formula (on the fixed sample),
        every atom's otherwise; then positions, energies, forces."""
        z = self.z_flat
        if _same_formula_on_sample(self.molecule_offsets, z):
            z = z[:self.molecule_offsets[1]]
        arrays = (z, self.R_flat, self.E, self.F_flat)
        return md5FromArraysAndStrings(*(a for a in arrays if a is not None))

    def getLattice(self, indices=None):
        if self.lattice is None:
            return None
        return self.lattice if indices is None else self.lattice[indices]

    def toMetaDict(self) -> dict:
        meta = super().toMetaDict()
        meta["source_type"] = self.reader_name     # a saved session finds the reader by it
        return meta


class ReaderFieldSource:
    """What ``_extractPredictionFields`` reads a prediction's Dataset Fields
    from, over a reader's arrays. No fields yet: step 5 of the reader plan
    fills these in."""

    def __init__(self, arrays, contents):
        self.arrays, self.contents = arrays, contents

    def availableFieldKeys(self):
        return [], []

    def getFrameField(self, key, indices=None):
        return None

    def getAtomField(self, key, indices=None):
        return None

    def getAtomFieldMissing(self, key, indices=None):
        return None

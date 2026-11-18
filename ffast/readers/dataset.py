"""FFAST's dataset, built from the arrays a reader returned (ADR 0095).

The reader never sees this class: it returns plain numpy arrays, and the
rename of any field in here cannot break an outside reader.
"""
from __future__ import annotations

import logging
import os

import numpy as np
from ffast.io.batched import BatchedArray, asarray

from ffast.cache.fingerprint import md5FromArraysAndStrings
from ffast.loaders.ase import _same_formula_on_sample
from ffast.loaders.dataset import FlatDatasetLoader, isOneFrame
from ffast.readers.contract import ReaderError
from ffast.readers.pairing import pair_names


logger = logging.getLogger("FFAST")


def _has_nan(value):
    known = getattr(value, "has_nan", None)
    return bool(np.isnan(value).any()) if known is None else known


class ReaderFields:
    """Dataset Fields (ADR 0023) over the ``frame:<field>`` and ``atom:<field>``
    arrays a reader returned: one number per frame, one per atom. A frame with
    no value is NaN there. The rules are the old loaders': a frame field is
    available only when every frame has it; an atom field is available with
    NaN where a frame lacks it, and ``getAtomFieldMissing`` says where.

    Needs ``field_arrays`` (the arrays), ``molecule_offsets``, ``N`` and
    ``isVariable``."""

    def _field(self, kind, key):
        return self.field_arrays.get(f"{kind}:{key}")

    def availableFieldKeys(self):
        frame = sorted(k[6:] for k, v in self.field_arrays.items()
                       if k.startswith("frame:") and not _has_nan(v))
        atom = sorted(k[5:] for k in self.field_arrays if k.startswith("atom:"))
        return frame, atom

    def getFrameField(self, key, indices=None):
        v = self._field("frame", key)
        if v is None or _has_nan(v):
            logger.warning("Dataset field: frame field '%s' missing or not a number in some frame; "
                           "field unavailable", key)
            return None
        v = asarray(v, dtype=np.float64)
        if indices is None:
            return v
        return v[indices] if isOneFrame(indices) else v[np.asarray(indices)]

    def _atoms_of(self, flat, indices):
        if indices is None and isinstance(flat, BatchedArray) and self.isVariable:
            return flat
        if isOneFrame(indices):
            return flat[self.molecule_offsets[indices]:self.molecule_offsets[indices + 1]]
        if not self.isVariable:
            frames = flat.reshape(self.N, -1, *flat.shape[1:])
            return (frames if isinstance(frames, BatchedArray) else frames.copy()) if indices is None else frames[np.asarray(indices)]
        indices = range(self.N) if indices is None else indices
        return [flat[self.molecule_offsets[i]:self.molecule_offsets[i + 1]] for i in indices]

    def getAtomField(self, key, indices=None):
        col = self._field("atom", key)
        if col is None:
            logger.warning("Dataset field: atom field '%s' missing or not a per-atom number; "
                           "field unavailable", key)
            return None
        return self._atoms_of(asarray(col, dtype=np.float64), indices)

    def getAtomFieldMissing(self, key, indices=None):
        """Where atom field ``key`` has no value; None when every atom has one."""
        col = self._field("atom", key)
        if col is None or not _has_nan(col):
            return None
        return self._atoms_of(np.isnan(col), indices)


class ReaderDataset(ReaderFields, FlatDatasetLoader):
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
        self.storage = getattr(arrays, "store", None)
        self.reader_name = reader_name
        self.sourceFrameCount = loaded.n_file_frames   # the file's frames, before the stride
        self.sourceFrames = (self.storage.frames if self.storage is not None else
                             list(loaded.frames))     # which file frames this dataset kept
        if "positions" not in arrays:
            raise ReaderError(f"{file} has no positions, so it cannot load as a dataset")
        if "numbers" not in arrays:
            raise ReaderError(f"{file} has no atomic numbers, so it cannot load as a dataset")

        counts = np.asarray(arrays["n_atoms"], dtype=np.int64)
        self.N = len(counts)
        self.molecule_offsets = (self.storage.offsets if self.storage is not None else
                                 np.concatenate([[0], np.cumsum(counts)]).astype(int))
        self.R_flat = asarray(arrays["positions"])
        self.z_flat = asarray(arrays["numbers"])
        self.memory_mode = "saving" if self.storage is not None else "auto"

        self.arrays = arrays          # all the file's arrays, until the predictions in it are loaded
        self.field_arrays = {k: v for k, v in arrays.items() if k.startswith(("frame:", "atom:"))}
        self.contents = contents
        self.energy_key, self.force_key = pair_names(
            contents.get("energies") or [], contents.get("forces") or [],
            energy_key, force_key, file=file)
        energy = arrays.get(f"energy:{self.energy_key}") if self.energy_key else None
        self.E = None if energy is None else asarray(energy).reshape(-1)
        forces = arrays.get(f"forces:{self.force_key}") if self.force_key else None
        self.F_flat = None if forces is None else asarray(forces)
        cell = arrays.get("cell")
        self.lattice = None if cell is None else asarray(cell)

        n0 = int(counts[0])
        same = self.storage.uniform if self.storage is not None else bool((counts == n0).all()) and (
            n0 == 0 or bool((self.z_flat.reshape(self.N, n0) == self.z_flat[:n0]).all()))
        self.isVariable = not same
        self.chem = self.getChemicalFormula()
        self._unitsOfTheFile(contents.get("units") or {}, file)

    def getFingerprint(self):
        """The formula the ASE and sGDML loaders used, unchanged, so saved
        sessions and caches still match (ADR 0058, section 3): frame 0's
        elements when every frame has the same formula (on the fixed sample),
        every atom's otherwise; then positions, energies, forces."""
        if self.storage is not None:
            import hashlib
            store = self.storage
            z = (hashlib.md5(store.first_numbers).digest() if store.same_formula else
                 bytes.fromhex(store.info["numbers"]["digest"]))
            parts = [z, bytes.fromhex(store.info["positions"]["digest"])]
            for key in (f"energy:{self.energy_key}", f"forces:{self.force_key}"):
                if key in store.info:
                    parts.append(bytes.fromhex(store.info[key]["digest"]))
            final = hashlib.md5()
            for part in parts:
                final.update(part)
            return final.hexdigest()
        z = self.z_flat
        if _same_formula_on_sample(self.molecule_offsets, z):
            z = z[:self.molecule_offsets[1]]
        arrays = (z, self.R_flat, self.E, self.F_flat)
        return md5FromArraysAndStrings(*(a for a in arrays if a is not None))

    def _unitsOfTheFile(self, labels, file):
        """The reader's unit labels become the dataset's units; the user can
        change them. A dimension FFAST has no unit for is left out."""
        known = {d: u for d, u in labels.items() if d in self.UNIT_DIMENSIONS}
        for dimension in sorted(set(labels) - set(known)):
            logger.warning("%s: a unit for '%s' is left out; FFAST names units for %s only",
                           file, dimension, " and ".join(self.UNIT_DIMENSIONS))
        if known:
            self.setUnits(known)

    def getLattice(self, indices=None):
        if self.lattice is None:
            return None
        return self.lattice if indices is None else self.lattice[indices]

    def toMetaDict(self) -> dict:
        meta = super().toMetaDict()
        meta["source_type"] = self.reader_name     # a saved session finds the reader by it
        if getattr(self, "sampling", None) is not None:
            meta["sampling"] = dict(self.sampling)  # a sample by energy (ADR 0096): the rail says so
        return meta

    def metaElements(self):
        if self.storage is None:
            return super().metaElements()
        z = self.storage.first_numbers.tolist()
        return {"elements": None if self.isVariable else z,
                "first_elements": z if self.isVariable else None,
                "element_set": sorted(self.storage.element_set)}

    def hasForces(self):
        return self.F_flat is not None


class ReaderFieldSource(ReaderFields):
    """What ``_extractPredictionFields`` reads a prediction's Dataset Fields
    from: the fields in a reader's arrays, with the frames' atom counts."""

    def __init__(self, arrays, contents=None):
        self.field_arrays = {k: v for k, v in arrays.items() if k.startswith(("frame:", "atom:"))}
        counts = np.asarray(arrays["n_atoms"], dtype=np.int64)
        self.N = len(counts)
        self.molecule_offsets = np.concatenate([[0], np.cumsum(counts)]).astype(int)
        self.isVariable = bool((counts != counts[0]).any()) if self.N else False

import os
import hashlib
import logging
import numpy as np
from ffast.cache.fingerprint import md5FromParts
from ffast.loaders.dataset import FlatDatasetLoader, isOneFrame
import ase.io
from ase.calculators.calculator import PropertyNotImplementedError, all_properties
from ase.io.extxyz import REV_PROPERTY_NAME_MAP, per_config_properties
from ffast.io import extxyz_arrays as xa   # call xa.read_frames: tests patch it

# The discoverable plugin (loadData/SmartASELoader, registered under the
# "ase (auto)" datasetName) lives in ffast.plugins.loaders.ase (ADR 0048); this
# module keeps only the loader classes + shared Dataset Field readers that the
# Loading Coordinator and other server code import directly.

logger = logging.getLogger("FFAST")

# Standard ASE arrays keys that are not user-facing Dataset Fields.
_RESERVED_ARRAY_KEYS = {"positions", "numbers", "momenta"}


def _sample_indices(n, sampleSize=60):
    """isUniformAtomsList's frames: all of a short list, else an even sample
    with the first and last frame."""
    if n <= sampleSize:
        return range(n)
    step = (n - 1) / (sampleSize - 1)
    return sorted({int(round(i * step)) for i in range(sampleSize)} | {0, n - 1})


def isUniformAtomsList(atomsList, sampleSize=60):
    """Decide whether every frame in ``atomsList`` has the same chemical formula.

    The rule that chooses :class:`aseDatasetLoader`'s fingerprint formula, for
    datasets and predictions alike: frame 0's elements only when the formulas
    agree, every frame's otherwise. It keeps the fingerprints files had before
    the two ASE loaders merged (ADR 0058, decision A). It no longer sets
    ``isVariable``, which asks for the same atoms in the same order in every
    frame (ADR 0058 step 6).

    Full scans are avoided: ``atomsList`` is routinely a lazy ``AtomsList`` /
    ``Trajectory`` where materialising every frame costs a re-read of the whole
    file.  Lists at or below ``sampleSize`` are checked exhaustively; larger
    ones on an evenly-spaced sample that always includes the first and last
    frame.  The spacing is deterministic on purpose — a random sample can
    classify the same file differently between runs, which changes the
    dataset's fingerprint.
    """
    n = len(atomsList)
    if n < 2:
        return True

    indices = _sample_indices(n, sampleSize)

    reference = None
    for i in indices:
        formula = atomsList[i].get_chemical_formula()
        if reference is None:
            reference = formula
        elif formula != reference:
            return False
    return True


def _same_formula_on_sample(offsets, numbers):
    """isUniformAtomsList on joined arrays: equal formulas are equal sorted
    element lists."""
    n = len(offsets) - 1
    if n < 2:
        return True
    first = None
    for i in _sample_indices(n):
        z = np.sort(numbers[offsets[i]:offsets[i + 1]])
        if first is None:
            first = z
        elif not np.array_equal(z, first):
            return False
    return True


PROGRESS_EVERY = 10_000


def _atoms_subset(atomsList, indices):
    """Resolve a frame-index selector to (list_of_atoms, is_scalar).

    Mirrors the ``indices`` contract of getForces/getEnergies: None → all
    frames, a scalar → that single frame, an iterable → that subset.
    """
    if indices is None:
        return [atomsList[i] for i in range(len(atomsList))], False
    if isOneFrame(indices):
        return [atomsList[indices]], True
    return [atomsList[i] for i in indices], False


def read_frame_field(atomsList, key, indices=None):
    """Read a per-frame scalar Dataset Field (``atoms.info[key]``).

    Strict, all-or-nothing (ADR 0023): returns an ``(N,)`` float array only if
    the key is present in every selected frame and is a numeric scalar there;
    otherwise logs and returns ``None``. A scalar ``indices`` returns the bare
    value, matching getEnergies.
    """
    atoms, is_scalar = _atoms_subset(atomsList, indices)
    vals = []
    for a in atoms:
        if key not in a.info:
            logger.warning("Dataset field: frame field '%s' missing in a frame; field unavailable", key)
            return None
        v = np.asarray(a.info[key])
        if v.ndim != 0 or not np.issubdtype(v.dtype, np.number):
            logger.warning("Dataset field: frame field '%s' is not a numeric per-frame scalar (shape %s, dtype %s); skipped", key, v.shape, v.dtype)
            return None
        vals.append(float(v))
    arr = np.asarray(vals, dtype=np.float64)
    return arr[0] if is_scalar else arr


def read_atom_field(atomsList, key, variable, indices=None):
    """Read a per-atom scalar Dataset Field (``atoms.arrays[key]``).

    Strict, all-or-nothing (ADR 0023). Uniform datasets return ``(N, nAtoms)``;
    variable datasets return a per-frame list of ``(n_atoms_i,)`` arrays (the
    same contract as getForces, so InputResolver._flatten aligns them). Wrong
    width (e.g. a per-atom *vector*), a missing key, or non-numeric data → logs
    and returns ``None``. A scalar ``indices`` returns the single ``(n,)`` array.
    """
    atoms, is_scalar = _atoms_subset(atomsList, indices)
    per_frame = []
    for a in atoms:
        if key not in a.arrays:
            logger.warning("Dataset field: atom field '%s' missing in a frame; field unavailable", key)
            return None
        v = np.asarray(a.arrays[key])
        if v.ndim != 1 or v.shape[0] != len(a) or not np.issubdtype(v.dtype, np.number):
            logger.warning("Dataset field: atom field '%s' is not a numeric per-atom scalar (shape %s, dtype %s); skipped", key, v.shape, v.dtype)
            return None
        per_frame.append(v.astype(np.float64))
    if is_scalar:
        return per_frame[0]
    if variable:
        return per_frame
    return np.asarray(per_frame, dtype=np.float64)


def available_field_keys(atomsList):
    """Discovery: (frame_keys, atom_keys) eligible as Dataset Fields.

    Inspects the first frame only (cheap). Frame keys are numeric scalars in
    ``atoms.info``; atom keys are numeric per-atom scalars in ``atoms.arrays``
    excluding reserved ASE keys. Used by ``ffast-cli dataset keys``.
    """
    if not atomsList:
        return [], []
    a = atomsList[0]
    frame_keys = []
    for k, v in a.info.items():
        v = np.asarray(v)
        if v.ndim == 0 and np.issubdtype(v.dtype, np.number):
            frame_keys.append(k)
    atom_keys = []
    for k in a.arrays.keys():
        if k in _RESERVED_ARRAY_KEYS:
            continue
        v = np.asarray(a.arrays[k])
        if v.ndim == 1 and v.shape[0] == len(a) and np.issubdtype(v.dtype, np.number):
            atom_keys.append(k)
    return sorted(frame_keys), sorted(atom_keys)


class aseDatasetLoader(FlatDatasetLoader):
    """The one loader for ASE-readable files (ADR 0058, step 3).

    Uniform (every frame the same atoms in the same order) or variable is
    decided from the frames and kept in ``isVariable``. Every frame is read
    once, in the constructor, into joined arrays (ADR 0076); one-frame reads
    are answered from them. The getAll* methods answer the same way on both kinds;
    the older no-argument calls keep each kind's shape until their last
    callers move (ADR 0058, steps 4 and 6): (frames, atoms, ...) arrays for
    uniform data, lists for variable.
    """

    datasetName = "ase"
    datasetFileExtension = "*"
    saveFormats = ["db", "xyz", "extxyz", "traj", "vasp", "dftb"]

    def __init__(self, path, atomsList=None, selected_energy_key=None, selected_force_key=None,
                 *args, progress=None, **kwargs):
        super().__init__(path)

        if atomsList is None:
            logger.warning("atomsList was none, hence loading the entire dataset.")
            from ffast.io.xyz import read_ase_or_explain
            self.atomsList = read_ase_or_explain(path, index=':')
        else:
            self.atomsList = atomsList

        _, self.file_extension = os.path.splitext(path)
        self.N = len(self.atomsList)
        self.selected_energy_key = selected_energy_key
        self.selected_force_key = selected_force_key

        force_keys = self._detectForceKeys()
        energy_keys = self._detectEnergyKeys()
        if self.selected_energy_key == "":
            self._energy_key = None
        elif self.selected_energy_key == "energy":
            self._energy_key = None
        elif self.selected_energy_key:
            self._energy_key = self.selected_energy_key
        else:
            self._energy_key = energy_keys[0] if energy_keys else None

        if self.selected_force_key == "":
            self._force_key = None
        elif self.selected_force_key == "forces":
            self._force_key = None
        elif self.selected_force_key:
            self._force_key = self.selected_force_key
        else:
            self._force_key = force_keys[0] if force_keys else None

        # Every frame is read once, here (read-once plan 2026-11-02, ADR 0076).
        self._fields = None   # the FrameArrays, when the fast reader read the file
        arrays = self._readFast(progress)
        if arrays is not None:
            counts, numbers, R = arrays.counts, arrays.numbers, arrays.positions
            F = arrays.columns.get(self._force_key or "forces")
            E = arrays.values.get(self._energy_key or "energy")
            cells = arrays.cells
            self._fields = arrays
        else:
            counts, numbers, R, F, E, cells = self._readWithASE(progress)

        self.molecule_offsets = np.concatenate([[0], np.cumsum(counts)]).astype(int)
        self._z_flat = numbers
        self._joinedArrays = (R, F, E)
        self._has_forces = F is not None
        self._has_energies = E is not None
        self.lattice = cells

        n0 = int(counts[0]) if len(counts) else 0
        same_atoms = bool((counts == n0).all()) and (
            n0 == 0 or bool((numbers.reshape(len(counts), n0) == numbers[:n0]).all()))
        self.isVariable = not same_atoms
        self._fingerprintOneFrameOfElements = _same_formula_on_sample(self.molecule_offsets, numbers)

        self.chem = self.getChemicalFormula()

    def _firstFrame(self):
        """Frame 0, read alone: an indexed file must not load a whole chunk
        just to list its keys."""
        index = getattr(self.atomsList, "frame_index", None)
        if index is not None:
            return index.read_one(0)
        return self.atomsList[0] if self.N else None

    def _readFast(self, progress):
        """The fast reader's arrays for an indexed file, or None when ASE must
        read it. A second loader on the same AtomsList (in-file predictions)
        reuses them."""
        index = getattr(self.atomsList, "frame_index", None)
        if index is None:
            return None
        arrays = getattr(self.atomsList, "frame_arrays", None)
        if arrays is not None:
            return arrays
        name = os.path.basename(self.path)
        try:
            arrays = xa.read_frames(index, progress=progress)
        except xa.PropertiesDiffer as exc:
            logger.warning("%s: %s Reading it with ASE, which is slower.", name, exc)
            return None
        except xa.NotFast as exc:
            logger.info("%s is read with ASE: %s", name, exc)
            return None
        self.atomsList.frame_arrays = arrays
        return arrays

    def _readWithASE(self, progress):
        """Every frame through ASE, in one pass."""
        counts, numbers, R, F, E, cells = [], [], [], [], [], []
        for i, atoms in enumerate(self.atomsList):
            counts.append(len(atoms))
            numbers.append(atoms.get_atomic_numbers())
            R.append(atoms.get_positions())
            cells.append(atoms.get_cell().array)
            if F is not None:
                forces = self._readForces(atoms)
                if forces is None:
                    F = None
                else:
                    F.append(forces)
            if E is not None:
                energy = self._readEnergy(atoms)
                if energy is None:
                    E = None
                else:
                    E.append(energy)
            if progress and (i + 1) % PROGRESS_EVERY == 0:
                progress(i + 1, self.N)
        return (np.array(counts, dtype=np.int64),
                np.concatenate(numbers),
                np.vstack(R),
                None if F is None else np.vstack(F),
                None if E is None else np.array(E),
                np.array(cells, dtype=np.float64).reshape(-1, 3, 3))

    def _readForces(self, atoms):
        if self._force_key:
            return atoms.arrays[self._force_key]
        try:
            return atoms.get_forces()
        except (PropertyNotImplementedError, RuntimeError):
            return None

    def _readEnergy(self, atoms):
        if self._energy_key:
            return atoms.info[self._energy_key]
        try:
            return atoms.get_potential_energy()
        except (PropertyNotImplementedError, RuntimeError):
            return None

    def getFingerprint(self):
        """md5 of (elements, positions, energies, forces): the same bytes, in
        the same order, as the frame-by-frame hash it replaces (ADR 0058
        section 3), so saved sessions and caches still match."""
        R, F, E = self._joinedArrays
        z = self._z_flat
        if self._fingerprintOneFrameOfElements:
            z = z[:self.molecule_offsets[1]]
        parts = [hashlib.md5(np.ascontiguousarray(z)), hashlib.md5(np.ascontiguousarray(R))]
        if E is not None:
            parts.append(hashlib.md5(np.ascontiguousarray(E)))
        if F is not None:
            parts.append(hashlib.md5(np.ascontiguousarray(F)))
        return md5FromParts(parts)

    def hasForces(self):
        return self._has_forces

    @property
    def z_flat(self):
        return self._z_flat

    @property
    def R_flat(self):
        return self._joinedArrays[0]

    @property
    def F_flat(self):
        return self._joinedArrays[1]

    @property
    def E(self):
        return self._joinedArrays[2]

    def _detectForceKeys(self):
        first = self._firstFrame()
        if first is None:
            return []
        return [key for key in first.arrays.keys() if "force" in key.lower()]

    def _detectEnergyKeys(self):
        first = self._firstFrame()
        if first is None:
            return []
        return [key for key in first.info.keys() if "energy" in key.lower()]

    def getLattice(self, indices=None):
        if self.lattice is None:
            return None
        if indices is None:
            return self.lattice
        return self.lattice[indices]

    def ForceKeys(self):
        """Get force keys (for compatibility with uniform loader)."""
        return self._detectForceKeys()

    def EneregyKeys(self):
        """Get energy keys (for compatibility with uniform loader)."""
        return self._detectEnergyKeys()

    # Dataset Fields (ADR 0023): from the arrays when the fast reader read
    # the file, from the frames otherwise.

    def _atomColumns(self):
        """Per-atom numeric scalars under ASE's names, as ASE's arrays hold them."""
        out = {}
        for name, col in self._fields.columns.items():
            ase_name = REV_PROPERTY_NAME_MAP.get(name, name)
            if col.ndim == 1 and ase_name not in all_properties and ase_name not in _RESERVED_ARRAY_KEYS:
                out[ase_name] = col
        return out

    def getFrameField(self, key, indices=None):
        if self._fields is None:
            return read_frame_field(self.atomsList, key, indices=indices)
        v = None if key in per_config_properties else self._fields.values.get(key)
        if v is None:
            logger.warning("Dataset field: frame field '%s' missing or not a number in some frame; field unavailable", key)
            return None
        v = v.astype(np.float64)
        if indices is None:
            return v
        return v[indices] if isOneFrame(indices) else v[np.asarray(indices)]

    def getAtomField(self, key, indices=None):
        if self._fields is None:
            return read_atom_field(self.atomsList, key, variable=self.isVariable, indices=indices)
        col = self._atomColumns().get(key)
        if col is None:
            logger.warning("Dataset field: atom field '%s' missing or not a per-atom number; field unavailable", key)
            return None
        col = col.astype(np.float64)
        if isOneFrame(indices):
            return self._frame(col, indices)
        return self._frames(col, indices)

    def getAtomFieldMissing(self, key, indices=None):
        """Where atom field ``key`` is missing because its frame does not list
        the column (ADR 0076), in getAtomField's shape; None when no frame
        lacks it. A NaN the file holds is not missing: it stays a bad value."""
        if self._fields is None:
            return None
        mask = next((m for name, m in self._fields.missing.items()
                     if REV_PROPERTY_NAME_MAP.get(name, name) == key), None)
        if mask is None:
            return None
        if isOneFrame(indices):
            return self._frame(mask, indices)
        return self._frames(mask, indices)

    def availableFieldKeys(self):
        if self._fields is None:
            return available_field_keys(self.atomsList)
        frame_keys = sorted(k for k in self._fields.values if k not in per_config_properties)
        return frame_keys, sorted(self._atomColumns())

    @staticmethod
    def saveDataset(dataset, path, format=None, taskID=None):
        """Save any dataset (file, subset or atom subset) to an ASE format,
        frame by frame with each frame's own elements, writing the energies
        and forces it has (ADR 0058 step 6). A frame with no atoms (an element
        subset's frame without those elements, ADR 0067) is left out; returns
        how many were."""
        from ase import Atoms
        from ase.calculators.calculator import Calculator

        class FakeCalc(Calculator):
            def __init__(self):
                super().__init__()   # the .traj and .db writers read `parameters`

        has_forces = dataset.hasForces()
        try:
            has_energies = dataset.getN() > 0 and dataset.getEnergies(0) is not None
        except RuntimeError:
            has_energies = False

        atoms = []
        skipped = 0
        for i in range(dataset.getN()):
            z = dataset.getElements(i)
            if len(z) == 0:
                skipped += 1
                continue
            atom = Atoms(positions=dataset.getCoordinates(i),
                         symbols=[dataset.zIntToZStr[x] for x in z])
            results = {}
            if has_forces:
                results["forces"] = dataset.getForces(i)
            if has_energies:
                results["energy"] = dataset.getEnergies(i)
            if results:
                atom.calc = FakeCalc()
                atom.calc.results = results
            atoms.append(atom)

        ase.io.write(path, atoms, format=format)
        return skipped

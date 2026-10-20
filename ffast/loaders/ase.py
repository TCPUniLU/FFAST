import os
import hashlib
import logging
import numpy as np
from ffast.cache.fingerprint import md5FromParts
from ffast.loaders.dataset import DatasetLoader, VariableDatasetLoader, isOneFrame
import ase.io
from ase.calculators.calculator import PropertyNotImplementedError

# The discoverable plugin (loadData/SmartASELoader, registered under the
# "ase (auto)" datasetName) lives in ffast.plugins.loaders.ase (ADR 0048); this
# module keeps only the loader classes + shared Dataset Field readers that the
# Loading Coordinator and other server code import directly.

logger = logging.getLogger("FFAST")

# Standard ASE arrays keys that are not user-facing Dataset Fields.
_RESERVED_ARRAY_KEYS = {"positions", "numbers", "momenta"}


def isUniformAtomsList(atomsList, sampleSize=60):
    """Decide whether every frame in ``atomsList`` is the same molecule.

    The one rule that picks the ASE loader flavour, for datasets and
    predictions alike (ADR 0058, decision A). The uniform
    :class:`aseDatasetLoader` reads frame 0's atom count *and* atomic numbers
    and applies them to every frame, so "uniform" has to mean same
    *composition* — comparing atom counts alone would accept frames that share
    a count but not their elements, and silently stamp frame 0's ``z`` onto all
    of them.  Hence chemical formulas.

    Full scans are avoided: ``atomsList`` is routinely a lazy ``AtomsList`` /
    ``Trajectory`` where materialising every frame costs a re-read of the whole
    file.  Lists at or below ``sampleSize`` are checked exhaustively; larger
    ones on an evenly-spaced sample that always includes the first and last
    frame.  The spacing is deterministic on purpose — a random sample can
    classify the same file differently between runs, which changes the loader
    class and therefore the dataset's identity.
    """
    n = len(atomsList)
    if n < 2:
        return True

    if n <= sampleSize:
        indices = range(n)
    else:
        step = (n - 1) / (sampleSize - 1)
        indices = sorted({int(round(i * step)) for i in range(sampleSize)} | {0, n - 1})

    reference = None
    for i in indices:
        formula = atomsList[i].get_chemical_formula()
        if reference is None:
            reference = formula
        elif formula != reference:
            return False
    return True


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


class aseDatasetLoader(DatasetLoader):
    datasetName = "ase"
    datasetFileExtension = "*"
    saveFormats = ["db", "xyz", "extxyz", "traj", "vasp", "dftb"]

    def __init__(self, path, atomsList=None, selected_energy_key=None, selected_force_key=None, *args, **kwargs):
        super().__init__(path)

        # Read file only if atomsList not provided (avoid double-read)
        if atomsList is None:
            logger.warning("atomsList was none, hence loading the entire dataset.")
            from ffast.io.xyz import read_ase_or_explain
            self.atomsList = read_ase_or_explain(path, index=':')
        else:
            self.atomsList = atomsList

        _, self.file_extension = os.path.splitext(path)
        self.N = len(self.atomsList)

        exAtoms = self.atomsList[0]  # assumes all the same molecule!!

        self.nAtoms = len(exAtoms)
        # Where each frame's atoms start, as in VariableDatasetLoader: every 
        # frame has nAtoms atoms, so the offsets are 0, n, 2n, ...
        self.molecule_offsets = np.arange(self.N + 1) * self.nAtoms
        self.z = exAtoms.get_atomic_numbers()

        if hasattr(exAtoms, "cell"):
            self.lattice = exAtoms.cell
        else:
            self.lattice = None

        self.chem = self.zToChemicalFormula(self.z)

        # Store key selections
        self.selected_energy_key = selected_energy_key
        self.selected_force_key = selected_force_key

    def ForceKeys(self):
        exAtoms = self.atomsList[0]
        num_key = 0
        forcekeys = []
        for key in exAtoms.arrays.keys():
            if "force" in key.lower():
                logger.debug(f"Found forces in array '{key}' for index 0.")
                num_key += 1
                forcekeys.append(key)

        return forcekeys

    def EneregyKeys(self):
        exAtoms = self.atomsList[0]
        num_key = 0
        energykeys = []
        for key in exAtoms.info.keys():
            if "energy" in key.lower():
                logger.info(f"Found energy in array '{key}' for index 0.")
                num_key += 1
                energykeys.append(key)

        return energykeys

    def getN(self):
        return self.N

    def getNAtoms(self, index=None):
        if index is None:
            return self.nAtoms
        return int(self.molecule_offsets[index + 1] - self.molecule_offsets[index])

    def getChemicalFormula(self):
        return self.chem

    def getCoordinates(self, indices=None):
        # probably should just do it once at the start and save it as np arrays?
        if indices is None:
            indices = np.arange(self.N)
        elif isOneFrame(indices):
            return self.atomsList[indices].get_positions()

        R = []
        for idx in indices:
            R.append(self.atomsList[idx].get_positions())

        return np.array(R)

    def getEnergies(self, indices=None):
        # probably should just do it once at the start and save it as np arrays?
        keys = self.EneregyKeys()

        # Determine which key to use
        if hasattr(self, 'selected_energy_key') and self.selected_energy_key is not None:
            selected_key = self.selected_energy_key
        elif len(keys) > 0:
            selected_key = keys[0]  # Default to first key
        else:
            selected_key = ""  # No keys, use calculator

        # Check if calculator should be used:
        # - Empty string explicitly selected (calculator chosen in dialog), OR
        # - Key is literally 'energy' (standard ASE calculator result key), OR
        # - No keys available
        use_calculator = (selected_key == "" or selected_key == "energy")

        if use_calculator:
            if indices is None:
                indices = np.arange(self.N)
            elif isOneFrame(indices):
                return self.atomsList[indices].get_potential_energy()

            R = []
            for idx in indices:
                R.append(self.atomsList[idx].get_potential_energy())
        else:
            # Use the selected key from info dictionary
            logger.info(f"Using energy key '{selected_key}' from {keys}")
            if indices is None:
                indices = np.arange(self.N)
            elif isOneFrame(indices):
                return self.atomsList[indices].info[selected_key]

            R = []
            for idx in indices:
                R.append(self.atomsList[idx].info[selected_key])
        return np.array(R)

    def getForces(self, indices=None):
        # probably should just do it once at the start and save it as np arrays?
        keys = self.ForceKeys()

        # Determine which key to use
        if hasattr(self, 'selected_force_key') and self.selected_force_key is not None:
            selected_key = self.selected_force_key
        elif len(keys) > 0:
            selected_key = keys[0]  # Default to first key
        else:
            selected_key = ""  # No keys, use calculator

        # Check if calculator should be used:
        # - Empty string explicitly selected (calculator chosen in dialog), OR
        # - Key is literally 'forces' (standard ASE calculator result key), OR
        # - No keys available
        use_calculator = (selected_key == "" or selected_key == "forces")

        if use_calculator:
            if indices is None:
                indices = np.arange(self.N)
            elif isOneFrame(indices):
                return self.atomsList[indices].get_forces()

            R = []
            for idx in indices:
                R.append(self.atomsList[idx].get_forces())
        else:
            # Use the selected key from arrays dictionary
            logger.info(f"Using force key '{selected_key}' from {keys}")
            if indices is None:
                indices = np.arange(self.N)
            elif isOneFrame(indices):
                return self.atomsList[indices].arrays[selected_key]

            R = []
            for idx in indices:
                R.append(self.atomsList[idx].arrays[selected_key])

        return np.array(R)

    def getElements(self, index=None):
        return self.z

    def getFrameField(self, key, indices=None):
        return read_frame_field(self.atomsList, key, indices=indices)

    def getAtomField(self, key, indices=None):
        return read_atom_field(self.atomsList, key, variable=False, indices=indices)

    def availableFieldKeys(self):
        return available_field_keys(self.atomsList)

    def getLattice(self, indices=None):
        """Return the unit cell/lattice for specified frame(s)."""
        if indices is None:
            indices = np.arange(self.N)
        elif isOneFrame(indices):
            return self.atomsList[indices].get_cell()

        R = []
        for idx in indices:
            R.append(self.atomsList[idx].get_cell())
        return np.array(R)

    @staticmethod
    def saveDataset(dataset, path, format=None, taskID=None):
        from ase import Atoms
        from ase.calculators.calculator import Calculator

        R, F = dataset.getCoordinates(), dataset.getForces()
        E, zStr = dataset.getEnergies(), dataset.getElementsName()

        atoms = []

        class FakeCalc(Calculator):
            def __init__(self):
                pass

        for i in range(R.shape[0]):
            atom = Atoms(positions=R[i], symbols=zStr)
            atom.calc = FakeCalc()
            atom.calc.results = {"forces": F[i], "energy": E[i]}
            atoms.append(atom)

        ase.io.write(path, atoms, format=format)


class VariableASEDatasetLoader(VariableDatasetLoader):
    """Loader for ASE datasets with variable-sized molecules."""

    datasetName = "ase (variable)"
    datasetFileExtension = "*"
    saveFormats = ["db", "xyz", "extxyz", "traj", "vasp", "dftb"]

    def __init__(self, path, atomsList=None, selected_energy_key=None, selected_force_key=None, *args, **kwargs):
        super().__init__(path)

        # Read file only if atomsList not provided (avoid double-read)
        if atomsList is None:
            logger.warning("atomsList was none, hence loading the entire dataset.")
            from ffast.io.xyz import read_ase_or_explain
            self.atomsList = read_ase_or_explain(path, index=':')
        else:
            self.atomsList = atomsList

        _, self.file_extension = os.path.splitext(path)
        self.N = len(self.atomsList)

        # Store key selections
        self.selected_energy_key = selected_energy_key
        self.selected_force_key = selected_force_key

        # Joined arrays of all frames, built on the first request (see _joined)
        self._z_flat = None
        self._joinedArrays = None

        # Detect force and energy keys from first frame
        force_keys = self._detectForceKeys()
        energy_keys = self._detectEnergyKeys()

        # Determine which keys to use:
        # - Empty string "" means use calculator (user explicitly selected it)
        # - Literal 'forces'/'energy' means use calculator (standard ASE keys)
        # - Specific key string means use that key from arrays/info
        # - None or not set means use first available key (legacy behavior)
        if self.selected_energy_key == "":
            self._energy_key = None  # Use calculator
        elif self.selected_energy_key == "energy":
            self._energy_key = None  # Standard ASE key, use calculator
        elif self.selected_energy_key:
            self._energy_key = self.selected_energy_key
        else:
            self._energy_key = energy_keys[0] if energy_keys else None

        if self.selected_force_key == "":
            self._force_key = None  # Use calculator
        elif self.selected_force_key == "forces":
            self._force_key = None  # Standard ASE key, use calculator
        elif self.selected_force_key:
            self._force_key = self.selected_force_key
        else:
            self._force_key = force_keys[0] if force_keys else None

        # Only what one frame cannot tell is kept: where each frame's atoms
        # start, and whether every frame has forces and energies (ADR 0058,
        # decision B). Positions and forces are read per frame or joined later.
        counts = []
        forces_missing = energies_missing = False
        for atoms in self.atomsList:
            counts.append(len(atoms))
            if not forces_missing and self._readForces(atoms) is None:
                forces_missing = True
            if not energies_missing and self._readEnergy(atoms) is None:
                energies_missing = True
        self.molecule_offsets = np.concatenate([[0], np.cumsum(counts)]).astype(int)
        self._has_forces = not forces_missing
        self._has_energies = not energies_missing

        # Uniform or variable is decided by the data, by the same rule that
        # chose this loader (isUniformAtomsList). Until step 4 moves callers to
        # one meaning per call, the two kinds answer all-frames questions in
        # different shapes: uniform as (frames, atoms, ...) arrays, variable as
        # lists of per-frame arrays.
        self.isVariable = not isUniformAtomsList(self.atomsList)

        # Store lattice info if present
        if hasattr(self.atomsList[0], "cell"):
            self.lattice = [atoms.get_cell() for atoms in self.atomsList]
        else:
            self.lattice = None

        self.chem = self.getChemicalFormula()

    def _readForces(self, atoms):
        """One frame's forces, or None when the frame has none."""
        if self._force_key:
            return atoms.arrays[self._force_key]
        try:
            return atoms.get_forces()
        except (PropertyNotImplementedError, RuntimeError):
            return None

    def _readEnergy(self, atoms):
        """One frame's energy, or None when the frame has none."""
        if self._energy_key:
            return atoms.info[self._energy_key]
        try:
            return atoms.get_potential_energy()
        except (PropertyNotImplementedError, RuntimeError):
            return None

    def getFingerprint(self):
        """md5 of (elements, positions, energies, forces), read one frame at a
        time so all frames are never held at once (ADR 0058, decision B).
        Same recipe and result as md5FromArraysAndStrings (see md5FromParts).

        The two formulas in use before the loaders merged are kept, so saved
        sessions and caches still match (ADR 0058 section 3): a variable
        dataset hashes every frame's elements, a uniform one only frame 0's.
        """
        z_hash, r_hash, f_hash = hashlib.md5(), hashlib.md5(), hashlib.md5()
        energies = []
        forces_missing = energies_missing = False

        for i, atoms in enumerate(self.atomsList):
            if self.isVariable or i == 0:
                z_hash.update(np.ascontiguousarray(atoms.get_atomic_numbers()))
            r_hash.update(np.ascontiguousarray(atoms.get_positions()))
            if not forces_missing:
                forces = self._readForces(atoms)
                if forces is None:
                    forces_missing = True
                else:
                    f_hash.update(np.ascontiguousarray(forces))
            if not energies_missing:
                energy = self._readEnergy(atoms)
                if energy is None:
                    energies_missing = True
                else:
                    energies.append(energy)

        parts = [z_hash, r_hash]
        if not energies_missing:
            parts.append(hashlib.md5(np.array(energies)))
        if not forces_missing:
            parts.append(f_hash)
        return md5FromParts(parts)

    # All frames: the joined arrays VariableDatasetLoader reads, built on the
    # first request and kept, since rebuilding them reads the whole file
    # (ADR 0058, decision B).

    def hasForces(self):
        return self._has_forces

    @property
    def z_flat(self):
        """Every atom's element, frame after frame. One small number per
        atom, and every dataset summary sends it, so it is joined apart from
        the large positions and forces."""
        if self._z_flat is None:
            self._z_flat = np.concatenate([atoms.get_atomic_numbers() for atoms in self.atomsList])
        return self._z_flat

    @property
    def R_flat(self):
        return self._joined()[0]

    @property
    def F_flat(self):
        return self._joined()[1]

    @property
    def E(self):
        return self._joined()[2]

    def _joined(self):
        """Positions, forces (None if absent) and energies (None if absent)
        of all frames, read once on the first request."""
        if self._joinedArrays is None:
            frames = self.atomsList
            R = np.vstack([atoms.get_positions() for atoms in frames])
            F = np.vstack([self._readForces(atoms) for atoms in frames]) if self._has_forces else None
            E = np.array([self._readEnergy(atoms) for atoms in frames]) if self._has_energies else None
            self._joinedArrays = (R, F, E)
        return self._joinedArrays

    # One frame is read from the file (ADR 0058, decision B); all or several
    # frames come from the joined arrays above, in the shape of the kind.

    def _uniformFrames(self, flat, indices):
        """A uniform dataset's joined per-atom array as (frames, atoms, 3),
        for all frames or the selected ones. A copy, as the uniform loader
        always returned a fresh array: callers may change it in place."""
        frames = flat.reshape(self.N, -1, *flat.shape[1:])
        if indices is None:
            return frames.copy()
        return frames[np.asarray(indices)]

    def getNAtoms(self, index=None):
        if index is None and not self.isVariable:
            # Uniform: callers expect one count, shared by all frames.
            return int(self.molecule_offsets[1] - self.molecule_offsets[0])
        return super().getNAtoms(index)

    def getChemicalFormula(self):
        if self.isVariable:
            return super().getChemicalFormula()  # "Variable (2-8 atoms)"
        return self.zToChemicalFormula(self.getElements())

    def getCoordinates(self, indices=None):
        if isOneFrame(indices):
            return self.atomsList[indices].get_positions()
        if self.isVariable:
            return super().getCoordinates(indices)
        return self._uniformFrames(self.R_flat, indices)

    def getElements(self, index=None):
        if isOneFrame(index):
            return self.atomsList[index].get_atomic_numbers()
        if self.isVariable:
            return super().getElements(index)
        # Uniform: callers expect one structure's elements, shared by all.
        return self.atomsList[0].get_atomic_numbers()

    def getForces(self, indices=None):
        if not self._has_forces:
            raise RuntimeError("Forces are not available for this dataset.")
        if isOneFrame(indices):
            return self._readForces(self.atomsList[indices])
        if self.isVariable:
            return super().getForces(indices)
        return self._uniformFrames(self.F_flat, indices)

    def getEnergies(self, indices=None):
        if isOneFrame(indices):
            if not self._has_energies:
                raise RuntimeError("Energies are not available for this dataset.")
            return self._readEnergy(self.atomsList[indices])
        return super().getEnergies(indices)

    def _detectForceKeys(self):
        """Detect force keys in the first atoms object."""
        if not self.atomsList:
            return []
        exAtoms = self.atomsList[0]
        force_keys = []
        for key in exAtoms.arrays.keys():
            if "force" in key.lower():
                logger.debug(f"Found forces in array '{key}' for index 0.")
                force_keys.append(key)
        return force_keys

    def _detectEnergyKeys(self):
        """Detect energy keys in the first atoms object."""
        if not self.atomsList:
            return []
        exAtoms = self.atomsList[0]
        energy_keys = []
        for key in exAtoms.info.keys():
            if "energy" in key.lower():
                logger.debug(f"Found energy in array '{key}' for index 0.")
                energy_keys.append(key)
        return energy_keys

    def getLattice(self, indices=None):
        """Return the unit cell/lattice for specified frame(s)."""
        if not hasattr(self, 'lattice') or self.lattice is None:
            return None

        if indices is None:
            return np.array(self.lattice)
        elif isOneFrame(indices):
            return self.lattice[indices]

        return np.array([self.lattice[i] for i in indices])

    def ForceKeys(self):
        """Get force keys (for compatibility with uniform loader)."""
        return self._detectForceKeys()

    def EneregyKeys(self):
        """Get energy keys (for compatibility with uniform loader)."""
        return self._detectEnergyKeys()

    def getFrameField(self, key, indices=None):
        return read_frame_field(self.atomsList, key, indices=indices)

    def getAtomField(self, key, indices=None):
        return read_atom_field(self.atomsList, key, variable=self.isVariable, indices=indices)

    def availableFieldKeys(self):
        return available_field_keys(self.atomsList)

    @staticmethod
    def saveDataset(dataset, path, format=None, taskID=None):
        """Save variable dataset to ASE format."""
        from ase import Atoms
        from ase.calculators.calculator import Calculator

        if not dataset.isVariable:
            # Fall back to uniform saver
            return aseDatasetLoader.saveDataset(dataset, path, format, taskID)

        atoms = []

        class FakeCalc(Calculator):
            def __init__(self):
                pass

        for i in range(dataset.getN()):
            r = dataset.getCoordinates(i)  # (n_atoms_i, 3)
            f = dataset.getForces(i)  # (n_atoms_i, 3)
            e = dataset.getEnergies(i)  # scalar
            z = dataset.getElements(i)  # (n_atoms_i,)
            zStr = [dataset.zIntToZStr[x] for x in z]

            atom = Atoms(positions=r, symbols=zStr)
            atom.calc = FakeCalc()
            atom.calc.results = {"forces": f, "energy": e}
            atoms.append(atom)

        ase.io.write(path, atoms, format=format)

import os
import logging
import numpy as np
from ffast.loaders.dataset import DatasetLoader, VariableDatasetLoader
import ase.io
from collections.abc import Iterable

# The discoverable plugin (loadData/SmartASELoader, registered under the
# "ase (auto)" datasetName) lives in ffast.plugins.loaders.ase (ADR 0048); this
# module keeps only the loader classes + shared Dataset Field readers that the
# Loading Coordinator and other server code import directly.

logger = logging.getLogger("FFAST")

# Standard ASE arrays keys that are not user-facing Dataset Fields.
_RESERVED_ARRAY_KEYS = {"positions", "numbers", "momenta"}


def _atoms_subset(atomsList, indices):
    """Resolve a frame-index selector to (list_of_atoms, is_scalar).

    Mirrors the ``indices`` contract of getForces/getEnergies: None → all
    frames, a scalar → that single frame, an iterable → that subset.
    """
    if indices is None:
        return [atomsList[i] for i in range(len(atomsList))], False
    if not isinstance(indices, np.ndarray) and not hasattr(indices, "__iter__"):
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

    def getNAtoms(self):
        return self.nAtoms

    def getChemicalFormula(self):
        return self.chem

    def getCoordinates(self, indices=None):
        # probably should just do it once at the start and save it as np arrays?
        if indices is None:
            indices = np.arange(self.N)
        elif not isinstance(indices, Iterable):
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
            elif not isinstance(indices, Iterable):
                return self.atomsList[indices].get_potential_energy()

            R = []
            for idx in indices:
                R.append(self.atomsList[idx].get_potential_energy())
        else:
            # Use the selected key from info dictionary
            logger.info(f"Using energy key '{selected_key}' from {keys}")
            if indices is None:
                indices = np.arange(self.N)
            elif not isinstance(indices, Iterable):
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
            elif not isinstance(indices, Iterable):
                return self.atomsList[indices].get_forces()

            R = []
            for idx in indices:
                R.append(self.atomsList[idx].get_forces())
        else:
            # Use the selected key from arrays dictionary
            logger.info(f"Using force key '{selected_key}' from {keys}")
            if indices is None:
                indices = np.arange(self.N)
            elif not isinstance(indices, Iterable):
                return self.atomsList[indices].arrays[selected_key]

            R = []
            for idx in indices:
                R.append(self.atomsList[idx].arrays[selected_key])

        return np.array(R)

    def getElements(self):
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
        elif not isinstance(indices, Iterable):
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

        # Build flat arrays
        R_list, F_list, E_list, z_list = [], [], [], []
        offsets = [0]

        # Detect force and energy keys from first frame
        force_keys = self._detectForceKeys()
        energy_keys = self._detectEnergyKeys()

        # Determine which keys to use:
        # - Empty string "" means use calculator (user explicitly selected it)
        # - Literal 'forces'/'energy' means use calculator (standard ASE keys)
        # - Specific key string means use that key from arrays/info
        # - None or not set means use first available key (legacy behavior)
        if self.selected_energy_key == "":
            selected_energy_key = None  # Use calculator
        elif self.selected_energy_key == "energy":
            selected_energy_key = None  # Standard ASE key, use calculator
        elif self.selected_energy_key:
            selected_energy_key = self.selected_energy_key
        else:
            selected_energy_key = energy_keys[0] if energy_keys else None

        if self.selected_force_key == "":
            selected_force_key = None  # Use calculator
        elif self.selected_force_key == "forces":
            selected_force_key = None  # Standard ASE key, use calculator
        elif self.selected_force_key:
            selected_force_key = self.selected_force_key
        else:
            selected_force_key = force_keys[0] if force_keys else None

        for atoms in self.atomsList:
            n_atoms = len(atoms)
            R_list.append(atoms.get_positions())

            # Handle forces
            if selected_force_key:
                # Use specific key from arrays
                F_list.append(atoms.arrays[selected_force_key])
            else:
                # Use calculator
                try:
                    F_list.append(atoms.get_forces())
                except:
                    # If forces not available, use zeros
                    F_list.append(np.zeros((n_atoms, 3)))

            # Handle energies
            if selected_energy_key:
                # Use specific key from info
                E_list.append(atoms.info[selected_energy_key])
            else:
                # Use calculator
                try:
                    E_list.append(atoms.get_potential_energy())
                except:
                    # If energy not available, use zero
                    E_list.append(0.0)

            z_list.append(atoms.get_atomic_numbers())
            offsets.append(offsets[-1] + n_atoms)

        # Convert to flat arrays
        self.R_flat = np.vstack(R_list)
        self.F_flat = np.vstack(F_list)
        self.E = np.array(E_list)
        self.z_flat = np.concatenate(z_list)
        self.molecule_offsets = np.array(offsets)

        # Store lattice info if present
        if hasattr(self.atomsList[0], "cell"):
            self.lattice = [atoms.get_cell() for atoms in self.atomsList]
        else:
            self.lattice = None

        # Chemical formula: show range
        atom_counts = self.getNAtoms()
        self.chem = f"Variable ({atom_counts.min()}-{atom_counts.max()} atoms)"

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
        elif not isinstance(indices, Iterable):
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
        return read_atom_field(self.atomsList, key, variable=True, indices=indices)

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

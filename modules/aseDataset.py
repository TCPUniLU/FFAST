import os
import logging
import numpy as np
from datasetLoaders.loader import DatasetLoader, VariableDatasetLoader
from client.dataType import AtomsList
import ase.io
from ase.io.trajectory import Trajectory
from collections.abc import Iterable

logger = logging.getLogger("FFAST")

class aseDatasetLoader(DatasetLoader):
    datasetName = "ase"
    datasetFileExtension = "*"
    saveFormats = ["db", "xyz", "extxyz", "traj", "vasp", "dftb"]

    def __init__(self, path, atomsList=None, selected_energy_key=None, selected_force_key=None, *args, **kwargs):
        super().__init__(path, *args, **kwargs)

        # Read file only if atomsList not provided (avoid double-read)
        if atomsList is None:
            logger.warning("atomsList was none, hence loading the entire dataset.")
            self.atomsList = ase.io.read(path, index=':')
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
        use_calculator = (selected_key == "" or selected_key == "energy" or selected_key == "<Use Energy>")

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
        use_calculator = (selected_key == "" or selected_key == "forces" or selected_key == "<Use Force>")

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
        super().__init__(path, *args, **kwargs)

        # Read file only if atomsList not provided (avoid double-read)
        if atomsList is None:
            logger.warning("atomsList was none, hence loading the entire dataset.")
            self.atomsList = ase.io.read(path, index=':')
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
        if self.selected_energy_key == "" or self.selected_energy_key == "<Use Energy>":
            selected_energy_key = None  # Use calculator
        elif self.selected_energy_key == "energy":
            selected_energy_key = None  # Standard ASE key, use calculator
        elif self.selected_energy_key:
            selected_energy_key = self.selected_energy_key
        else:
            selected_energy_key = energy_keys[0] if energy_keys else None

        if self.selected_force_key == "" or self.selected_force_key == "<Use Force>":
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


def loadData(env):
    """Smart loader that auto-detects uniform vs variable datasets."""

    class SmartASELoader:
        datasetName = "ase (auto)"
        datasetFileExtension = "*"
        saveFormats = ["db", "xyz", "extxyz", "traj", "vasp", "dftb"]

        def check_homogeneity(self, atoms_list):
            """
            Assume there are only two molecular structures in the world, then in a variable dataset the probability that
            two random drawn molecules are the same would be 1/2. Therefore, the probability that 3 random drawn
            molecules are the same would be 1/8. If we do this random comparison 20 times then the probability that at
            every random selection, all of the molecules are the same would be (1/8)^20 ~ 0. That's how this method
            guess with almost certainty whether a dataset is variable or fixed without iterating through the whole
            dataset.
            :param atoms_list: the dataset.
            :return: whether the dataset is homogeneous or not.
            """
            for i in range(20):
                temp_atoms_list = []
                for j in np.random.choice(len(atoms_list), size=3, replace=False):
                    temp_atoms_list.append(atoms_list[j].get_chemical_formula())
                if len(set(temp_atoms_list)) != 1:
                    return False

            return True

        def __call__(self, path: str, selected_energy_key=None, selected_force_key=None,
                     prediction_keys=None, show_dialog=True, slice_num=0, file_size=0):
            """Load ASE dataset with optional key selection.

            Args:
                path: Path to dataset file
                selected_energy_key: Pre-selected energy key for reference
                selected_force_key: Pre-selected force key for reference
                prediction_keys: List of (energy_key, force_key, model_name) tuples
                show_dialog: Whether to show selection dialog (False when loading from session)

            Returns:
                tuple: (dataset_loader, prediction_keys) or (None, None) if cancelled
            """
            # Read file ONCE
            if slice_num == 0:
                if path.endswith(".traj"):
                    logger.info("Trajectory dataset detected, loading with class ase.io.Trajectory")
                    atomsList = Trajectory(path)
                else:
                    atomsList = AtomsList(path)
            elif slice_num == -1:
                atomsList = ase.io.read(path, index=':')
            else:
                atomsList = ase.io.read(path, index=slice(0, None, slice_num))
                file_size /= float(slice_num)

            # atom_counts = [len(atoms) for atoms in atomsList] --> inefficient for large datasets because it
            # literally creates a copy of the entire dataset on RAM, just to check whether the dataset is variable or
            # fixed. Instead, the following probabilistic method:
            fixed_or_variable = self.check_homogeneity(atomsList)
            # Handle key selection first (if dialog needed)
            if show_dialog and (selected_energy_key is None or selected_force_key is None):
                # Create temporary loader just to detect keys and show dialog
                if fixed_or_variable:
                    temp_loader = aseDatasetLoader(path, atomsList=atomsList)
                else:
                    temp_loader = VariableASEDatasetLoader(path, atomsList=atomsList)

                # Check if multiple keys exist
                energy_keys = temp_loader.EneregyKeys()
                force_keys = temp_loader.ForceKeys()

                if len(energy_keys) > 1 or len(force_keys) > 1:
                    selection = temp_loader.promptKeySelection()

                    if selection is None:
                        # User cancelled
                        logger.info("Dataset loading cancelled by user")
                        return None, None

                    selected_energy_key = selection['energy_ref']
                    selected_force_key = selection['force_ref']
                    prediction_keys = selection['predictions']

            # Create loader with selected keys passed to constructor
            if fixed_or_variable:
                # Uniform dataset
                logger.info(f"Loading uniform ASE dataset: {len(atomsList)} molecules, {len(atomsList[0])} atoms each")
                loader = aseDatasetLoader(
                    path,
                    atomsList=atomsList,
                    selected_energy_key=selected_energy_key,
                    selected_force_key=selected_force_key,
                    file_size=file_size
                )
            else:
                # Variable dataset
                logger.info(
                    f"Loading variable ASE dataset: {len(atomsList)} molecules.")
                loader = VariableASEDatasetLoader(
                    path,
                    atomsList=atomsList,
                    selected_energy_key=selected_energy_key,
                    selected_force_key=selected_force_key,
                    file_size=file_size
                )

            return loader, prediction_keys or []

    env.initialiseDatasetType(SmartASELoader())

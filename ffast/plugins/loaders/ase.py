"""Discoverable ASE dataset-loader plugin (ADR 0048).

Bundled inside the ``ffast`` package (not the Desktop-Client ``modules/``
tree), so it registers on a headless ``pip install ffast`` with no ``modules/``
present. The loader classes themselves live in ``ffast.loaders.ase``, shared
with server code that constructs them directly (Loading Coordinator, session
export); this module only holds the smart auto-detecting entry point that
``ffast.core.plugin_discovery`` calls.
"""
import logging
import ase.io
from ase.io.trajectory import Trajectory

from ffast.core.data_types import AtomsList
from ffast.loaders.ase import aseDatasetLoader, VariableASEDatasetLoader, isUniformAtomsList

logger = logging.getLogger("FFAST")


def loadData(env):
    """Smart loader that auto-detects uniform vs variable datasets."""

    class SmartASELoader:
        datasetName = "ase (auto)"
        datasetFileExtension = "*"
        saveFormats = ["db", "xyz", "extxyz", "traj", "vasp", "dftb"]

        def __call__(self, path: str, selected_energy_key=None, selected_force_key=None,
                     prediction_keys=None, show_dialog=True, slice_num=0):
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

            # Uniform or variable: the same fixed-sample rule predictions use, so
            # a file is classified the same way on every load (ADR 0058).
            fixed_or_variable = isUniformAtomsList(atomsList)
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
                    selected_force_key=selected_force_key
                )
            else:
                # Variable dataset
                logger.info(
                    f"Loading variable ASE dataset: {len(atomsList)} molecules.")
                loader = VariableASEDatasetLoader(
                    path,
                    atomsList=atomsList,
                    selected_energy_key=selected_energy_key,
                    selected_force_key=selected_force_key
                )

            return loader, prediction_keys or []

    env.initialiseDatasetType(SmartASELoader())

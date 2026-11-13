"""Discoverable ASE dataset-loader plugin (ADR 0048).

Bundled inside the ``ffast`` package (not an external ``modules/``
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
from ffast.io.frame_index import is_indexable, read_every
from ffast.loaders.ase import aseDatasetLoader

logger = logging.getLogger("FFAST")


def loadData(env):
    """Smart loader that auto-detects uniform vs variable datasets."""

    class SmartASELoader:
        datasetName = "ase (auto)"
        datasetFileExtension = "*"
        saveFormats = ["db", "xyz", "extxyz", "traj", "vasp", "dftb"]

        def __call__(self, path: str, selected_energy_key=None, selected_force_key=None,
                     prediction_keys=None, show_dialog=True, slice_num=0, progress=None):
            """Load an ASE dataset with the keys the caller chose.

            Args:
                path: Path to dataset file
                selected_energy_key: Pre-selected energy key for reference
                selected_force_key: Pre-selected force key for reference
                prediction_keys: List of (energy_key, force_key, model_name) tuples
                show_dialog: Ignored. Keys are chosen before the load starts
                    (the client asks); kept so existing callers still work.
                progress: optional progress(done, total) called as frames are read (ADR 0076)

            Returns:
                tuple: (dataset_loader, prediction_keys)
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
            elif is_indexable(path):
                atomsList = AtomsList(path, step=slice_num)
            else:
                atomsList = read_every(path, slice_num)

            # One loader for every ASE file; it decides uniform or variable from
            # the frames itself, by the fixed-sample rule (ADR 0058).
            loader = aseDatasetLoader(
                path,
                atomsList=atomsList,
                selected_energy_key=selected_energy_key,
                selected_force_key=selected_force_key,
                progress=progress,
            )
            logger.info(
                "Loading %s ASE dataset: %d molecules",
                "variable" if loader.isVariable else "uniform", len(atomsList),
            )
            return loader, prediction_keys or []

    env.initialiseDatasetType(SmartASELoader())

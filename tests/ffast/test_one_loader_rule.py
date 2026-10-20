"""
Opening a dataset picks uniform or variable by the same rule as predictions.

Dataset opening used a random sample of frames (``check_homogeneity``), so a
large file with a rare different frame could be classified differently on
each load, changing the loader class and so the dataset's fingerprint.
Predictions used a fixed sample. Both now use ``isUniformAtomsList``
(ADR 0058, decision A).
"""
import types

import ase.io
import numpy as np
from ase import Atoms

from ffast.loaders.ase import isUniformAtomsList
from ffast.plugins.loaders.ase import loadData

N_FRAMES = 100
UNSAMPLED = 21  # a frame the fixed 60-frame sample of 100 frames skips


def _file_with_a_hidden_odd_frame(tmp_path):
    frames = [Atoms("H2O", positions=np.zeros((3, 3)) + i) for i in range(N_FRAMES)]
    frames[UNSAMPLED] = Atoms("NH3", positions=np.zeros((4, 3)))
    path = tmp_path / "hidden.xyz"
    ase.io.write(path, frames, format="extxyz")
    return path, frames


def _dataset_opener():
    registered = {}
    loadData(types.SimpleNamespace(initialiseDatasetType=lambda t: registered.setdefault("ase", t)))
    return registered["ase"]


def test_dataset_opening_classifies_a_file_as_the_shared_rule_does_every_time(tmp_path):
    path, frames = _file_with_a_hidden_odd_frame(tmp_path)
    expected_uniform = isUniformAtomsList(frames)
    open_dataset = _dataset_opener()

    for _ in range(20):
        dataset, _ = open_dataset(str(path), show_dialog=False)
        assert dataset.isVariable is (not expected_uniform)

"""
Opening a dataset picks uniform or variable by the same rule as predictions.

Dataset opening used a random sample of frames (``check_homogeneity``), so a
large file with a rare different frame could be classified differently on
each load, changing the loader class and so the dataset's fingerprint.
Predictions used a fixed sample. Both now use ``isUniformAtomsList`` for the
fingerprint (ADR 0058, decision A). Since ADR 0058 step 6 "uniform" asks more:
the same atoms in the same order in every frame, checked on every frame, so a
frame the sample skips is still found.
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


def test_dataset_opening_classifies_a_file_the_same_way_every_time(tmp_path):
    path, frames = _file_with_a_hidden_odd_frame(tmp_path)
    assert isUniformAtomsList(frames)          # the fingerprint's sample misses it
    open_dataset = _dataset_opener()

    fingerprints = set()
    for _ in range(20):
        dataset, _ = open_dataset(str(path), show_dialog=False)
        assert dataset.isVariable is True      # every frame is checked
        fingerprints.add(dataset.getFingerprint())
    assert len(fingerprints) == 1


def test_frames_listing_the_same_atoms_in_another_order_are_not_uniform():
    """HOH then OHH: the same formula, so the fingerprint keeps its uniform
    formula, but each frame needs its own element list."""
    from ffast.loaders.ase import aseDatasetLoader

    frames = [Atoms("HOH", positions=np.zeros((3, 3))), Atoms("OHH", positions=np.zeros((3, 3)))]
    dataset = aseDatasetLoader("o.xyz", atomsList=frames)
    assert dataset.isVariable is True
    assert isUniformAtomsList(frames)

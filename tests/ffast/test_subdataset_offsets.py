"""A frame subset of structures of different sizes knows where each one's
atoms start.

Regression: per-structure metrics (force error per structure, the Forces MAE
timeline) cut the flat per-atom arrays at ``molecule_offsets``. A SubDataset
of a dataset whose structures differ in size had none, so those metrics
failed for it and a plot of the subset showed nothing. The subset now builds
its offsets from the atom counts of the structures it holds.
"""
import types

import numpy as np
import pytest

from ffast.loaders.dataset import SubDataset


def _sub(indices, variable=True):
    parent = types.SimpleNamespace(isVariable=variable, getNAtoms=lambda: np.array([3, 1, 2, 5]))
    sub = SubDataset.__new__(SubDataset)
    sub.parent, sub.indices = parent, indices
    return sub


def test_offsets_follow_the_structures_the_subset_holds():
    assert _sub([3, 0]).molecule_offsets.tolist() == [0, 5, 8]
    assert _sub(np.array([1, 2])).molecule_offsets.tolist() == [0, 1, 3]


def test_a_subset_of_same_sized_structures_has_none():
    with pytest.raises(AttributeError):
        _sub([0, 1], variable=False).molecule_offsets

"""A frame subset of structures of different sizes knows where each one's
atoms start.

Regression: per-structure metrics (force error per structure, the Forces MAE
timeline) cut the flat per-atom arrays at ``molecule_offsets``. A subset of a
dataset whose structures differ in size had none, so those metrics failed for it
and a plot of the subset showed nothing. A view builds its offsets from the
structures it holds.
"""
import types

import numpy as np
import pytest

from ffast.loaders.dataset import DatasetView


def _subset(frames, variable=True):
    parent = types.SimpleNamespace(loaded=True, fingerprint="P", getName=lambda: "parent",
                                   isVariable=variable,
                                   getOffsets=lambda: np.array([0, 3, 4, 6, 11]))
    return DatasetView.subset(parent, None, frames, "Subset")


def test_offsets_follow_the_structures_the_subset_holds():
    assert _subset([3, 0]).molecule_offsets.tolist() == [0, 5, 8]
    assert _subset(np.array([1, 2])).molecule_offsets.tolist() == [0, 1, 3]


def test_a_subset_of_same_sized_structures_has_none():
    with pytest.raises(AttributeError):
        _subset([0, 1], variable=False).molecule_offsets

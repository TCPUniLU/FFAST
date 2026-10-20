"""A frame subset draws the bonds of the structures it holds.

Regression: a subset of a dataset whose structures differ in size has no shared
bond lengths, so its bond matrix compared distances with None, the scene builder
dropped the bonds, and a subset made with SUB showed bare atoms where its parent
showed bonds. A view asks its parent for the bonds of the frame it is.
"""
import types

import numpy as np

from ffast.loaders.dataset import DatasetView


def _subset(frames):
    # Each frame's bonds name the frame, so the test can tell them apart.
    parent = types.SimpleNamespace(loaded=True, fingerprint="P", getName=lambda: "parent",
                                   getBondMatrix=lambda index: np.full((2, 2), index == 7))
    return DatasetView.subset(parent, None, frames, "Subset")


def test_a_frame_subset_takes_the_bonds_of_its_parents_frame():
    assert _subset([3, 7]).getBondMatrix(1).all()
    assert not _subset([3, 7]).getBondMatrix(0).any()


def test_it_works_with_frames_as_a_list_or_an_array():
    assert _subset(np.array([7])).getBondMatrix(0).all()
    assert _subset([7]).getBondMatrix(0).all()

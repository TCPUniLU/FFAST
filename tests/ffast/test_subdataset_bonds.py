"""A frame subset draws the bonds of the structures it holds.

Regression: a SubDataset of a dataset whose structures differ in size has no
shared bond lengths (``bondSizes`` is None), so ``getBondMatrix`` compared
distances with None, the scene builder dropped the bonds, and a subset made
with SUB showed bare atoms where its parent showed bonds. The subset now asks
its parent for the bonds of the frame it is. The methods run unbound
(duck-typed self) to avoid the heavy SubDataset constructor.
"""
import types

import numpy as np

from ffast.loaders.dataset import SubDataset


class _Parent:
    """Each frame's bonds name the frame, so the test can tell them apart."""

    def getBondMatrix(self, index):
        return np.full((2, 2), index == 7)


def _sub(indices):
    sub = types.SimpleNamespace(parent=_Parent(), indices=indices)
    sub.getBondMatrix = lambda index: SubDataset.getBondMatrix(sub, index)
    return sub


def test_a_frame_subset_takes_the_bonds_of_its_parents_frame():
    assert SubDataset.getBondMatrix(_sub([3, 7]), 1).all()
    assert not SubDataset.getBondMatrix(_sub([3, 7]), 0).any()


def test_it_works_with_indices_as_a_list_or_an_array():
    assert SubDataset.getBondMatrix(_sub(np.array([7])), 0).all()
    assert SubDataset.getBondMatrix(_sub([7]), 0).all()

"""An atom subset's predictions keep its atoms, for a variable file too.

Regression: a variable file's predictions are stored as one array per frame,
and ``AtomFilteredEntity`` read ``.shape`` off that list, so every metric over
predicted forces of an atom subset of a variable file failed with
``'list' object has no attribute 'shape'`` and its 2D plots stayed empty.
"""
import numpy as np

from ffast.core.data_types import DataEntity


def _forces(*natoms):
    """One (atoms, 3) array per frame; atom i of frame f holds 10*f + i."""
    return [np.array([[10 * f + i] * 3 for i in range(n)], dtype=float)
            for f, n in enumerate(natoms)]


def test_per_frame_forces_keep_the_subsets_atoms():
    entity = DataEntity("forces", forces=_forces(3, 5)).getAtomFilteredEntity([0, 2])
    kept = entity.get("forces")
    assert [f[:, 0].tolist() for f in kept] == [[0, 2], [10, 12]]


def test_uniform_forces_keep_the_subsets_atoms():
    forces = np.stack(_forces(3, 3)) - np.array([0, 10])[:, None, None]
    kept = DataEntity("forces", forces=forces).getAtomFilteredEntity([0, 2]).get("forces")
    assert kept.shape == (2, 2, 3) and kept[:, :, 0].tolist() == [[0, 2], [0, 2]]

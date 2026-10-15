"""
Every dataset kind answers per-frame questions the same way, whatever its parent.

Regressions, each hidden by a caller that caught the error and logged it:

- Elements: the uniform ASE loader's ``getElements()`` and the atom subset's
  took no frame number, while variable datasets and frame subsets did.
  Extract calls ``parent.getElements(0)``, so on a dataset whose structures
  all have the same atoms it created no subset. The browser Extract test
  missed it because its example file holds structures of different sizes.
- Unit cell: both subset kinds' ``getLattice()`` took no frame number, so the
  scene builder's ``getLattice(i)`` failed and a subset never showed the cell.
  A frame subset also answered with all of its parent's frames, not its own.
- Subset of a subset: a frame subset of a uniform parent read the parent's
  ``z`` attribute, which a frame subset does not have, instead of asking
  ``getElements()``.
"""
from ase import Atoms
import numpy as np
from ffast.loaders.ase import aseDatasetLoader
from ffast.loaders.dataset import AtomFilteredDataset 
from ffast.loaders.dataset import SubDataset


def _uniform():
    frames = [Atoms("H2O", positions=[[0, 0, 0], [0, 0, 1], [1, 0, 0]]) for _ in range(10)]
    dataset = aseDatasetLoader("test.xyz", atomsList=frames)
    dataset.initialise()
    return dataset


def test_uniform_dataset_answers_elements_for_one_frame():
    dataset = _uniform()
    np.testing.assert_array_equal(dataset.getElements(0), [1, 1, 8])


def test_atom_subset_answers_elements_for_one_frame():
    dataset = _uniform()
    subset = AtomFilteredDataset(dataset, np.array([0, 2]))
    np.testing.assert_array_equal(subset.getElements(0), [1, 8])


def _periodic():
    frames = [Atoms("H2O", positions=[[0, 0, 0], [0, 0, 1], [1, 0, 0]],
                    cell=[i + 1, i + 1, i + 1], pbc=True) for i in range(10)]
    dataset = aseDatasetLoader("periodic.xyz", atomsList=frames)
    dataset.initialise()
    return dataset


def test_frame_subset_answers_the_cell_of_its_own_frame():
    parent = _periodic()
    subset = SubDataset(parent, None, np.array([3, 7]), "Subset")
    subset.initialise()
    np.testing.assert_array_equal(subset.getLattice(1), np.eye(3) * 8)


def test_atom_subset_answers_the_cell_of_one_frame():
    parent = _periodic()
    subset = AtomFilteredDataset(parent, np.array([0, 2]))
    np.testing.assert_array_equal(subset.getLattice(4), np.eye(3) * 5)


def test_subset_of_a_subset_of_a_uniform_dataset_answers_elements():
    dataset = _uniform()
    subset = SubDataset(dataset, None, np.array([2, 4, 6]),subName="Subset")
    subset.initialise()
    subset_of_subset = SubDataset(subset, None, np.array([0,2]), subName="SubSubset")
    subset_of_subset.initialise()
    np.testing.assert_array_equal(subset_of_subset.getElements(1), [1, 1, 8])

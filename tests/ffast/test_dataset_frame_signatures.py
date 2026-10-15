"""
Every dataset answers "which elements are in frame i?" with a frame number.

Regression: the uniform ASE loader's ``getElements()`` and the atom subset's
``getElements()`` took no frame number, while variable datasets and frame
subsets did. Extract calls ``parent.getElements(0)``, so on a dataset whose
structures all have the same atoms it failed with ``TypeError`` and created
no subset; the server only logged a warning. The browser Extract test missed
it because its example file holds structures of different sizes.
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

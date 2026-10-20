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

Three more, kept as expected failures until ADR 0058 step 5 replaced the
three subset classes with one view:

- An atom subset of a variable dataset found no bonds: it had no bond sizes
  to compare with (``'<' not supported between 'float' and 'NoneType'``).
- A frame subset of a variable dataset could not pack its arrays for the
  client (``inhomogeneous shape``).
- Freezing an atom subset read ``modelDep``, which it did not have.
"""
import types

from ase import Atoms
import numpy as np
from ffast.core.environment import Environment
from ffast.loaders.ase import aseDatasetLoader
from ffast.loaders.dataset import DatasetView
from ffast.protocol.rpc import pack_arrays


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
    subset = DatasetView.atomSubset(dataset, np.array([0, 2]))
    np.testing.assert_array_equal(subset.getElements(0), [1, 8])


def _periodic():
    frames = [Atoms("H2O", positions=[[0, 0, 0], [0, 0, 1], [1, 0, 0]],
                    cell=[i + 1, i + 1, i + 1], pbc=True) for i in range(10)]
    dataset = aseDatasetLoader("periodic.xyz", atomsList=frames)
    dataset.initialise()
    return dataset


def test_frame_subset_answers_the_cell_of_its_own_frame():
    parent = _periodic()
    subset = DatasetView.subset(parent, None, np.array([3, 7]), "Subset")
    subset.initialise()
    np.testing.assert_array_equal(subset.getLattice(1), np.eye(3) * 8)


def test_atom_subset_answers_the_cell_of_one_frame():
    parent = _periodic()
    subset = DatasetView.atomSubset(parent, np.array([0, 2]))
    np.testing.assert_array_equal(subset.getLattice(4), np.eye(3) * 5)


def test_subset_of_a_subset_of_a_uniform_dataset_answers_elements():
    dataset = _uniform()
    subset = DatasetView.subset(dataset, None, np.array([2, 4, 6]),subName="Subset")
    subset.initialise()
    subset_of_subset = DatasetView.subset(subset, None, np.array([0,2]), subName="SubSubset")
    subset_of_subset.initialise()
    np.testing.assert_array_equal(subset_of_subset.getElements(1), [1, 1, 8])


def _variable():
    rng = np.random.default_rng(0)
    frames = [Atoms(s, positions=rng.normal(size=(len(Atoms(s)), 3)) * 0.6)
              for s in ["H2O", "CH4", "NH3", "C2H6"]]
    dataset = aseDatasetLoader("variable.xyz", atomsList=frames)
    dataset.initialise()
    return dataset


def test_atom_subset_of_a_variable_parent_finds_the_bonds_between_its_atoms():
    parent = _variable()
    view = DatasetView.atomSubset(parent, np.array([0, 1]))
    view.initialise()
    for i in range(view.getN()):
        np.testing.assert_array_equal(view.getBondMatrix(i), parent.getBondMatrix(i)[:2, :2])


def test_subset_of_a_variable_parent_packs_its_arrays():
    parent = _variable()
    view = DatasetView.subset(parent, None, np.array([3, 0]), "Subset")
    view.initialise()
    arrays = view.to_transfer_arrays()
    assert arrays["offsets"].tolist() == [0, 8, 11]
    pack_arrays("fp", arrays)


def _freeze(view):
    added = []
    env = types.SimpleNamespace(datasets=types.SimpleNamespace(
        get=lambda fp: view if fp == "x" else None, add=added.append))
    Environment.freezeSubDataset(env, "x")
    return added


def test_only_a_subset_that_follows_a_zoom_can_be_frozen():
    parent = _variable()
    atoms = DatasetView.atomSubset(parent, np.array([0, 1]))
    frozen = DatasetView.frozenSubset(parent, None, np.array([1]), "Frozen")
    assert _freeze(atoms) == [] and _freeze(frozen) == []

    live = DatasetView.subset(parent, None, np.array([2, 1]), "Subset")
    live.initialise()
    [copy] = _freeze(live)
    assert copy.frozen and copy.frames.tolist() == [2, 1]
    live.setIndices([0])
    assert copy.frames.tolist() == [2, 1]

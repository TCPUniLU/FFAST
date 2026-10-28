"""Extract is offered for whole datasets and frozen subsets.

A frozen subset keeps its frames, so its atoms make a fixed dataset. A live
subset changes with the plot's zoom, and an atom subset already chose its
atoms, so neither offers Extract. The browser's ``canExtractFrom`` follows
the same rule.
"""
import types

from ase import Atoms
import numpy as np

from ffast.core.environment import Environment
from ffast.loaders.ase import aseDatasetLoader
from ffast.loaders.dataset import DatasetView
from ffast.protocol import control


def _variable():
    frames = [Atoms("H2O"), Atoms("CH4"), Atoms("H2"), Atoms("NH3")]
    parent = aseDatasetLoader("var.xyz", atomsList=frames)
    parent.initialise()
    return parent


def test_whole_datasets_and_frozen_subsets_offer_extract():
    parent = _variable()
    assert parent.canExtractAtoms
    assert DatasetView.frozenSubset(parent, None, np.array([0, 1]), "E").canExtractAtoms


def test_live_atom_and_element_subsets_do_not():
    parent = _variable()
    assert not DatasetView.subset(parent, None, np.array([0, 1]), "E").canExtractAtoms
    assert not DatasetView.elementSubset(parent, [1]).canExtractAtoms
    uniform = aseDatasetLoader("uni.xyz", atomsList=[Atoms("H2O"), Atoms("H2O")])
    uniform.initialise()
    assert not DatasetView.atomSubset(uniform, [0, 1]).canExtractAtoms


def test_atoms_extracted_from_a_frozen_subset_need_only_its_structures():
    # H2 (frame 2) lacks atom 2, but the frozen subset holds frames 0 and 1,
    # which are both water: the frozen subset's structures do not differ.
    parent = aseDatasetLoader("mix.xyz", atomsList=[Atoms("H2O"), Atoms("H2O"), Atoms("H2")])
    parent.initialise()
    frozen = DatasetView.frozenSubset(parent, None, np.array([0, 1]), "E")
    frozen.initialise()
    told = []
    env = types.SimpleNamespace(
        datasets=types.SimpleNamespace(get=lambda fp: None, add=lambda d: None),
        remote=types.SimpleNamespace(tellServer=lambda e, *a, **k: told.append((e, k))))
    Environment.createAtomFilteredDataset(env, frozen, [0, 2])
    assert told == [(control.CREATE_SUBSET,
                     {"parent_fingerprint": frozen.fingerprint, "indices": [0, 2]})]


def test_elements_extracted_from_a_frozen_subset_keep_its_frames():
    parent = _variable()
    frozen = DatasetView.frozenSubset(parent, None, np.array([3, 1]), "E")
    frozen.initialise()
    carbons = DatasetView.elementSubset(frozen, [6])
    carbons.initialise()
    assert carbons.getN() == 2
    assert [carbons.getElements(i).tolist() for i in range(2)] == [[], [6]]

"""
The one ASE loader keeps each kind's old shape for all-frames answers.

ADR 0058 step 3 merged the uniform and variable ASE loaders. Step 4 added
the getAll* methods (tests/ffast/test_all_frames_methods.py) and step 6 moved
the metrics, prediction loading and export to them. The older no-argument
calls still serve the wire to the Qt proxy and the getAll* defaults the sGDML
dataset and the proxy rely on, and those expect these shapes. This test goes
with ADR 0058 step 7 and issue #22. (While both classes existed, the merged
one was checked against the old uniform loader question by question: commits
66c23de..79930eb.)
"""
import numpy as np
from ase import Atoms
from ase.calculators.singlepoint import SinglePointCalculator

from ffast.loaders.ase import aseDatasetLoader


def _loader(formulas):
    rng = np.random.default_rng(9)
    frames = []
    for formula in formulas:
        atoms = Atoms(formula, positions=rng.normal(size=(len(Atoms(formula)), 3)))
        atoms.calc = SinglePointCalculator(
            atoms, energy=float(rng.normal()), forces=rng.normal(size=(len(atoms), 3)))
        frames.append(atoms)
    dataset = aseDatasetLoader("x.xyz", atomsList=frames)
    dataset.initialise()
    return dataset


def test_uniform_data_answers_all_frames_as_stacked_arrays():
    dataset = _loader(["H2O"] * 5)
    assert dataset.isVariable is False

    for answer in (dataset.getCoordinates(), dataset.getForces()):
        assert type(answer) is np.ndarray and answer.shape == (5, 3, 3)
    assert dataset.getCoordinates([3, 0]).shape == (2, 3, 3)
    assert dataset.getElements().tolist() == [1, 1, 8]          # one structure's
    assert dataset.getNAtoms() == 3 and type(dataset.getNAtoms()) is int
    assert dataset.getChemicalFormula() == "H2O"
    assert dataset.bondSizes.shape == (3, 3)                      # shared bond table
    assert dataset.toMetaDict()["variable"] is False
    assert dataset.to_transfer_arrays()["R"].shape == (5, 3, 3)


def test_variable_data_answers_all_frames_as_lists():
    dataset = _loader(["H2O", "CH4", "H2"])
    assert dataset.isVariable is True

    for answer in (dataset.getCoordinates(), dataset.getForces()):
        assert type(answer) is list and [len(a) for a in answer] == [3, 5, 2]
    assert len(dataset.getElements()) == 10                       # every atom's
    assert dataset.getNAtoms().tolist() == [3, 5, 2]
    assert dataset.getChemicalFormula() == "Variable (2-5 atoms)"
    assert dataset.bondSizes is None                              # per structure
    assert dataset.toMetaDict()["variable"] is True
    assert dataset.to_transfer_arrays()["R_flat"].shape == (10, 3)

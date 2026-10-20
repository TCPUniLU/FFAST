"""
A variable dataset reads one frame from the file (ADR 0058, decision B).

The answers must equal what slicing the joined arrays gave before:
VariableDatasetLoader's own methods are the oracle, frame by frame.
"""
import numpy as np
import pytest
from ase import Atoms
from ase.calculators.singlepoint import SinglePointCalculator

from ffast.loaders.ase import VariableASEDatasetLoader
from ffast.loaders.dataset import VariableDatasetLoader


def _variable():
    rng = np.random.default_rng(5)
    frames = [Atoms(s, positions=rng.normal(size=(n, 3))) for s, n in [("H2O", 3), ("CH4", 5), ("H2", 2)]]
    for atoms in frames:
        atoms.calc = SinglePointCalculator(
            atoms, energy=float(rng.normal()), forces=rng.normal(size=(len(atoms), 3)))
    return VariableASEDatasetLoader("v.xyz", atomsList=frames)


@pytest.mark.parametrize("ask", ["getCoordinates", "getElements", "getForces", "getEnergies"])
def test_one_frame_read_from_the_file_equals_the_array_slice(ask):
    dataset = _variable()
    for i in range(dataset.getN()):
        from_file = getattr(dataset, ask)(i)
        from_arrays = getattr(VariableDatasetLoader, ask)(dataset, i)
        np.testing.assert_array_equal(from_file, from_arrays)

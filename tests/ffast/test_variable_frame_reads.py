"""
A variable dataset answers one-frame reads from its joined arrays, built once
at load (ADR 0076, which replaces ADR 0058 decision B).

The answers must equal what slicing the joined arrays gave before:
FlatDatasetLoader's own methods are the oracle, frame by frame.
"""
import numpy as np
import pytest
from ase import Atoms
from ase.calculators.singlepoint import SinglePointCalculator

from ffast.loaders.ase import aseDatasetLoader
from ffast.loaders.dataset import FlatDatasetLoader


def _variable():
    rng = np.random.default_rng(5)
    frames = [Atoms(s, positions=rng.normal(size=(n, 3))) for s, n in [("H2O", 3), ("CH4", 5), ("H2", 2)]]
    for atoms in frames:
        atoms.calc = SinglePointCalculator(
            atoms, energy=float(rng.normal()), forces=rng.normal(size=(len(atoms), 3)))
    return aseDatasetLoader("v.xyz", atomsList=frames)


@pytest.mark.parametrize("ask", ["getCoordinates", "getElements", "getForces", "getEnergies"])
def test_one_frame_read_from_the_file_equals_the_array_slice(ask):
    dataset = _variable()
    for i in range(dataset.getN()):
        from_file = getattr(dataset, ask)(i)
        from_arrays = getattr(FlatDatasetLoader, ask)(dataset, i)
        np.testing.assert_array_equal(from_file, from_arrays)


def test_the_joined_arrays_are_built_once_at_load():
    """ADR 0076: the constructor reads every frame once; opening, summaries and
    one-frame reads reuse the same arrays."""
    dataset = _variable()
    joined = dataset._joinedArrays
    assert joined is not None
    dataset.initialise()
    dataset.toMetaDict()
    for i in range(dataset.getN()):
        dataset.getCoordinates(i), dataset.getForces(i), dataset.getEnergies(i)
    dataset.getForces(), dataset.getCoordinates()
    assert dataset._joinedArrays is joined

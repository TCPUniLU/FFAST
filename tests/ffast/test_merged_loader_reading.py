"""
The per-frame loader reads uniform data exactly as the uniform loader did.

ADR 0058 step 3 merges the two ASE loaders into the one that reads frames on
request (VariableASEDatasetLoader). Until the old uniform loader is deleted it
is the oracle: given the same uniform frames, both must answer every reading
question alike, in the same shape. Two loaders side by side, one the
reference: differential testing.
"""
import numpy as np
import pytest
from ase import Atoms
from ase.calculators.singlepoint import SinglePointCalculator

from ffast.loaders.ase import VariableASEDatasetLoader, aseDatasetLoader


def _frames(results=True, periodic=False, field=False, energy_key=False):
    rng = np.random.default_rng(7)
    frames = []
    for i in range(5):
        atoms = Atoms("H2O", positions=rng.normal(size=(3, 3)),
                      cell=[i + 1] * 3 if periodic else None, pbc=periodic)
        if results:
            atoms.calc = SinglePointCalculator(
                atoms, energy=float(rng.normal()), forces=rng.normal(size=(3, 3)))
        if field:
            atoms.arrays["charges"] = rng.normal(size=3)
        if energy_key:
            atoms.info["ref_energy"] = float(rng.normal())
        frames.append(atoms)
    return frames


# name -> (how the frames are made, how the loader is asked to read them)
DATA = {
    "energies and forces": (dict(), dict()),
    "no results": (dict(results=False), dict()),
    "periodic": (dict(periodic=True), dict()),
    "atom field": (dict(field=True), dict()),
    "energy from an info key": (dict(energy_key=True), dict(selected_energy_key="ref_energy")),
}

QUESTIONS = {
    "coordinates, all": lambda d: d.getCoordinates(),
    "coordinates, one": lambda d: d.getCoordinates(2),
    "coordinates, several": lambda d: d.getCoordinates([3, 0]),
    "forces, all": lambda d: d.getForces(),
    "forces, one": lambda d: d.getForces(2),
    "forces, several": lambda d: d.getForces([3, 0]),
    "energies, all": lambda d: d.getEnergies(),
    "energies, one": lambda d: d.getEnergies(2),
    "energies, several": lambda d: d.getEnergies([3, 0]),
    "elements, all": lambda d: d.getElements(),
    "elements, one": lambda d: d.getElements(2),
    "lattice, all": lambda d: d.getLattice(),
    "lattice, one": lambda d: d.getLattice(2),
    "lattice, several": lambda d: d.getLattice([1, 0]),
    "atom field, all": lambda d: d.getAtomField("charges"),
    "atom field, several": lambda d: d.getAtomField("charges", [3, 0]),
    "field keys": lambda d: d.availableFieldKeys(),
}


def _answer(dataset, question):
    """The answer, or the kind of error raised for an absent result."""
    try:
        return QUESTIONS[question](dataset)
    except RuntimeError:  # absent result; PropertyNotImplementedError is one too
        return RuntimeError


@pytest.mark.parametrize("question", list(QUESTIONS))
@pytest.mark.parametrize("data", list(DATA))
def test_uniform_data_reads_as_the_uniform_loader_read_it(data, question):
    make, read = DATA[data]
    old = aseDatasetLoader("u.xyz", atomsList=_frames(**make), **read)
    new = VariableASEDatasetLoader("u.xyz", atomsList=_frames(**make), **read)

    assert new.isVariable is False
    expected, got = _answer(old, question), _answer(new, question)
    if expected is RuntimeError or got is RuntimeError:
        assert got is expected
    elif expected is None or got is None:
        assert got is expected
    elif isinstance(expected, tuple):
        assert got == expected
    else:
        # Same type, not only same numbers: callers tell a list of frames
        # from one (frames, atoms, 3) array with isinstance(..., list).
        assert type(got) is type(expected)
        assert np.shape(got) == np.shape(expected)
        np.testing.assert_allclose(np.asarray(got, dtype=float), np.asarray(expected, dtype=float))

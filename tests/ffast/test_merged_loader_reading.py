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
    # identity
    "frame count": lambda d: d.getN(),
    "atom count, all": lambda d: d.getNAtoms(),
    "atom count, one": lambda d: d.getNAtoms(2),
    "chemical formula": lambda d: d.getChemicalFormula(),
    "chem attribute": lambda d: d.chem,
    "element names": lambda d: d.getElementsName(),
    "is uniform": lambda d: d.isUniform(),
    "fingerprint": lambda d: d.getFingerprint(),
    # reporting
    "bond sizes": lambda d: d.bondSizes,
    "bond matrix": lambda d: d.getBondMatrix(2),
    "bond indices": lambda d: d.getBondIndices(2),
    "distances, all": lambda d: d.getPDist(),
    "distances, one": lambda d: d.getPDist(2),
    "info panel": lambda d: d.getBaseInfo(),
    # The loader's own name differs until the old class is deleted (step 3c).
    "summary": lambda d: {k: v for k, v in d.toMetaDict().items() if k != "source_type"},
    "browser transfer": lambda d: d.to_transfer_arrays(),
}


def _answer(dataset, question):
    """The answer, or the kind of error raised for an absent result."""
    try:
        return QUESTIONS[question](dataset)
    except RuntimeError:  # absent result; PropertyNotImplementedError is one too
        return RuntimeError


def _assert_same(got, expected):
    """Equal in value, shape and type, walking into dicts and sequences."""
    if expected is RuntimeError or expected is None or got is RuntimeError or got is None:
        assert got is expected
    elif isinstance(expected, dict):
        assert type(got) is dict and got.keys() == expected.keys()
        for key in expected:
            _assert_same(got[key], expected[key])
    elif isinstance(expected, (str, bool, int, float)):
        assert type(got) is type(expected)
        assert got == expected
    elif isinstance(expected, (tuple, list)) and not (expected and isinstance(expected[0], np.ndarray)):
        assert type(got) is type(expected) and len(got) == len(expected)
        for g, e in zip(got, expected):
            _assert_same(g, e)
    else:
        # Same type, not only same numbers: callers tell a list of frames
        # from one (frames, atoms, 3) array with isinstance(..., list).
        assert type(got) is type(expected)
        assert np.shape(got) == np.shape(expected)
        np.testing.assert_allclose(np.asarray(got, dtype=float), np.asarray(expected, dtype=float))


@pytest.mark.parametrize("question", list(QUESTIONS))
@pytest.mark.parametrize("data", list(DATA))
def test_uniform_data_reads_as_the_uniform_loader_read_it(data, question):
    make, read = DATA[data]
    old = aseDatasetLoader("u.xyz", atomsList=_frames(**make), **read)
    new = VariableASEDatasetLoader("u.xyz", atomsList=_frames(**make), **read)
    old.initialise()
    new.initialise()

    assert new.isVariable is False
    _assert_same(_answer(new, question), _answer(old, question))

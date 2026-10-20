"""
Fingerprints of file datasets and views must not change (ADR 0058, section 3).

A file dataset's fingerprint is the key of its saved session entry and of its
saved prediction caches. If a refactor changes how it is computed, every saved
session and cache silently stops matching.

The expected values below were computed from the code as it stood before the
refactor (2026-10-16). They are literals on purpose: a test that recomputed
them with the same code could never fail.

If a value here must change on purpose, say so in the commit, and in the ADR.
The one case ADR 0058 allows is a variable file with no forces, which is not
pinned here because its value is expected to change.
"""
import numpy as np
import pytest
from ase import Atoms
from ase.calculators.singlepoint import SinglePointCalculator

from ffast.core.loading_coordinator import LoadingCoordinator
from ffast.loaders.dataset import DatasetView


def _with_results(atoms, i):
    rng = np.random.default_rng(100 + i)
    atoms.calc = SinglePointCalculator(
        atoms, energy=float(rng.normal()), forces=rng.normal(size=(len(atoms), 3)))
    return atoms


def _uniform(results):
    frames = [Atoms("H2O", positions=[[0, 0, 0], [0, 0, 1 + 0.1 * i], [1, 0, 0]])
              for i in range(6)]
    if results:
        frames = [_with_results(a, i) for i, a in enumerate(frames)]
    return LoadingCoordinator._aseLoaderFor("u.xyz", atomsList=frames)


def _variable_with_forces():
    rng = np.random.default_rng(1)
    frames = [_with_results(Atoms(s, positions=rng.normal(size=(n, 3))), i)
              for i, (s, n) in enumerate([("H2O", 3), ("CH4", 5), ("H2", 2), ("NH3", 4)])]
    return LoadingCoordinator._aseLoaderFor("v.xyz", atomsList=frames)


def _same_formula_different_order():
    """Both frames are H2O, but frame 1 lists its atoms as O, H, H."""
    positions = [[0, 0, 0], [0, 0, 1], [1, 0, 0]]
    frames = [Atoms("HOH", positions=positions), Atoms("OHH", positions=positions)]
    return LoadingCoordinator._aseLoaderFor("o.xyz", frames)


def _initialised(dataset):
    dataset.initialise()
    return dataset


FRAMES = np.array([3, 0])
ATOMS = np.array([0, 1])

# name -> (builder, expected fingerprint)
CASES = {
    "uniform, coordinates only": (lambda: _initialised(_uniform(False)), "b7caebb08df9bbdc3a3603fc94b78e58"),
    "uniform, energies and forces": (lambda: _initialised(_uniform(True)), "5f42b75c5eabfacbec2764e184f0e7c2"),
    "variable, energies and forces": (lambda: _initialised(_variable_with_forces()), "328e2260e4fee6afa0a35d2ff2f8d09b"),
    "live subset of uniform": (
        lambda: _initialised(DatasetView.subset(_initialised(_uniform(True)), None, FRAMES, "S")), "579905436f335c30db08e81818dd8cde"),
    "frozen subset of uniform": (
        lambda: _initialised(DatasetView.frozenSubset(_initialised(_uniform(True)), None, FRAMES, "S")), "0bbfaa860df0d3b5165ba80dadb03168"),
    "atom subset of uniform": (
        lambda: _initialised(DatasetView.atomSubset(_initialised(_uniform(True)), ATOMS)), "d7e258a5e4b147518b495c184fd4ebbc"),
    "live subset of variable": (
        lambda: _initialised(DatasetView.subset(_initialised(_variable_with_forces()), None, FRAMES, "S")), "dbde3e571cf466753ffb35fcafe95e7e"),
    "frozen subset of variable": (
        lambda: _initialised(DatasetView.frozenSubset(_initialised(_variable_with_forces()), None, FRAMES, "S")), "a0645fcc35cb98dfd6a2029760d1d0fd"),
    "atom subset of variable": (
        lambda: _initialised(DatasetView.atomSubset(_initialised(_variable_with_forces()), ATOMS)), "7cb961757a4d4253fd9ff48766939e8c"),
    "same formula, atoms in different order": (
        lambda: _initialised(_same_formula_different_order()), "e7b297e53caa1652933b842080bfb299"),
    }


@pytest.mark.parametrize("name", list(CASES))
def test_fingerprint_is_unchanged(name):
    build, expected = CASES[name]
    assert build().fingerprint == expected


def test_live_subset_fingerprint_ignores_its_frames():
    """A live subset is refreshed as the plot zoom changes, so its identity
    must not depend on which frames it currently holds."""
    parent = _initialised(_uniform(True))
    a = _initialised(DatasetView.subset(parent, None, np.array([3, 0]), "S"))
    b = _initialised(DatasetView.subset(parent, None, np.array([1, 2, 4]), "S"))
    assert a.fingerprint == b.fingerprint


def test_frozen_subset_fingerprint_depends_on_its_frames():
    parent = _initialised(_uniform(True))
    a = _initialised(DatasetView.frozenSubset(parent, None, np.array([3, 0]), "S"))
    b = _initialised(DatasetView.frozenSubset(parent, None, np.array([1, 2, 4]), "S"))
    assert a.fingerprint != b.fingerprint

def test_frame_with_reordered_atoms_reports_its_own_elements():
    """The old uniform loader gave frame 0's elements for every frame; the
    merged loader reads each frame's own (ADR 0058 step 3). The fingerprint
    above is unchanged: it still hashes frame 0's elements (decision A)."""
    dataset = _initialised(_same_formula_different_order())
    np.testing.assert_array_equal(dataset.getElements(1), [8, 1, 1])


@pytest.mark.parametrize("energies, forces", [(True, True), (True, False), (False, True), (False, False)],
                         ids=["both", "energies only", "forces only", "neither"])
def test_frame_by_frame_fingerprint_matches_the_array_formula(energies, forces):
    """The variable loader hashes one frame at a time (ADR 0058, decision B).
    The array formula it replaced is the oracle, for every mix of results."""
    from ffast.loaders.ase import aseDatasetLoader
    from ffast.loaders.dataset import VariableDatasetLoader
    rng = np.random.default_rng(3)
    frames = [Atoms(s, positions=rng.normal(size=(n, 3))) for s, n in [("H2O", 3), ("CH4", 5), ("H2", 2)]]
    for atoms in frames:
        results = {}
        if energies:
            results["energy"] = float(rng.normal())
        if forces:
            results["forces"] = rng.normal(size=(len(atoms), 3))
        if results:
            atoms.calc = SinglePointCalculator(atoms, **results)
    dataset = aseDatasetLoader("v.xyz", atomsList=frames)
    assert dataset.getFingerprint() == VariableDatasetLoader.getFingerprint(dataset)

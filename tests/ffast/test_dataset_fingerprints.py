"""
Fingerprints of file datasets and views must not change (ADR 0058, section 3).

A file dataset's fingerprint is the key of its saved session entry and of its
saved prediction caches. If a refactor changes how it is computed, every saved
session and cache silently stops matching.

The expected values below were computed from the code as it stood before the
refactor (2026-10-16). They are literals on purpose: a test that recomputed
them with the same code could never fail.

If a value here must change on purpose, say so in the commit, and in the ADR.
Every value was replaced on purpose when fingerprints moved from md5 to chunked
SHA-256 (ADR 0107); what they hash (which arrays, which formula) is unchanged.
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
    "uniform, coordinates only": (lambda: _initialised(_uniform(False)), "5171c529082db9ed912dcbc138494cd86a37fbd8e53016367e17c9f5a27247f9"),
    "uniform, energies and forces": (lambda: _initialised(_uniform(True)), "6882b73be1d53c5f5854bb721f6bf18098770580050d85b166ae9167a2119add"),
    "variable, energies and forces": (lambda: _initialised(_variable_with_forces()), "39b996bcba126e99b7782156fbf4676d12ffb9960123c3b586fe41d2d0820070"),
    "live subset of uniform": (
        lambda: _initialised(DatasetView.subset(_initialised(_uniform(True)), None, FRAMES, "S")), "ba55bf1480f3a2a9049ba9e8d173ea395b371e7a31fbfb2f49a7aa9b8f72662e"),
    "frozen subset of uniform": (
        lambda: _initialised(DatasetView.frozenSubset(_initialised(_uniform(True)), None, FRAMES, "S")), "3097c3011e29e71d4bd09b759a57cc3d0e0a16975cbf4af3b4f4c46a4d23d0aa"),
    "atom subset of uniform": (
        lambda: _initialised(DatasetView.atomSubset(_initialised(_uniform(True)), ATOMS)), "47ef76380e66839bae4f79ab1adc0ae3c65d4e5b0df5cf90423571431ab0f29f"),
    "live subset of variable": (
        lambda: _initialised(DatasetView.subset(_initialised(_variable_with_forces()), None, FRAMES, "S")), "b9e2e77d2b5d0038c039707b7597fb27b7d7adb4ce8ad2f29417e2b64c742287"),
    "frozen subset of variable": (
        lambda: _initialised(DatasetView.frozenSubset(_initialised(_variable_with_forces()), None, FRAMES, "S")), "ea336af030d2870775d7f95f97fe62d577d1320a3d07ab93a95351fec9dd3a87"),
    # Atom numbers are refused below a variable parent since ADR 0067; its
    # element subset's formula is new there (2026-10-28).
    "element subset of variable": (
        lambda: _initialised(DatasetView.elementSubset(_initialised(_variable_with_forces()), [6])), "31484212efe195b6a91a93b415910cf16e09dca07eb383adcab21f8e97bd1571"),
    "same formula, atoms in different order": (
        lambda: _initialised(_same_formula_different_order()), "2c2dda76e4cb21746b6912aa7f0db21869368a7657ad659f12d7e35c73dd0cef"),
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
    from ffast.loaders.dataset import FlatDatasetLoader
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
    assert dataset.getFingerprint() == FlatDatasetLoader.getFingerprint(dataset)


def test_sgdml_file_fingerprint_is_unchanged(tmp_path):
    """Computed before the sGDML loader moved to the flat storage (ADR 0058
    step 7)."""
    from ffast.plugins.models.sgdml import sGDMLDatasetLoader
    rng = np.random.default_rng(7)
    path = tmp_path / "s.npz"
    np.savez(path, R=rng.normal(size=(4, 3, 3)), E=rng.normal(size=(4, 1)),
             F=rng.normal(size=(4, 3, 3)), z=np.array([1, 1, 8]))
    assert _initialised(sGDMLDatasetLoader(str(path))).fingerprint == "ac28b172d873bb1e23602c5e8ba6e21017c495a397b1107991d0d2823a3f77cc"


def test_the_md5_field_of_an_sgdml_file_keeps_sgdmls_recipe(tmp_path):
    """The dataset's fingerprint moved to chunked SHA-256 (ADR 0107), but
    sGDML checks the ``md5`` field of its own files: md5 of the md5 of each
    array's flattened bytes."""
    import hashlib
    from ffast.cache.fingerprint import sgdml_md5
    rng = np.random.default_rng(7)
    R, E, F = rng.normal(size=(4, 3, 3)), rng.normal(size=(4, 1)), rng.normal(size=(4, 3, 3))
    outer = hashlib.md5()
    for a in (R, E, F):
        outer.update(hashlib.md5(a.ravel()).digest())
    assert sgdml_md5(R, E, F) == outer.hexdigest()

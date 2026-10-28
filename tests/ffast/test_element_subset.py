"""An element subset keeps the atoms of chosen elements in every frame (ADR 0067).

Typing "C" in Extract used to name frame 0's carbons by number, and an atom
subset kept those numbers in every frame: on a dataset whose structures differ,
frame 50's atoms 0, 3 and 7 were whatever atoms that structure had there. An
element subset tests each atom's own element, frame by frame, as
``where=element(...)`` does. A frame without the elements stays, empty.
"""
from __future__ import annotations

import numpy as np
import pytest

ase = pytest.importorskip("ase")
from ase import Atoms  # noqa: E402
from ase.calculators.singlepoint import SinglePointCalculator  # noqa: E402

from ffast.loaders.ase import aseDatasetLoader  # noqa: E402
from ffast.loaders.dataset import DatasetView  # noqa: E402
from ffast.visualization.scene_builder import resolve_element_tokens  # noqa: E402

CARBON, HYDROGEN, OXYGEN = 6, 1, 8


def _frame(symbols, seed, energy):
    rng = np.random.default_rng(seed)
    n = len(Atoms(symbols))
    atoms = Atoms(symbols, positions=rng.normal(size=(n, 3)))
    atoms.calc = SinglePointCalculator(atoms, energy=energy, forces=rng.normal(size=(n, 3)))
    return atoms


def _parent(frames, name="mem.extxyz"):
    parent = aseDatasetLoader(name, atomsList=frames)
    parent.initialise()
    return parent


def _variable_parent():
    """Frame 1 has no carbon; the others hold it at different atom numbers."""
    return _parent([_frame("CH4", 0, -1.0), _frame("H2O", 1, -2.0),
                    _frame("HOCH3", 2, -3.0), _frame("C2H6", 3, -4.0)], "mem-var.extxyz")


def _uniform_parent():
    return _parent([_frame("CH3OH", i, -float(i)) for i in range(3)], "mem-uni.extxyz")


def _element_subset(parent, keep):
    view = DatasetView.elementSubset(parent, keep)
    view.initialise()
    return view


def _carbons(parent, i):
    return np.flatnonzero(np.asarray(parent.getElements(i)) == CARBON)


# ── "C" keeps exactly the carbons, frame by frame ─────────────────────────────

def test_c_keeps_exactly_the_carbons_of_every_frame():
    parent = _variable_parent()
    view = _element_subset(parent, [CARBON])
    for i in range(parent.getN()):
        kept = _carbons(parent, i)
        assert view.getElements(i).tolist() == [CARBON] * len(kept)
        np.testing.assert_allclose(view.getCoordinates(i),
                                   np.asarray(parent.getCoordinates(i))[kept])
        np.testing.assert_allclose(view.getForces(i), np.asarray(parent.getForces(i))[kept])
        assert view.getNAtoms(i) == len(kept)


def test_the_flat_arrays_hold_the_same_carbons():
    parent = _variable_parent()
    view = _element_subset(parent, [CARBON])
    assert np.diff(view.getOffsets()).tolist() == [1, 0, 1, 2]
    assert view.getAllElements().tolist() == [CARBON] * 4
    expected = np.concatenate([np.asarray(parent.getCoordinates(i))[_carbons(parent, i)]
                               for i in range(parent.getN())])
    np.testing.assert_allclose(view.getAllCoordinates(), expected)


# ── a frame with none of the elements stays, empty ────────────────────────────

def test_a_frame_without_carbon_stays_empty_and_frame_numbers_match():
    parent = _variable_parent()
    view = _element_subset(parent, [CARBON])
    assert view.getN() == parent.getN()
    assert view.getNAtoms(1) == 0
    assert np.asarray(view.getCoordinates(1)).shape == (0, 3)
    assert np.asarray(view.getElements(1)).shape == (0,)
    assert np.asarray(view.getBondMatrix(1)).shape == (0, 0)
    # The structure's energy is still the whole structure's, frame for frame.
    assert view.getEnergies(1) == parent.getEnergies(1)
    np.testing.assert_allclose(view.getAllEnergies(), parent.getAllEnergies())


class _Events:
    def eventPush(self, *args, **kwargs):
        pass


class _Model:
    fingerprint = "model-fp"
    isGhost = True


def test_a_per_frame_force_metric_gives_nan_on_the_empty_frame():
    from ffast.cache import CacheKey
    from ffast.cache.store import DataCache
    from ffast.core.data_service import DataService
    from ffast.core.data_types import DataEntity
    from ffast.metrics.builtin import force_metrics  # noqa: F401 register
    from ffast.metrics.executor import InProcessExecutor
    from ffast.metrics.registry import default_registry

    parent = _variable_parent()
    view = _element_subset(parent, [CARBON])
    model = _Model()
    data = DataService(cache=DataCache(), models={model.fingerprint: model},
                       datasets={parent.fingerprint: parent, view.fingerprint: view},
                       tm=None, events=_Events(), executor=InProcessExecutor(default_registry))
    predicted = [np.asarray(parent.getForces(i)) + 1.0 for i in range(parent.getN())]
    data.cache[CacheKey("forces", model.fingerprint, parent.fingerprint).format()] = \
        DataEntity("forces", forces=predicted)

    key = data.generateMetric("ffast.force_difference[structure;component;mae]", {}, model, view)
    assert key is not None, data.metric_failures
    values = np.asarray(data.cache[key].values, dtype=float)
    assert values.shape == (4,)
    assert np.isnan(values[1])
    np.testing.assert_allclose(values[[0, 2, 3]], 1.0)


def test_predicted_forces_follow_each_frames_carbons():
    from ffast.core.data_types import DataEntity

    parent = _variable_parent()
    view = _element_subset(parent, [CARBON])
    predicted = [np.asarray(parent.getForces(i)) for i in range(parent.getN())]
    kept = DataEntity("forces", forces=predicted).getAtomFilteredEntity(view).get("forces")
    for i in range(parent.getN()):
        np.testing.assert_allclose(kept[i], view.getForces(i))


# ── export skips empty frames ──────────────────────────────────────────────────

def test_export_skips_empty_frames_and_says_how_many(tmp_path):
    view = _element_subset(_variable_parent(), [CARBON])
    out = tmp_path / "carbons.extxyz"
    skipped = aseDatasetLoader.saveDataset(view, str(out), "extxyz")
    assert skipped == 1
    back = ase.io.read(str(out), index=":")
    assert [frame.get_chemical_symbols() for frame in back] == [["C"], ["C"], ["C", "C"]]


# ── atom numbers are refused on a variable dataset ─────────────────────────────

def test_an_atom_subset_of_a_variable_dataset_is_refused():
    with pytest.raises(ValueError, match="differ"):
        DatasetView.atomSubset(_variable_parent(), np.array([0, 1]))


# ── on a uniform dataset "C" is today's atom subset ───────────────────────────

def test_on_a_uniform_dataset_c_holds_the_atom_subsets_atoms():
    parent = _uniform_parent()
    by_element = _element_subset(parent, [CARBON])
    by_number = DatasetView.atomSubset(parent, _carbons(parent, 0))
    by_number.initialise()
    assert by_element.getN() == by_number.getN()
    np.testing.assert_array_equal(by_element.getElements(), by_number.getElements())
    np.testing.assert_allclose(by_element.getCoordinates(), by_number.getCoordinates())
    np.testing.assert_allclose(by_element.getForces(), by_number.getForces())
    np.testing.assert_array_equal(by_element.getOffsets(), by_number.getOffsets())
    np.testing.assert_allclose(by_element.getAllCoordinates(), by_number.getAllCoordinates())
    assert by_element.getChemicalFormula() == by_number.getChemicalFormula()


# ── one fingerprint for the same elements kept ────────────────────────────────

def test_c_and_c_minus_h_on_a_c_h_dataset_are_one_subset():
    parent = _parent([_frame("CH4", 0, -1.0), _frame("C2H6", 1, -2.0)], "mem-ch.extxyz")
    present = np.unique(parent.getAllElements())
    keeps = [resolve_element_tokens(tokens, present) for tokens in (["C"], ["C", "-H"], ["-H"])]
    assert keeps == [[CARBON]] * 3
    prints = {_element_subset(parent, keep).fingerprint for keep in keeps}
    assert len(prints) == 1


def test_an_element_subset_is_not_the_atom_subset_of_the_same_numbers():
    parent = _uniform_parent()
    assert (DatasetView.elementSubsetFingerprint(parent, [CARBON])
            != DatasetView.atomSubsetFingerprint(parent, [CARBON]))


def test_resolving_tokens_refuses_what_is_not_an_element():
    with pytest.raises(ValueError, match="Xx"):
        resolve_element_tokens(["Xx"], [1, 6])


# ── views of an element subset ────────────────────────────────────────────────

@pytest.mark.parametrize("make", [DatasetView.subset, DatasetView.frozenSubset])
def test_a_subset_of_an_element_subset_holds_its_frames_carbons(make):
    parent = _variable_parent()
    carbons = _element_subset(parent, [CARBON])
    frames = np.array([3, 1, 0])
    view = make(carbons, None, frames, "Plot")
    view.initialise()
    assert view.getN() == 3
    for i, f in enumerate(frames):
        np.testing.assert_allclose(view.getCoordinates(i), carbons.getCoordinates(int(f)))
        assert view.getElements(i).tolist() == carbons.getElements(int(f)).tolist()
    assert np.diff(view.getOffsets()).tolist() == [2, 0, 1]
    np.testing.assert_allclose(view.getAllForces(), np.concatenate(
        [carbons.getForces(int(f)) for f in frames]).reshape(-1, 3))

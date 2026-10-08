"""A frame subset of structures of different sizes describes its own atoms.

Regression: ``SubDataset.getElements()`` handed out the parent's whole element
list, while positions and offsets were the subset's. Metrics that weigh atoms
by element (radius of gyration, per-element force errors, masses) cut that
list at the subset's offsets and so read other frames' elements: since the
subset got its own offsets (48a402e) the numbers were silently wrong, before
that the radius of gyration failed. The subset's announcement to other
windows also said every structure had the same size.
"""
import numpy as np
import pytest

from ffast.loaders.dataset import SubDataset, VariableDatasetLoader
from ffast.metrics.builtin.structure_metrics import gyradius
from ffast.metrics.input_resolver import InputResolver

COUNTS = [3, 1, 2, 5]
FRAMES = [3, 0]


class _Variable(VariableDatasetLoader):
    def __init__(self):
        super().__init__("variable.xyz")
        rng = np.random.default_rng(0)
        self.molecule_offsets = np.concatenate([[0], np.cumsum(COUNTS)])
        total = int(self.molecule_offsets[-1])
        self.R_flat = rng.normal(size=(total, 3))
        self.F_flat = rng.normal(size=(total, 3))
        self.z_flat = np.array([1, 6, 8, 7, 1, 1, 6, 6, 8, 8, 16])
        self.E = rng.normal(size=len(COUNTS))
        self.N = len(COUNTS)
        self.name = "variable"
        self.fingerprint = "parent-fp"
        self.loaded = True


@pytest.fixture
def parent():
    return _Variable()


@pytest.fixture
def sub(parent):
    s = SubDataset(parent, None, np.array(FRAMES), "Subset")
    s.initialise()
    return s


def _frames(parent, ref):
    """The parent's ``ref`` input restricted to FRAMES, per atom."""
    values = InputResolver(None).resolve(ref, dataset=parent)
    offs = parent.molecule_offsets
    return np.concatenate([values[offs[i]:offs[i + 1]] for i in FRAMES])


@pytest.mark.parametrize("ref", ["reference.elements", "reference.positions",
                                 "reference.forces", "reference.masses"])
def test_per_atom_inputs_are_the_parents_at_the_subset_frames(parent, sub, ref):
    got = InputResolver(None).resolve(ref, dataset=sub)
    np.testing.assert_array_equal(got, _frames(parent, ref))


def test_radius_of_gyration_matches_the_parent_at_the_same_frames(parent, sub):
    res = InputResolver(None)
    whole = gyradius(res.resolve("reference.positions", dataset=parent),
                     res.resolve("reference.elements", dataset=parent),
                     res.resolve("offsets", dataset=parent))
    part = gyradius(res.resolve("reference.positions", dataset=sub),
                    res.resolve("reference.elements", dataset=sub),
                    res.resolve("offsets", dataset=sub))
    np.testing.assert_allclose(part, whole[FRAMES])


def test_element_names_are_the_subsets_own(sub):
    assert sub.getElementsName() == ["C", "C", "O", "O", "S", "H", "C", "O"]


def test_the_announcement_describes_the_subsets_own_atoms(sub):
    meta = sub.toMetaDict()
    assert meta["variable"] is True
    assert meta["offsets"] == [0, 5, 8]
    assert meta["elements"] == [6, 6, 8, 8, 16, 1, 6, 8]
    assert meta["n"] == 2
    assert meta["parent"] == "parent-fp" and meta["parent_frames"] == FRAMES

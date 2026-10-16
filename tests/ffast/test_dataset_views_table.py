"""
Every kind of dataset and view answers every per-frame question the same way.

ADR 0058, step 1. A small model of "what should frame i hold?" (the ORACLE) is
computed from plain ASE structures, with no FFAST code. The real datasets are
then asked the same questions and must agree with the oracle. One test case per
(parent kind x view kind x question), so a red cell names the exact gap.

Cells that fail today are marked ``xfail(strict=True)`` with the error seen.
Strict means: when a later step fixes a cell, the test turns red until the
marker is removed. That is how the steps of the ADR get checked off.
"""
import numpy as np
import pytest
from ase import Atoms

from ffast.core.loading_coordinator import LoadingCoordinator
from ffast.loaders.dataset import AtomFilteredDataset, FrozenSubDataset, SubDataset


# ---- the data: same positions for both parent kinds' truth ---------------

def _uniform_atoms():
    return [Atoms("H2O", positions=[[0, 0, 0], [0, 0, 1 + 0.1 * i], [1, 0, 0]],
                  cell=[i + 1] * 3, pbc=True) for i in range(6)]


def _variable_atoms():
    rng = np.random.default_rng(1)
    return [Atoms(sym, positions=rng.normal(size=(n, 3)), cell=[i + 1] * 3, pbc=True)
            for i, (sym, n) in enumerate([("H2O", 3), ("CH4", 5), ("H2", 2), ("NH3", 4),
                                          ("H2O", 3), ("C2H6", 8)])]


PARENTS = {
    "uniform": _uniform_atoms,
    "variable": _variable_atoms,
}


# ---- the oracle: a view is (frame numbers, atom numbers or None) ---------

class Truth:
    """What a dataset *should* hold: a list of structures, a frame map and an
    atom mask. Cutting a view composes the maps; no FFAST code is involved."""

    def __init__(self, structures, frames=None, atoms=None):
        self.structures = structures
        self.frames = list(range(len(structures))) if frames is None else list(frames)
        self.atoms = atoms  # None = every atom

    def _mask(self, array):
        return array if self.atoms is None else array[self.atoms_for()]

    def atoms_for(self):
        return np.asarray(self.atoms)

    def cut_frames(self, frames):
        return Truth(self.structures, [self.frames[f] for f in frames], self.atoms)

    def cut_atoms(self, atoms):
        return Truth(self.structures, self.frames, atoms)

    # the questions
    def n_frames(self):
        return len(self.frames)

    def elements(self, i):
        return self._mask(self.structures[self.frames[i]].numbers)

    def coordinates(self, i):
        return self._mask(self.structures[self.frames[i]].positions)

    def lattice(self, i):
        return np.asarray(self.structures[self.frames[i]].cell)

    def n_atoms(self, i):
        return len(self.elements(i))


# ---- view builders: each returns (real dataset, truth) given a parent ----

def _view_none(parent, truth):
    return parent, truth


def _view_frames(parent, truth):
    frames = np.array([3, 0, 2])
    view = SubDataset(parent, None, frames, "Subset")
    view.initialise()
    return view, truth.cut_frames(frames)


def _view_frozen(parent, truth):
    frames = np.array([3, 0, 2])
    view = FrozenSubDataset(parent, None, frames, "Frozen")
    view.initialise()
    return view, truth.cut_frames(frames)


def _view_atoms(parent, truth):
    atoms = np.array([0, 2])
    view = AtomFilteredDataset(parent, atoms)
    view.initialise()
    return view, truth.cut_atoms(atoms)


def _view_frames_of_frames(parent, truth):
    inner, inner_truth = _view_frames(parent, truth)
    frames = np.array([2, 0])
    view = SubDataset(inner, None, frames, "SubSubset")
    view.initialise()
    return view, inner_truth.cut_frames(frames)


def _view_atoms_of_frames(parent, truth):
    inner, inner_truth = _view_frames(parent, truth)
    atoms = np.array([0, 2])
    view = AtomFilteredDataset(inner, atoms)
    view.initialise()
    return view, inner_truth.cut_atoms(atoms)


def _view_frames_of_atoms(parent, truth):
    inner, inner_truth = _view_atoms(parent, truth)
    frames = np.array([1, 2])
    view = SubDataset(inner, None, frames, "Subset")
    view.initialise()
    return view, inner_truth.cut_frames(frames)


VIEWS = {
    "file": _view_none,
    "subset": _view_frames,
    "frozen": _view_frozen,
    "atom-subset": _view_atoms,
    "subset-of-subset": _view_frames_of_frames,
    "atom-subset-of-subset": _view_atoms_of_frames,
    "subset-of-atom-subset": _view_frames_of_atoms,
}


# ---- the questions: each takes (dataset, truth) and asserts --------------

def q_n_frames(ds, truth):
    assert ds.getN() == truth.n_frames()


def q_elements(ds, truth):
    for i in range(truth.n_frames()):
        np.testing.assert_array_equal(ds.getElements(i), truth.elements(i))


def q_lattice(ds, truth):
    for i in range(truth.n_frames()):
        np.testing.assert_allclose(ds.getLattice(i), truth.lattice(i))


def q_coordinates(ds, truth):
    for i in range(truth.n_frames()):
        np.testing.assert_allclose(ds.getCoordinates(i), truth.coordinates(i))


def q_n_atoms(ds, truth):
    for i in range(truth.n_frames()):
        assert ds.getNAtoms(i) == truth.n_atoms(i)



def q_bond_matrix_shape(ds, truth):
    for i in range(truth.n_frames()):
        n = truth.n_atoms(i)
        assert np.asarray(ds.getBondMatrix(i)).shape == (n, n)


QUESTIONS = {
    "n_frames": q_n_frames,
    "elements": q_elements,
    "lattice": q_lattice,
    "coordinates": q_coordinates,
    "bond_matrix_shape": q_bond_matrix_shape,
    "n_atoms": q_n_atoms,
}


# ---- known gaps: (parent, view, question) -> (error type, why) -----------
_NO_BONDS = (TypeError, "atom subset of a variable parent has no bond sizes")
_ONE_FRAME_ELEMENTS = (AssertionError, "atom subset uses frame 0's elements for every "
                       "frame (deliberate today; the single view changes it)")
_MASK_TOO_BIG = (IndexError, "undecided: an atom subset asks for an atom that a "
                             "smaller structure (H2) does not have")
_NO_FRAME_ARG = (TypeError, "atom subset's getNAtoms() takes no frame number (step 5)")



KNOWN_GAPS = {
    ("variable", "atom-subset", "bond_matrix_shape"): _NO_BONDS,
    ("variable", "atom-subset-of-subset", "bond_matrix_shape"): _NO_BONDS,
    ("variable", "subset-of-atom-subset", "bond_matrix_shape"): _NO_BONDS,
    ("variable", "atom-subset", "elements"): _ONE_FRAME_ELEMENTS,
    ("variable", "atom-subset-of-subset", "elements"): _ONE_FRAME_ELEMENTS,
    ("variable", "subset-of-atom-subset", "elements"): _ONE_FRAME_ELEMENTS,
    ("variable", "atom-subset", "coordinates"): _ONE_FRAME_ELEMENTS,
    ("variable", "atom-subset-of-subset", "coordinates"): _ONE_FRAME_ELEMENTS,
    ("variable", "subset-of-atom-subset", "coordinates"): _ONE_FRAME_ELEMENTS,
    ("variable", "atom-subset", "coordinates"): _MASK_TOO_BIG,
    ("variable", "atom-subset-of-subset", "coordinates"): _MASK_TOO_BIG,
    ("variable", "subset-of-atom-subset", "coordinates"): _MASK_TOO_BIG,
    ("uniform", "atom-subset", "n_atoms"): _NO_FRAME_ARG,
    ("uniform", "atom-subset-of-subset", "n_atoms"): _NO_FRAME_ARG,
    ("variable", "atom-subset", "n_atoms"): _NO_FRAME_ARG,
    ("variable", "atom-subset-of-subset", "n_atoms"): _NO_FRAME_ARG,
    ("variable", "subset-of-atom-subset", "n_atoms"): _MASK_TOO_BIG,

}


def _cases():
    for p in PARENTS:
        for v in VIEWS:
            for q in QUESTIONS:
                marks = []
                if (p, v, q) in KNOWN_GAPS:
                    error, why = KNOWN_GAPS[(p, v, q)]
                    marks.append(pytest.mark.xfail(raises=error, reason=why, strict=True))
                yield pytest.param(p, v, q, id=f"{p}-{v}-{q}", marks=marks)


@pytest.mark.parametrize("parent_kind, view_kind, question", list(_cases()))
def test_view_answers_per_frame_question(parent_kind, view_kind, question):
    structures = PARENTS[parent_kind]()
    parent = LoadingCoordinator._aseLoaderFor(f"{parent_kind}.xyz", atomsList=structures)
    parent.initialise()
    dataset, truth = VIEWS[view_kind](parent, Truth(structures))
    QUESTIONS[question](dataset, truth)

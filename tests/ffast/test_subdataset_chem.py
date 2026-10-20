"""A subset's chemical formula comes from its parent's, never from ``parent.chem``.

Regression: ``chem`` was set only on atom subsets, but every parent type
(remote proxy, uniform, variable, nested subset) implements
``getChemicalFormula``. Reading ``self.parent.chem`` crashed with
``AttributeError: '<X>' object has no attribute 'chem'`` for remote and
subset-of-subset parents, which aborted the subset's SideBar item, so toggling
subbing off could not hide it.
"""
import types

import numpy as np
import pytest

from ffast.loaders.dataset import DatasetView


def _parent(variable=False, offsets=None, formula=None):
    """Like the remote proxy: answers getChemicalFormula, has no ``chem``."""
    parent = types.SimpleNamespace(loaded=True, fingerprint="P", isVariable=variable,
                                   getName=lambda: "parent")
    if formula is not None:
        parent.getChemicalFormula = formula
    if offsets is not None:
        parent.getOffsets = lambda: np.asarray(offsets)
    return parent


def _subset(parent, frames=(0, 1)):
    return DatasetView.subset(parent, None, np.array(frames), "Subset")


def test_a_uniform_parents_formula_is_used():
    assert _subset(_parent(formula=lambda: "C9H8O4")).getChemicalFormula() == "C9H8O4"


def test_a_subset_of_a_subset_reaches_the_file():
    inner = _subset(_parent(formula=lambda: "C9H8O4"))
    inner.fingerprint = "INNER"
    assert _subset(inner, frames=[1]).getChemicalFormula() == "C9H8O4"


def test_below_a_variable_parent_the_range_of_sizes_is_shown():
    parent = _parent(variable=True, offsets=[0, 21, 42, 63])
    assert _subset(parent, frames=[0, 2]).getChemicalFormula() == "Variable (21-21 atoms)"


def test_a_structure_without_atoms_shows_a_range_from_zero():
    parent = _parent(variable=True, offsets=[0, 0, 3])
    assert _subset(parent, frames=[0]).getChemicalFormula() == "Variable (0-0 atoms)"


def test_the_parents_own_error_is_not_masked():
    def boom():
        raise RuntimeError("parent boom")

    with pytest.raises(RuntimeError, match="parent boom"):
        _subset(_parent(formula=boom)).getChemicalFormula()


def _variableFile(offsets, elements):
    """A variable file's flat answers: where each frame starts, every atom's Z."""
    parent = _parent(variable=True, offsets=offsets)
    parent.getN = lambda: len(offsets) - 1
    parent.getAllElements = lambda: np.asarray(elements)
    return parent


def test_an_atom_subset_with_the_same_elements_everywhere_shows_their_formula():
    # methane (C H H H H) then ethane (C C H H H H H H); atoms 0 and 2 are C, H in both
    parent = _variableFile([0, 5, 13], [6, 1, 1, 1, 1, 6, 6, 1, 1, 1, 1, 1, 1])
    assert DatasetView.atomSubset(parent, [0, 2]).getChemicalFormula() == "C1H1"


def test_an_atom_subset_whose_elements_differ_between_frames_shows_the_range():
    # atom 1 is H in methane but C in ethane
    parent = _variableFile([0, 5, 13], [6, 1, 1, 1, 1, 6, 6, 1, 1, 1, 1, 1, 1])
    assert DatasetView.atomSubset(parent, [0, 1]).getChemicalFormula() == "Variable (2-2 atoms)"

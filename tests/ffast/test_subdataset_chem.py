"""SubDataset.getChemicalFormula must delegate, never read ``parent.chem``.

Regression: ``chem`` is set only on AtomFilteredDataset, but every parent type
(remote proxy, uniform, variable, nested sub) implements ``getChemicalFormula``.
Reading ``self.parent.chem`` crashed with ``AttributeError: '<X>' object has no
attribute 'chem'`` for remote / sub-of-sub parents, which aborted the sub's
SideBar item construction — so toggling subbing off could not hide it and the
sub-dataset "did not disappear". The method is exercised unbound (duck-typed
self) to avoid the heavy SubDataset constructor.
"""
import types

import numpy as np
import pytest

from datasetLoaders.loader import SubDataset


class _UniformRemoteParent:
    """Like RemoteDataset: implements getChemicalFormula, has NO `chem` attr."""
    isVariable = False

    def getChemicalFormula(self):
        return "C9H8O4"


def _formula_for_parent(parent):
    return SubDataset.getChemicalFormula(types.SimpleNamespace(parent=parent))


def test_uniform_parent_delegates_instead_of_reading_chem():
    assert _formula_for_parent(_UniformRemoteParent()) == "C9H8O4"


def test_nested_sub_parent_recurses_to_root():
    inner = _UniformRemoteParent()
    sub_parent = types.SimpleNamespace(parent=inner, isVariable=False)
    sub_parent.getChemicalFormula = lambda: SubDataset.getChemicalFormula(sub_parent)
    assert _formula_for_parent(sub_parent) == "C9H8O4"


def test_variable_parent_branch_unchanged():
    fake = types.SimpleNamespace(
        parent=types.SimpleNamespace(isVariable=True),
        getNAtoms=lambda: np.array([21, 21, 21]),
    )
    assert SubDataset.getChemicalFormula(fake) == "Variable (21-21 atoms)"


def test_variable_parent_scalar_count_returns_n_atoms():
    # Non-ndarray getNAtoms hits the `{count} atoms` else-branch — covers a
    # zero-atom / scalar-count variable parent.
    fake = types.SimpleNamespace(
        parent=types.SimpleNamespace(isVariable=True),
        getNAtoms=lambda: 0,
    )
    assert SubDataset.getChemicalFormula(fake) == "0 atoms"


def test_variable_parent_zero_atom_array_formats_range():
    fake = types.SimpleNamespace(
        parent=types.SimpleNamespace(isVariable=True),
        getNAtoms=lambda: np.array([0]),
    )
    assert SubDataset.getChemicalFormula(fake) == "Variable (0-0 atoms)"


def test_parent_formula_error_propagates_not_masked():
    # Delegation means a parent whose own getChemicalFormula raises propagates
    # that error — it is NOT swallowed, and crucially it is the parent's error,
    # not the old AttributeError from reading `parent.chem`.
    class _RaisingParent:
        isVariable = False

        def getChemicalFormula(self):
            raise RuntimeError("parent boom")

    with pytest.raises(RuntimeError, match="parent boom"):
        _formula_for_parent(_RaisingParent())

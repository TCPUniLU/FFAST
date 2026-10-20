"""An atom subset of a *variable* dataset exports each frame's own atoms.

Regression for the latent bug noted in ADR 0045 Phase 4: an atom subset
(CREATE_SUBSET / the Extract-Subset pane) of a variable-sized parent crashed on
export, because the uniform saver read a list of ragged per-frame arrays.

Until ADR 0058 step 5 the atom subset called itself uniform and wrote frame 0's
elements into every frame, which was wrong whenever another frame listed
different atoms at those positions. A view follows its parent's kind, so the
export takes the per-frame path and every frame keeps its own elements.
"""
from __future__ import annotations

import numpy as np
import pytest

ase = pytest.importorskip("ase")
from ase import Atoms  # noqa: E402
from ase.calculators.singlepoint import SinglePointCalculator  # noqa: E402

from ffast.loaders.dataset import DatasetView, stackFrames  # noqa: E402
from ffast.loaders.ase import aseDatasetLoader  # noqa: E402


def _variable_parent():
    """Frame 0 is C H H O (4 atoms); frame 1 is O C H (3 atoms)."""
    f0 = Atoms(symbols="CHHO", positions=[[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    f0.calc = SinglePointCalculator(f0, energy=-1.0, forces=np.arange(12, dtype=float).reshape(4, 3))
    f1 = Atoms(symbols="OCH", positions=[[2, 0, 0], [2, 1, 0], [2, 0, 1]])
    f1.calc = SinglePointCalculator(f1, energy=-2.0, forces=np.arange(9, dtype=float).reshape(3, 3) + 100)
    parent = aseDatasetLoader("mem-variable.extxyz", atomsList=[f0, f1])
    parent.initialise()
    return parent


def _atom_subset():
    view = DatasetView.atomSubset(_variable_parent(), np.array([0, 1]))
    view.initialise()
    return view


def test_each_frame_keeps_its_own_elements():
    view = _atom_subset()
    assert view.getElements(0).tolist() == [6, 1]   # C H
    assert view.getElements(1).tolist() == [8, 6]   # O C, not frame 0's C H


def test_the_selected_atoms_of_every_frame_stack_into_one_array():
    view = _atom_subset()
    R = stackFrames(view.getAllCoordinates(), view.getOffsets())
    assert R.shape == (2, 2, 3)
    np.testing.assert_allclose(R[0], [[0, 0, 0], [1, 0, 0]])
    np.testing.assert_allclose(R[1], [[2, 0, 0], [2, 1, 0]])

    F = stackFrames(view.getAllForces(), view.getOffsets())
    np.testing.assert_allclose(F[1], [[100, 101, 102], [103, 104, 105]])


def test_export_writes_each_frames_own_atoms(tmp_path):
    out = tmp_path / "filtered.extxyz"
    aseDatasetLoader.saveDataset(_atom_subset(), str(out), "extxyz")

    back = ase.io.read(str(out), index=":")
    assert [frame.get_chemical_symbols() for frame in back] == [["C", "H"], ["O", "C"]]
    np.testing.assert_allclose(back[0].get_positions(), [[0, 0, 0], [1, 0, 0]])
    np.testing.assert_allclose(back[1].get_positions(), [[2, 0, 0], [2, 1, 0]])

"""AtomFilteredDataset export when the parent is a *variable* dataset.

Regression for the latent bug noted in ADR 0045 Phase 4: an atom-filtered
subset (CREATE_SUBSET / the Extract-Subset pane) of a variable-sized parent
crashed on export. The uniform ``aseDatasetLoader.saveDataset`` reads
``dataset.getCoordinates()`` (all frames), and a *variable* parent returns a
**list** of ragged per-frame arrays — so ``len(a.shape)`` raised
``AttributeError`` before a single structure was written.

An atom-filter keeps the *same* atom indices in every frame, so its output has
a constant atom count (``len(indices)``) — it is uniform by contract even over
a variable parent. These tests pin that: the all-frames accessors extract the
selected atoms from each frame and stack them into a uniform ``(N, k, 3)``
array, and a round-trip export reproduces the filtered geometry.
"""
from __future__ import annotations

import numpy as np
import pytest

ase = pytest.importorskip("ase")
from ase import Atoms  # noqa: E402
from ase.calculators.singlepoint import SinglePointCalculator  # noqa: E402

from ffast.loaders.dataset import AtomFilteredDataset  # noqa: E402
from ffast.loaders.ase import (  # noqa: E402
    VariableASEDatasetLoader,
    aseDatasetLoader,
)


def _variable_parent():
    """A 2-frame variable dataset: frame 0 has 4 atoms, frame 1 has 3."""
    frames = []
    # Frame 0: C H H O (4 atoms)
    f0 = Atoms(
        symbols="CHHO",
        positions=[[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
    )
    f0.calc = SinglePointCalculator(
        f0, energy=-1.0, forces=np.arange(12, dtype=float).reshape(4, 3)
    )
    # Frame 1: C H O (3 atoms) — different size ⇒ genuinely variable
    f1 = Atoms(symbols="CHO", positions=[[2, 0, 0], [2, 1, 0], [2, 0, 1]])
    f1.calc = SinglePointCalculator(
        f1, energy=-2.0, forces=np.arange(9, dtype=float).reshape(3, 3) + 100
    )
    frames.extend([f0, f1])

    parent = VariableASEDatasetLoader("mem-variable.extxyz", atomsList=frames)
    parent.initialise()
    return parent


def test_parent_is_variable():
    parent = _variable_parent()
    assert parent.isVariable is True


def test_filtered_all_frames_coordinates_stack_to_uniform():
    """getCoordinates() (all frames) must return a uniform (N, k, 3) array —
    not a list, and not crash — even though the parent returns a list."""
    parent = _variable_parent()
    filt = AtomFilteredDataset(parent, np.array([0, 1]))
    filt.initialise()

    R = filt.getCoordinates()
    R = np.asarray(R)
    assert R.shape == (2, 2, 3)
    # Frame 0 atoms 0,1 = C(0,0,0), H(1,0,0)
    np.testing.assert_allclose(R[0], [[0, 0, 0], [1, 0, 0]])
    # Frame 1 atoms 0,1 = C(2,0,0), H(2,1,0)
    np.testing.assert_allclose(R[1], [[2, 0, 0], [2, 1, 0]])


def test_filtered_all_frames_forces_stack_to_uniform():
    parent = _variable_parent()
    filt = AtomFilteredDataset(parent, np.array([0, 1]))
    filt.initialise()

    F = np.asarray(filt.getForces())
    assert F.shape == (2, 2, 3)
    np.testing.assert_allclose(F[0], [[0, 1, 2], [3, 4, 5]])
    np.testing.assert_allclose(F[1], [[100, 101, 102], [103, 104, 105]])


def test_filtered_reports_frame0_elements_and_count():
    parent = _variable_parent()
    filt = AtomFilteredDataset(parent, np.array([0, 1]))
    filt.initialise()

    assert filt.getNAtoms() == 2
    assert list(filt.getElementsName()) == ["C", "H"]
    # An atom-filter is uniform-count regardless of a variable parent.
    assert bool(filt.isVariable) is False


def test_export_variable_parent_roundtrip(tmp_path):
    """The full export path (uniform saver, chosen by isVariable=False) writes
    the filtered geometry and reads back the right shape and elements."""
    parent = _variable_parent()
    filt = AtomFilteredDataset(parent, np.array([0, 1]))
    filt.initialise()

    out = tmp_path / "filtered.extxyz"
    # Mirror the server's routing: isVariable=False ⇒ uniform saver.
    aseDatasetLoader.saveDataset(filt, str(out), "extxyz")

    back = ase.io.read(str(out), index=":")
    assert len(back) == 2
    for frame in back:
        assert len(frame) == 2
        assert list(frame.get_chemical_symbols()) == ["C", "H"]
    np.testing.assert_allclose(back[0].get_positions(), [[0, 0, 0], [1, 0, 0]])
    np.testing.assert_allclose(back[1].get_positions(), [[2, 0, 0], [2, 1, 0]])

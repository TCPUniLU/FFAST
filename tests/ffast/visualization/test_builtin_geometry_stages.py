import numpy as np
import pytest
from ffast.visualization.stages.builtin.geometry_stages import (
    bond_indices,
    bond_positions,
    unit_cell_edges,
)


# --- bond_indices ---

def test_bond_indices_basic():
    pos = np.array([[0.0, 0, 0], [1.0, 0, 0], [10.0, 0, 0]])
    sizes = np.ones((3, 3)) * 1.5
    idx = bond_indices(pos, sizes)
    assert idx.shape == (1, 2)
    assert list(idx[0]) == [0, 1]


def test_bond_indices_no_bonds():
    pos = np.array([[0.0, 0, 0], [10.0, 0, 0]])
    sizes = np.ones((2, 2)) * 1.0
    idx = bond_indices(pos, sizes)
    assert len(idx) == 0


def test_bond_indices_all_bonded():
    pos = np.array([[0.0, 0, 0], [1.0, 0, 0], [2.0, 0, 0]])
    sizes = np.ones((3, 3)) * 1.5
    idx = bond_indices(pos, sizes)
    assert idx.shape == (2, 2)
    pairs = set(map(tuple, idx.tolist()))
    assert (0, 1) in pairs
    assert (1, 2) in pairs


def test_bond_indices_i_less_than_j():
    pos = np.array([[0.0, 0, 0], [1.0, 0, 0]])
    sizes = np.ones((2, 2)) * 1.5
    idx = bond_indices(pos, sizes)
    for i, j in idx:
        assert i < j


def test_bond_indices_no_self_bonds():
    pos = np.array([[0.0, 0, 0], [1.0, 0, 0]])
    sizes = np.ones((2, 2)) * 1.5
    idx = bond_indices(pos, sizes)
    for i, j in idx:
        assert i != j


def test_bond_indices_asymmetric_cutoffs():
    pos = np.array([[0.0, 0, 0], [1.0, 0, 0], [2.0, 0, 0]])
    sizes = np.zeros((3, 3))
    sizes[0, 1] = sizes[1, 0] = 1.5   # bond 0-1 allowed
    # bond 1-2 not allowed
    idx = bond_indices(pos, sizes)
    assert len(idx) == 1
    assert list(idx[0]) == [0, 1]


# --- bond_positions ---

def test_bond_positions_basic():
    pos = np.array([[0.0, 0, 0], [1.0, 0, 0], [5.0, 0, 0]])
    idx = np.array([[0, 1]])
    segs = bond_positions(pos, idx)
    assert segs is not None
    assert segs.shape == (2, 3)
    assert np.allclose(segs[0], [0, 0, 0])
    assert np.allclose(segs[1], [1, 0, 0])


def test_bond_positions_empty_indices():
    pos = np.array([[0.0, 0, 0], [1.0, 0, 0]])
    idx = np.empty((0, 2), dtype=int)
    result = bond_positions(pos, idx)
    assert result is None


def test_bond_positions_none_indices():
    pos = np.array([[0.0, 0, 0]])
    result = bond_positions(pos, None)
    assert result is None


def test_bond_positions_multiple_bonds():
    pos = np.array([[0.0, 0, 0], [1.0, 0, 0], [2.0, 0, 0]])
    idx = np.array([[0, 1], [1, 2]])
    segs = bond_positions(pos, idx)
    assert segs.shape == (4, 3)   # 2 bonds x 2 endpoints


def test_bond_positions_segments_mode_interleaved():
    pos = np.array([[0.0, 0, 0], [1.0, 0, 0], [2.0, 0, 0], [3.0, 0, 0]])
    idx = np.array([[0, 1], [2, 3]])
    segs = bond_positions(pos, idx)
    # segment 1: pos[0] then pos[1]
    assert np.allclose(segs[0], pos[0])
    assert np.allclose(segs[1], pos[1])
    # segment 2: pos[2] then pos[3]
    assert np.allclose(segs[2], pos[2])
    assert np.allclose(segs[3], pos[3])


# --- unit_cell_edges ---

def test_unit_cell_edges_shape():
    lattice = np.eye(3) * 5.0
    origin = np.zeros(3)
    edges = unit_cell_edges(lattice, origin)
    assert edges.shape == (24, 3)   # 12 edges x 2 endpoints


def test_unit_cell_edges_cubic_corners():
    lattice = np.eye(3) * 2.0
    origin = np.zeros(3)
    edges = unit_cell_edges(lattice, origin)
    # all coordinates must be 0.0 or 2.0
    assert np.all((edges == 0.0) | (edges == 2.0))


def test_unit_cell_edges_origin_offset():
    lattice = np.eye(3)
    origin = np.array([1.0, 2.0, 3.0])
    edges = unit_cell_edges(lattice, origin)
    # min corner should be origin
    assert np.allclose(np.min(edges, axis=0), origin)
    # max corner should be origin + (1,1,1)
    assert np.allclose(np.max(edges, axis=0), origin + 1.0)


def test_unit_cell_edges_non_cubic():
    lattice = np.diag([2.0, 3.0, 4.0])
    origin = np.zeros(3)
    edges = unit_cell_edges(lattice, origin)
    assert edges.shape == (24, 3)
    assert np.allclose(np.max(edges, axis=0), [2.0, 3.0, 4.0])


def test_unit_cell_edges_ase_cell_object():
    class FakeCell:
        def __init__(self, arr):
            self.array = arr

    lattice = FakeCell(np.eye(3) * 3.0)
    origin = np.zeros(3)
    edges = unit_cell_edges(lattice, origin)
    assert edges.shape == (24, 3)
    assert np.allclose(np.max(edges, axis=0), 3.0)


def test_unit_cell_edges_12_unique_edge_pairs():
    lattice = np.eye(3)
    origin = np.zeros(3)
    edges = unit_cell_edges(lattice, origin)
    # 12 edges → 12 pairs of consecutive rows
    pairs = [(tuple(edges[2*i]), tuple(edges[2*i+1])) for i in range(12)]
    # all pairs must be distinct
    assert len(set(pairs)) == 12

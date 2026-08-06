import numpy as np
import pytest
from ffast.visualization.stages.builtin.geometry_stages import unit_cell_edges


# --- bond_indices ---

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

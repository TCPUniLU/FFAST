import numpy as np
import pytest
from ffast.visualization.stages.builtin.force_stages import _arrow_mesh


# --- _arrow_mesh (internal mesh builder) ---

def test_arrow_mesh_returns_arrays():
    starts = np.array([[0.0, 0, 0]])
    ends = np.array([[1.0, 0, 0]])
    verts, faces = _arrow_mesh(starts, ends)
    assert verts is not None
    assert faces is not None
    assert verts.ndim == 2 and verts.shape[1] == 3
    assert faces.ndim == 2 and faces.shape[1] == 3


def test_arrow_mesh_zero_length_returns_none():
    starts = np.array([[0.0, 0, 0], [1.0, 0, 0]])
    ends = starts.copy()   # zero-length arrows
    verts, faces = _arrow_mesh(starts, ends)
    assert verts is None
    assert faces is None


def test_arrow_mesh_partial_zero_skipped():
    starts = np.array([[0.0, 0, 0], [0.0, 0, 0]])
    ends = np.array([[1.0, 0, 0], [0.0, 0, 0]])   # only first arrow is non-zero
    verts, faces = _arrow_mesh(starts, ends)
    assert verts is not None   # one arrow survives


def test_arrow_mesh_vertex_count_scales_with_n():
    # Each arrow contributes a fixed number of vertices — two arrows → 2x vertices
    s1 = np.array([[0.0, 0, 0]])
    e1 = np.array([[1.0, 0, 0]])
    v1, f1 = _arrow_mesh(s1, e1)

    s2 = np.array([[0.0, 0, 0], [2.0, 0, 0]])
    e2 = np.array([[1.0, 0, 0], [3.0, 0, 0]])
    v2, f2 = _arrow_mesh(s2, e2)

    assert v2.shape[0] == 2 * v1.shape[0]
    assert f2.shape[0] == 2 * f1.shape[0]


def test_arrow_mesh_faces_are_valid_indices():
    starts = np.array([[0.0, 0, 0], [0.0, 1, 0]])
    ends = np.array([[1.0, 0, 0], [0.0, 2, 0]])
    verts, faces = _arrow_mesh(starts, ends)
    assert np.all(faces >= 0)
    assert np.all(faces < len(verts))


def test_arrow_mesh_along_all_axes():
    for axis in range(3):
        starts = np.zeros((1, 3))
        ends = np.zeros((1, 3))
        ends[0, axis] = 1.0
        verts, faces = _arrow_mesh(starts, ends)
        assert verts is not None


def test_arrow_mesh_antiparallel_z():
    starts = np.array([[0.0, 0, 0]])
    ends = np.array([[0.0, 0, -1.0]])
    verts, faces = _arrow_mesh(starts, ends)
    assert verts is not None


# --- force_arrows (stage function) ---


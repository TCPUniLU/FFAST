import numpy as np
import pytest
from ffast.visualization.stages.builtin.force_stages import _arrow_mesh, force_arrows


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

def test_force_arrows_basic_shape():
    pos = np.zeros((3, 3))
    forces = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    verts, faces = force_arrows(pos, forces, length_factor=100.0)
    assert verts is not None
    assert verts.shape[1] == 3
    assert faces.shape[1] == 3


def test_force_arrows_zero_forces_returns_none():
    pos = np.zeros((3, 3))
    forces = np.zeros((3, 3))
    verts, faces = force_arrows(pos, forces)
    assert verts is None
    assert faces is None


def test_force_arrows_normalised_mode():
    pos = np.zeros((2, 3))
    forces = np.array([[10, 0, 0], [0, 20, 0]], dtype=float)
    # large length_factor ensures L > _HEAD_LENGTH for both modes
    verts_norm, _ = force_arrows(pos, forces, normalised=True, length_factor=100.0)
    verts_raw, _ = force_arrows(pos, forces, normalised=False, length_factor=100.0)
    assert verts_norm is not None
    assert verts_raw is not None
    # normalised: both arrows share the same max-norm scaling → equal lengths
    # raw: arrows scale with force magnitude → unequal lengths
    # vertex arrays must differ
    assert not np.allclose(verts_norm, verts_raw)


def test_force_arrows_length_factor_scales_arrows():
    # forces large enough so shaft_L = L - _HEAD_LENGTH > 0 for both factors
    pos = np.zeros((1, 3))
    forces = np.array([[500, 0, 0]], dtype=float)  # L=1 for lf=1, L=2 for lf=2
    v1, _ = force_arrows(pos, forces, length_factor=1.0, normalised=False)
    v2, _ = force_arrows(pos, forces, length_factor=2.0, normalised=False)
    extent1 = np.max(v1[:, 0]) - np.min(v1[:, 0])
    extent2 = np.max(v2[:, 0]) - np.min(v2[:, 0])
    assert extent2 > extent1


def test_force_arrows_registered_in_default_registry():
    from ffast.visualization.stages.registry import _default_registry
    _, fn = _default_registry.get("ffast.force_arrows")
    assert fn is force_arrows

import numpy as np
import pytest
from ffast.visualization.stages.builtin.atom_stages import atom_positions, atom_sizes, atom_colors


# --- atom_positions ---

def test_atom_positions_no_transforms():
    R = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    result = atom_positions(R)
    assert np.allclose(result, R)


def test_atom_positions_none_transforms():
    R = np.array([[1.0, 0.0, 0.0]])
    result = atom_positions(R, transforms=None)
    assert np.allclose(result, R)


def test_atom_positions_translation():
    R = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    t = np.array([0.0, 1.0, 0.0])
    result = atom_positions(R, transforms=[t])
    assert np.allclose(result, [[0, 1, 0], [1, 1, 0]])


def test_atom_positions_rotation_matrix():
    # atom_positions uses row-vector convention: R @ M
    # with M = [[0,-1,0],[1,0,0],[0,0,1]], [1,0,0] @ M = [0,-1,0]
    R = np.array([[1.0, 0.0, 0.0]])
    rot = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], dtype=float)
    result = atom_positions(R, transforms=[rot])
    assert np.allclose(result, [[0.0, -1.0, 0.0]], atol=1e-10)


def test_atom_positions_multiple_transforms_applied_in_order():
    R = np.array([[1.0, 0.0, 0.0]])
    t1 = np.array([1.0, 0.0, 0.0])   # translate → [2, 0, 0]
    t2 = np.array([0.0, 1.0, 0.0])   # translate → [2, 1, 0]
    result = atom_positions(R, transforms=[t1, t2])
    assert np.allclose(result, [[2.0, 1.0, 0.0]])


def test_atom_positions_does_not_mutate_input():
    R = np.array([[1.0, 2.0, 3.0]])
    original = R.copy()
    atom_positions(R, transforms=[np.array([1.0, 0.0, 0.0])])
    assert np.allclose(R, original)


def test_atom_positions_output_shape():
    R = np.random.randn(15, 3)
    result = atom_positions(R)
    assert result.shape == (15, 3)


# --- atom_sizes ---

def test_atom_sizes_shape():
    z = np.array([1, 6, 8])
    sizes = atom_sizes(z)
    assert sizes.shape == (3,)


def test_atom_sizes_known_values():
    z = np.array([1, 6])       # H, C
    sizes = atom_sizes(z)
    assert np.isclose(sizes[0], 0.37)  # H covalent radius
    assert np.isclose(sizes[1], 0.77)  # C covalent radius


def test_atom_sizes_scale_applied():
    z = np.array([1])
    base = atom_sizes(z, scale=1.0)
    scaled = atom_sizes(z, scale=2.0)
    assert np.allclose(scaled, base * 2.0)


def test_atom_sizes_scale_one_is_default():
    z = np.array([6, 8])
    assert np.allclose(atom_sizes(z), atom_sizes(z, scale=1.0))


# --- atom_colors ---

def test_atom_colors_shape():
    z = np.array([1, 6, 8])
    colors = atom_colors(z)
    assert colors.shape == (3, 4)
    assert colors.dtype == np.float32


def test_atom_colors_alpha_is_one():
    z = np.array([1, 6, 7, 8])
    colors = atom_colors(z)
    assert np.allclose(colors[:, 3], 1.0)


def test_atom_colors_h_is_white():
    z = np.array([1])
    colors = atom_colors(z)
    assert np.allclose(colors[0, :3], [1.0, 1.0, 1.0], atol=1e-3)


def test_atom_colors_dimming_scales_rgb():
    z = np.array([1])
    full = atom_colors(z, dimming=1.0)
    half = atom_colors(z, dimming=0.5)
    assert np.allclose(half[0, :3], full[0, :3] * 0.5, atol=1e-6)
    assert np.allclose(half[0, 3], 1.0)   # alpha unchanged


def test_atom_colors_zero_dimming():
    z = np.array([6])
    colors = atom_colors(z, dimming=0.0)
    assert np.allclose(colors[0, :3], 0.0)
    assert np.allclose(colors[0, 3], 1.0)

import numpy as np
import pytest
from ffast.visualization.stages.builtin.transform_stages import kabsch_alignment


def _apply_transforms(coords, transforms):
    R = coords.copy()
    for t in transforms:
        t = np.asarray(t)
        if t.ndim == 1:
            R = R + t
        else:
            R = R @ t
    return R


# --- kabsch_alignment ---

def test_kabsch_returns_three_transforms():
    coords = np.random.randn(10, 3)
    transforms = kabsch_alignment(coords, coords)
    assert len(transforms) == 3


def test_kabsch_transforms_are_arrays():
    coords = np.random.randn(5, 3)
    transforms = kabsch_alignment(coords, coords)
    for t in transforms:
        assert hasattr(t, "__len__")


def test_kabsch_identity_input():
    coords = np.random.randn(8, 3)
    transforms = kabsch_alignment(coords, coords)
    result = _apply_transforms(coords, transforms)
    assert np.allclose(result, coords, atol=1e-10)


def test_kabsch_pure_translation():
    coords = np.random.randn(6, 3)
    shift = np.array([1.0, 2.0, 3.0])
    reference = coords + shift
    transforms = kabsch_alignment(coords, reference)
    result = _apply_transforms(coords, transforms)
    assert np.allclose(result, reference, atol=1e-10)


def test_kabsch_pure_rotation():
    rng = np.random.default_rng(42)
    coords = rng.standard_normal((10, 3))
    # center both to remove translation
    coords -= coords.mean(axis=0)
    # rotate reference by 45° around z
    angle = np.pi / 4
    rot = np.array([
        [np.cos(angle), -np.sin(angle), 0],
        [np.sin(angle),  np.cos(angle), 0],
        [0,              0,             1],
    ])
    reference = coords @ rot.T
    reference -= reference.mean(axis=0)

    transforms = kabsch_alignment(coords, reference)
    result = _apply_transforms(coords, transforms)
    assert np.allclose(result, reference, atol=1e-10)


def test_kabsch_translation_and_rotation():
    rng = np.random.default_rng(7)
    coords = rng.standard_normal((12, 3))
    angle = np.pi / 3
    rot = np.array([
        [np.cos(angle), 0, np.sin(angle)],
        [0,             1, 0],
        [-np.sin(angle),0, np.cos(angle)],
    ])
    shift = np.array([5.0, -3.0, 2.0])
    reference = coords @ rot.T + shift

    transforms = kabsch_alignment(coords, reference)
    result = _apply_transforms(coords, transforms)
    assert np.allclose(result, reference, atol=1e-8)


def test_kabsch_heavy_only_uses_subset():
    rng = np.random.default_rng(99)
    coords = rng.standard_normal((10, 3))
    reference = coords.copy()
    elements = np.array([1, 6, 7, 8, 1, 6, 7, 8, 1, 6])  # mix of H(1) and heavy

    # With heavy_only=True, alignment computed on non-H atoms only
    t_heavy = kabsch_alignment(coords, reference, elements=elements, heavy_only=True)
    t_all = kabsch_alignment(coords, reference, elements=elements, heavy_only=False)
    # Both should align well on identical coords
    r_heavy = _apply_transforms(coords, t_heavy)
    r_all = _apply_transforms(coords, t_all)
    assert np.allclose(r_heavy, reference, atol=1e-10)
    assert np.allclose(r_all, reference, atol=1e-10)


def test_kabsch_heavy_only_false_no_elements_needed():
    coords = np.random.randn(5, 3)
    transforms = kabsch_alignment(coords, coords, elements=None, heavy_only=False)
    result = _apply_transforms(coords, transforms)
    assert np.allclose(result, coords, atol=1e-10)


def test_kabsch_rotation_matrix_is_orthogonal():
    coords = np.random.randn(8, 3)
    reference = np.random.randn(8, 3)
    transforms = kabsch_alignment(coords, reference)
    rot = np.asarray(transforms[1])
    assert rot.shape == (3, 3)
    assert np.allclose(rot @ rot.T, np.eye(3), atol=1e-10)


def test_kabsch_rotation_preserves_handedness():
    coords = np.random.randn(8, 3)
    reference = np.random.randn(8, 3)
    transforms = kabsch_alignment(coords, reference)
    rot = np.asarray(transforms[1])
    assert np.isclose(np.linalg.det(rot), 1.0, atol=1e-10)


def test_kabsch_registered_in_default_registry():
    from ffast.visualization.stages.registry import _default_registry
    _, fn = _default_registry.get("ffast.kabsch_alignment")
    assert fn is kabsch_alignment

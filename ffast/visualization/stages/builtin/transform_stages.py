from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

from ffast.visualization.stages.registry import stage


# ── math helpers (ported from client/mathUtils.py, no client dependency) ──────

def _vv0_angle(v, v0, direction_vector=None):
    u, u0 = v / np.linalg.norm(v), v0 / np.linalg.norm(v0)
    if direction_vector is None:
        sign = 1
    else:
        cross = np.cross(v, v0)
        sign = -np.sign(np.dot(direction_vector, cross))
    return sign * np.arccos(np.clip(np.dot(u, u0), -1.0, 1.0))


def _vv0_rotation_matrix(v, v0):
    v_perp = np.cross(v, v0)
    norm = np.linalg.norm(v_perp)
    if norm < 1e-12:
        return np.eye(3)
    v_perp = v_perp / norm
    angle = _vv0_angle(v, v0)
    return Rotation.from_rotvec(-angle * v_perp).as_matrix()


def _perp_component(v, v_ref, unitary=False):
    v_par = np.dot(v, v_ref) * v_ref
    v_perp = v - v_par
    if unitary:
        norm = np.linalg.norm(v_perp)
        if norm > 1e-12:
            v_perp = v_perp / norm
    return v_perp


@stage(
    id="ffast.kabsch_alignment",
    inputs={
        "coords": "frame.positions",
        "reference": "frame.reference_positions",
        "elements": "frame.elements",
    },
    outputs={"transforms": "list[ndarray] — [-centroid, rotation(3x3), +ref_centroid]"},
    parameters={
        "heavy_only": {"type": "bool", "default": True, "role": "compute"},
    },
    tests=[
        {
            "inputs": {
                "coords":     [[1.0,1.0,0.0],[1.0,-1.0,0.0],[-1.0,1.0,0.0],[-1.0,-1.0,0.0]],
                "reference":  [[1.0,1.0,0.0],[1.0,-1.0,0.0],[-1.0,1.0,0.0],[-1.0,-1.0,0.0]],
            },
            "parameters": {"heavy_only": False},
            "expected": [
                [0.0, 0.0, 0.0],
                [[1.0,0.0,0.0],[0.0,1.0,0.0],[0.0,0.0,1.0]],
                [0.0, 0.0, 0.0],
            ],
            "atol": 1e-10,
        },
    ],
)
def kabsch_alignment(
    coords: np.ndarray,
    reference: np.ndarray,
    elements: np.ndarray | None = None,
    *,
    heavy_only: bool = True,
) -> list:
    """Returns Kabsch rigid-alignment transforms that map coords onto reference.

    Compatible with canvas.currentTransformations: apply each transform to R via
    R + t (translation vector) or R @ M (rotation matrix).
    """
    indices = None
    if heavy_only and elements is not None:
        heavy = np.where(np.asarray(elements) > 1)[0]
        if len(heavy) > 0:
            indices = heavy

    if indices is None:
        indices = np.arange(len(coords))

    r_sel = coords[indices]
    r0_sel = reference[indices]

    tgt_centroid = r_sel.mean(axis=0)
    ref_centroid = r0_sel.mean(axis=0)

    cov = (r_sel - tgt_centroid).T @ (r0_sel - ref_centroid)
    u, _, vt = np.linalg.svd(cov)
    det_sign = np.sign(np.linalg.det(u @ vt))
    correction = np.eye(3, dtype=np.float64)
    correction[2, 2] = det_sign if det_sign != 0 else 1.0
    rotation = u @ correction @ vt

    return [-tgt_centroid, rotation, ref_centroid]


def atom_align(
    coords: np.ndarray,
    reference: np.ndarray,
    elements: np.ndarray | None = None,
    *,
    atom_indices: list,
    reference_frame: int = 0,
) -> list:
    """3-atom frame alignment: translate+rotate so atoms n1/n2/n3 overlap reference.

    Returns 5 transforms in canvas.currentTransformations format (translations as
    1-D vectors, rotations as 3x3 matrices). Ported from loupeAtomAlign.getTransform.
    """
    if len(atom_indices) != 3:
        return []

    n1, n2, n3 = int(atom_indices[0]), int(atom_indices[1]), int(atom_indices[2])
    r  = np.array(coords,     dtype=np.float64)
    r0 = np.array(reference,  dtype=np.float64)

    # 1. Translate so atom n1 overlaps reference n1
    d = r0[n1] - r[n1]
    r = r + d
    transforms = [d]

    # 2. Rotate v12 onto reference v12 (origin at r0[n1])
    rot1 = _vv0_rotation_matrix(r[n2] - r[n1], r0[n2] - r0[n1])
    transforms.append(-r0[n1])          # shift origin to n1
    transforms.append(rot1)             # rotate
    r = (r - r0[n1]) @ rot1

    # 3. Rotate around v12 to align atom n3 in the plane
    v12 = r[n2] - r[n1]
    u12 = v12 / np.linalg.norm(v12)
    vpp  = _perp_component(r[n3],          u12, unitary=True)
    vpp0 = _perp_component(r0[n3] - r0[n1], u12, unitary=True)
    angle = _vv0_angle(vpp, vpp0, direction_vector=u12)
    rot2 = Rotation.from_rotvec(angle * u12).as_matrix()
    transforms.append(rot2)
    transforms.append(r0[n1])           # restore origin

    return transforms

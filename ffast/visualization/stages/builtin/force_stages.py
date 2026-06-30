from __future__ import annotations

import numpy as np

from ffast.visualization.stages.registry import stage

_SHAFT_RADIUS = 0.05
_HEAD_RADIUS = 0.12
_HEAD_LENGTH = 0.25
_N_SEGMENTS = 8


def _batch_rotation_z_to(U: np.ndarray) -> np.ndarray:
    """(N,3) unit vectors → (N,3,3) rotation matrices mapping +z to each U[i]."""
    N = len(U)
    R = np.tile(np.eye(3, dtype=float), (N, 1, 1))

    parallel = np.abs(U[:, 2]) > 0.9999
    antipar = parallel & (U[:, 2] < 0)
    R[antipar, 1, 1] = -1.0
    R[antipar, 2, 2] = -1.0

    sel = ~parallel
    if not np.any(sel):
        return R

    u = U[sel]
    z = np.zeros_like(u)
    z[:, 2] = 1.0
    axis = np.cross(z, u)
    axis /= np.linalg.norm(axis, axis=1, keepdims=True)

    c = u[:, 2, np.newaxis, np.newaxis]
    s = np.sqrt(np.maximum(0.0, 1.0 - c**2))
    kx, ky, kz = axis[:, 0], axis[:, 1], axis[:, 2]
    M = len(u)
    K = np.zeros((M, 3, 3))
    K[:, 0, 1] = -kz
    K[:, 0, 2] = ky
    K[:, 1, 0] = kz
    K[:, 1, 2] = -kx
    K[:, 2, 0] = -ky
    K[:, 2, 1] = kx

    I = np.tile(np.eye(3, dtype=float), (M, 1, 1))
    KK = np.einsum("nij,njk->nik", K, K)
    R[sel] = I + s * K + (1.0 - c) * KK
    return R


def _arrow_mesh(starts: np.ndarray, ends: np.ndarray) -> tuple[np.ndarray, np.ndarray] | tuple[None, None]:
    """Batched cylinder+cone mesh for a set of arrows.

    Returns (vertices (V,3), faces (F,3)) or (None, None) when all arrows are zero-length.
    """
    n = _N_SEGMENTS
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
    cos_a, sin_a = np.cos(angles), np.sin(angles)
    js = np.arange(n)
    js1 = (js + 1) % n

    shaft_can = np.vstack([
        np.c_[_SHAFT_RADIUS * cos_a, _SHAFT_RADIUS * sin_a, np.zeros(n)],
        np.c_[_SHAFT_RADIUS * cos_a, _SHAFT_RADIUS * sin_a, np.ones(n)],
    ])
    shaft_faces = np.vstack([
        np.c_[js, js1, n + js],
        np.c_[js1, n + js1, n + js],
    ])

    cone_can = np.vstack([
        np.c_[_HEAD_RADIUS * cos_a, _HEAD_RADIUS * sin_a, np.zeros(n)],
        [[0.0, 0.0, 1.0]],
    ])
    cone_faces = np.c_[js, js1, np.full(n, n)]

    shaft_cap_can = np.vstack([
        [[0.0, 0.0, 0.0]],
        np.c_[_SHAFT_RADIUS * cos_a, _SHAFT_RADIUS * sin_a, np.zeros(n)],
    ])
    cone_cap_can = np.vstack([
        [[0.0, 0.0, 0.0]],
        np.c_[_HEAD_RADIUS * cos_a, _HEAD_RADIUS * sin_a, np.zeros(n)],
    ])
    cap_faces = np.c_[np.zeros(n, int), js1 + 1, js + 1]

    D = ends - starts
    lengths = np.linalg.norm(D, axis=1)
    mask = lengths > 1e-10
    if not np.any(mask):
        return None, None

    S = starts[mask]
    D = D[mask]
    L = lengths[mask]
    N = len(S)
    U = D / L[:, None]
    Rot = _batch_rotation_z_to(U)

    shaft_L = np.maximum(0.0, L - _HEAD_LENGTH)
    cone_starts = S + U * shaft_L[:, None]

    def _transform(can, scale_z, origin):
        v = np.tile(can, (N, 1, 1))
        if scale_z is not None:
            v[:, :, 2] *= scale_z[:, None]
        return np.einsum("nij,nkj->nki", Rot, v) + origin[:, None, :]

    _CAP_BIAS = 0.002
    sv = _transform(shaft_can, shaft_L, S)
    cv = _transform(cone_can, np.full(N, _HEAD_LENGTH), cone_starts)
    bsv = _transform(shaft_cap_can, None, S - _CAP_BIAS * U)
    bcv = _transform(cone_cap_can, None, cone_starts - _CAP_BIAS * U)

    all_verts = np.vstack([
        sv.reshape(-1, 3),
        bsv.reshape(-1, 3),
        cv.reshape(-1, 3),
        bcv.reshape(-1, 3),
    ])

    i = np.arange(N)
    s_off  = (i * 2 * n)[:, None, None]
    bs_off = (N * 2 * n + i * (n + 1))[:, None, None]
    c_off  = (N * (3 * n + 1) + i * (n + 1))[:, None, None]
    bc_off = (N * (4 * n + 2) + i * (n + 1))[:, None, None]

    all_faces = np.vstack([
        (shaft_faces[None] + s_off).reshape(-1, 3),
        (cap_faces[None] + bs_off).reshape(-1, 3),
        (cone_faces[None] + c_off).reshape(-1, 3),
        (cap_faces[None] + bc_off).reshape(-1, 3),
    ])

    return all_verts, all_faces


@stage(
    id="ffast.force_arrows",
    inputs={
        "positions": "stage.ffast.atom_positions.positions",
        "forces": "prediction.forces",
    },
    outputs={
        "vertices": "(V,3) float64 arrow mesh vertices",
        "faces": "(F,3) int64 arrow mesh face indices",
    },
    parameters={
        "length_factor": {"type": "float", "default": 1.0, "role": "present"},
        "normalised": {"type": "bool", "default": False, "role": "present"},
    },
    tests=[
        {
            "inputs": {"positions": [[0.0, 0.0, 0.0]], "forces": [[0.0, 0.0, 0.0]]},
            "expected": [None, None],
        },
    ],
)
def force_arrows(
    positions: np.ndarray,
    forces: np.ndarray,
    *,
    length_factor: float = 1.0,
    normalised: bool = False,
) -> tuple[np.ndarray, np.ndarray] | tuple[None, None]:
    F = np.asarray(forces, dtype=float)

    if normalised:
        norms = np.linalg.norm(F, axis=1)
        max_norm = np.max(norms)
        if max_norm > 1e-10:
            F = F / max_norm * length_factor / 5.0
        else:
            return None, None
    else:
        F = F * length_factor / 500.0

    starts = np.asarray(positions, dtype=float)
    ends = starts + F
    return _arrow_mesh(starts, ends)

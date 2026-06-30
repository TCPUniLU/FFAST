"""Geometry metrics relocated from legacy Loupe modules.

- ``ffast.gyradius`` — radius of gyration (from ``modules/loupeGyradius.py``),
  atomic-number-weighted, per structure.
- ``ffast.distance`` / ``ffast.angle`` / ``ffast.dihedral`` — measurements
  between selected atoms (from ``modules/loupeInfoSelect.py``). They take a
  scientific ``selection`` (atom indices) as input, matching the decision to
  make interactive measurements server-owned Metrics.

All are pure geometry: no model, no prediction.

These metrics carry no schema label/description (``label=""``/``description=""``);
their docstrings are developer docs only.
"""
import numpy as np
from jaxtyping import Float

from ffast.metrics import metric, units, inputs as I
from ffast.metrics.signature import Ref

# id = METRIC_NAMESPACE + "." + function name (inputs/shape/params inferred from
# the signature; see ffast/metrics/signature.py).
METRIC_NAMESPACE = "ffast"


@metric(
    label="",
    description="",
    unit=units.length,
    tests=[
        {
            # single structure: two equal-weight atoms at ±1 along x → Rg = 1
            "inputs": {
                "positions": [[1.0, 0.0, 0.0], [-1.0, 0.0, 0.0]],
                "elements": [1, 1],
            },
            "expected": 1.0,
            "atol": 1e-10,
        },
        {
            # uniform: 2 frames x 2 atoms; second frame at ±2 → Rg = 1, 2
            "inputs": {
                "positions": [
                    [[1.0, 0.0, 0.0], [-1.0, 0.0, 0.0]],
                    [[2.0, 0.0, 0.0], [-2.0, 0.0, 0.0]],
                ],
                "elements": [1, 1],
            },
            "expected": [1.0, 2.0],
            "atol": 1e-10,
        },
    ],
)
def gyradius(
    positions: Ref[I.reference_positions],
    elements: Ref[I.reference_elements],
    offsets=None,
) -> Float[np.ndarray, "N_frames"]:
    """Atomic-number-weighted radius of gyration per structure.

    Accepts three input shapes (matching what the InputResolver supplies):
    - uniform trajectory ``positions (N, M, 3)`` + ``elements (M,)`` → ``(N,)``;
    - variable dataset ``positions (total, 3)`` + ``elements (total,)`` plus
      ``offsets`` (molecule boundaries) → ``(N,)``;
    - a single structure ``positions (n, 3)`` (no offsets) → scalar.
    """
    R = np.asarray(positions, dtype=np.float64)
    z = np.asarray(elements, dtype=np.float64)

    if R.ndim == 3:
        # (N, M, 3) uniform; z is (M,)
        w = z / np.sum(z)
        com = np.einsum("a,nax->nx", w, R)            # (N, 3) weighted COM
        s2 = np.sum((R - com[:, None, :]) ** 2, axis=2)  # (N, M)
        return np.sqrt(s2 @ w)                         # (N,)

    if offsets is not None:
        offs = np.asarray(offsets)
        out = np.empty(len(offs) - 1, dtype=np.float64)
        for i in range(len(offs) - 1):
            r = R[offs[i]:offs[i + 1]]
            zi = z[offs[i]:offs[i + 1]]
            sw = np.sum(zi)
            com = np.sum(zi[:, None] * r, axis=0) / sw
            s2 = np.sum((r - com) ** 2, axis=1)
            out[i] = np.sqrt(np.sum(zi * s2) / sw)
        return out

    # single structure (n_atoms, 3) → scalar
    sw = np.sum(z)
    com = np.sum(z[:, None] * R, axis=0) / sw
    s2 = np.sum((R - com) ** 2, axis=1)
    return np.sqrt(np.sum(z * s2) / sw)


@metric(
    label="",
    description="",
    unit=units.length,
    tests=[
        {
            "inputs": {
                "positions": [[0.0, 0.0, 0.0], [3.0, 4.0, 0.0]],
                "selection": [0, 1],
            },
            "expected": 5.0,
            "atol": 1e-10,
        },
    ],
)
def distance(
    positions: Ref[I.reference_positions],
    selection: Ref[I.selection_indices],
) -> float:
    """Distance between the first two selected atoms."""
    R = np.asarray(positions, dtype=np.float64)
    i, j = int(selection[0]), int(selection[1])
    return np.sqrt(np.sum((R[i] - R[j]) ** 2))


@metric(
    label="",
    description="",
    unit=units.angle,
    tests=[
        {
            # i,j,k with vertex j; (i-j)=x, (k-j)=y → 90°
            "inputs": {
                "positions": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                "selection": [0, 1, 2],
            },
            "expected": 90.0,
            "atol": 1e-9,
        },
    ],
)
def angle(
    positions: Ref[I.reference_positions],
    selection: Ref[I.selection_indices],
) -> float:
    """Angle (degrees) at the middle of three selected atoms i-j-k."""
    R = np.asarray(positions, dtype=np.float64)
    i, j, k = int(selection[0]), int(selection[1]), int(selection[2])
    v, v0 = R[k] - R[j], R[i] - R[j]
    u = v / np.linalg.norm(v)
    u0 = v0 / np.linalg.norm(v0)
    return np.degrees(np.arccos(np.clip(np.dot(u, u0), -1.0, 1.0)))


@metric(
    label="",
    description="",
    unit=units.angle,
    tests=[
        {
            # i,j,k,l forming a 90° torsion
            "inputs": {
                "positions": [
                    [1.0, 0.0, 0.0], [0.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0], [0.0, 1.0, 1.0],
                ],
                "selection": [0, 1, 2, 3],
            },
            "expected": 90.0,
            "atol": 1e-9,
        },
    ],
)
def dihedral(
    positions: Ref[I.reference_positions],
    selection: Ref[I.selection_indices],
) -> float:
    """Dihedral (degrees, unsigned) of four selected atoms i-j-k-l."""
    R = np.asarray(positions, dtype=np.float64)
    p = R[[int(selection[0]), int(selection[1]), int(selection[2]), int(selection[3])]]
    b = (p[:-1] - p[1:]).copy()
    b[0] *= -1.0
    v0 = np.cross(b[0], b[1])
    v2 = np.cross(b[2], b[1])
    v0 /= np.linalg.norm(v0)
    v2 /= np.linalg.norm(v2)
    return np.degrees(np.arccos(np.clip(np.dot(v0, v2), -1.0, 1.0)))

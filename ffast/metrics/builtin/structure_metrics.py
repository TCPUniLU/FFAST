"""Geometry metrics relocated from legacy Loupe modules.

- ``ffast.gyradius`` — radius of gyration (from ``modules/loupeGyradius.py``),
  atomic-number-weighted, per structure.
- ``ffast.distance`` / ``ffast.angle`` / ``ffast.dihedral`` — measurements
  between selected atoms (from ``modules/loupeInfoSelect.py``). They take a
  scientific ``selection`` (atom indices) as input, matching the decision to
  make interactive measurements server-owned Metrics.

All are pure geometry: no model, no prediction. The measurement maths itself
lives in ``ffast.chemistry.geometry``, shared with the Qt Info tool.
"""
import numpy as np

from ffast.chemistry import geometry
from ffast.metrics import metric, dims, inputs as I, units


@metric(
    id="ffast.gyradius",
    inputs={"positions": I.reference_positions, "elements": I.reference_elements},
    optional_inputs=["offsets"],
    shape=(dims.N_frames,),
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
            # uniform: 2 frames × 2 atoms; second frame at ±2 → Rg = 1, 2
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
def gyradius(positions, elements, offsets=None):
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
        total_z = np.sum(z)
        if total_z == 0:
            raise ValueError("gyradius: total atomic-number weight is zero")
        w = z / total_z
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
            if sw == 0:
                raise ValueError("gyradius: total atomic-number weight is zero")
            com = np.sum(zi[:, None] * r, axis=0) / sw
            s2 = np.sum((r - com) ** 2, axis=1)
            out[i] = np.sqrt(np.sum(zi * s2) / sw)
        return out

    # single structure (n_atoms, 3) → scalar
    sw = np.sum(z)
    if sw == 0:
        raise ValueError("gyradius: total atomic-number weight is zero")
    com = np.sum(z[:, None] * R, axis=0) / sw
    s2 = np.sum((R - com) ** 2, axis=1)
    return np.sqrt(np.sum(z * s2) / sw)


@metric(
    id="ffast.distance",
    inputs={"positions": I.reference_positions, "selection": I.selection_indices},
    shape=(dims.scalar,),
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
def distance(positions, selection):
    """Distance between the first two selected atoms."""
    R = np.asarray(positions, dtype=np.float64)
    return geometry.distance(*R[[int(s) for s in selection[:2]]])


@metric(
    id="ffast.angle",
    inputs={"positions": I.reference_positions, "selection": I.selection_indices},
    shape=(dims.scalar,),
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
def angle(positions, selection):
    """Angle (degrees) at the middle of three selected atoms i-j-k."""
    R = np.asarray(positions, dtype=np.float64)
    return geometry.angle(*R[[int(s) for s in selection[:3]]])


@metric(
    id="ffast.dihedral",
    inputs={"positions": I.reference_positions, "selection": I.selection_indices},
    shape=(dims.scalar,),
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
def dihedral(positions, selection):
    """Dihedral (degrees, unsigned) of four selected atoms i-j-k-l."""
    R = np.asarray(positions, dtype=np.float64)
    return geometry.dihedral(*R[[int(s) for s in selection[:4]]])

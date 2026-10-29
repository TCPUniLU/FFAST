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
from ffast.metrics.models import WithWarnings
from ffast.metrics.segments import segmentSums

# Ported from dev (b746f06): the old Gyration tab warned the same way.
PERIODIC_WARNING = (
    "The radius of gyration is not meaningful for periodic systems: it "
    "measures the unwrapped coordinates in the cell, not a molecule."
)


@metric(
    id="ffast.gyradius",
    inputs={"positions": I.reference_positions, "elements": I.reference_elements,
            "periodic": I.reference_periodic},
    optional_inputs=["offsets", "periodic"],
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
            # 2 frames × 2 atoms, flat; second frame at ±2 → Rg = 1, 2
            "inputs": {
                "positions": [
                    [1.0, 0.0, 0.0], [-1.0, 0.0, 0.0],
                    [2.0, 0.0, 0.0], [-2.0, 0.0, 0.0],
                ],
                "elements": [1, 1, 1, 1],
                "offsets": [0, 2, 4],
            },
            "expected": [1.0, 2.0],
            "atol": 1e-10,
        },
    ],
)
def gyradius(positions, elements, offsets=None, periodic=False):
    """Atomic-number-weighted radius of gyration per structure.

    Positions are flat ``(atoms, 3)`` and elements every atom's z, as the
    InputResolver supplies them (ADR 0058 step 6): with ``offsets`` → ``(N,)``,
    one radius per structure; without, one structure → a scalar. On
    periodic data the values come with a warning.
    """
    values = _gyradius(positions, elements, offsets)
    return WithWarnings(values, [PERIODIC_WARNING]) if periodic else values


def _gyradius(positions, elements, offsets):
    R = np.asarray(positions, dtype=np.float64)
    z = np.asarray(elements, dtype=np.float64)

    if offsets is not None:
        sw = segmentSums(z, offsets)
        if np.any(sw == 0):
            raise ValueError("gyradius: total atomic-number weight is zero")
        com = segmentSums(z[:, None] * R, offsets) / sw[:, None]
        s2 = np.sum((R - np.repeat(com, np.diff(np.asarray(offsets, dtype=np.intp)), axis=0)) ** 2, axis=1)
        return np.sqrt(segmentSums(z * s2, offsets) / sw)

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

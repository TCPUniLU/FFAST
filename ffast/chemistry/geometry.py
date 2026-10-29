"""Atom measurements — distance, angle, dihedral — in one place.

The server metrics (``ffast.distance`` / ``ffast.angle`` / ``ffast.dihedral``)
and the Qt Info tool both call these. The browser keeps a JavaScript copy
(``ffast/renderers/web/static/measure.js``) so its read-out is instant;
``tests/ffast/geometry_cases.json`` pins both to the same answers
(docs/research/measurement-logic-across-clients.md).

Angles are in degrees. The dihedral is unsigned (0–180°), as the Qt Loupe has
always shown it. A measurement whose direction is undefined — an outer atom on
the vertex of an angle, or three atoms in a line inside a dihedral — returns
``nan`` rather than a plausible-looking number.
"""

from __future__ import annotations

import numpy as np

#: Squared length (Å² for a bond, Å⁴ for a plane normal) below which a direction
#: counts as undefined. The JavaScript copy uses the same value.
UNDEFINED_BELOW = 1e-12


def _unit(v: np.ndarray) -> np.ndarray | None:
    n2 = float(np.dot(v, v))
    return None if n2 < UNDEFINED_BELOW else v / np.sqrt(n2)


def _angle_between(u: np.ndarray | None, v: np.ndarray | None) -> float:
    if u is None or v is None:
        return float("nan")
    return float(np.degrees(np.arccos(np.clip(np.dot(u, v), -1.0, 1.0))))


def distance(a, b) -> float:
    """Distance between two atoms."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return float(np.linalg.norm(a - b))


def angle(a, b, c) -> float:
    """Angle a–b–c at vertex ``b``, in degrees (0–180)."""
    a, b, c = (np.asarray(p, dtype=float) for p in (a, b, c))
    return _angle_between(_unit(a - b), _unit(c - b))


def dihedral(a, b, c, d) -> float:
    """Unsigned dihedral a–b–c–d in degrees (0–180): the angle between the
    a–b–c and b–c–d planes."""
    a, b, c, d = (np.asarray(p, dtype=float) for p in (a, b, c, d))
    axis = b - c
    return _angle_between(_unit(np.cross(b - a, axis)), _unit(np.cross(c - d, axis)))

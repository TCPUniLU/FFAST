"""The one Python implementation of atom measurements (distance, angle, dihedral).

The server metrics and the Qt Info tool both use ``ffast.chemistry.geometry``;
the browser keeps a JavaScript copy for instant read-outs. ``geometry_cases.json``
holds the answers both must give — the browser half is
``tests/ffast/renderers/web/test_measure_parity.py``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from ffast.chemistry import geometry

CASES = json.loads((Path(__file__).parent / "geometry_cases.json").read_text())
MEASURES = [
    (kind, case)
    for kind in ("distance", "angle", "dihedral")
    for case in CASES[kind]
]


@pytest.mark.parametrize(
    "kind,case", MEASURES, ids=[f"{k}: {c['name']}" for k, c in MEASURES]
)
def test_geometry_gives_the_shared_answer(kind, case):
    got = getattr(geometry, kind)(*np.asarray(case["points"], dtype=float))
    if case["expected"] is None:
        assert math.isnan(got), f"expected undefined (nan), got {got}"
    else:
        assert got == pytest.approx(case["expected"], abs=1e-6)


def test_server_metrics_use_the_same_maths():
    """The server metrics take positions + a selection and must agree."""
    from ffast.metrics.builtin import structure_metrics as sm

    pts = [[1, 0, 0], [0, 0, 0], [0, 0, 1], [0.5, 0.8660254037844386, 1]]
    assert sm.dihedral(pts, [0, 1, 2, 3]) == pytest.approx(60.0)
    assert sm.angle(pts, [0, 1, 2]) == pytest.approx(90.0)
    assert sm.distance(pts, [0, 1]) == pytest.approx(1.0)

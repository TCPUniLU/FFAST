from __future__ import annotations

import numpy as np

from ffast.visualization.stages.registry import stage


@stage(
    id="ffast.unit_cell_edges",
    inputs={
        "lattice": "frame.lattice",
        "origin": "view.cell_origin",
    },
    outputs={"segments": "(24,3) float64 unit cell edge endpoints (12 edges x 2 points)"},
    tests=[
        {
            "inputs": {
                "lattice": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                "origin": [0.0, 0.0, 0.0],
            },
            "expected": [
                [0,0,0],[1,0,0], [1,0,0],[1,1,0], [1,1,0],[0,1,0], [0,1,0],[0,0,0],
                [0,0,1],[1,0,1], [1,0,1],[1,1,1], [1,1,1],[0,1,1], [0,1,1],[0,0,1],
                [0,0,0],[0,0,1], [1,0,0],[1,0,1], [0,1,0],[0,1,1], [1,1,0],[1,1,1],
            ],
            "atol": 1e-10,
        },
    ],
)
def unit_cell_edges(lattice: np.ndarray, origin: np.ndarray) -> np.ndarray:
    if hasattr(lattice, "array"):
        lattice = np.array(lattice.array)
    else:
        lattice = np.asarray(lattice, dtype=float)

    origin = np.asarray(origin, dtype=float)
    a, b, c = lattice[0], lattice[1], lattice[2]

    corners = np.array([
        origin,
        origin + a,
        origin + b,
        origin + a + b,
        origin + c,
        origin + a + c,
        origin + b + c,
        origin + a + b + c,
    ])

    edge_pairs = [
        (0, 1), (1, 3), (3, 2), (2, 0),
        (4, 5), (5, 7), (7, 6), (6, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]

    return np.vstack([[corners[i], corners[j]] for i, j in edge_pairs])

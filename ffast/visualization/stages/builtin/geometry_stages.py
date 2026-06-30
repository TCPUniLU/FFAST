from __future__ import annotations

import numpy as np

from ffast.visualization.stages.registry import stage


@stage(
    id="ffast.bond_indices",
    inputs={
        "positions": "frame.positions",
        "bond_sizes": "dataset.bond_sizes",
    },
    outputs={"indices": "(M,2) int64 bond pairs with i<j"},
    tests=[
        {
            "inputs": {
                "positions": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [10.0, 0.0, 0.0]],
                "bond_sizes": [2.0, 2.0, 2.0],
            },
            "expected": [[0, 1]],
            "atol": 0,
        },
    ],
)
def bond_indices(positions: np.ndarray, bond_sizes: np.ndarray) -> np.ndarray:
    from scipy.spatial import distance_matrix
    d = distance_matrix(positions, positions)
    adj = d < bond_sizes
    i, j = np.where(adj)
    mask = i < j
    return np.stack([i[mask], j[mask]], axis=1)


@stage(
    id="ffast.bond_positions",
    inputs={
        "positions": "stage.ffast.atom_positions.positions",
        "indices": "stage.ffast.bond_indices.indices",
    },
    outputs={"segments": "(2M,3) float64 line segment endpoints for vispy segments mode, or None"},
    tests=[
        {
            "inputs": {"positions": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], "indices": [[0, 1]]},
            "expected": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
            "atol": 1e-10,
        },
        {
            "inputs": {"positions": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], "indices": []},
            "expected": None,
        },
    ],
)
def bond_positions(
    positions: np.ndarray,
    indices: np.ndarray,
) -> np.ndarray | None:
    if indices is None or len(indices) == 0:
        return None
    return positions[indices].reshape(-1, 3)


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

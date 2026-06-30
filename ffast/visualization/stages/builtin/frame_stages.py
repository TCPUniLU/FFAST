from __future__ import annotations

import numpy as np

from ffast.visualization.stages.registry import stage


@stage(
    id="ffast.frame",
    inputs={
        "trajectory": "dataset.all_positions",
        "index": "view.structure_index",
    },
    outputs={"positions": "(N,3) float64 positions of the selected structure"},
    parameters={
        "index": {"type": "float", "default": 0.0, "role": "compute", "scope": "view"},
    },
    tests=[
        {
            "inputs": {
                "trajectory": [
                    [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
                    [[0.0, 1.0, 0.0], [1.0, 1.0, 0.0]],
                ],
            },
            "parameters": {"index": 1.0},
            "expected": [[0.0, 1.0, 0.0], [1.0, 1.0, 0.0]],
            "atol": 1e-10,
        },
    ],
)
def frame(trajectory: np.ndarray, *, index: float = 0.0) -> np.ndarray:
    """Select one structure's positions from a trajectory.

    ``trajectory`` is (T,N,3) for multi-frame data or (N,3) for a single
    structure. The index is clamped to the available frame range.
    """
    R = np.asarray(trajectory, dtype=float)
    if R.ndim == 2:
        return R
    i = int(index)
    i = max(0, min(i, R.shape[0] - 1))
    return R[i]

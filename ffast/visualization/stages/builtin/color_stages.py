from __future__ import annotations

import numpy as np

from ffast.visualization.stages.registry import stage

def _species_mean_expand(per_atom_values: np.ndarray, elements: np.ndarray) -> np.ndarray:
    """Replace each atom's value with the mean of its element type."""
    result = np.empty_like(per_atom_values, dtype=np.float64)
    for z in np.unique(elements):
        mask = elements == z
        result[mask] = np.mean(per_atom_values[mask])
    return result


@stage(
    id="ffast.displacement_stats",
    inputs={"trajectory": "dataset.all_positions"},
    outputs={
        "d_total": "(N,) float64 per-atom RMS displacement from first to last frame",
        "d_mean": "(N,) float64 per-atom mean RMS step displacement",
    },
    tests=[
        {
            "inputs": {
                "trajectory": [
                    [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                    [[1.0, 1.0, 1.0], [0.0, 0.0, 0.0]],
                ],
            },
            "expected": [[1.0, 0.0], [1.0, 0.0]],
            "atol": 1e-10,
        },
    ],
)
def displacement_stats(
    trajectory: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute per-atom displacement statistics over a trajectory.

    trajectory: (T, N, 3) array of positions for all frames.
    Returns (d_total, d_mean) where each is (N,).
    """
    R = np.asarray(trajectory)  # (T, N, 3)

    diff_total = R[-1] - R[0]
    d_total = np.sqrt(np.mean(diff_total**2, axis=1))

    diff_steps = R[1:] - R[:-1]  # (T-1, N, 3)
    d_steps = np.sqrt(np.mean(diff_steps**2, axis=2))  # (T-1, N)
    d_mean = np.mean(d_steps, axis=0)

    return d_total, d_mean

from __future__ import annotations

import numpy as np

from ffast.visualization.stages.registry import stage

_COLORMAPS = ["inferno", "plasma", "viridis", "coolwarm", "hot", "bwr", "force_error"]

# Blue→green→yellow→dark-red→red gradient used for force error coloring.
_FORCE_ERROR_COLORS = [
    (0.1, 0.1, 0.9),
    (0.1, 0.9, 0.1),
    (0.9, 0.9, 0.1),
    (0.5, 0.1, 0.1),
    (0.9, 0.1, 0.1),
]

_force_error_cmap = None


def _get_colormap(name: str):
    if name == "force_error":
        global _force_error_cmap
        if _force_error_cmap is None:
            from vispy.color import Colormap  # type: ignore[import]
            _force_error_cmap = Colormap(_FORCE_ERROR_COLORS)
        return _force_error_cmap
    from vispy.color import get_colormap  # type: ignore[import]
    return get_colormap(name)


@stage(
    id="ffast.value_colors",
    inputs={"values": "metric.values"},
    outputs={"colors": "(N,4) float32 RGBA colors mapped from scalar values"},
    parameters={
        "colormap": {
            "type": "choice",
            "choices": _COLORMAPS,
            "default": "inferno",
            "role": "present",
        },
    },
)
def value_colors(
    values: np.ndarray,
    vmin: float | None = None,
    vmax: float | None = None,
    *,
    colormap: str = "inferno",
) -> np.ndarray:
    v = np.asarray(values, dtype=np.float32).ravel()
    lo = float(np.nanmin(v)) if vmin is None else vmin
    hi = float(np.nanmax(v)) if vmax is None else vmax

    if hi == lo:
        normalized = np.zeros_like(v)
    else:
        normalized = np.clip((v - lo) / (hi - lo), 0.0, 1.0)

    cmap = _get_colormap(colormap)
    return cmap[normalized].rgba


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

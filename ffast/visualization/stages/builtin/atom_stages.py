from __future__ import annotations

import numpy as np

from ffast.visualization.stages.registry import stage


@stage(
    id="ffast.atom_positions",
    inputs={
        "positions": "frame.positions",
        "transforms": "view.transforms",
    },
    outputs={"positions": "(N,3) float64 transformed atom positions"},
    tests=[
        {
            "inputs": {"positions": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]},
            "expected": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            "atol": 1e-10,
        },
        {
            "inputs": {"positions": [[0.0, 0.0, 0.0]], "transforms": [[1.0, 2.0, 3.0]]},
            "expected": [[1.0, 2.0, 3.0]],
            "atol": 1e-10,
        },
    ],
)
def atom_positions(
    positions: np.ndarray,
    transforms: list | None = None,
) -> np.ndarray:
    R = np.array(positions, dtype=float)
    for t in ([] if transforms is None else transforms):
        t = np.asarray(t)
        if t.ndim == 1:
            R = R + t
        else:
            R = R @ t
    return R


@stage(
    id="ffast.atom_sizes",
    inputs={"z": "frame.elements"},
    outputs={"sizes": "(N,) float32 display radii"},
    parameters={
        "scale": {"type": "float", "default": 1.0, "role": "present"},
    },
)
def atom_sizes(z: np.ndarray, *, scale: float = 1.0) -> np.ndarray:
    # deferred import so ffast package loads without legacy config on path
    from config.atoms import covalentRadii  # type: ignore[import]
    return covalentRadii[z] * scale


@stage(
    id="ffast.atom_colors",
    inputs={"z": "frame.elements"},
    outputs={"colors": "(N,4) float32 RGBA element colors"},
    parameters={
        "dimming": {"type": "float", "default": 1.0, "role": "present", "min": 0.0, "max": 1.0},
    },
)
def atom_colors(z: np.ndarray, *, dimming: float = 1.0) -> np.ndarray:
    from config.atoms import atomColors  # type: ignore[import]
    n = len(z)
    rgba = np.ones((n, 4), dtype=np.float32)
    rgba[:, :3] = atomColors[z] / 255.0 * dimming
    return rgba

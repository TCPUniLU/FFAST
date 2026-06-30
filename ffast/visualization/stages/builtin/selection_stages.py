from __future__ import annotations

import numpy as np

from ffast.visualization.stages.registry import stage


@stage(
    id="ffast.selection_mask",
    inputs={
        "positions": "frame.positions",
        "indices": "view.selection_indices",
    },
    outputs={"mask": "(N,) bool selection membership"},
    tests=[
        {
            "inputs": {
                "positions": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]],
                "indices": [0, 2],
            },
            "expected": [True, False, True],
            "atol": 0,
        },
        {
            "inputs": {"positions": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]},
            "expected": [False, False],
            "atol": 0,
        },
    ],
)
def selection_mask(positions: np.ndarray, indices: list | None = None) -> np.ndarray:
    """Boolean membership mask for a named scientific selection.

    Out-of-range indices are silently dropped so a selection survives a frame
    whose atom count shrank.
    """
    n = len(np.asarray(positions))
    mask = np.zeros(n, dtype=bool)
    if indices is None:
        return mask
    idx = np.asarray(indices, dtype=int).ravel()
    idx = idx[(idx >= 0) & (idx < n)]
    mask[idx] = True
    return mask


@stage(
    id="ffast.atom_filter",
    inputs={
        "positions": "frame.positions",
        "indices": "view.filter_indices",
    },
    outputs={"mask": "(N,) bool atoms to display"},
    parameters={
        "invert": {"type": "bool", "default": False, "role": "compute", "scope": "view_dataset"},
    },
    tests=[
        {
            "inputs": {
                "positions": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]],
                "indices": [1],
            },
            "expected": [False, True, False],
            "atol": 0,
        },
        {
            "inputs": {
                "positions": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
                "indices": [],
            },
            "expected": [True, True],
            "atol": 0,
        },
    ],
)
def atom_filter(
    positions: np.ndarray,
    indices: list | None = None,
    *,
    invert: bool = False,
) -> np.ndarray:
    """Keep-mask for explicit-index atom filtering (legacy ``atomFilterIndices``).

    An empty/None index list keeps every atom. ``invert`` flips membership so the
    listed atoms are hidden instead of isolated.
    """
    n = len(np.asarray(positions))
    if indices is None or len(indices) == 0:
        return np.ones(n, dtype=bool)
    keep = np.zeros(n, dtype=bool)
    idx = np.asarray(indices, dtype=int).ravel()
    idx = idx[(idx >= 0) & (idx < n)]
    keep[idx] = True
    return ~keep if invert else keep

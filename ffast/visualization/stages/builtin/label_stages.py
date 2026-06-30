from __future__ import annotations

import numpy as np

from ffast.visualization.stages.registry import stage


@stage(
    id="ffast.atom_labels",
    inputs={
        "positions": "stage.ffast.atom_positions.positions",
        "elements": "frame.elements",
    },
    outputs={
        "positions": "(K,3) float64 label anchor positions",
        "texts": "list[str] label strings, one per atom",
    },
    parameters={
        "mode": {
            "type": "choice",
            "choices": ["index", "element"],
            "default": "index",
            "role": "present",
            "scope": "view",
        },
    },
    tests=[
        {
            "inputs": {"positions": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]},
            "parameters": {"mode": "index"},
            "expected": [[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], ["0", "1"]],
            "atol": 1e-10,
        },
    ],
)
def atom_labels(
    positions: np.ndarray,
    elements: np.ndarray | None = None,
    *,
    mode: str = "index",
) -> tuple[np.ndarray, list[str]]:
    """Per-atom text labels anchored at atom positions.

    ``index`` labels each atom with its position in the structure; ``element``
    labels with the element symbol (falling back to the atomic number when no
    elements are supplied or a Z has no known symbol).
    """
    P = np.asarray(positions, dtype=float)
    n = len(P)
    if mode == "element" and elements is not None:
        from config.atoms import zIntToZStr  # type: ignore[import]
        z = np.asarray(elements).ravel()
        texts = [zIntToZStr.get(int(zi), str(int(zi))) for zi in z[:n]]
    else:
        texts = [str(i) for i in range(n)]
    return P, texts

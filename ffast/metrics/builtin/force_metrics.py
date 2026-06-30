from typing import Literal

import numpy as np
from jaxtyping import Float

from ffast.metrics import metric, units, inputs as I
from ffast.metrics.signature import Ref

# id = METRIC_NAMESPACE + "." + function name (inputs/shape/params inferred from
# the signature; see ffast/metrics/signature.py).
METRIC_NAMESPACE = "ffast"


@metric(
    label="Force Difference",
    unit=units.force,
    tests=[
        {
            "inputs": {
                "reference": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                "predicted": [[2.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            },
            "parameters": {},
            "expected": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            "atol": 1e-10,
        }
    ],
)
def force_difference(
    reference: Ref[I.reference_forces],
    predicted: Ref[I.prediction_forces],
) -> Float[np.ndarray, "N_atoms xyz"]:
    return predicted - reference


@metric(
    label="Reference Forces",
    unit=units.force,
    tests=[
        {
            "inputs": {"reference": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]]},
            "parameters": {},
            "expected": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            "atol": 1e-10,
        }
    ],
)
def force_reference(
    reference: Ref[I.reference_forces],
) -> Float[np.ndarray, "N_atoms xyz"]:
    # Pass-through metric (see ffast.energy_reference): exposes ground-truth
    # forces through the server-owned metric channel for the Forces Scatter plot.
    return np.asarray(reference)


@metric(
    label="Predicted Forces",
    unit=units.force,
    tests=[
        {
            "inputs": {"predicted": [[2.0, 0.0, 0.0], [0.0, 0.0, 0.0]]},
            "parameters": {},
            "expected": [[2.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            "atol": 1e-10,
        }
    ],
)
def force_prediction(
    predicted: Ref[I.prediction_forces],
) -> Float[np.ndarray, "N_atoms xyz"]:
    return np.asarray(predicted)


@metric(
    label="Force Error (per atom)",
    description="Per-atom mean absolute force error between prediction and reference.",
    unit=units.force,
    tests=[
        {
            "inputs": {
                "reference": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                "predicted": [[2.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            },
            "parameters": {"norm": "l2"},
            "expected": [1.0, 0.0],
            "atol": 1e-6,
        },
        {
            "inputs": {
                "reference": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                "predicted": [[2.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            },
            "parameters": {"norm": "l1"},
            "expected": [1.0 / 3.0, 0.0],
            "atol": 1e-6,
        },
    ],
)
def force_mae(
    force_difference: Ref["ffast.force_difference"],
    *,
    norm: Literal["l1", "l2"] = "l2",
) -> Float[np.ndarray, "N_atoms"]:
    if norm == "l1":
        return np.mean(np.abs(force_difference), axis=-1)
    return np.linalg.norm(force_difference, axis=-1)


@metric(
    label="Force RMSE (per frame)",
    unit=units.force,
    tests=[
        {
            # 1 molecule, 2 atoms — offsets=[0,2]
            "inputs": {
                "reference": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                "predicted": [[2.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                "offsets": [0, 2],
            },
            "parameters": {},
            "expected": [0.7071067811865476],
            "atol": 1e-6,
        }
    ],
)
def force_rmse(
    force_mae: Ref["ffast.force_mae"],
    offsets=None,
) -> Float[np.ndarray, "N_frames"]:
    if offsets is not None:
        offsets = np.asarray(offsets, dtype=np.intp)
        return np.array([
            np.sqrt(np.mean(force_mae[offsets[i]:offsets[i + 1]] ** 2))
            for i in range(len(offsets) - 1)
        ])
    return np.sqrt(np.mean(force_mae ** 2, axis=-1))


@metric(
    label="Force MAE (per frame)",
    description="Per-structure mean of the per-atom force error magnitude.",
    unit=units.force,
    tests=[
        {
            # 1 molecule, 2 atoms — offsets=[0,2]
            "inputs": {
                "reference": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                "predicted": [[2.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                "offsets": [0, 2],
            },
            "parameters": {},
            "expected": [0.5],
            "atol": 1e-6,
        }
    ],
)
def force_mae_per_structure(
    force_mae: Ref["ffast.force_mae"],
    offsets=None,
) -> Float[np.ndarray, "N_frames"]:
    if offsets is not None:
        offsets = np.asarray(offsets, dtype=np.intp)
        return np.array([
            np.mean(force_mae[offsets[i]:offsets[i + 1]])
            for i in range(len(offsets) - 1)
        ])
    return np.mean(force_mae, axis=-1)


@metric(
    label="Force MAE",
    unit=units.force,
    tests=[
        {
            "inputs": {
                "reference": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                "predicted": [[2.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            },
            "parameters": {},
            "expected": 0.5,
            "atol": 1e-6,
        }
    ],
)
def force_mae_global(force_mae: Ref["ffast.force_mae"]) -> float:
    return np.mean(force_mae)


@metric(
    label="Force RMSE",
    unit=units.force,
    tests=[
        {
            "inputs": {
                "reference": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                "predicted": [[2.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            },
            "parameters": {},
            "expected": 0.7071067811865476,
            "atol": 1e-6,
        }
    ],
)
def force_rmse_global(force_mae: Ref["ffast.force_mae"]) -> float:
    return np.sqrt(np.mean(force_mae ** 2))


@metric(
    label="Force Component MAE",
    unit=units.force,
    tests=[
        {
            "inputs": {
                "reference": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                "predicted": [[2.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            },
            "parameters": {},
            "expected": 1.0 / 6.0,
            "atol": 1e-6,
        }
    ],
)
def force_component_mae(force_difference: Ref["ffast.force_difference"]) -> float:
    return np.mean(np.abs(force_difference))


@metric(
    label="Force Component RMSE",
    unit=units.force,
    tests=[
        {
            "inputs": {
                "reference": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                "predicted": [[2.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            },
            "parameters": {},
            "expected": 0.408248290463863,
            "atol": 1e-6,
        }
    ],
)
def force_component_rmse(force_difference: Ref["ffast.force_difference"]) -> float:
    return np.sqrt(np.mean(force_difference ** 2))


@metric(
    label="Net Force Residual (per frame)",
    description=(
        "Net force-difference vector per structure: sum of the force-error "
        "vectors over all atoms. Measures residual linear-momentum error."
    ),
    unit=units.force,
    tests=[
        {
            # 1 structure, 2 atoms; net = sum([[1,0,0],[1,0,0]]) = [[2,0,0]]
            "inputs": {
                "reference": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                "predicted": [[2.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
                "offsets": [0, 2],
            },
            "parameters": {},
            "expected": [[2.0, 0.0, 0.0]],
            "atol": 1e-10,
        }
    ],
)
def force_net_residual(
    force_difference: Ref["ffast.force_difference"],
    offsets=None,
) -> Float[np.ndarray, "N_frames xyz"]:
    if offsets is not None:
        offsets = np.asarray(offsets, dtype=np.intp)
        return np.array([
            np.asarray(force_difference[offsets[i]:offsets[i + 1]]).sum(axis=0)
            for i in range(len(offsets) - 1)
        ])
    # uniform: (N_frames, N_atoms, 3) — sum over the atoms axis
    return np.asarray(force_difference).sum(axis=-2)


@metric(
    label="Net Force MAE (per frame)",
    description=(
        "Per-structure MAE of the net force residual vector: "
        "mean over xyz components of |net_residual| per frame."
    ),
    unit=units.force,
    tests=[
        {
            # net_residual = [[2,0,0]] → mean(abs, axis=-1) = [2/3]
            "inputs": {
                "reference": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                "predicted": [[2.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
                "offsets": [0, 2],
            },
            "parameters": {},
            "expected": [2.0 / 3.0],
            "atol": 1e-10,
        }
    ],
)
def force_net_mae_per_structure(
    force_net_residual: Ref["ffast.force_net_residual"],
) -> Float[np.ndarray, "N_frames"]:
    return np.mean(np.abs(np.asarray(force_net_residual)), axis=-1)


@metric(
    label="Net Force MAE",
    description="Global MAE of the net force residual: mean |net_residual| over all frames and components.",
    unit=units.force,
    tests=[
        {
            # net_residual = [[2,0,0]] → mean(abs) = 2/3
            "inputs": {
                "reference": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                "predicted": [[2.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
                "offsets": [0, 2],
            },
            "parameters": {},
            "expected": 2.0 / 3.0,
            "atol": 1e-10,
        }
    ],
)
def force_net_mae(force_net_residual: Ref["ffast.force_net_residual"]) -> float:
    return np.mean(np.abs(np.asarray(force_net_residual)))


@metric(
    label="Net Force RMSE",
    description="Global RMSE of the net force residual: RMS of net_residual over all frames and components.",
    unit=units.force,
    tests=[
        {
            # net_residual = [[2,0,0]] → sqrt(mean([4,0,0])) = sqrt(4/3)
            "inputs": {
                "reference": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                "predicted": [[2.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
                "offsets": [0, 2],
            },
            "parameters": {},
            "expected": (4.0 / 3.0) ** 0.5,
            "atol": 1e-10,
        }
    ],
)
def force_net_rmse(force_net_residual: Ref["ffast.force_net_residual"]) -> float:
    r = np.asarray(force_net_residual)
    return np.sqrt(np.mean(r ** 2))

from typing import Literal

import numpy as np
from jaxtyping import Float

from ffast.metrics import metric, units, inputs as I
from ffast.metrics.segments import framesOrOne, segmentMeans, segmentSums
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
    label="Force Error Magnitude (per atom)",
    description=(
        "Length of each atom's force error vector |F_pred - F_ref| (norm l2); "
        "with norm l1, the mean of its three absolute components."
    ),
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
    label="Force Error Magnitude, RMS (per frame)",
    description=(
        "Root mean square over each structure's atoms of the error vector "
        "length; always sqrt(3) times Force RMSE (per frame)."
    ),
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
    force_mae = np.asarray(force_mae)
    return np.sqrt(segmentMeans(force_mae ** 2, framesOrOne(offsets, force_mae)))


@metric(
    label="Force Error Magnitude, mean (per frame)",
    description="Per-structure mean of the per-atom force error vector length.",
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
    return segmentMeans(force_mae, framesOrOne(offsets, force_mae))


@metric(
    label="Force Error Magnitude, mean",
    description="Mean over all atoms of the force error vector length.",
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
    label="Force Error Magnitude, RMS",
    description="Root mean square over all atoms of the force error vector length; always sqrt(3) times Force RMSE.",
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
    label="Force MAE",
    description=(
        "Mean of |F_pred - F_ref| over every x, y and z component of every "
        "atom, as MACE and NequIP report it."
    ),
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
    label="Force RMSE",
    description=(
        "Root mean square of F_pred - F_ref over every x, y and z component of "
        "every atom, as MACE and NequIP report it."
    ),
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
    label="Force MAE (per frame)",
    description="Force MAE of each structure: the mean over its atoms' x, y and z components.",
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
            "expected": [1.0 / 6.0],
            "atol": 1e-6,
        }
    ],
)
def force_component_mae_per_structure(
    force_difference: Ref["ffast.force_difference"],
    offsets=None,
) -> Float[np.ndarray, "N_frames"]:
    fd = np.asarray(force_difference)
    return segmentMeans(np.abs(fd).mean(axis=-1), framesOrOne(offsets, fd))


@metric(
    label="Force RMSE (per frame)",
    description="Force RMSE of each structure: the root mean square over its atoms' x, y and z components.",
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
            "expected": [0.408248290463863],
            "atol": 1e-6,
        }
    ],
)
def force_component_rmse_per_structure(
    force_difference: Ref["ffast.force_difference"],
    offsets=None,
) -> Float[np.ndarray, "N_frames"]:
    fd = np.asarray(force_difference)
    return np.sqrt(segmentMeans((fd ** 2).mean(axis=-1), framesOrOne(offsets, fd)))


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
    return segmentSums(force_difference, framesOrOne(offsets, force_difference))


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

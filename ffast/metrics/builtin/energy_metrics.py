import numpy as np
from jaxtyping import Float

from ffast.metrics import metric, units, inputs as I
from ffast.metrics.signature import Ref

# id = METRIC_NAMESPACE + "." + function name (inputs/shape/params inferred from
# the signature; see ffast/metrics/signature.py).
METRIC_NAMESPACE = "ffast"


@metric(
    label="Energy Difference (per frame)",
    unit=units.energy,
    tests=[
        {
            "inputs": {
                "reference": [1.0, 2.0, 3.0],
                "predicted": [1.5, 2.5, 3.5],
            },
            "parameters": {},
            "expected": [0.5, 0.5, 0.5],
            "atol": 1e-10,
        }
    ],
)
def energy_difference(
    reference: Ref[I.reference_energies],
    predicted: Ref[I.prediction_energies],
) -> Float[np.ndarray, "N_frames"]:
    return predicted - reference


@metric(
    label="Reference Energy (per frame)",
    unit=units.energy,
    tests=[
        {
            "inputs": {"reference": [1.0, 2.0, 3.0]},
            "parameters": {},
            "expected": [1.0, 2.0, 3.0],
            "atol": 1e-10,
        }
    ],
)
def energy_reference(
    reference: Ref[I.reference_energies],
) -> Float[np.ndarray, "N_frames"]:
    # Pass-through metric: exposes ground-truth energies through the server-owned
    # metric channel (Stage 4a) so plots (e.g. Energy Scatter) need no raw client
    # arrays.
    return np.asarray(reference)


@metric(
    label="Predicted Energy (per frame)",
    unit=units.energy,
    tests=[
        {
            "inputs": {"predicted": [1.5, 2.5, 3.5]},
            "parameters": {},
            "expected": [1.5, 2.5, 3.5],
            "atol": 1e-10,
        }
    ],
)
def energy_prediction(
    predicted: Ref[I.prediction_energies],
) -> Float[np.ndarray, "N_frames"]:
    return np.asarray(predicted)


@metric(
    label="Energy Shift",
    unit=units.energy,
    tests=[
        {
            "inputs": {
                "reference": [1.0, 2.0, 3.0],
                "predicted": [1.5, 2.5, 3.5],
            },
            "parameters": {},
            "expected": 0.5,
            "atol": 1e-10,
        }
    ],
)
def energy_shift(energy_difference: Ref["ffast.energy_difference"]) -> float:
    return np.mean(energy_difference)


@metric(
    label="Energy MAE",
    unit=units.energy,
    tests=[
        {
            "inputs": {
                "reference": [0.0, 2.0],
                "predicted": [1.0, 2.0],
            },
            "parameters": {},
            "expected": 0.5,
            "atol": 1e-10,
        }
    ],
)
def energy_mae(energy_difference: Ref["ffast.energy_difference"]) -> float:
    return np.mean(np.abs(energy_difference))


@metric(
    label="Energy RMSE",
    unit=units.energy,
    tests=[
        {
            "inputs": {
                "reference": [0.0, 2.0],
                "predicted": [1.0, 2.0],
            },
            "parameters": {},
            "expected": 0.7071067811865476,
            "atol": 1e-6,
        }
    ],
)
def energy_rmse(energy_difference: Ref["ffast.energy_difference"]) -> float:
    return np.sqrt(np.mean(energy_difference ** 2))


@metric(
    label="Energy MAE (shifted)",
    unit=units.energy,
    tests=[
        {
            "inputs": {
                "reference": [0.0, 0.0],
                "predicted": [0.5, 0.5],
            },
            "parameters": {},
            "expected": 0.0,
            "atol": 1e-10,
        }
    ],
)
def energy_mae_shifted(
    energy_difference: Ref["ffast.energy_difference"],
    energy_shift: Ref["ffast.energy_shift"],
) -> float:
    return np.mean(np.abs(energy_difference - energy_shift))


@metric(
    label="Energy RMSE (shifted)",
    unit=units.energy,
    tests=[
        {
            "inputs": {
                "reference": [0.0, 0.0],
                "predicted": [0.5, 0.5],
            },
            "parameters": {},
            "expected": 0.0,
            "atol": 1e-10,
        }
    ],
)
def energy_rmse_shifted(
    energy_difference: Ref["ffast.energy_difference"],
    energy_shift: Ref["ffast.energy_shift"],
) -> float:
    return np.sqrt(np.mean((energy_difference - energy_shift) ** 2))

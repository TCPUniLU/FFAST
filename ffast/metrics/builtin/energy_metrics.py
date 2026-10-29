import numpy as np
from jaxtyping import Float

from ffast.metrics import metric, units, inputs as I
from ffast.metrics.signature import Ref

# id = METRIC_NAMESPACE + "." + function name (inputs/shape/params inferred from
# the signature; see ffast/metrics/signature.py).
METRIC_NAMESPACE = "ffast"


@metric(
    label="Energy Difference",
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


# Energy errors are these two base metrics reduced with the keep boxes
# (ADR 0065): Energy MAE is ffast.energy_difference[dataset;mae], the energy
# shift ffast.energy_difference[dataset;mean].
@metric(
    label="Energy Difference (shifted)",
    description=(
        "Energy difference of each frame minus its mean over the dataset, so a "
        "constant offset between the model and the reference is taken out."
    ),
    unit=units.energy,
    tests=[
        {
            "inputs": {"reference": [1.0, 2.0, 3.0], "predicted": [1.5, 2.5, 4.5]},
            "parameters": {},
            "expected": [-1.0 / 3.0, -1.0 / 3.0, 2.0 / 3.0],
            "atol": 1e-10,
        }
    ],
)
def energy_difference_shifted(
    energy_difference: Ref["ffast.energy_difference"],
) -> Float[np.ndarray, "N_frames"]:
    d = np.asarray(energy_difference, dtype=np.float64)
    return d - np.mean(d)

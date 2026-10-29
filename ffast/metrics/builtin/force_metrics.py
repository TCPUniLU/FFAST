import numpy as np
from jaxtyping import Float

from ffast.metrics import metric, units, inputs as I
from ffast.metrics.segments import framesOrOne, segmentSums
from ffast.metrics.signature import Ref

# id = METRIC_NAMESPACE + "." + function name (inputs/shape/params inferred from
# the signature; see ffast/metrics/signature.py).
METRIC_NAMESPACE = "ffast"

# Force errors are base metrics reduced with the keep boxes (ADR 0065): Force
# MAE per frame is ffast.force_difference[structure;component;mae].


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
    label="Net Force Residual",
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


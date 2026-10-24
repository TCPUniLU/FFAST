import numpy as np
from ffast.metrics import metric, dims, inputs as I, units

# Acceleration errors are this base metric reduced with the keep boxes
# (ADR 0065): ffast.accel_difference[structure;length;rmse] and so on.


@metric(
    id="ffast.accel_difference",
    label="Acceleration Difference",
    inputs={
        "force_difference": "ffast.force_difference",
        "masses": I.reference_masses,
    },
    shape=(dims.N_atoms, dims.xyz),
    unit=units.acceleration,
    tests=[
        {
            "inputs": {
                "reference": [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                "predicted": [[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]],
                "masses": [1.0, 2.0],
            },
            "parameters": {},
            "expected": [[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]],
            "atol": 1e-10,
        }
    ],
)
def accel_difference(force_difference, masses):
    fd = np.asarray(force_difference)
    m = np.asarray(masses, dtype=np.float64)
    if np.any(m == 0.0):
        raise ValueError("accel_difference: zero-mass atom(s) make acceleration undefined")
    return fd / m[:, np.newaxis]


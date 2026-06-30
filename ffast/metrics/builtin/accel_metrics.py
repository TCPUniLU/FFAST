from typing import Literal

import numpy as np
from jaxtyping import Float

from ffast.metrics import metric, units, inputs as I
from ffast.metrics.signature import Ref

# id = METRIC_NAMESPACE + "." + function name (inputs/shape/params inferred from
# the signature; see ffast/metrics/signature.py).
METRIC_NAMESPACE = "ffast"


@metric(
    label="Acceleration Difference",
    unit=units.acceleration,
    tests=[
        {
            "inputs": {
                "reference": [[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]],
                "predicted": [[[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]]],
                "masses": [1.0, 2.0],
            },
            "parameters": {},
            "expected": [[[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]]],
            "atol": 1e-10,
        }
    ],
)
def accel_difference(
    force_difference: Ref["ffast.force_difference"],
    masses: Ref[I.reference_masses],
) -> Float[np.ndarray, "N_atoms xyz"]:
    fd = np.asarray(force_difference)
    if fd.ndim == 2:
        fd = fd[np.newaxis]
    m = np.asarray(masses, dtype=np.float64)
    return fd / m[np.newaxis, :, np.newaxis]


@metric(
    label="Acceleration MAE (per frame)",
    unit=units.acceleration,
    tests=[
        {
            "inputs": {
                "reference": [[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]],
                "predicted": [[[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]]],
                "masses": [1.0, 2.0],
            },
            "parameters": {"norm": "l2"},
            "expected": [[5.0, 0.0]],
            "atol": 1e-6,
        },
        {
            "inputs": {
                "reference": [[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]],
                "predicted": [[[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]]],
                "masses": [1.0, 2.0],
            },
            "parameters": {"norm": "l1"},
            "expected": [[7.0 / 3.0, 0.0]],
            "atol": 1e-6,
        },
    ],
)
def accel_mae(
    accel_difference: Ref["ffast.accel_difference"],
    *,
    norm: Literal["l1", "l2"] = "l2",
) -> Float[np.ndarray, "N_frames N_atoms"]:
    if norm == "l1":
        return np.mean(np.abs(accel_difference), axis=-1)
    return np.linalg.norm(accel_difference, axis=-1)


@metric(
    label="Acceleration RMSE (per frame)",
    unit=units.acceleration,
    tests=[
        {
            "inputs": {
                "reference": [[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]],
                "predicted": [[[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]]],
                "masses": [1.0, 2.0],
            },
            "parameters": {},
            "expected": [3.5355339059327378],
            "atol": 1e-6,
        }
    ],
)
def accel_rmse(accel_mae: Ref["ffast.accel_mae"]) -> Float[np.ndarray, "N_frames"]:
    return np.sqrt(np.mean(accel_mae ** 2, axis=-1))


@metric(
    label="Acceleration MAE",
    unit=units.acceleration,
    tests=[
        {
            "inputs": {
                "reference": [[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]],
                "predicted": [[[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]]],
                "masses": [1.0, 2.0],
            },
            "parameters": {},
            "expected": 2.5,
            "atol": 1e-6,
        }
    ],
)
def accel_mae_global(accel_mae: Ref["ffast.accel_mae"]) -> float:
    return np.mean(accel_mae)


@metric(
    label="Acceleration RMSE",
    unit=units.acceleration,
    tests=[
        {
            "inputs": {
                "reference": [[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]],
                "predicted": [[[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]]],
                "masses": [1.0, 2.0],
            },
            "parameters": {},
            "expected": 3.5355339059327378,
            "atol": 1e-6,
        }
    ],
)
def accel_rmse_global(accel_mae: Ref["ffast.accel_mae"]) -> float:
    return np.sqrt(np.mean(accel_mae ** 2))


@metric(
    label="Acceleration Error",
    description="Per-atom mean absolute acceleration error (force error divided by mass).",
    unit=units.acceleration,
    tests=[
        {
            "inputs": {
                "reference": [[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]],
                "predicted": [[[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]]],
                "masses": [1.0, 2.0],
            },
            "parameters": {},
            "expected": [5.0, 0.0],
            "atol": 1e-6,
        }
    ],
)
def accel_mae_per_atom(accel_mae: Ref["ffast.accel_mae"]) -> Float[np.ndarray, "N_atoms"]:
    am = np.asarray(accel_mae)
    if am.ndim == 1:
        am = am[np.newaxis]
    return np.mean(am, axis=0)


@metric(
    label="Acceleration Error (by element)",
    unit=units.acceleration,
    tests=[
        {
            "inputs": {
                "reference": [[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]],
                "predicted": [[[3.0, 4.0, 0.0], [0.0, 0.0, 0.0]]],
                "masses": [1.0, 2.0],
                "elements": [1, 6],
            },
            "parameters": {},
            "expected": [5.0, 0.0],
            "atol": 1e-6,
        }
    ],
)
def accel_mae_per_element(
    accel_mae: Ref["ffast.accel_mae"],
    elements: Ref[I.reference_elements],
) -> Float[np.ndarray, "N_elements"]:
    am = np.asarray(accel_mae)
    if am.ndim == 1:
        am = am[np.newaxis]
    el = np.asarray(elements)
    unique_z = np.unique(el)
    result = np.empty(len(unique_z), dtype=np.float64)
    for i, z in enumerate(unique_z):
        result[i] = np.mean(am[:, el == z])
    return result

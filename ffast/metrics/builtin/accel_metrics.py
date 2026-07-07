import numpy as np
from ffast.metrics import metric, dims, inputs as I, units


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
def accel_difference(force_difference, masses):
    fd = np.asarray(force_difference)
    if fd.ndim == 2:
        fd = fd[np.newaxis]
    m = np.asarray(masses, dtype=np.float64)
    if np.any(m == 0.0):
        raise ValueError("accel_difference: zero-mass atom(s) make acceleration undefined")
    return fd / m[np.newaxis, :, np.newaxis]


@metric(
    id="ffast.accel_mae",
    label="Acceleration MAE (per frame)",
    inputs={"accel_difference": "ffast.accel_difference"},
    shape=(dims.N_frames, dims.N_atoms),
    unit=units.acceleration,
    parameters={
        "norm": {"type": "choice", "choices": ["l1", "l2"], "default": "l2", "role": "compute"},
    },
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
def accel_mae(accel_difference, *, norm="l2"):
    if norm == "l1":
        return np.mean(np.abs(accel_difference), axis=-1)
    return np.linalg.norm(accel_difference, axis=-1)


@metric(
    id="ffast.accel_rmse",
    label="Acceleration RMSE (per frame)",
    inputs={"accel_mae": "ffast.accel_mae"},
    shape=(dims.N_frames,),
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
def accel_rmse(accel_mae):
    return np.sqrt(np.mean(accel_mae ** 2, axis=-1))


@metric(
    id="ffast.accel_mae_global",
    label="Acceleration MAE",
    inputs={"accel_mae": "ffast.accel_mae"},
    shape=(dims.scalar,),
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
def accel_mae_global(accel_mae):
    return np.mean(accel_mae)


@metric(
    id="ffast.accel_rmse_global",
    label="Acceleration RMSE",
    inputs={"accel_mae": "ffast.accel_mae"},
    shape=(dims.scalar,),
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
def accel_rmse_global(accel_mae):
    return np.sqrt(np.mean(accel_mae ** 2))


@metric(
    id="ffast.accel_mae_per_atom",
    label="Acceleration Error",
    description="Per-atom mean absolute acceleration error (force error divided by mass).",
    inputs={"accel_mae": "ffast.accel_mae"},
    shape=(dims.N_atoms,),
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
def accel_mae_per_atom(accel_mae):
    am = np.asarray(accel_mae)
    if am.ndim == 1:
        am = am[np.newaxis]
    return np.mean(am, axis=0)


@metric(
    id="ffast.accel_mae_per_element",
    label="Acceleration Error (by element)",
    inputs={
        "accel_mae": "ffast.accel_mae",
        "elements": I.reference_elements,
    },
    shape=(dims.N_elements,),
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
def accel_mae_per_element(accel_mae, elements):
    am = np.asarray(accel_mae)
    if am.ndim == 1:
        am = am[np.newaxis]
    el = np.asarray(elements)
    unique_z = np.unique(el)
    result = np.empty(len(unique_z), dtype=np.float64)
    for i, z in enumerate(unique_z):
        result[i] = np.mean(am[:, el == z])
    return result

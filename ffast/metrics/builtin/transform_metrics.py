"""Transform Metrics — reductions expressed as metric-on-metric dependencies.

ADR 0021: 2D Panels never compute. Every plot-side reduction (smoothing, KDE
density, downsampling) is a Metric whose input is another Metric, so the Metric
Graph computes it server-side and the client just draws the resulting array. A
transform's tunable settings are ``role: "compute"`` Parameter Schemas, so the
auto-generated Panel control routes a debounced ``SET_PARAMETER`` straight into
the existing cache-keyed recompute path (the window/etc. is folded into the
``CacheKey``).

This module is the Phase-0 proof set: the specific Transform Metrics the
"Basic Errors" tab needs, hand-registered. The Phase-5 compiler will emit
equivalent declarations automatically from a Panel's ``{metric, transform,
params}`` (using deterministic ids — note ``#`` is not a legal metric id, so the
compiler will use underscores as these hand-written ids do).
"""
from typing import Annotated

import numpy as np
from jaxtyping import Float

from ffast.metrics import metric, units
from ffast.metrics.signature import P, Ref

# Shared reduction bodies live in the Phase-5 compiler module so ffast/ stays
# self-contained; these literal Phase-0/4 metrics reuse them. The compiler can
# emit equivalent metrics automatically from {metric, transform, params}.
from ffast.metrics.transforms import _smooth, _mirror_kde  # noqa: E402

# id = METRIC_NAMESPACE + "." + function name (inputs/shape/params inferred from
# the signature; see ffast/metrics/signature.py).
METRIC_NAMESPACE = "ffast"

# Shared compute-parameter types (replace the old _WINDOW/_SHIFTED dicts).
# A keyword-only arg annotated with one of these becomes the matching Parameter
# Schema; the default value supplies the schema default.
Window = Annotated[int, P(
    min=1, max=10000, label="Smoothing",
    description="Sliding-average window (frames).",
)]

# Energy-offset toggle (Phase 4): when on, subtract the mean energy offset
# mean(E_pred − E_true) = ffast.energy_shift. A `role:"compute"` bool so it is
# part of computation identity / cache key; the Basic Errors tab drives it across
# all energy panels via one shared checkbox (see UI/panels.setSharedParam).
Shifted = Annotated[bool, P(
    label="Shift",
    description="Subtract the mean energy offset mean(E_pred − E_true).",
)]


# --- 0c: smoothed timelines (compute param: window) -------------------------
# Each takes a per-frame Metric as input (static Metric Graph edge → Model A)
# and exposes `window` as a compute parameter that the Panel surfaces as a
# slider. window=1 is identity, which the tests assert.

@metric(
    label="Forces RMSE (smoothed)",
    unit=units.force,
    tests=[
        {  # window=1 ⇒ identity of ffast.force_rmse ([0.7071] from its own test)
            "inputs": {
                "reference": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                "predicted": [[2.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                "offsets": [0, 2],
            },
            "parameters": {"window": 1},
            "expected": [0.7071067811865476],
            "atol": 1e-6,
        }
    ],
)
def force_rmse_smoothed(
    src: Ref["ffast.force_rmse"],
    *,
    window: Window = 1,
) -> Float[np.ndarray, "N_frames"]:
    return _smooth(src, window)


@metric(
    label="Forces MAE (smoothed)",
    unit=units.force,
    tests=[
        {  # window=1 ⇒ identity of ffast.force_mae_per_structure ([0.5])
            "inputs": {
                "reference": [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                "predicted": [[2.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                "offsets": [0, 2],
            },
            "parameters": {"window": 1},
            "expected": [0.5],
            "atol": 1e-6,
        }
    ],
)
def force_mae_per_structure_smoothed(
    src: Ref["ffast.force_mae_per_structure"],
    *,
    window: Window = 1,
) -> Float[np.ndarray, "N_frames"]:
    return _smooth(src, window)


@metric(
    label="Energy MAE (smoothed)",
    description="Smoothed |energy difference| per frame (optionally shifted, then smooth-then-abs; matches the legacy energy timeline).",
    unit=units.energy,
    tests=[
        {  # window=1, unshifted ⇒ |energy_difference| = |[0.5,0.5,0.5]|
            "inputs": {"reference": [1.0, 2.0, 3.0], "predicted": [1.5, 2.5, 3.5]},
            "parameters": {"window": 1},
            "expected": [0.5, 0.5, 0.5],
            "atol": 1e-10,
        },
        {  # shifted ⇒ diff−mean(diff) = 0
            "inputs": {"reference": [1.0, 2.0, 3.0], "predicted": [1.5, 2.5, 3.5]},
            "parameters": {"window": 1, "shifted": True},
            "expected": [0.0, 0.0, 0.0],
            "atol": 1e-10,
        },
    ],
)
def energy_error_smoothed(
    src: Ref["ffast.energy_difference"],
    shift: Ref["ffast.energy_shift"],
    *,
    window: Window = 1,
    shifted: Shifted = False,
) -> Float[np.ndarray, "N_frames"]:
    d = np.asarray(src) - (float(shift) if shifted else 0.0)
    return np.abs(_smooth(d, window))


# --- 0c: KDE densities (no params — parity with the legacy mirrorKDE) -------
# Emit the (curve_xy, grid) shape added in 0a. No bandwidth control today; it
# can later become a `role: "compute"` float param without touching consumers.

@metric(
    label="Forces RMSE distribution",
    unit=units.force,
)
def force_rmse_density(
    src: Ref["ffast.force_rmse"],
) -> Float[np.ndarray, "curve_xy grid"]:
    return _mirror_kde(src)


@metric(
    label="Forces MAE distribution",
    unit=units.force,
)
def force_mae_per_structure_density(
    src: Ref["ffast.force_mae_per_structure"],
) -> Float[np.ndarray, "curve_xy grid"]:
    return _mirror_kde(src)


@metric(
    label="Energy MAE distribution",
    unit=units.energy,
)
def energy_difference_density(
    src: Ref["ffast.energy_difference"],
    shift: Ref["ffast.energy_shift"],
    *,
    shifted: Shifted = False,
) -> Float[np.ndarray, "curve_xy grid"]:
    d = np.asarray(src) - (float(shift) if shifted else 0.0)
    return _mirror_kde(d)


# --- 0c/Phase 4: energy scatter (shifted prediction) + energy tables ---------
# These give the Basic Errors energy panels a single `shifted` knob instead of
# the legacy paired metrics (energy_mae vs energy_mae_shifted).

@metric(
    label="Predicted Energy (optionally shifted)",
    unit=units.energy,
    tests=[
        {
            "inputs": {"reference": [1.0, 2.0, 3.0], "predicted": [1.5, 2.5, 3.5]},
            "parameters": {"shifted": True},
            "expected": [1.0, 2.0, 3.0],  # pred − mean(pred−ref) = ref here
            "atol": 1e-10,
        }
    ],
)
def energy_prediction_shifted(
    pred: Ref["ffast.energy_prediction"],
    shift: Ref["ffast.energy_shift"],
    *,
    shifted: Shifted = False,
) -> Float[np.ndarray, "N_frames"]:
    return np.asarray(pred) - (float(shift) if shifted else 0.0)


@metric(
    label="Energy MAE",
    description="Mean |energy difference|, optionally offset-shifted (one param-driven metric for both Basic Errors table modes).",
    unit=units.energy,
    tests=[
        {
            "inputs": {"reference": [1.0, 2.0, 3.0], "predicted": [1.5, 2.5, 3.5]},
            "parameters": {},
            "expected": 0.5,
            "atol": 1e-10,
        },
        {
            "inputs": {"reference": [1.0, 2.0, 3.0], "predicted": [1.5, 2.5, 3.5]},
            "parameters": {"shifted": True},
            "expected": 0.0,
            "atol": 1e-10,
        },
    ],
)
def energy_mae_p(
    diff: Ref["ffast.energy_difference"],
    shift: Ref["ffast.energy_shift"],
    *,
    shifted: Shifted = False,
) -> float:
    d = np.asarray(diff) - (float(shift) if shifted else 0.0)
    return np.mean(np.abs(d))


@metric(
    label="Energy RMSE",
    unit=units.energy,
    tests=[
        {
            "inputs": {"reference": [1.0, 2.0, 3.0], "predicted": [1.5, 2.5, 3.5]},
            "parameters": {},
            "expected": 0.5,
            "atol": 1e-10,
        },
        {
            "inputs": {"reference": [1.0, 2.0, 3.0], "predicted": [1.5, 2.5, 3.5]},
            "parameters": {"shifted": True},
            "expected": 0.0,
            "atol": 1e-10,
        },
    ],
)
def energy_rmse_p(
    diff: Ref["ffast.energy_difference"],
    shift: Ref["ffast.energy_shift"],
    *,
    shifted: Shifted = False,
) -> float:
    d = np.asarray(diff) - (float(shift) if shifted else 0.0)
    return np.sqrt(np.mean(d ** 2))

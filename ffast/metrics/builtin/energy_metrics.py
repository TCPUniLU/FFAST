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


# ── Energy parity plot (ADR 0078, amended 2026-11-13) ─────────────────────────
# Every structure, true against predicted energy, as a density, and the worst
# ones as dots. Drawing all 1,773,605 energies of shard_03 as dots froze the
# page (MeluXina, 2026-11-13).

from ffast.metrics.builtin.force_metrics import (  # noqa: E402
    PARITY_BINS, PARITY_OUTLIERS, parity_density,
)
from ffast.metrics.builtin.transform_metrics import Shifted  # noqa: E402


def _shifted(predicted, shift, shifted):
    p = np.asarray(predicted, dtype=np.float64).ravel()
    return p - float(np.asarray(shift).ravel()[0]) if shifted else p


def worst_structures(true, pred, k=PARITY_OUTLIERS):
    """The ``k`` structures furthest from the diagonal, worst first, one row
    each: true, predicted, structure. A structure with a NaN on either side
    is left out."""
    t = np.asarray(true, dtype=np.float64).ravel()
    p = np.asarray(pred, dtype=np.float64).ravel()
    err = np.abs(p - t)
    idx = np.flatnonzero(np.isfinite(err))
    if idx.size > k:
        idx = idx[np.argpartition(-err[idx], k - 1)[:k]]
    idx = idx[np.argsort(-err[idx], kind="stable")]
    return np.column_stack([t[idx], p[idx], idx]).astype(float)


@metric(
    label="Energy parity density",
    description=(
        "Every structure, true against predicted energy, counted on a "
        f"{PARITY_BINS}x{PARITY_BINS} grid. Row 0 holds the bin centres."
    ),
    unit=units.energy,
)
def energy_parity_density(
    reference: Ref[I.reference_energies],
    predicted: Ref[I.prediction_energies],
    shift: Ref["ffast.energy_difference[dataset;mean]"],
    *,
    shifted: Shifted = False,
) -> Float[np.ndarray, "parity_rows grid"]:
    return parity_density(reference, _shifted(predicted, shift, shifted))


@metric(
    label="Worst energies",
    description=(
        f"The {PARITY_OUTLIERS:,} structures whose predicted energy is furthest "
        "from the true one: true, predicted, structure."
    ),
    unit=units.energy,
)
def energy_parity_outliers(
    reference: Ref[I.reference_energies],
    predicted: Ref[I.prediction_energies],
    shift: Ref["ffast.energy_difference[dataset;mean]"],
    *,
    shifted: Shifted = False,
) -> Float[np.ndarray, "N_outliers energy_outlier_fields"]:
    return worst_structures(reference, _shifted(predicted, shift, shifted))

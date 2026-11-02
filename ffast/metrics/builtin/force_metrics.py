import numpy as np
from jaxtyping import Float

from ffast.metrics import metric, units, inputs as I
from ffast.metrics.resources import map_chunks
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



# ── Force parity plot (ADR 0078) ──────────────────────────────────────────────
# Every force component, true against predicted, as a density, and the worst
# ones as dots. Sending every component took >4 GB for 1.77M structures
# (MeluXina, 2026-11-02).

PARITY_BINS = 300
PARITY_OUTLIERS = 50_000
_CHUNK = 2_000_000   # components per chunk: bounds each thread's temporary arrays


def _components(true, pred):
    return np.asarray(true, dtype=float).ravel(), np.asarray(pred, dtype=float).ravel()


def parity_density(true, pred, bins=PARITY_BINS, chunk=_CHUNK):
    """``(bins + 1, bins)``: row 0 the bin centres of both axes, rows 1.. the
    number of components in each bin, [predicted bin][true bin]. A component
    with a NaN on either side is left out. Chunks run on the shared chunk pool."""
    t, p = _components(true, pred)
    starts = range(0, t.size, chunk)

    def finite(start):
        a, b = t[start:start + chunk], p[start:start + chunk]
        keep = np.isfinite(a) & np.isfinite(b)
        return a[keep], b[keep]

    def extent(start):
        a, b = finite(start)
        return (min(a.min(), b.min()), max(a.max(), b.max())) if a.size else None

    ends = [e for e in map_chunks(extent, starts) if e is not None]
    lo = min((e[0] for e in ends), default=np.inf)
    hi = max((e[1] for e in ends), default=-np.inf)
    if not np.isfinite(lo):
        lo, hi = 0.0, 1.0
    if hi <= lo:
        hi = lo + 1.0
    width = (hi - lo) / bins

    def count(start):
        a, b = finite(start)
        col = np.minimum(((a - lo) / width).astype(np.int64), bins - 1)
        row = np.minimum(((b - lo) / width).astype(np.int64), bins - 1)
        return np.bincount(row * bins + col, minlength=bins * bins)

    counts = np.zeros(bins * bins, dtype=np.int64)
    for part in map_chunks(count, starts):
        counts += part
    grid = np.empty((bins + 1, bins))
    grid[0] = lo + (np.arange(bins) + 0.5) * width
    grid[1:] = counts.reshape(bins, bins)
    return grid


def worst_components(true, pred, offsets, k=PARITY_OUTLIERS, chunk=_CHUNK):
    """The ``k`` components furthest from the diagonal, worst first, one row
    each: true, predicted, structure, atom within it, component (0-2 for
    x-z). A component with a NaN on either side is left out. Chunks run on the
    shared chunk pool."""
    t, p = _components(true, pred)

    def worst(start):
        err = np.abs(p[start:start + chunk] - t[start:start + chunk])
        err[~np.isfinite(err)] = -1.0
        idx = np.argpartition(-err, k - 1)[:k] if err.size > k else np.arange(err.size)
        idx = idx[err[idx] >= 0]
        return idx + start, err[idx]

    parts = map_chunks(worst, range(0, t.size, chunk))
    picked = [i for i, _ in parts]
    errors = [e for _, e in parts]
    idx = np.concatenate(picked) if picked else np.zeros(0, dtype=np.int64)
    err = np.concatenate(errors) if errors else np.zeros(0)
    if idx.size > k:
        keep = np.argpartition(-err, k - 1)[:k]
        idx, err = idx[keep], err[keep]
    idx = idx[np.argsort(-err, kind="stable")]
    atom = idx // 3
    starts = framesOrOne(offsets, range(t.size // 3))
    frame = np.searchsorted(starts[1:], atom, side="right")
    return np.column_stack([t[idx], p[idx], frame, atom - starts[frame], idx % 3]).astype(float)


@metric(
    label="Force parity density",
    description=(
        "Every force component, true against predicted, counted on a "
        f"{PARITY_BINS}x{PARITY_BINS} grid. Row 0 holds the bin centres."
    ),
    unit=units.force,
    hints={"memory_mb": 4096},
)
def force_parity_density(
    reference: Ref[I.reference_forces],
    predicted: Ref[I.prediction_forces],
) -> Float[np.ndarray, "parity_rows grid"]:
    return parity_density(reference, predicted)


@metric(
    label="Worst force components",
    description=(
        f"The {PARITY_OUTLIERS:,} force components furthest from the diagonal: "
        "true, predicted, structure, atom, component."
    ),
    unit=units.force,
    hints={"memory_mb": 4096},
)
def force_parity_outliers(
    reference: Ref[I.reference_forces],
    predicted: Ref[I.prediction_forces],
    offsets=None,
) -> Float[np.ndarray, "N_outliers outlier_fields"]:
    return worst_components(reference, predicted, offsets)

import numpy as np
from jaxtyping import Float

from ffast.metrics import metric, units, inputs as I
from ffast.metrics.signature import Ref
from ffast.metrics.transforms import _abs_kde

# id = METRIC_NAMESPACE + "." + function name (inputs/shape/params inferred from
# the signature; see ffast/metrics/signature.py).
METRIC_NAMESPACE = "ffast"


def _per_structure_element_mae(force_difference, elements, offsets, z_target):
    """Per-structure MAE over atoms of element ``z_target`` (uniform or variable).

    Array form of the reducer that used to live in ``UI/panels.py`` (so it can run
    server-side inside a Metric). Uniform: ``fd`` is (n_struct, n_atoms, 3) and
    ``elements`` is (n_atoms,). Variable: ``fd`` is flat (n_tot, 3) with
    ``offsets`` and ``elements`` is the flat per-atom z."""
    fd = np.asarray(force_difference)
    el = np.asarray(elements)
    if offsets is not None:
        offs = np.asarray(offsets, dtype=np.intp)
        out = []
        for i in range(len(offs) - 1):
            seg = fd[offs[i]:offs[i + 1]]
            zseg = el[offs[i]:offs[i + 1]]
            sel = zseg == z_target
            if np.any(sel):
                out.append(np.mean(np.abs(seg[sel])))
        return np.asarray(out, dtype=np.float64)
    if fd.ndim == 2:
        fd = fd[np.newaxis]
    sel = el == z_target
    if not np.any(sel):
        return np.asarray([], dtype=np.float64)
    d = fd[:, sel, :].reshape(fd.shape[0], -1)
    return np.mean(np.abs(d), axis=1)


@metric(
    label="Force Error (per element)",
    unit=units.force,
    tests=[
        {
            "inputs": {
                "reference": [[[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]]],
                "predicted": [[[2.0, 0.0, 0.0], [2.0, 0.0, 0.0]]],
                "elements": [1, 6],
            },
            "parameters": {},
            "expected": [1.0 / 3.0, 0.0],
            "atol": 1e-6,
        }
    ],
)
def force_mae_per_element(
    force_difference: Ref["ffast.force_difference"],
    elements: Ref[I.reference_elements],
) -> Float[np.ndarray, "N_elements"]:
    fd = np.asarray(force_difference)
    if fd.ndim == 2:
        fd = fd[np.newaxis]
    el = np.asarray(elements)
    unique_z = np.unique(el)
    result = np.empty(len(unique_z), dtype=np.float64)
    for i, z in enumerate(unique_z):
        result[i] = np.mean(np.abs(fd[:, el == z, :]))
    return result


@metric(
    label="Force RMSE (by element)",
    unit=units.force,
    tests=[
        {
            "inputs": {
                "reference": [[[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]]],
                "predicted": [[[2.0, 0.0, 0.0], [2.0, 0.0, 0.0]]],
                "elements": [1, 6],
            },
            "parameters": {},
            "expected": [0.5773502691896258, 0.0],
            "atol": 1e-6,
        }
    ],
)
def force_rmse_per_element(
    force_difference: Ref["ffast.force_difference"],
    elements: Ref[I.reference_elements],
) -> Float[np.ndarray, "N_elements"]:
    fd = np.asarray(force_difference)
    if fd.ndim == 2:
        fd = fd[np.newaxis]
    el = np.asarray(elements)
    unique_z = np.unique(el)
    result = np.empty(len(unique_z), dtype=np.float64)
    for i, z in enumerate(unique_z):
        result[i] = np.sqrt(np.mean(fd[:, el == z, :] ** 2))
    return result


@metric(
    label="Force MAE distribution (per element)",
    description=(
        "Per-structure force MAE distribution, KDE'd per element — one "
        "(curve_xy, grid) curve per sorted-unique element, each over its OWN "
        "x-range (legacy per-element bounds; no shared-grid padding). The "
        "server-computed replacement for the old inline per-element reduction in "
        "the Atomic Errors density Panel. Row order matches sorted unique Z "
        "(== _element_order(dataset)), so the grouped-density Kind maps Z→row."
    ),
    unit=units.force,
)
def force_mae_per_structure_per_element_kde(
    force_difference: Ref["ffast.force_difference"],
    elements: Ref[I.reference_elements],
    offsets=None,
) -> Float[np.ndarray, "N_elements curve_xy grid"]:
    el = np.asarray(elements)
    unique_z = np.unique(el)
    # Each element KDE'd over its own [min·0.95, max·1.05] (legacy _abs_kde), so a
    # low-error element doesn't stretch a tail across the global range.
    curves = [
        _abs_kde(_per_structure_element_mae(force_difference, el, offsets, z))
        for z in unique_z
    ]
    return np.stack(curves)  # (N_elements, 2, grid)

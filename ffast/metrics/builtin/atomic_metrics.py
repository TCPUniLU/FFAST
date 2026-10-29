import numpy as np
from jaxtyping import Float

from ffast.metrics import metric, units, inputs as I
from ffast.metrics.segments import framesOrOne, segmentSums
from ffast.metrics.signature import Ref
from ffast.metrics.transforms import _abs_kde

# id = METRIC_NAMESPACE + "." + function name (inputs/shape/params inferred from
# the signature; see ffast/metrics/signature.py).
METRIC_NAMESPACE = "ffast"


def _per_structure_element_mae(force_difference, elements, offsets, z_target):
    """Per-structure MAE over atoms of element ``z_target``. ``fd`` is flat
    (atoms, 3), ``elements`` is every atom's z, ``offsets`` says where each
    structure starts (ADR 0058 step 6). Structures without the element are
    left out."""
    fd = np.asarray(force_difference)
    el = np.asarray(elements)
    offsets = framesOrOne(offsets, fd)
    sel = (el == z_target).astype(np.float64)
    abs_sum = segmentSums(np.abs(fd).sum(axis=1) * sel, offsets)
    count = segmentSums(sel, offsets)
    has = count > 0
    return abs_sum[has] / (count[has] * fd.shape[1])


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

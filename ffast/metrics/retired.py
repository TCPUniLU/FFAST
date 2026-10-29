"""The fixed force, acceleration and energy error metrics ADR 0065 deleted.

Each is now one setting of a base metric's keep boxes. A session, formula or
tab that names an old id is refused with a message naming that setting
(section 7); there is no translation on load.
"""
from __future__ import annotations

from ffast.metrics.boxes import COMBINE_LABELS, KEEP_ORDER, STATISTIC_LABELS, parse_box_id

_FD = "ffast.force_difference"
_AD = "ffast.accel_difference"
_ED = "ffast.energy_difference"
_NR = "ffast.force_net_residual"

# old id → the box id that gives the same numbers
REPLACEMENTS: dict[str, str] = {
    "ffast.force_mae": f"{_FD}[atom+structure;length]",
    "ffast.force_rmse": f"{_FD}[structure;length;rmse]",
    "ffast.force_mae_per_structure": f"{_FD}[structure;length;mae]",
    "ffast.force_mae_global": f"{_FD}[dataset;length;mae]",
    "ffast.force_rmse_global": f"{_FD}[dataset;length;rmse]",
    "ffast.force_component_mae": f"{_FD}[dataset;component;mae]",
    "ffast.force_component_rmse": f"{_FD}[dataset;component;rmse]",
    "ffast.force_component_mae_per_structure": f"{_FD}[structure;component;mae]",
    "ffast.force_component_rmse_per_structure": f"{_FD}[structure;component;rmse]",
    "ffast.force_mae_per_element": f"{_FD}[element;component;mae]",
    "ffast.force_rmse_per_element": f"{_FD}[element;component;rmse]",
    "ffast.force_net_mae_per_structure": f"{_NR}[structure;component;mae]",
    "ffast.force_net_mae": f"{_NR}[dataset;component;mae]",
    "ffast.force_net_rmse": f"{_NR}[dataset;component;rmse]",
    "ffast.accel_mae": f"{_AD}[atom+structure;length]",
    "ffast.accel_rmse": f"{_AD}[structure;length;rmse]",
    "ffast.accel_mae_global": f"{_AD}[dataset;length;mae]",
    "ffast.accel_rmse_global": f"{_AD}[dataset;length;rmse]",
    "ffast.accel_component_mae": f"{_AD}[dataset;component;mae]",
    "ffast.accel_component_rmse": f"{_AD}[dataset;component;rmse]",
    "ffast.accel_mae_per_atom": f"{_AD}[atom;length;mae]",
    "ffast.accel_mae_per_element": f"{_AD}[element;length;mae]",
    "ffast.energy_shift": f"{_ED}[dataset;mean]",
    "ffast.energy_mae": f"{_ED}[dataset;mae]",
    "ffast.energy_rmse": f"{_ED}[dataset;rmse]",
    "ffast.energy_mae_shifted": "ffast.energy_difference_shifted[dataset;mae]",
    "ffast.energy_rmse_shifted": "ffast.energy_difference_shifted[dataset;rmse]",
    # The Basic Errors toggles (ADR 0065 decided point 2).
    "ffast.energy_mae_p": f"{_ED}[dataset;mae]",
    "ffast.energy_rmse_p": f"{_ED}[dataset;rmse]",
}
# Old ids whose replacement is not a box setting.
_OTHER = {
    "ffast.energy_prediction_shifted":
        'use Predicted Energy (ffast.energy_prediction) with the "shift" transform',
}

BASE_LABELS = {
    _FD: "Force Difference",
    _AD: "Acceleration Difference",
    _ED: "Energy Difference",
    "ffast.energy_difference_shifted": "Energy Difference (shifted)",
    _NR: "Net Force Residual",
}


def describe(box: str) -> str:
    """A box id in the words of the boxes: "Force Difference with structure ✓,
    Length, RMSE"."""
    base, setting = parse_box_id(box)
    ticks = ", ".join(f"{'x/y/z' if k == 'xyz' else k} ✓"
                      for k in KEEP_ORDER if k in setting.keep) or "no box ticked"
    words = [ticks]
    if setting.combine:
        words.append(COMBINE_LABELS[setting.combine].capitalize())
    if setting.statistic:
        words.append(STATISTIC_LABELS[setting.statistic])
    return f"{BASE_LABELS.get(base, base)} with {', '.join(words)} ({box})"


def retired_message(metric_id: str) -> str | None:
    """Why ``metric_id`` is refused and what to use instead, or None when it
    is not a deleted id."""
    if metric_id in REPLACEMENTS:
        return f"{metric_id} no longer exists; use {describe(REPLACEMENTS[metric_id])}"
    if metric_id in _OTHER:
        return f"{metric_id} no longer exists; {_OTHER[metric_id]}"
    return None

"""Formulas that compute exactly what some built-in Python metrics compute.

The Metrics pane offers Duplicate for every metric (ADR 0057 amendment
2026-10-23). A formula works on one value at a time, so only the built-ins that
never average, total or smooth have a true formula; they are listed here, and a
test runs each formula against its Python metric. Duplicating any other
built-in starts a formula that takes that metric as its input.

A metric with parameters is listed with the formula for its default ones.
"""
from __future__ import annotations

_F = {"pred_F": "prediction.forces", "ref_F": "reference.forces"}
_E = {"pred_E": "prediction.energies", "ref_E": "reference.energies"}

# metric id → (expr, vars)
EQUIVALENTS: dict[str, tuple[str, dict[str, str]]] = {
    "ffast.force_difference": ("pred_F - ref_F", _F),
    "ffast.force_mae": ("norm(pred_F - ref_F)", _F),            # norm = "l2"
    # |F / m| = |F| / m, so the mass need not divide each part
    "ffast.accel_mae": ("norm(pred_F - ref_F) / m", {**_F, "m": "reference.masses"}),   # norm = "l2"
    "ffast.force_prediction": ("pred_F", {"pred_F": _F["pred_F"]}),
    "ffast.force_reference": ("ref_F", {"ref_F": _F["ref_F"]}),
    "ffast.energy_difference": ("pred_E - ref_E", _E),
    "ffast.energy_prediction": ("pred_E", {"pred_E": _E["pred_E"]}),
    "ffast.energy_reference": ("ref_E", {"ref_E": _E["ref_E"]}),
}


def equivalent_of(metric_id: str) -> tuple[str, dict[str, str]] | None:
    """The exact formula of a built-in metric, or None when it has none."""
    entry = EQUIVALENTS.get(metric_id)
    return (entry[0], dict(entry[1])) if entry else None

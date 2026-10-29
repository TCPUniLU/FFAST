"""Formulas that compute exactly what some built-in Python metrics compute.

The Metrics pane offers Duplicate for every metric (ADR 0057 amendment
2026-10-23), and the keep boxes read a built-in through its formula twin
(ADR 0065 section 3). The base metrics and pass-throughs are listed here, and
a test runs each formula against its Python metric. Duplicating any other
built-in (densities, smoothing, geometry, the radius of gyration) starts a
formula that takes that metric as its input.
"""
from __future__ import annotations

_F = {"pred_F": "prediction.forces", "ref_F": "reference.forces"}
_E = {"pred_E": "prediction.energies", "ref_E": "reference.energies"}
_A = {**_F, "m": "reference.masses"}

# metric id → (expr, vars)
EQUIVALENTS: dict[str, tuple[str, dict[str, str]]] = {
    # The five base metrics (ADR 0065 section 6).
    "ffast.force_difference": ("pred_F - ref_F", _F),
    # each atom's mass divides its x, y and z (ADR 0065 section 8)
    "ffast.accel_difference": ("(pred_F - ref_F) / m", _A),
    "ffast.energy_difference": ("pred_E - ref_E", _E),
    "ffast.energy_difference_shifted": ("pred_E - ref_E - mean(pred_E - ref_E, by=all)", _E),
    "ffast.force_net_residual": ("sum(pred_F - ref_F, by=structure)", _F),
    # Pass-throughs.
    "ffast.force_prediction": ("pred_F", {"pred_F": _F["pred_F"]}),
    "ffast.force_reference": ("ref_F", {"ref_F": _F["ref_F"]}),
    "ffast.energy_prediction": ("pred_E", {"pred_E": _E["pred_E"]}),
    "ffast.energy_reference": ("ref_E", {"ref_E": _E["ref_E"]}),
}


def equivalent_of(metric_id: str) -> tuple[str, dict[str, str]] | None:
    """The exact formula of a built-in metric, or None when it has none."""
    entry = EQUIVALENTS.get(metric_id)
    return (entry[0], dict(entry[1])) if entry else None

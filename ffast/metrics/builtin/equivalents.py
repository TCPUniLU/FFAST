"""Formulas that compute exactly what some built-in Python metrics compute.

The Metrics pane offers Duplicate for every metric (ADR 0057 amendment
2026-10-23). Since formulas can average, total and take extremes by
structure, element or all (ADR 0064), most built-ins have a true formula; they
are listed here, and a test runs each formula against its Python metric.
Duplicating any other built-in (densities, smoothing, geometry, the radius of
gyration) starts a formula that takes that metric as its input.

A metric with parameters is listed with the formula for its default ones.
"""
from __future__ import annotations

_F = {"pred_F": "prediction.forces", "ref_F": "reference.forces"}
_E = {"pred_E": "prediction.energies", "ref_E": "reference.energies"}
_A = {**_F, "m": "reference.masses"}

# The per-atom error each family starts from.
_FORCE = "norm(pred_F - ref_F)"
_ACCEL = "norm(pred_F - ref_F) / m"
_NET = "sum(pred_F - ref_F, by=structure)"
_SHIFTED = "pred_E - ref_E - mean(pred_E - ref_E, by=all)"

# metric id → (expr, vars)
EQUIVALENTS: dict[str, tuple[str, dict[str, str]]] = {
    "ffast.force_difference": ("pred_F - ref_F", _F),
    "ffast.force_mae": (_FORCE, _F),                            # norm = "l2"
    # |F / m| = |F| / m, so the mass need not divide each part
    "ffast.accel_mae": (_ACCEL, _A),   # norm = "l2"
    "ffast.force_prediction": ("pred_F", {"pred_F": _F["pred_F"]}),
    "ffast.force_reference": ("ref_F", {"ref_F": _F["ref_F"]}),
    "ffast.energy_difference": ("pred_E - ref_E", _E),
    "ffast.energy_prediction": ("pred_E", {"pred_E": _E["pred_E"]}),
    "ffast.energy_reference": ("ref_E", {"ref_E": _E["ref_E"]}),

    # Reductions (ADR 0064).
    "ffast.force_mae_per_structure": (f"mean({_FORCE}, by=structure)", _F),
    "ffast.force_rmse": (f"rms({_FORCE}, by=structure)", _F),
    "ffast.force_mae_global": (f"mean({_FORCE}, by=all)", _F),
    "ffast.force_rmse_global": (f"rms({_FORCE}, by=all)", _F),
    "ffast.force_component_mae": ("mean(mean_parts(abs(pred_F - ref_F)), by=all)", _F),
    "ffast.force_component_rmse": ("sqrt(mean(mean_parts((pred_F - ref_F) ** 2), by=all))", _F),
    "ffast.force_component_mae_per_structure": ("mean(mean_parts(abs(pred_F - ref_F)), by=structure)", _F),
    "ffast.force_component_rmse_per_structure": ("sqrt(mean(mean_parts((pred_F - ref_F) ** 2), by=structure))", _F),
    "ffast.force_mae_per_element": ("mean(mean_parts(abs(pred_F - ref_F)), by=element)", _F),
    "ffast.force_rmse_per_element": ("sqrt(mean(mean_parts((pred_F - ref_F) ** 2), by=element))", _F),
    "ffast.force_net_residual": (_NET, _F),
    "ffast.force_net_mae_per_structure": (f"mean_parts(abs({_NET}))", _F),
    "ffast.force_net_mae": (f"mean(mean_parts(abs({_NET})), by=all)", _F),
    "ffast.force_net_rmse": (f"sqrt(mean(mean_parts({_NET} ** 2), by=all))", _F),
    "ffast.accel_rmse": (f"rms({_ACCEL}, by=structure)", _A),
    "ffast.accel_mae_global": (f"mean({_ACCEL}, by=all)", _A),
    "ffast.accel_rmse_global": (f"rms({_ACCEL}, by=all)", _A),
    "ffast.accel_mae_per_element": (f"mean({_ACCEL}, by=element)", _A),
    "ffast.accel_component_mae": ("mean(mean_parts(abs(pred_F - ref_F)) / m, by=all)", _A),
    "ffast.accel_component_rmse": ("sqrt(mean(mean_parts((pred_F - ref_F) ** 2) / m ** 2, by=all))", _A),
    "ffast.energy_shift": ("mean(pred_E - ref_E, by=all)", _E),
    "ffast.energy_mae": ("mean(abs(pred_E - ref_E), by=all)", _E),
    "ffast.energy_rmse": ("rms(pred_E - ref_E, by=all)", _E),
    "ffast.energy_mae_shifted": (f"mean(abs({_SHIFTED}), by=all)", _E),
    "ffast.energy_rmse_shifted": (f"rms({_SHIFTED}, by=all)", _E),
    "ffast.energy_mae_p": ("mean(abs(pred_E - ref_E), by=all)", _E),        # shifted = False
    "ffast.energy_rmse_p": ("rms(pred_E - ref_E, by=all)", _E),             # shifted = False
    "ffast.energy_prediction_shifted": ("pred_E", {"pred_E": _E["pred_E"]}),  # shifted = False
}


def equivalent_of(metric_id: str) -> tuple[str, dict[str, str]] | None:
    """The exact formula of a built-in metric, or None when it has none."""
    entry = EQUIVALENTS.get(metric_id)
    return (entry[0], dict(entry[1])) if entry else None

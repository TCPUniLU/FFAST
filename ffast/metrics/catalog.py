"""Serializable metric catalog for renderer clients (ADR 0010 / 0016).

The server owns the metric registry; renderer clients must not build UI from
their own local registry (they would miss config-loaded external metrics and
could diverge from the server). The server sends this catalog over the protocol
and the client builds its metric controls from it.
"""
from __future__ import annotations

from ffast.metrics.dims import shape_to_str


def _param_to_dict(p) -> dict:
    """Flatten a ParameterSchema to plain JSON-able fields for transport."""
    return {
        "type": getattr(p, "type", None),
        "default": getattr(p, "default", None),
        "label": getattr(p, "label", "") or "",
        "choices": list(getattr(p, "choices", []) or []),
        "min": getattr(p, "min", None),
        "max": getattr(p, "max", None),
    }


def build_metric_catalog(registry) -> list[dict]:
    """Catalog of every registered metric as plain dicts.

    Each entry: ``{id, label, shape, unit, parameters}`` where ``shape`` is the
    string form (e.g. ``"N_atoms"``, ``"N_elements"``, ``"scalar"``) and
    ``parameters`` maps name → flattened ParameterSchema. Clients filter by
    ``shape`` for their use (atom coloring keeps ``N_atoms`` / ``N_elements``).
    """
    catalog = []
    for mid in sorted(registry.list_metrics()):
        schema, _ = registry.get(mid)
        catalog.append({
            "id": mid,
            "label": schema.label or mid,
            "shape": shape_to_str(schema.shape),
            "unit": schema.unit,
            "parameters": {k: _param_to_dict(p) for k, p in schema.parameters.items()},
        })
    return catalog

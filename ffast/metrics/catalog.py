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
        # role lets a renderer show only "compute" params as retune controls
        # (ADR 0021 / ADR 0045 Phase 3 panel controls); "present" params are
        # applied differently and must not appear as recompute inputs.
        "role": getattr(p, "role", None),
    }


def python_source(fn) -> str | None:
    """The function behind a Python metric, from its ``def`` line (the
    decorator, with its tests, is left out); None when it has no source file."""
    import inspect
    try:
        lines = inspect.getsource(inspect.unwrap(fn)).splitlines()
    except (OSError, TypeError):
        return None
    start = next((i for i, line in enumerate(lines) if line.lstrip().startswith("def ")), 0)
    return inspect.cleandoc("\n".join(lines[start:])) if lines else None


def build_metric_catalog(registry, user_metrics=None) -> list[dict]:
    """Catalog of every registered metric as plain dicts.

    Each entry: ``{id, label, shape, unit, parameters}`` where ``shape`` is the
    string form (e.g. ``"N_atoms"``, ``"N_elements"``, ``"scalar"``) and
    ``parameters`` maps name → flattened ParameterSchema. Clients filter by
    ``shape`` for their use (atom coloring keeps ``N_atoms`` / ``N_elements``).

    With ``user_metrics`` (ADR 0057) each entry also says where the metric
    comes from and its formula if it is an Expression Metric. Who uses it is a
    message of its own (``METRIC_USERS``, ADR 0062).

    A Python metric also carries its ``source``, and ``equivalent_expr`` /
    ``equivalent_vars`` when a formula computes exactly the same
    (ffast/metrics/builtin/equivalents.py): what Duplicate starts from.
    """
    from ffast.metrics.builtin.equivalents import equivalent_of
    from ffast.metrics.expr import _ExprFn
    catalog = []
    for mid in sorted(registry.list_metrics()):
        schema, fn = registry.get(mid)
        is_formula = isinstance(fn, _ExprFn)
        equivalent = None if is_formula else equivalent_of(mid)
        catalog.append({
            "source": None if is_formula else python_source(fn),
            "equivalent_expr": equivalent[0] if equivalent else None,
            "equivalent_vars": equivalent[1] if equivalent else None,
            "origin": user_metrics.origin(mid) if user_metrics is not None else "built-in",
            "expr": fn.expr if is_formula else None,
            "vars": fn.user_vars if is_formula else None,
            "id": mid,
            "label": schema.label or mid,
            "shape": shape_to_str(schema.shape),
            "unit": schema.unit,
            "parameters": {k: _param_to_dict(p) for k, p in schema.parameters.items()},
        })
    return catalog

"""Dataset Field passthrough-metric compiler (ADR 0023).

A **Dataset Field** is a numeric key carried by a loaded file beyond the core
arrays — a per-frame ``atoms.info`` scalar (Frame Field) or a per-atom
``atoms.arrays`` scalar (Atom Field). This module turns a declarative
``[[metrics.fields]]`` entry into a registered **passthrough Metric** so the
field can be plotted or used for atom coloring with no Python:

    [[metrics.fields]]
    id    = "mylab.charges"
    ref   = "reference.atoms.charges"   # atoms.<key> → per-atom, info.<key> → per-frame
    label = "Partial charge"
    unit  = "dimensionless"

Shape is inferred from the field kind. The metric body is a single module-level
function (``field_passthrough``) shared by *every* compiled field metric — the
key rides in the input ref, not in a closure — so the whole registry stays
picklable for the metric worker pool (same constraint as transforms.py).
"""
from __future__ import annotations

import numpy as np

from ffast.metrics import dims, units
from ffast.metrics.inputs import parse_field_ref
from ffast.metrics.registry import default_registry


def field_passthrough(value):
    """Expose a resolved Dataset Field unchanged.

    Module-level (not a closure) so a compiled field metric — and therefore the
    whole registry — pickles to the metric worker pool. The actual file key is
    carried by the metric's input ref, so one body serves all field metrics.
    """
    return np.asarray(value)


# atoms.<key> → per-atom scalar; info.<key> → per-frame scalar.
_KIND_SHAPE = {"atoms": (dims.N_atoms,), "info": (dims.N_frames,)}


def compile_field_metric(id, ref, *, label="", unit="", registry=None) -> str:
    """Register a passthrough Metric for one Dataset Field ref. Idempotent."""
    registry = registry if registry is not None else default_registry
    parsed = parse_field_ref(ref)
    if parsed is None:
        raise ValueError(
            f"compile_field_metric: '{ref}' is not a Dataset Field ref "
            f"({{reference,prediction}}.{{info,atoms}}.<key>)"
        )
    _side, kind, key = parsed
    if registry.has(id):
        return id
    decorator = registry.metric(
        id=id,
        label=label or key,
        inputs={"value": ref},
        shape=_KIND_SHAPE[kind],
        unit=unit or units.dimensionless,
    )
    decorator(field_passthrough)
    return id


def declared_field_keys(side, registry=None) -> dict:
    """All Dataset Field keys any registered metric references for ``side``.

    Returns ``{"info": set(keys), "atoms": set(keys)}``. The prediction load path
    uses this to eager-extract only the declared set (ADR 0023, Q8). Call after
    metrics are registered (config compiled), so the set is complete.
    """
    registry = registry if registry is not None else default_registry
    out = {"info": set(), "atoms": set()}
    for mid in registry.list_metrics():
        schema, _ = registry.get(mid)
        for ref in schema.inputs.values():
            parsed = parse_field_ref(ref)
            if parsed is not None and parsed[0] == side:
                out[parsed[1]].add(parsed[2])
    return out


def compile_field_metrics(configs, registry=None) -> list[str]:
    """Compile a list of FieldMetricConfig (or dict-likes) → registered ids."""
    out = []
    for c in configs:
        out.append(
            compile_field_metric(
                c.id, c.ref, label=getattr(c, "label", ""),
                unit=getattr(c, "unit", ""), registry=registry,
            )
        )
    return out

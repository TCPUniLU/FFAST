"""The inputs a formula can use for one dataset and prediction (ADR 0057 rule 10).

The Metrics pane shows these as a searchable list and inserts the one clicked.
Four groups: **Reference** and **Prediction** (the raw arrays), **File keys**
(extra numeric keys of the dataset file and of the prediction file; Dataset
Fields, ADR 0023) and **Metrics** (any registered metric, so formulas can build
on each other). An entry that exists on only one side — a prediction has no
positions, a key sits in the reference file only — says which side in ``only``.

Pure: reads the dataset, the prediction fields and the registry, touches no
metric data.
"""
from __future__ import annotations

import re

from ffast.metrics.dims import shape_to_str

# (ref suffix, short name part, label, shape). Only the continuous raw inputs an
# Expression Metric accepts (ffast.metrics.expr._RAW_REF_SHAPE).
_RAW = (
    ("energies", "E", "Energy", "N_frames"),
    ("forces", "F", "Forces", "(N_atoms, xyz)"),
    ("positions", "R", "Positions", "(N_atoms, xyz)"),
    ("masses", "m", "Masses", "N_atoms"),
)
_REFERENCE_ONLY = {"positions", "masses"}


def _ident(text: str) -> str:
    """A key or id part as a formula variable name."""
    out = re.sub(r"\W+", "_", text).strip("_") or "x"
    return out if not out[0].isdigit() else f"_{out}"


def _entry(ref, group, name, label, shape, only=None, origin=None) -> dict:
    return {"ref": ref, "group": group, "name": name, "label": label,
            "shape": shape, "only": only, "origin": origin}


def _file_keys(dataset) -> tuple[list[str], list[str]]:
    try:
        frame, atoms = dataset.availableFieldKeys()
    except Exception:
        return [], []
    return list(frame), list(atoms)


def _prediction_keys(prediction_fields, model, dataset) -> tuple[list[str], list[str]]:
    """Keys of the prediction file's fields, found against the dataset or the
    parent it was cut from (sub-datasets store theirs on the parent)."""
    if not prediction_fields or model is None:
        return [], []
    ds = dataset
    while ds is not None:
        store = prediction_fields.get((getattr(model, "fingerprint", None),
                                       getattr(ds, "fingerprint", None)))
        if store is not None:
            return sorted(store.get("info", {})), sorted(store.get("atoms", {}))
        ds = getattr(ds, "parent", None)
    return [], []


def build_input_catalog(registry, dataset=None, model=None, prediction_fields=None,
                        user_metrics=None) -> list[dict]:
    """The inputs for ``dataset`` (and ``model``'s prediction), as plain dicts:
    ``{ref, group, name, label, shape, only, origin}``. ``name`` is a short
    variable name to insert; the editor makes it unique."""
    out: list[dict] = []

    for suffix, short, label, shape in _RAW:
        out.append(_entry(f"reference.{suffix}", "Reference", f"ref_{short}", label, shape,
                          only="reference" if suffix in _REFERENCE_ONLY else None))
    if model is not None:
        for suffix, short, label, shape in _RAW:
            if suffix in _REFERENCE_ONLY:
                continue
            out.append(_entry(f"prediction.{suffix}", "Prediction", f"pred_{short}", label, shape))

    ref_frame, ref_atoms = _file_keys(dataset) if dataset is not None else ([], [])
    pred_frame, pred_atoms = _prediction_keys(prediction_fields, model, dataset)
    for kind, shape, ref_keys, pred_keys in (
        ("info", "N_frames", ref_frame, pred_frame),
        ("atoms", "N_atoms", ref_atoms, pred_atoms),
    ):
        for key in sorted(set(ref_keys) | set(pred_keys)):
            on_ref, on_pred = key in ref_keys, key in pred_keys
            only = None if on_ref and on_pred else ("reference" if on_ref else "prediction")
            if on_ref:
                out.append(_entry(f"reference.{kind}.{key}", "File keys",
                                  f"ref_{_ident(key)}", key, shape, only))
            if on_pred:
                out.append(_entry(f"prediction.{kind}.{key}", "File keys",
                                  f"pred_{_ident(key)}", key, shape, only))

    for mid in sorted(registry.list_metrics()):
        if "__" in mid:     # a transform made for a plot, not a metric to build on
            continue
        schema, _fn = registry.get(mid)
        out.append(_entry(mid, "Metrics", _ident(mid.rsplit(".", 1)[-1]),
                          schema.label or mid, shape_to_str(schema.shape),
                          origin=user_metrics.origin(mid) if user_metrics is not None else None))
    return out

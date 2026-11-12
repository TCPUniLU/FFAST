"""Prediction Dataset Fields storage and lifecycle (ADR 0023).

A prediction's extra fields (``prediction.{info,atoms}.<key>``) are read once,
at prediction load, because the prediction's loader is discarded afterwards.
They live here keyed by ``(model_fp, dataset_fp)``::

    {(model_fp, dataset_fp): {"info": {key: arr}, "atoms": {key: arr},
                              "missing": {key: mask}}}

This module is the one owner of when an entry appears and disappears:

- :meth:`PredictionFieldStore.replace` — a load always replaces what an
  earlier load stored under the same key. A load that brings no fields removes
  the entry, so a field the new source lacks resolves to nothing instead of the
  old source's values.
- :meth:`PredictionFieldStore.drop_object` — deleting a dataset or a model
  releases every entry that names it.

The store is still a ``dict``, so readers that look entries up directly (the
input resolver, colour values, the server session snapshot) keep working.
"""


class PredictionFieldStore(dict):
    """Prediction fields keyed by ``(model_fp, dataset_fp)``."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # (model_fp, dataset_fp) -> {ref: hash of its values}: names a field's
        # content in the metric cache (a prediction's fingerprint covers only
        # its energies and forces). Goes whenever the fields are replaced.
        self._digests: dict = {}

    def replace(self, model_fp, dataset_fp, fields):
        """Store ``fields`` for one prediction, dropping whatever was there.

        ``fields`` with no ``info`` and no ``atoms`` values (or ``None``)
        removes the entry."""
        key = (model_fp, dataset_fp)
        self._digests.pop(key, None)
        if fields and (fields.get("info") or fields.get("atoms")):
            self[key] = fields
        else:
            self.pop(key, None)

    def drop_object(self, fingerprint):
        """Release every entry whose model or dataset is ``fingerprint``."""
        for key in [k for k in self if fingerprint in k]:
            del self[key]
        for key in [k for k in self._digests if fingerprint in k]:
            del self._digests[key]

    def digests(self, model_fp, dataset_fp) -> dict:
        """The ``{ref: hash}`` held for one prediction's fields, to fill in."""
        return self._digests.setdefault((model_fp, dataset_fp), {})

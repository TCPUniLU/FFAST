"""InputResolver — resolves a metric's symbolic input refs to concrete arrays.

ADR 0019 D2. Resolution lives in the Environment (server-side) because in remote
mode the prediction/reference arrays exist only on the cluster; transferring them
to the client before metric execution would defeat the purpose of remote compute.

Each ``ffast.metrics.inputs`` ref maps to an
``(env, model, dataset) -> np.ndarray | None`` sourcing rule:

- ``reference.*`` come from the dataset (``getEnergies``/``getForces``/...).
- ``prediction.*`` come from the model-prediction DataTypes via
  ``env.getData("energy"/"forces", model, dataset)``.  When the prediction has
  not been generated yet, ``resolve`` returns ``None`` and the generation queue
  defers the metric until the prediction exists (see
  ``Environment.handleGenerationQueue``).

Variable datasets return per-molecule lists; reference and prediction are
flattened identically so a metric's elementwise math (``predicted - reference``)
lines up, and ``offsets`` is supplied so per-frame reductions still work.
"""
from __future__ import annotations

import logging

import numpy as np

from ffast.metrics.execution import InputSource
from ffast.metrics.inputs import parse_field_ref

logger = logging.getLogger("FFAST")

# prediction ref -> (prediction DataType key, DataEntity field)
_PREDICTION_REFS = {
    "prediction.energies": ("energy", "energy"),
    "prediction.forces": ("forces", "forces"),
}


def collect_prediction_refs(registry, metric_id, _acc=None, _seen=None):
    """All ``prediction.*`` refs a metric needs, following metric deps transitively.

    A metric whose direct inputs are only other metrics (e.g. energy_mae →
    energy_difference) still needs a model prediction at the leaves, so callers
    must look through the whole dep tree, not just the top-level inputs.
    """
    acc = set() if _acc is None else _acc
    seen = set() if _seen is None else _seen
    if metric_id in seen:
        return acc
    seen.add(metric_id)
    schema, _ = registry.get(metric_id)
    for ref in schema.inputs.values():
        if registry.has(ref):
            collect_prediction_refs(registry, ref, acc, seen)
        elif ref in _PREDICTION_REFS:
            acc.add(ref)
        else:
            # Prediction Dataset Fields (ADR 0023) also make a metric
            # prediction-dependent, even though they are not generatable.
            parsed = parse_field_ref(ref)
            if parsed is not None and parsed[0] == "prediction":
                acc.add(ref)
    return acc


def metric_needs_prediction(metric_id, registry=None):
    """True if a metric (transitively) depends on any model prediction input."""
    if registry is None:
        from ffast.metrics.registry import default_registry as registry
    return bool(collect_prediction_refs(registry, metric_id))


def _flatten(value):
    """Collapse a variable dataset's per-molecule list into one array.

    Reference and prediction values are flattened the same way, so the flat
    arrays stay elementwise-aligned for metrics like ``force_difference``.
    """
    if value is None:
        return None
    if isinstance(value, list):
        if len(value) == 0:
            return None
        return np.concatenate([np.asarray(v) for v in value], axis=0)
    return np.asarray(value)


def _flatPerAtom(value, axes):
    """Per-atom values of all frames as one ``(atoms, ...)`` array (ADR 0058
    step 6). Predictions and atom fields are kept in each kind's shape: a list
    of per-frame arrays, or ``(frames, atoms, ...)``. ``axes`` is how many axes
    one atom's value has (1 for a force, 0 for a scalar field); an array with
    two more still has its frame axis."""
    if isinstance(value, list):
        return _flatten(value)
    if value is None:
        return None
    value = np.asarray(value)
    if value.ndim == axes + 2:
        return value.reshape(-1, *value.shape[2:])
    return value


class _ResolverSource(InputSource):
    """Metric Execution Context input source backed by an ``InputResolver``.

    Resolves each raw input by its symbolic ref against the env/DataService for a
    given ``(model, dataset)``.  Resolution is always "found" — an unavailable
    input surfaces as ``None`` (``InputResolver.resolve`` never raises), which the
    plan builder then treats as an optional/missing input.
    """

    def __init__(self, resolver, model, dataset):
        self._resolver = resolver
        self._model = model
        self._dataset = dataset

    def get(self, metric_id, local_key, ref):
        return True, self._resolver.resolve(ref, model=self._model, dataset=self._dataset)


class InputResolver:
    def __init__(self, env):
        self.env = env

    def resolve(self, ref, model=None, dataset=None):
        """Map one symbolic ref to a full-dataset array, or ``None`` if unavailable."""
        try:
            if ref == "reference.energies":
                return dataset.getAllEnergies()
            # Per-atom inputs are flat on every kind: one row per atom, frame
            # after frame, with offsets saying where each frame starts.
            if ref == "reference.forces":
                return dataset.getAllForces()
            if ref == "reference.positions":
                return dataset.getAllCoordinates()
            if ref == "reference.elements":
                return dataset.getAllElements()
            if ref == "reference.masses":
                return self._masses(dataset)
            if ref == "reference.periodic":
                return self._periodic(dataset)
            if ref in _PREDICTION_REFS:
                dt_key, field = _PREDICTION_REFS[ref]
                entity = self.env.getData(dt_key, model=model, dataset=dataset)
                if entity is None:
                    return None
                if field == "forces":
                    return _flatPerAtom(entity.get(field), axes=1)
                return _flatten(entity.get(field))
            if ref == "offsets":
                return self._offsets(dataset)
            if ref == "n_atoms":
                return self._n_atoms(dataset)
            # Dataset Field refs (ADR 0023): {reference,prediction}.{info,atoms}.<key>
            parsed = parse_field_ref(ref)
            if parsed is not None:
                side, kind, key = parsed
                if side == "reference":
                    if kind == "atoms":
                        return _flatPerAtom(dataset.getAtomField(key), axes=0)
                    return _flatten(dataset.getFrameField(key))
                return self._prediction_field(model, dataset, kind, key)
            # stress / selection not sourced yet — treated as optional/None
            if ref in ("reference.stress", "prediction.stress", "selection.indices"):
                return None
        except Exception as exc:
            logger.warning("InputResolver: failed to resolve %r: %s", ref, exc)
            return None
        logger.error("InputResolver: unknown input ref %r", ref)
        return None

    def _prediction_field(self, model, dataset, kind, key):
        """Resolve a ``prediction.{info,atoms}.<key>`` ref.

        Prediction Dataset Fields are eagerly extracted at prediction-load time
        into ``DataService.predictionFields[(model_fp, dataset_fp)]`` (the
        prediction's ASE source is otherwise discarded — ADR 0023). Real-model
        predictions carry no such entry → ``None``. A DatasetView may read a
        parent's stored fields; apply each view's frame and atom mapping before
        returning the values.
        """
        from ffast.loaders.dataset import DatasetView

        store = getattr(self.env, "predictionFields", None)
        if not store or model is None or dataset is None:
            return None
        model_fp = getattr(model, "fingerprint", None)
        ds = dataset
        views = []
        while ds is not None:
            by_kind = store.get((model_fp, getattr(ds, "fingerprint", None)))
            if by_kind is not None:
                value = by_kind.get(kind, {}).get(key)
                if value is None:
                    return None
                values = (_flatPerAtom(value, axes=0) if kind == "atoms"
                          else _flatten(value))
                for view in reversed(views):
                    indices = (view._flatIndex() if kind == "atoms"
                               else view._parentFrames())
                    values = np.asarray(values)[indices]
                return values
            if isinstance(ds, DatasetView):
                views.append(ds)
            ds = getattr(ds, "parent", None)
        return None

    def _masses(self, dataset):
        if hasattr(dataset, "getMasses"):
            try:
                return np.asarray(dataset.getMasses(), dtype=np.float64)
            except Exception:
                pass
        # Fall back to standard atomic masses from the elements (Z).
        try:
            from ase.data import atomic_masses
            z = np.asarray(dataset.getAllElements(), dtype=int)
            return np.asarray(atomic_masses[z], dtype=np.float64)
        except Exception as exc:
            logger.warning("InputResolver: reference.masses unavailable: %s", exc)
            return None

    def _offsets(self, dataset):
        return np.asarray(dataset.getOffsets())

    def _periodic(self, dataset):
        """Whether any frame has a unit cell. ASE stores a zero cell for a
        molecule, and an sGDML file without a lattice has none at all."""
        lattice = dataset.getLattice() if hasattr(dataset, "getLattice") else None
        if lattice is None:
            return False
        return bool(np.any(np.asarray(lattice, dtype=float) != 0))

    def _n_atoms(self, dataset):
        """Per-structure atom count (the reserved Expression Variable, ADR 0042).

        ``np.diff`` of the dataset's offsets: one count per frame, whether or
        not the frames differ in size, so per-atom normalisation lines up with
        per-structure quantities.
        """
        if dataset is None:
            return None
        return np.diff(np.asarray(dataset.getOffsets()))

    # ── metric input assembly ──────────────────────────────────────────────

    def missing_prediction_keys(self, metric_id, model=None, dataset=None):
        """Prediction DataType keys (``energy``/``forces``) the metric needs but
        that aren't cached yet, so the queue can generate them first."""
        from ffast.metrics.registry import default_registry as registry

        missing = []
        for ref in collect_prediction_refs(registry, metric_id):
            # Prediction Dataset Fields are not generatable (they arrive with the
            # loaded prediction file), so they have no DataType key to queue.
            if ref not in _PREDICTION_REFS:
                continue
            dt_key, _ = _PREDICTION_REFS[ref]
            if self.env.getData(dt_key, model=model, dataset=dataset) is None:
                if dt_key not in missing:
                    missing.append(dt_key)
        return missing

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
                return _flatten(dataset.getEnergies())
            if ref == "reference.forces":
                return _flatten(dataset.getForces())
            if ref == "reference.positions":
                return _flatten(dataset.getCoordinates())
            if ref == "reference.elements":
                return np.asarray(dataset.getElements())
            if ref == "reference.masses":
                return self._masses(dataset)
            if ref in _PREDICTION_REFS:
                dt_key, field = _PREDICTION_REFS[ref]
                entity = self.env.getData(dt_key, model=model, dataset=dataset)
                if entity is None:
                    return None
                return _flatten(entity.get(field))
            if ref == "offsets":
                return self._offsets(dataset)
            # Dataset Field refs (ADR 0023): {reference,prediction}.{info,atoms}.<key>
            parsed = parse_field_ref(ref)
            if parsed is not None:
                side, kind, key = parsed
                if side == "reference":
                    if kind == "atoms":
                        return _flatten(dataset.getAtomField(key))
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
        predictions carry no such entry → ``None``. For a Sub/Atom-filtered
        dataset the fields were stored against the parent, so walk up to it.
        """
        store = getattr(self.env, "predictionFields", None)
        if not store or model is None or dataset is None:
            return None
        model_fp = getattr(model, "fingerprint", None)
        ds = dataset
        while ds is not None:
            by_kind = store.get((model_fp, getattr(ds, "fingerprint", None)))
            if by_kind is not None:
                return _flatten(by_kind.get(kind, {}).get(key))
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
            z = np.asarray(dataset.getElements(), dtype=int)
            return np.asarray(atomic_masses[z], dtype=np.float64)
        except Exception as exc:
            logger.warning("InputResolver: reference.masses unavailable: %s", exc)
            return None

    def _offsets(self, dataset):
        if getattr(dataset, "isVariable", False):
            return np.asarray(dataset.molecule_offsets)
        return None

    # ── metric input assembly ──────────────────────────────────────────────

    def build_metric_inputs(self, metric_id, model=None, dataset=None):
        """Flat ``{input_key: array}`` for a metric and its transitive deps.

        The Metric Execution Context (ADR 0035) walks the dependency tree; this
        is its env-backed sourcing adapter.  Metric-dep refs stay as dependency
        markers (the executor resolves those internally, so we only supply the
        leaf raw inputs); every other ref — including optional ones like
        ``offsets`` — is resolved via ``InputResolver.resolve``.  The flat dict
        harvests each plan step's raw bindings; values may be ``None`` for
        optional/unavailable inputs.

        Note: keyed by each metric's *local* input names. The built-in metrics
        never mix an energy and a force tree, so a local name
        (``reference``/``predicted``) never maps to two different refs; first
        binding wins if a future metric does combine them.
        """
        from ffast.metrics.execution import RawInput, build_execution_plan
        from ffast.metrics.registry import default_registry as registry

        plan = build_execution_plan(
            registry, metric_id, {}, _ResolverSource(self, model, dataset)
        )
        inputs: dict = {}
        for step in plan.steps:
            for key, binding in step.bindings.items():
                if isinstance(binding, RawInput) and key not in inputs:
                    inputs[key] = binding.value
        return inputs

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

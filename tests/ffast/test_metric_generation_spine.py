"""ADR 0019 spine: InputResolver + metric compute path.

Exercises the real InputResolver, the real InProcessExecutor over the built-in
metric registry, and a faithful re-implementation of the 3-line cache-store that
Environment.generateMetric performs. Environment itself is NOT imported here: its
module pulls torch + Qt, which the unit-test interpreter need not have. The queue
wiring (handleGenerationQueue) is covered by the headless/app integration run.
"""
from __future__ import annotations

import numpy as np

from client.inputResolver import InputResolver, metric_needs_prediction
from ffast.metrics.builtin import energy_metrics, force_metrics  # noqa: F401 register
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.models import MetricResult
from ffast.metrics.registry import default_registry


# ── lightweight fakes ─────────────────────────────────────────────────────────

class _Entity:
    def __init__(self, **fields):
        self._fields = fields

    def get(self, key=None):
        return self._fields.get(key)


class _Dataset:
    isVariable = False
    fingerprint = "ds-fp"

    def __init__(self, energies, forces):
        self._e = np.asarray(energies, dtype=np.float64)
        self._f = np.asarray(forces, dtype=np.float64)

    def getEnergies(self, indices=None):
        return self._e

    def getForces(self, indices=None):
        return self._f

    def getElements(self, index=None):
        return np.array([6, 1], dtype=np.int64)


class _Model:
    fingerprint = "model-fp"


class _FakeEnv:
    """Minimal env surface used by InputResolver and the compute helper."""

    def __init__(self, dataset, predictions=None):
        self.cache = {}
        self._metricRequests = {}
        self._inputResolver = None
        self._metricExecutor = None
        # predictions: {("energy"|"forces", model_fp, dataset_fp): _Entity}
        self._predictions = predictions or {}
        self.events = []

    @property
    def inputResolver(self):
        if self._inputResolver is None:
            self._inputResolver = InputResolver(self)
        return self._inputResolver

    @property
    def metricExecutor(self):
        if self._metricExecutor is None:
            self._metricExecutor = InProcessExecutor(default_registry)
        return self._metricExecutor

    def make_metric_cache_key(self, metric_id, params, model, dataset):
        import hashlib, json
        mfp = model.fingerprint if model is not None else "nil"
        dfp = dataset.fingerprint if dataset is not None else "nil"
        if params:
            h = hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8]
            return f"{metric_id}__{h}__{mfp}__{dfp}"
        return f"{metric_id}__{mfp}__{dfp}"

    def registerMetricRequest(self, metric_id, params, model, dataset):
        key = self.make_metric_cache_key(metric_id, params, model, dataset)
        self._metricRequests[key] = (metric_id, params or {}, model, dataset)
        return key

    def generateMetric(self, metric_id, params, model, dataset, key=None):
        # mirrors Environment.generateMetric
        if key is None:
            key = self.make_metric_cache_key(metric_id, params, model, dataset)
        inputs = self.inputResolver.build_metric_inputs(metric_id, model=model, dataset=dataset)
        result = self.metricExecutor.run(metric_id, inputs, params or {})
        if isinstance(result, MetricResult):
            self.cache[key] = result
            self.events.append(("DATA_UPDATED", (key,)))
            return True
        return False

    def eventPush(self, name, *args, **kwargs):
        self.events.append((name, args))

    def getData(self, dtKey, model=None, dataset=None):
        mfp = model.fingerprint if model is not None else "nil"
        dfp = dataset.fingerprint if dataset is not None else "nil"
        return self._predictions.get((dtKey, mfp, dfp))


# ── InputResolver ──────────────────────────────────────────────────────────────

def test_resolve_reference_refs():
    ds = _Dataset(energies=[1.0, 2.0], forces=[[1.0, 0, 0], [0, 0, 0]])
    env = _FakeEnv(ds)
    r = InputResolver(env)
    np.testing.assert_array_equal(r.resolve("reference.energies", dataset=ds), [1.0, 2.0])
    np.testing.assert_array_equal(
        r.resolve("reference.forces", dataset=ds), [[1.0, 0, 0], [0, 0, 0]]
    )
    assert r.resolve("reference.stress", dataset=ds) is None
    assert r.resolve("offsets", dataset=ds) is None  # uniform dataset


def test_resolve_prediction_requires_generated_data():
    ds = _Dataset(energies=[1.0], forces=[[0, 0, 0]])
    model = _Model()
    env = _FakeEnv(ds)  # no predictions cached
    r = InputResolver(env)
    assert r.resolve("prediction.forces", model=model, dataset=ds) is None

    # once the forces prediction exists, it resolves
    env._predictions[("forces", "model-fp", "ds-fp")] = _Entity(
        forces=np.array([[2.0, 0, 0]])
    )
    np.testing.assert_array_equal(
        r.resolve("prediction.forces", model=model, dataset=ds), [[2.0, 0, 0]]
    )


def test_build_metric_inputs_collects_leaf_refs():
    ds = _Dataset(energies=[1.0, 2.0], forces=[[1, 0, 0], [0, 0, 0]])
    model = _Model()
    env = _FakeEnv(
        ds,
        predictions={
            ("forces", "model-fp", "ds-fp"): _Entity(
                forces=np.array([[2.0, 0, 0], [0, 0, 0]])
            )
        },
    )
    r = InputResolver(env)
    inputs = r.build_metric_inputs("ffast.force_mae", model=model, dataset=ds)
    # force_mae → force_difference(reference=reference.forces, predicted=prediction.forces)
    assert set(inputs) >= {"reference", "predicted"}
    np.testing.assert_array_equal(inputs["reference"], [[1, 0, 0], [0, 0, 0]])
    np.testing.assert_array_equal(inputs["predicted"], [[2.0, 0, 0], [0, 0, 0]])


def test_metric_needs_prediction_is_transitive():
    """Regression: energy_mae's direct input is a metric ref, but it still needs
    a prediction at the leaves → must be detected as model-dependent."""
    assert metric_needs_prediction("ffast.energy_difference") is True
    assert metric_needs_prediction("ffast.energy_mae") is True       # metric-ref input
    assert metric_needs_prediction("ffast.energy_rmse_shifted") is True
    assert metric_needs_prediction("ffast.force_mae") is True        # metric-ref input
    assert metric_needs_prediction("ffast.force_rmse") is True


def test_missing_prediction_keys():
    ds = _Dataset(energies=[1.0], forces=[[0, 0, 0]])
    model = _Model()
    env = _FakeEnv(ds)
    r = InputResolver(env)
    # force_mae needs prediction.forces → "forces" missing
    assert r.missing_prediction_keys("ffast.force_mae", model=model, dataset=ds) == ["forces"]
    # energy_difference needs prediction.energies → "energy" missing
    assert r.missing_prediction_keys("ffast.energy_difference", model=model, dataset=ds) == ["energy"]
    # supply forces → no longer missing
    env._predictions[("forces", "model-fp", "ds-fp")] = _Entity(forces=np.zeros((1, 3)))
    assert r.missing_prediction_keys("ffast.force_mae", model=model, dataset=ds) == []


# ── generateMetric (compute → cache → DATA_UPDATED) ─────────────────────────────

def test_generate_metric_caches_result_and_fires_event():
    ds = _Dataset(energies=[1.0, 2.0], forces=[[1.0, 0, 0], [0, 0, 0]])
    model = _Model()
    env = _FakeEnv(
        ds,
        predictions={
            ("forces", "model-fp", "ds-fp"): _Entity(
                forces=np.array([[2.0, 0, 0], [0, 0, 0]])
            )
        },
    )
    key = env.registerMetricRequest("ffast.force_mae", {}, model, ds)
    ok = env.generateMetric("ffast.force_mae", {}, model, ds, key=key)

    assert ok is True
    assert key in env.cache
    result = env.cache[key]
    assert isinstance(result, MetricResult)
    # diff = [[1,0,0],[0,0,0]] → l2 per-atom = [1.0, 0.0]
    np.testing.assert_allclose(result.values, [1.0, 0.0], atol=1e-6)
    assert ("DATA_UPDATED", (key,)) in env.events


def test_generate_metric_failure_returns_false():
    ds = _Dataset(energies=[1.0], forces=[[0, 0, 0]])
    model = _Model()
    env = _FakeEnv(ds)  # no prediction → prediction.forces resolves None → metric fails
    key = env.registerMetricRequest("ffast.force_mae", {}, model, ds)
    ok = env.generateMetric("ffast.force_mae", {}, model, ds, key=key)
    assert ok is False
    assert key not in env.cache


def test_norm_parameter_changes_cache_identity():
    """Different compute params → different cache keys (D7)."""
    ds = _Dataset(energies=[0.0], forces=[[0, 0, 0]])
    model = _Model()
    env = _FakeEnv(ds)
    k_l2 = env.registerMetricRequest("ffast.force_mae", {"norm": "l2"}, model, ds)
    k_l1 = env.registerMetricRequest("ffast.force_mae", {"norm": "l1"}, model, ds)
    assert k_l2 != k_l1

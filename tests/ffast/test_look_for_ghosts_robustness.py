"""Regression: lookForGhosts must tolerate metric cache keys.

Real bug (2026-06-23, GUI): force-smoothing slider imported ghost predictions
→ ``Environment.lookForGhosts`` iterated *every* cache key and did
``(dataKey, modelKey, datasetKey) = cacheKey.split("__")``. That assumes exactly
3 parts (``forces__model__dataset``), but the cache now also holds Transform
Metric results whose keys have 4 parts
(``metricid__paramhash__model__dataset``) → ``ValueError: too many values to
unpack (expected 3)``. Same class as the queue-key bug: a cache-key-shape
assumption that the Panel constructor's metric keys violate.

Ghost recovery only concerns raw prediction-data keys; metric keys must be
skipped, not parsed.
"""
import types

import ffast.core.loading_coordinator as lcmod
from ffast.core.loading_coordinator import LoadingCoordinator
from ffast.cache import CacheKey


class _Models:
    def __init__(self):
        self.added = []

    def __contains__(self, key):
        return False  # nothing loaded → every prediction key wants a ghost

    def add(self, model):
        self.added.append(model)


class _Datasets:
    def exists(self, key):
        return True


def test_lookForGhosts_skips_metric_cache_keys(monkeypatch):
    created = []

    class _FakeGhost:
        def __init__(self, env, modelKey):
            created.append(modelKey)

        def initialise(self):
            pass

    monkeypatch.setattr(lcmod, "GhostModelLoader", _FakeGhost)

    fake = types.SimpleNamespace(
        cache={
            "forces__modelA__ds1": object(),                       # 3-part → ghost
            "energy__modelC__ds1": object(),                       # 3-part → ghost
            "ffast.force_rmse_smoothed__ph__modelB__ds1": object(),  # 4-part metric → skip
            "ffast.energy_difference_density__ph2__modelB__ds1": object(),  # 4-part → skip
        },
        models=_Models(),
        datasets=_Datasets(),
    )

    LoadingCoordinator(fake).lookForGhosts()  # must not raise (was ValueError)

    # Only the raw prediction keys recover ghosts; metric keys are skipped.
    assert set(created) == {"modelA", "modelC"}


# ── skip branches: None fingerprints, already-loaded, missing dataset ─────────

class _TrackingModels:
    """Realistic Models double: ``add`` marks the model loaded so a later
    ``__contains__`` reflects it (unlike the always-False ``_Models`` above,
    which cannot exercise the 'already loaded' skip branch)."""

    def __init__(self, preloaded=()):
        self._loaded = set(preloaded)
        self.added = []

    def __contains__(self, key):
        return key in self._loaded

    def add(self, model):
        self._loaded.add(model.modelKey)
        self.added.append(model.modelKey)


def _run_ghosts(monkeypatch, cache, models, datasets):
    created = []

    class _FakeGhost:
        def __init__(self, env, modelKey):
            self.modelKey = modelKey
            created.append(modelKey)

        def initialise(self):
            pass

    monkeypatch.setattr(lcmod, "GhostModelLoader", _FakeGhost)
    fake = types.SimpleNamespace(cache=cache, models=models, datasets=datasets)
    LoadingCoordinator(fake).lookForGhosts()
    return created


def test_none_model_fingerprint_key_is_skipped(monkeypatch):
    # A model-independent prediction key (model_fp serialized as `nil`) has no
    # model to ghost → skipped, not crashed.
    key = CacheKey("forces", None, "ds1").format()
    created = _run_ghosts(
        monkeypatch, {key: object()}, _Models(), _Datasets()
    )
    assert created == []


def test_none_dataset_fingerprint_key_is_skipped(monkeypatch):
    key = CacheKey("forces", "modelA", None).format()
    created = _run_ghosts(
        monkeypatch, {key: object()}, _Models(), _Datasets()
    )
    assert created == []


def test_already_loaded_model_is_not_reghosted(monkeypatch):
    # modelA is already loaded → the `modelKey not in models` guard skips it.
    key = CacheKey("forces", "modelA", "ds1").format()
    created = _run_ghosts(
        monkeypatch, {key: object()}, _TrackingModels(preloaded={"modelA"}), _Datasets()
    )
    assert created == []


def test_missing_dataset_is_skipped(monkeypatch):
    key = CacheKey("forces", "modelA", "ds1").format()
    datasets = types.SimpleNamespace(exists=lambda k: False)
    created = _run_ghosts(
        monkeypatch, {key: object()}, _Models(), datasets
    )
    assert created == []


def test_multiple_keys_for_same_model_create_one_ghost(monkeypatch):
    # Two prediction keys (forces + energy) for the SAME model must yield a
    # single ghost: the first `add` marks it loaded, so the second key hits the
    # already-loaded skip branch (dedup across keys of one model).
    cache = {
        CacheKey("forces", "modelA", "ds1").format(): object(),
        CacheKey("energy", "modelA", "ds1").format(): object(),
    }
    created = _run_ghosts(
        monkeypatch, cache, _TrackingModels(), _Datasets()
    )
    assert created == ["modelA"]

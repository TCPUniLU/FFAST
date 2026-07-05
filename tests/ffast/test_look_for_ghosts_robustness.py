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

import client.loading_coordinator as lcmod
from client.loading_coordinator import LoadingCoordinator


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

"""Regression: a malformed key in the generation queue must not crash the drain.

Real bug (server.log, 2026-06-23): a ``None`` cache key reached
``DataService.handleGenerationQueue`` → ``cacheKeyToComponents(None)`` →
``None.split("__")`` → ``AttributeError``. On the server this loop runs inside
``headlessEventLoop``, so the unhandled error tore down the whole server process
and every client saw "no close frame received or sent".

``DataService`` is constructed with light stubs (``initialiseDataTypes`` is
overridden) so the test avoids the torch/Qt import the full Environment pulls;
the queue-drain logic under test is independent of the DataType registry.
"""
import asyncio

from client.data_service import DataService


class _DS(DataService):
    def initialiseDataTypes(self):
        self.dataTypes = {}


class _Reg:
    def get(self, key):
        return None


class _Events:
    def eventPush(self, *a, **k):
        pass

    def push(self, *a, **k):
        pass


def _make():
    return _DS(cache={}, models=_Reg(), datasets=_Reg(), tm=None,
               events=_Events())


def test_malformed_queue_keys_are_dropped_not_crashed():
    ds = _make()
    ds.generationQueue.update([None, "nodunder", "only__two"])  # all unparseable
    asyncio.run(ds.handleGenerationQueue())  # must not raise
    assert ds.generationQueue == set()       # all dropped


def test_valid_metric_key_still_routes():
    ds = _make()
    key = "ffast.x__phash__nil__dsfp"          # 4 parts, registered metric request
    ds._metricRequests[key] = ("ffast.x", {}, None, None)
    routed = []
    ds.taskGenerateMetric = lambda *a, **k: routed.append(a)
    ds.generationQueue.add(key)
    asyncio.run(ds.handleGenerationQueue())
    assert routed, "valid metric-request key must route to taskGenerateMetric"
    assert key not in ds.generationQueue


def test_none_cache_key_is_not_enqueued_by_generateData():
    """generateData must not enqueue a None key (the upstream source of the bug)."""
    ds = _make()

    class _DT:
        def getCacheKey(self, model=None, dataset=None):
            return None  # the pathological case
        def generateData(self, model=None, dataset=None, taskID=None):
            return None  # generation "fails" → would reach the enqueue branch

    ds.dataTypes = {"forces": _DT()}
    ds.generateData("forces", model=None, dataset=None)
    assert ds.generationQueue == set()  # None was logged + skipped, not enqueued

"""The metric result store (ADR 0085): one computation per result, a reason
when there is none, a Running Task while it computes."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import numpy as np

from ffast.core.tasks import TaskManager
from ffast.metrics.builtin import energy_metrics, force_metrics  # noqa: F401 register
from ffast.metrics.models import MetricFailure, MetricResult
from ffast.metrics.registry import default_registry
from ffast.metrics.result_store import Missing, MetricResultStore, metric_task_name

PLAIN = "ffast.energy_reference"                                  # needs no prediction
NEEDS_PREDICTION = "ffast.force_difference[atom+structure;length]"


class _Named:
    def __init__(self, fp, name):
        self.fingerprint = fp
        self._name = name

    def getDisplayName(self):
        return self._name


def _result(value=1.0):
    return MetricResult(metric_id=PLAIN, shape="scalar", dtype="float64", unit="energy",
                        compute_parameters={}, implementation_hash="h",
                        values=np.array(value))


class _Env:
    """Only what the store uses (see its docstring)."""

    def __init__(self, outcomes=None, missing_keys=()):
        self.calls = []
        self.stopped = []
        self.release = None                 # a threading.Event the compute waits on
        self.started = threading.Event()
        self.outcomes = list(outcomes or [])
        self.held = {}                      # the executor's cache
        self.revision_bump = False
        self.dataset = _Named("ds", "shard_03")
        self.model = _Named("m1", "P1")
        self.datasets = SimpleNamespace(get=lambda fp: self.dataset if fp == "ds" else None)
        self.models = SimpleNamespace(get=lambda fp: self.model if fp == "m1" else None)
        self.tm = TaskManager()
        self.data = SimpleNamespace(
            make_metric_cache_key=self._key,
            inputResolver=SimpleNamespace(
                missing_prediction_keys=lambda *a, **k: list(missing_keys)),
            compute_metric=self._compute,
            metricExecutor=SimpleNamespace(cached=lambda k: self.held.get(k)),
        )

    def _key(self, metric_id, params, model, dataset):
        mfp = model.fingerprint if model else "nil"
        metric_id = f"{metric_id}__f{default_registry.revision}"
        token = f"__p{abs(hash(repr(sorted(params.items()))))}" if params else ""
        return f"{metric_id}{token}__{mfp}__{dataset.fingerprint}"

    def _compute(self, metric_id, params, model, dataset, *, stopped=None, key_out=None):
        self.calls.append(metric_id)
        self.stopped.append(stopped)
        self.started.set()
        if self.release is not None:
            self.release.wait(5)
        if self.revision_bump:
            self.revision_bump = False
            default_registry.revision += 1
        outcome = self.outcomes.pop(0) if self.outcomes else _result()
        if isinstance(outcome, MetricResult):
            key_out["key"] = f"step{len(self.calls)}"
            self.held[key_out["key"]] = outcome
        return outcome


def _run(coro):
    return asyncio.run(coro)


def test_two_asks_for_one_result_compute_it_once():
    env = _Env()
    env.release = threading.Event()
    store = MetricResultStore(env)

    async def scenario():
        a = asyncio.ensure_future(store.get(PLAIN, {}, "ds", None))
        b = asyncio.ensure_future(store.get(PLAIN, {}, "ds", None))
        await asyncio.sleep(0.05)
        env.release.set()
        return await a, await b

    a, b = _run(scenario())
    assert len(env.calls) == 1
    assert a is b and isinstance(a, MetricResult)


def test_peek_gives_what_get_computed_and_never_computes():
    env = _Env()
    store = MetricResultStore(env)
    assert store.peek(PLAIN, {}, "ds", None) is None
    assert env.calls == []
    got = _run(store.get(PLAIN, {}, "ds", None))
    assert store.peek(PLAIN, {}, "ds", None) is got
    assert len(env.calls) == 1


def test_a_result_the_cache_dropped_is_computed_again():
    env = _Env()
    store = MetricResultStore(env)
    _run(store.get(PLAIN, {}, "ds", None))
    env.held.clear()
    assert store.peek(PLAIN, {}, "ds", None) is None
    _run(store.get(PLAIN, {}, "ds", None))
    assert len(env.calls) == 2


def test_a_prediction_the_pair_lacks_is_refused_not_started():
    env = _Env(missing_keys=["forces"])
    store = MetricResultStore(env)
    out = _run(store.get(NEEDS_PREDICTION, {}, "ds", "m1"))
    assert isinstance(out, Missing) and out.kind == "needs_prediction"
    assert "shard_03" in out.text and "P1" in out.text
    assert env.calls == []


def test_a_prediction_metric_with_no_model_is_refused():
    env = _Env()
    out = _run(MetricResultStore(env).get(NEEDS_PREDICTION, {}, "ds", None))
    assert out.kind == "needs_prediction"
    assert env.calls == []


def test_an_unknown_dataset_or_model_is_object_gone():
    store = MetricResultStore(_Env())
    assert _run(store.get(PLAIN, {}, "nope", None)).kind == "object_gone"
    assert _run(store.get(PLAIN, {}, "ds", "nope")).kind == "object_gone"


def _failure(kind):
    return MetricFailure(metric_id=PLAIN, traceback="It broke", kind=kind)


def test_a_failed_metric_is_not_computed_again():
    env = _Env(outcomes=[_failure("failed")])
    store = MetricResultStore(env)
    first = _run(store.get(PLAIN, {}, "ds", None))
    second = _run(store.get(PLAIN, {}, "ds", None))
    assert first == second == Missing("failed", "It broke")
    assert len(env.calls) == 1


def test_a_missing_input_is_remembered_too():
    env = _Env(outcomes=[_failure("missing_input")])
    store = MetricResultStore(env)
    _run(store.get(PLAIN, {}, "ds", None))
    assert _run(store.get(PLAIN, {}, "ds", None)).kind == "missing_input"
    assert len(env.calls) == 1


def test_a_temporary_failure_is_computed_again():
    env = _Env(outcomes=[_failure("temporary")])
    store = MetricResultStore(env)
    assert _run(store.get(PLAIN, {}, "ds", None)).kind == "temporary"
    assert isinstance(_run(store.get(PLAIN, {}, "ds", None)), MetricResult)
    assert len(env.calls) == 2


def test_the_last_waiter_leaving_stops_the_computation():
    env = _Env()
    env.release = threading.Event()
    store = MetricResultStore(env)

    async def scenario():
        waiting = asyncio.ensure_future(store.get(PLAIN, {}, "ds", None))
        while not env.started.is_set():
            await asyncio.sleep(0.01)
        assert env.stopped[0]() is False
        waiting.cancel()
        await asyncio.sleep(0)
        stopped = env.stopped[0]()
        env.release.set()
        return stopped

    assert _run(scenario()) is True


def test_one_waiter_leaving_does_not_stop_it_for_the_other():
    env = _Env()
    env.release = threading.Event()
    store = MetricResultStore(env)

    async def scenario():
        leaving = asyncio.ensure_future(store.get(PLAIN, {}, "ds", None))
        staying = asyncio.ensure_future(store.get(PLAIN, {}, "ds", None))
        while not env.started.is_set():
            await asyncio.sleep(0.01)
        leaving.cancel()
        await asyncio.sleep(0)
        still_going = env.stopped[0]() is False
        env.release.set()
        return still_going, await staying

    still_going, result = _run(scenario())
    assert still_going and isinstance(result, MetricResult)


def test_a_change_to_the_metric_table_during_a_run_computes_again_under_the_new_key():
    env = _Env()
    env.revision_bump = True
    store = MetricResultStore(env)
    before = default_registry.revision
    try:
        got = _run(store.get(PLAIN, {}, "ds", None))
        assert len(env.calls) == 2
        assert store.peek(PLAIN, {}, "ds", None) is got      # found under the new key
    finally:
        default_registry.revision = before


def test_a_computing_metric_is_one_running_task_until_it_ends():
    env = _Env()
    env.release = threading.Event()
    store = MetricResultStore(env)

    async def scenario():
        a = asyncio.ensure_future(store.get(PLAIN, {}, "ds", "m1"))
        b = asyncio.ensure_future(store.get(PLAIN, {}, "ds", "m1"))
        while not env.started.is_set():
            await asyncio.sleep(0.01)
        during = dict(env.tm.runningTasks)
        env.release.set()
        await a, await b
        await asyncio.sleep(0)
        return during, dict(env.tm.runningTasks)

    during, after = _run(scenario())
    assert len(during) == 1
    (task,) = during.values()
    assert task["name"] == metric_task_name(PLAIN, _Named("m1", "P1"), _Named("ds", "shard_03"))
    assert (task["visual"], task["cancellable"]) == (True, False)
    assert after == {}


def test_forgetting_a_dataset_drops_its_results_and_failures():
    env = _Env(outcomes=[_failure("failed")])
    store = MetricResultStore(env)
    _run(store.get(PLAIN, {}, "ds", None))                  # a remembered failure
    _run(store.get("ffast.energy_reference", {"x": 1}, "ds", None))   # a stored result
    assert store.peek("ffast.energy_reference", {"x": 1}, "ds", None) is not None
    store.forget_objects("ds")
    assert store.peek("ffast.energy_reference", {"x": 1}, "ds", None) is None
    _run(store.get(PLAIN, {}, "ds", None))                  # computed again, not the old failure
    assert len(env.calls) == 3


# ── review findings, 2026-11-04 ───────────────────────────────────────────────

def test_a_computation_abandoned_while_it_waited_for_a_thread_never_starts():
    """With every thread busy, a request that leaves before a thread is free
    must not read its inputs once one is (ADR 0085 decision 8)."""
    env = _Env()
    env.release = threading.Event()
    store = MetricResultStore(env, threads=1)

    async def scenario():
        busy = asyncio.ensure_future(store.get(PLAIN, {}, "ds", None))
        while not env.started.is_set():
            await asyncio.sleep(0.01)
        queued = asyncio.ensure_future(store.get(PLAIN, {"other": 1}, "ds", None))
        await asyncio.sleep(0.05)
        queued.cancel()
        await asyncio.sleep(0)
        env.release.set()
        await busy
        await asyncio.sleep(0.1)          # the freed thread takes the queued work

    _run(scenario())
    assert env.calls == [PLAIN]


def test_a_result_finished_after_its_dataset_was_forgotten_is_not_kept():
    """A subset that follows the zoom changes its frames under one fingerprint:
    a run that started before the change must not file its old-frame result
    under the key the new frames will be asked by (decision 6)."""
    env = _Env()
    env.release = threading.Event()
    store = MetricResultStore(env)

    async def scenario():
        waiting = asyncio.ensure_future(store.get(PLAIN, {}, "ds", None))
        while not env.started.is_set():
            await asyncio.sleep(0.01)
        store.forget_objects("ds")        # SUBDATASET_INDICES_CHANGED meanwhile
        env.release.set()
        return await waiting

    got = _run(scenario())
    assert isinstance(got, MetricResult)                    # the waiter still gets its answer
    assert store.peek(PLAIN, {}, "ds", None) is None        # but it is not served again


def test_a_failure_finished_after_its_dataset_was_forgotten_is_not_remembered():
    env = _Env(outcomes=[_failure("failed")])
    env.release = threading.Event()
    store = MetricResultStore(env)

    async def scenario():
        waiting = asyncio.ensure_future(store.get(PLAIN, {}, "ds", None))
        while not env.started.is_set():
            await asyncio.sleep(0.01)
        store.forget_objects("ds")
        env.release.set()
        await waiting
        env.release = None
        return await store.get(PLAIN, {}, "ds", None)

    assert isinstance(_run(scenario()), MetricResult)       # computed again
    assert len(env.calls) == 2


def test_a_restarted_computation_keeps_its_own_running_task():
    """The first run, left by its last waiter, still runs until its thread
    sees the stop; the run asked for next must keep its own Tasks row."""
    env = _Env()
    env.release = threading.Event()
    store = MetricResultStore(env)

    async def scenario():
        first = asyncio.ensure_future(store.get(PLAIN, {}, "ds", None))
        while not env.started.is_set():
            await asyncio.sleep(0.01)
        first.cancel()
        await asyncio.sleep(0)
        env.started.clear()
        second = asyncio.ensure_future(store.get(PLAIN, {}, "ds", None))
        while not env.started.is_set():
            await asyncio.sleep(0.01)
        rows_while_both_run = len(env.tm.runningTasks)
        env.release.set()
        await second
        await asyncio.sleep(0.05)
        return rows_while_both_run, dict(env.tm.runningTasks)

    rows, after = _run(scenario())
    assert rows == 2
    assert after == {}

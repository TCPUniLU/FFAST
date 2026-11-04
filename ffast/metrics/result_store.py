"""The one place the server asks for a metric over a whole dataset (ADR 0085).

``peek`` reads a stored result and never computes. ``get`` computes it when it
is not stored, once per result however many ask at the same time, and says why
when there is none.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ffast.metrics.models import MetricFailure, MetricResult

logger = logging.getLogger(__name__)

_FAILURES_KEPT = 256
_ATTEMPTS = 3


@dataclass(frozen=True)
class Missing:
    """Why there is no result: a kind (ADR 0085) and a sentence.

    Kinds: ``needs_prediction``, ``failed``, ``missing_input``, ``temporary``,
    ``object_gone``."""
    kind: str
    text: str


def metric_task_name(metric_id, model, dataset) -> str:
    """"Computing Force MAE · shard_03 / P1": what a Running Task computes."""
    return f"Computing {_label(metric_id)}" + _on(dataset, model)


def _label(metric_id) -> str:
    from ffast.metrics.registry import default_registry
    try:
        return default_registry.get(metric_id)[0].label or metric_id
    except KeyError:
        return metric_id


def _on(dataset, model) -> str:
    names = (getattr(o, "getDisplayName", None) for o in (dataset, model))
    on = " / ".join(str(name()) for name in names if name)
    return f" · {on}" if on else ""


def _name(obj, fallback) -> str:
    name = getattr(obj, "getDisplayName", None)
    return str(name()) if name else fallback


class _Run:
    """One computation and the requests waiting for it. ``forgets`` is the
    store's count of forget calls when it started: an outcome is filed only
    when nothing was forgotten meanwhile (ADR 0085 decision 6)."""

    def __init__(self, stop, forgets, task_id) -> None:
        self.future = None
        self.waiters = 0
        self.stop = stop
        self.forgets = forgets
        self.task_id = task_id


class MetricResultStore:
    """Metric results over whole datasets, for one server (ADR 0085).

    Computations run on this store's own pool, up to ``threads`` at once (16).
    How many really run together is decided by the memory gate of the
    ServerExecutor; throughput of GIL-bound steps does not grow with threads,
    the large array operations release the GIL and do. The pool is separate
    from the default one: client worker threads block there awaiting server
    replies, and sharing one bounded pool would deadlock.
    """

    def __init__(self, env, *, threads: int = 16) -> None:
        self._env = env
        self._pool = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="ffast-metric")
        # Read by ``peek`` from any thread, so every change holds the lock.
        self._lock = threading.Lock()
        self._index: dict[str, object] = {}                 # server key → step cache key
        self._failures: OrderedDict[str, Missing] = OrderedDict()
        self._running: dict[str, _Run] = {}                 # event-loop thread only
        self._forgets = 0                                   # forget calls so far
        self._runs = 0                                      # computations started

    # ── reading ────────────────────────────────────────────────────────────

    def peek(self, metric_id, params, dataset_fp, model_fp) -> MetricResult | None:
        """The stored result, or None. Never computes."""
        found = self._known(metric_id, params, dataset_fp, model_fp)
        return found[3] if isinstance(found[3], MetricResult) else None

    def can_compute(self, metric_id, params, dataset_fp, model_fp) -> bool:
        """True when ``get`` would start a computation: the objects are there,
        no failure is remembered, and no prediction is missing. Safe from any
        thread."""
        model, dataset, _, known = self._known(metric_id, params, dataset_fp, model_fp)
        return known is None and self._needs_prediction(metric_id, model, dataset) is None

    async def get(self, metric_id, params, dataset_fp, model_fp) -> MetricResult | Missing:
        """The result, computing it if needed, or why there is none."""
        model, dataset, key, known = self._known(metric_id, params, dataset_fp, model_fp)
        if known is not None:
            return known
        # Off the event loop: for a subset it builds the subset's arrays.
        needs = await asyncio.to_thread(self._needs_prediction, metric_id, model, dataset)
        if needs is not None:
            return needs
        known = self._known(metric_id, params, dataset_fp, model_fp)[3]
        if known is not None:       # stored by a run that ended meanwhile
            return known

        run = self._running.get(key)
        if run is None:
            run = self._start(key, metric_id, params, model, dataset)
        run.waiters += 1
        try:
            return await asyncio.shield(run.future)
        finally:
            run.waiters -= 1
            if run.waiters == 0 and not run.future.done():
                # Nobody waits any more: stop it, and let the next ask start over.
                run.stop.set()
                if self._running.get(key) is run:
                    del self._running[key]

    # ── forgetting ─────────────────────────────────────────────────────────

    def forget_objects(self, fingerprint) -> None:
        """Drop what is stored for this dataset or model."""
        from ffast.cache import CacheKey

        def gone(key) -> bool:
            ck = CacheKey.try_parse(key)
            return ck is not None and (ck.matches_dataset(fingerprint) or ck.matches_model(fingerprint))

        self._forget(gone)

    def forget_metrics(self, metric_ids) -> None:
        """Drop what is stored for these metrics, whatever their parameters."""
        from ffast.cache import CacheKey

        ids = set(metric_ids)

        def gone(key) -> bool:
            ck = CacheKey.try_parse(key)
            if ck is None:
                return False
            return ck.dtype in ids or any(ck.dtype.startswith((f"{m}__p", f"{m}__f")) for m in ids)

        self._forget(gone)

    def _forget(self, gone) -> None:
        with self._lock:
            self._forgets += 1
            for key in [k for k in self._index if gone(k)]:
                del self._index[key]
            for key in [k for k in self._failures if gone(k)]:
                del self._failures[key]

    # ── the lookup both calls share (rules 1–4) ────────────────────────────

    def _known(self, metric_id, params, dataset_fp, model_fp):
        """``(model, dataset, server key, known)``; ``known`` is a result, a
        remembered or ``object_gone`` Missing, or None when it must be computed."""
        env = self._env
        model = env.models.get(model_fp) if model_fp else None
        dataset = env.datasets.get(dataset_fp) if dataset_fp else None
        if dataset is None or (model_fp and model is None):
            return model, dataset, None, Missing(
                "object_gone", "The dataset or prediction is no longer loaded")
        key = env.data.make_metric_cache_key(metric_id, params, model, dataset)
        with self._lock:
            failure = self._failures.get(key)
            step_key = self._index.get(key)
        if failure is not None:
            return model, dataset, key, failure
        if step_key is not None:
            result = env.data.metricExecutor.cached(step_key)
            if result is not None:
                return model, dataset, key, result
            with self._lock:
                if self._index.get(key) == step_key:
                    del self._index[key]
        return model, dataset, key, None

    def _needs_prediction(self, metric_id, model, dataset) -> Missing | None:
        from ffast.metrics.input_resolver import metric_needs_prediction

        try:
            if model is None:
                needs = metric_needs_prediction(metric_id)
            else:
                needs = bool(self._env.data.inputResolver.missing_prediction_keys(
                    metric_id, model=model, dataset=dataset))
        except KeyError:
            return None             # an unknown metric: the compute says why
        if not needs:
            return None
        text = f"{_label(metric_id)} needs a prediction"
        if model is not None:
            text += f" of {_name(dataset, 'the dataset')} by {_name(model, 'the model')}"
        return Missing("needs_prediction", text)

    # ── computing ──────────────────────────────────────────────────────────

    def _start(self, key, metric_id, params, model, dataset) -> _Run:
        loop = asyncio.get_running_loop()
        asked = time.monotonic()
        self._runs += 1
        with self._lock:
            forgets = self._forgets
        run = _Run(threading.Event(), forgets, f"metric:{key}#{self._runs}")

        def work():
            if run.stop.is_set():   # left while it waited for a thread: read nothing
                return Missing("temporary", "Nobody waits for it any more")
            logger.info("Metric %s: waited %d ms for a compute thread",
                        metric_id, (time.monotonic() - asked) * 1000)
            return self._compute(metric_id, params, model, dataset, run)

        run.future = loop.run_in_executor(self._pool, work)
        self._running[key] = run

        # One row per run: a run left by its waiters still runs until its
        # thread sees the stop, beside the one asked for next.
        tm = getattr(self._env, "tm", None)
        if tm is not None:
            tm.showWork(run.task_id, metric_task_name(metric_id, model, dataset))

        def done(_):
            if self._running.get(key) is run:
                del self._running[key]
            if tm is not None:
                tm.endWork(run.task_id)

        run.future.add_done_callback(done)
        return run

    def _compute(self, metric_id, params, model, dataset, run) -> MetricResult | Missing:
        """On a pool thread: compute, then index or remember the outcome."""
        from ffast.metrics.registry import default_registry

        data = self._env.data
        try:
            for _attempt in range(_ATTEMPTS):
                revision = default_registry.revision
                key = data.make_metric_cache_key(metric_id, params, model, dataset)
                step_key: dict = {}
                outcome = data.compute_metric(metric_id, params, model, dataset,
                                              stopped=run.stop.is_set, key_out=step_key)
                if default_registry.revision != revision and not run.stop.is_set():
                    logger.info("Metric %s changed while computing; computing again", metric_id)
                    continue
                return self._finish(key, metric_id, outcome, step_key.get("key"), run)
            logger.warning("Metric %s kept changing while computing; gave up", metric_id)
            return Missing("temporary", "The metric kept changing while it was computed")
        except Exception as exc:
            # A bug, not the metric's answer: said, not remembered.
            logger.exception("Metric %s: computing failed", metric_id)
            return Missing("failed", str(exc))

    def _finish(self, key, metric_id, outcome, step_key, run) -> MetricResult | Missing:
        if isinstance(outcome, MetricResult):
            if step_key is not None:
                with self._lock:
                    if self._forgets == run.forgets:
                        self._index[key] = step_key
            return outcome
        failure: MetricFailure = outcome
        reason = failure.reason or repr(failure)
        logger.error("Could not compute %s: %s", _label(metric_id), reason,
                     extra={"popup": False})
        logger.debug("Metric %s failed: %s", metric_id, failure.traceback)
        missing = Missing(failure.kind, reason)
        if failure.kind in ("failed", "missing_input"):
            with self._lock:
                if self._forgets != run.forgets:
                    return missing      # its dataset or model changed meanwhile
                self._failures.pop(key, None)
                self._failures[key] = missing
                while len(self._failures) > _FAILURES_KEPT:
                    self._failures.popitem(last=False)
        return missing

# 4. Saving a user metric is not one step, and the browser names results itself

**Status:** decided, see ADR 0061 · **Strength:** strong · **Dependencies:** local files and the
worker pool, both usable in tests

## In one sentence

When a user metric is saved, the server updates the metric registry in one step,
then clears old results and refreshes the worker processes in a second step.
Meanwhile the browser builds the cache key for results itself, without the
formula, so the fix that put the formula into cache keys (f946bf5) does not
protect browser requests.

## Background

- **Cache key**: the name a computed result is stored under,
  `metric-id__model-fingerprint__dataset-fingerprint` (`CONTEXT.md`). The same
  key means the stored result is reused.
- **f946bf5** (ADR 0057, rule 5) added a hash of the formula to the key for user
  metrics. A result computed with the old formula can then never be mistaken
  for one computed with the new formula.
- **Worker pool**: metrics run in separate processes, each holding a pickled
  copy of the registry. After a change, the workers are marked stale and
  replaced before their next task (7b64323).
- **Two caches:**
  - the **outer** cache in `DataService`, keyed by the cache key above;
  - the **inner** `MetricCache`, keyed by a hash of the metric's code plus
    hashes of its input arrays.

## Problem A: browser keys skip the formula hash (Read)

- **The browser builds the key in JavaScript.**
  `ffast/renderers/web/static/metrics.js:31-39` produces
  `id[__p<params>]__model__dataset`, with no formula hash.
- **The server uses a key it is sent as-is.**
  `ffast/session/server_session.py:1176` builds its own key only when
  `key is None`.
- **So the fix never reaches browser requests.** The browser always sends a
  key, and it is the only client that uses user metrics.
- **What protects them today.** `invalidate_metrics` clears the outer cache for
  the changed metric after a save. That covers the common case. It does not
  cover a result that was being computed during the save and is written back
  afterwards under the same formula-free key. (The code path was Read; the race
  was not reproduced.)
- **The same result can be stored twice.** Once under the browser's key, and
  once under the server's own key, which it builds for `_frames_in_view`.
- **Not tested.** `test_metrics_pane.py` sends `REQUEST_METRIC` without a key,
  so the browser-key path has no test. (Unchecked)
- **The key format is now written in two languages.** `CONTEXT.md` says the cache
  key turns into a string only at the disk and RPC boundaries; here the browser
  repeats the server's key format in JavaScript.

## Problem B: the change happens in two steps (Read)

`_change_metrics` (`ffast/session/server_session.py:1305-1337`):

- **Step 1** (`asyncio.to_thread`, :1320): `UserMetrics` updates the registry and
  writes the file.
- **Step 2** (`asyncio.to_thread`, :1323): cached results are dropped, and
  `refresh_registry()` marks the workers stale.

Between the two steps, a request from another window can start on a worker
that has not yet been marked stale. That worker runs the old formula, while the
main process builds the result's identity from the new one. This is a timing
window; it has not been reproduced.

## Problem C: things never told about a change (Read)

- **Prediction fields.** Values for `prediction.info.<key>` and
  `prediction.atoms.<key>` are extracted only when a prediction loads, and only
  for keys that some registered metric declares at that moment
  (`ffast/core/loading_coordinator.py:345-368`). A new user metric over a new
  prediction field gets nothing for predictions that are already loaded.
- **The inner MetricCache.** It has a `clear()` method
  (`ffast/metrics/cache.py:96`) with no caller anywhere. That is not a
  correctness problem, because its keys include the code hash, but it grows
  without limit.
- **Transforms** (Unchecked). A transform metric with an inherited shape keeps
  the old shape after an edit (`ffast/metrics/transforms.py:228`).
- **The registry has no lock** (Unchecked). The event loop, the two-thread metric
  pool and Try's threads all read it while it changes.

## Where the change rules live today

Three places, in two languages:

- `ffast/config/user_metrics.py`: the registry, the graph check, the file, the
  entry list.
- `ffast/session/server_session.py`: clearing the cache, refreshing the workers,
  telling every window.
- `ffast/renderers/web/static/app.js:2103-2108`: ask for the catalog again,
  maybe the tab layout, the user list, then recolour.

## Direction (not a design yet)

1. **One module applies a metric change completely,** with no gap between steps:
   registry, file, cached results, workers, and the notice to windows.
2. **Only the server builds cache keys.** The browser asks by metric, parameters,
   model and dataset, and matches the reply by a request ID.

## Questions to settle before building

1. **The request ID.** Reuse the existing `key` field as an opaque ID, or add a
   new field? This is a protocol change, so ADR 0033 (typed control messages)
   applies.
2. **"One step".** Achieve it with a lock that requests wait on, or with a
   version number that every request carries?
3. **New prediction fields.** Re-extract them when a metric that needs one is
   saved, or extract them lazily the first time they are used?
4. **The inner cache.** Should it be bounded, or cleared on each change?
5. **Brief 6.** Do this together with the shared user store? The metric side of
   that store would be where this single change step lives.

## Files

- `ffast/config/user_metrics.py`
- `ffast/session/server_session.py` (`_change_metrics`, `_apply_metric_change`,
  `_on_request_metric`)
- `ffast/core/data_service.py` (`make_metric_cache_key`, `invalidate_metrics`,
  `generateMetric`)
- `ffast/metrics/pool.py`, `ffast/metrics/cache.py`, `ffast/metrics/execution.py`
- `ffast/renderers/web/static/metrics.js`, `app.js`

## Related

- ADR 0035 and 0046 (metric execution context), ADR 0057 (rules 5, 6, 7)
- Commits f946bf5 (formula hash in the key), 7b64323 (one task per worker,
  stale workers)

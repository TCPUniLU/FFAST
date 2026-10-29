Status: Built 2026-10-21 on branch `metric-keys`, merged to `main` (38847d5). Decided after architecture review problem 4
(`docs/research/architecture-review-2026-10-14/04-metric-change-and-result-keys.md`).

# Only the server names metric results, and a metric change cannot mix formulas

## The problem

A user metric (ADR 0057) can be edited while the server runs. ADR 0057 rule 5
says that after an edit, no result computed with the old formula is ever shown.
Three gaps break that rule. All three were found by reading the code. None has
been seen happening.

**A. The browser names results itself.** The cache key is the name a computed
result is stored under. The server builds it with a hash of the formula
(`DataService.make_metric_cache_key`, the `__f<token>` part), so an edit gives a
new name. But `MetricClient` in `ffast/renderers/web/static/metrics.js` builds
its own key without that hash and sends it as `key`.
`ServerSession._on_request_metric` stores the result under whatever key it is
sent. So a computation that starts before a save and finishes after it writes
the old result back under a name that the next request reads. The same result
can also be stored twice: once under the browser's key and once under the
server's own key (from `_frames_in_view`). The desktop client
(`cluster/connection.py`, `request_metric`) also sends its own key and matches
the reply by it.

**B. The worker runs its own copy of the formulas.** Metrics run in a worker
process (`WorkerProcessExecutor`, `ffast/metrics/pool.py`). The worker unpickles
a copy of the whole registry at spawn and looks each function up by id in that
copy. The main process plans the run, labels the result and fills the inner
`MetricCache` using its own live registry: `run_plan` in
`ffast/metrics/execution.py` passes `fn` to the transport, and
`WorkerProcessExecutor.run` drops it. After a save, `refresh_registry()` marks
the worker stale, but only in a second step (`_change_metrics` runs the change
and then `_apply_metric_change` in two separate `asyncio.to_thread` calls). A
task in between runs the old formula, and its result is labelled and cached
with the new formula's hash. That poisons both caches. The brief said the inner
cache could not hold a wrong result; it can, in this case.

**C. Parts that are never told about a change.**

- *Prediction fields.* `LoadingCoordinator._extractPredictionFields` copies out
  only the `prediction.info.<key>` and `prediction.atoms.<key>` that some
  registered metric declares at load time. The prediction's loader is then
  discarded. A metric saved later over a new prediction field gets nothing for
  predictions that are already loaded.
- *The inner `MetricCache`* (`ffast/metrics/cache.py`) is never emptied. Its
  `clear()` has no caller. It grows with every dataset, frame range and edit.
- *Transforms.* `_register_compiled` (`ffast/metrics/transforms.py`) copies the
  source metric's shape at registration when the transform says
  `shape = "inherit"`. `UserMetrics._check_dependents` checks only formula
  dependents (`_ExprFn`), so a shape change to a metric that a tab transform
  reads leaves the transform with the old shape.
- *No lock on the registry.* The event loop, the two metric threads and Try's
  threads read it while a change is half done.

## The decision

### 1. Only the server builds cache keys; replies are matched by `request_id`

- `REQUEST_METRIC` loses `key` and gains a required `request_id: str`. The
  server treats it as an opaque label. It never parses it and never stores
  anything under it.
- `METRIC_RESULT` keeps its shape: `args = [request_id, metric_id]`.
- The server always builds the storage key itself. There is then one key per
  result, and it always includes the formula hash.
- The browser makes `request_id` from a counter (`"m1"`, `"m2"`, ...). It still
  merges identical requests that are in flight at the same time, by a label
  built from the request (`JSON.stringify([metricId, sortedParams, modelFp,
  datasetFp])`). On `METRICS_UPDATED` it forgets those labels, so a request made
  after a change never joins one made before it.
- The desktop client makes `request_id` with `uuid.uuid4().hex`. Its own local
  cache key is unchanged; it just no longer travels.

This is a protocol change (ADR 0033). An old desktop client against a new server
gets a validation error on `REQUEST_METRIC`. The browser client ships inside the
server wheel, so it always matches its server.

### 2. Each worker task carries the function it runs

`_ship_to_worker` sends `fn` in the task message. The worker calls the function
it is given and holds no registry. So the code that runs is always the code the
main process planned, labelled and cached with. The stale-worker machinery is
deleted: `WorkerProcessExecutor._registry_bytes`, `_stale`,
`refresh_registry()` (on both executors) and the registry argument of
`_worker_main`.

Every registry function is already picklable, because the whole registry is
pickled today. Built-in functions pickle by reference (module plus name).
Formula and transform functions pickle by value and are small.

### 3. A registry revision; a run that saw a change is discarded and retried

- `MetricRegistry` gets `revision: int`, starting at 0. It goes up by one in
  `register` (inside `metric()`), `unregister` and `restore`.
- `DataService.generateMetric`, when the caller passes no key: read the
  revision, build the key, run, then read the revision again. If it changed, the
  outcome (success or failure) is thrown away, not stored, and the run starts
  again with a fresh key. At most 3 attempts; then it returns `None` and logs a
  warning. On success it returns the key it stored under.
- When the caller passes a key and the revision changed, it does not store the
  result and returns `None`.
- Return type: the stored key (a non-empty `str`) or `None`. Callers that test
  truth keep working.

This replaces a lock. A save never waits for running metrics, and reading a
half-changed registry is harmless, because that run is discarded. The worker
side is already safe from decision 2.

### 4. One change step on the server

`_change_metrics` runs the `UserMetrics` change and `invalidate_metrics` in one
`asyncio.to_thread` call. After decisions 1–3, dropping old results only frees
memory; correctness comes from the formula hash in the key and the revision
check. The docstring of `invalidate_metrics` says so.

Moving this step into a shared store for user tabs and user metrics (review
brief 6) is out of scope. This ADR keeps the change step in one function so
that move can take it whole.

### 5. Every extra prediction field is extracted at load

`_extractPredictionFields` extracts the union of the declared keys and the
source's own `availableFieldKeys()`. These are numeric scalars per frame and
numeric scalars per atom only (`available_field_keys` in `ffast/loaders/ase.py`),
so the memory cost is small: one number per frame or per atom for each key.
Energy, forces and positions are excluded already (reserved, or not scalar).

### 6. The inner `MetricCache` is bounded by memory, oldest dropped first

- `MetricCache(max_bytes=DEFAULT_MAX_BYTES)` with
  `DEFAULT_MAX_BYTES = 1 << 30` (1 GiB).
- The size of an entry is `np.asarray(result.values).nbytes`.
- It is LRU (least recently used): `get` marks an entry as used; `put` adds it,
  then drops the least recently used entries until the total fits. An entry
  larger than `max_bytes` is not stored.
- It is guarded by a `threading.Lock`, because several threads share one cache.
- No setting and no environment variable. The constant can be changed in code.

### 7. A shape change that would break a transform is refused

`UserMetrics._check_dependents` also looks at `_TransformFn` dependents. If the
transform's `transform.shape == "inherit"`, it raises the same message as for a
formula dependent: `This change would give <dep> another shape (<old> → <new>)`.
An inherited unit is not checked. A stale unit only changes an axis label.

## Considered options

- **Keep `key` on the wire as the reply label** (no protocol change). Rejected:
  the name would keep saying "storage key" while meaning "reply label".
- **A reader/writer lock around every metric run, taken exclusively by a
  save.** Rejected: a save would wait for every running metric (up to the
  120 s time limit), atom colouring and Try would each need to take it too, and
  taking it twice in one thread can deadlock.
- **Keep the worker's registry copy and refresh it inside the change.**
  Rejected: a task can still be shipped between the change and the refresh.
  Sending the function removes the copy instead of racing it.
- **Re-read prediction files when a saved metric needs a new field.** Rejected:
  it needs the file path and loader kept around, and a slow re-read for large
  files.
- **Clear the inner cache on each change.** Rejected: it does not bound growth
  from loading new datasets, and it throws away results of metrics that did not
  change.

## What it gives up

- An old desktop client cannot request server metrics from a new server, and a
  new desktop client cannot from an old server.
- A metric whose run overlapped a save is computed again, up to 3 times.
- Two different browser windows asking for the same result at once compute it
  twice, if both miss the cache. This was already the case.
- Each worker task pickles one function. This costs microseconds.
- Prediction loads keep a few more small arrays.
- Not fixed: the stale inherited unit of a transform.

## Rules for building it

These are for whoever implements the steps, including a model working alone.

- **One commit per step**, so each can be reverted alone. Subjects start with
  `fix:`, `feat:` or `docs:`, as each step says. Stage files by name
  (`git add <files>`), never `git add -u` or `-A`. No `Co-Authored-By` trailer
  for a model.
- **Work on a branch in its own worktree**, not in the main folder, where the
  user commits at the same time:
  `git worktree add ../FFAST-metric-keys -b metric-keys main`. In the worktree,
  put `PYTHONPATH=$PWD` in front of every test command, and check once that
  `PYTHONPATH=$PWD ~/.venvs/ffast_env/bin/python -c "import ffast;
  print(ffast.__file__)"` prints a path inside the worktree. Merge to `main`
  only when the user says so.
- **Shell is zsh**: quote globs (`--include='*.py'`).
- **Write the step's tests first**, see them fail, then make them pass.
- **Run tests with the project venv**, not `uv run`:
  - one file: `~/.venvs/ffast_env/bin/python -m pytest <file> -q`
  - quick run before each commit:
    `~/.venvs/ffast_env/bin/python -m pytest -m "not integration" -q` (about 25 s)
  - steps 3 and 6 also need the browser suite:
    `caffeinate -i ~/.venvs/ffast_env/bin/python -m pytest tests/ffast/renderers/web -q`
    (about 7 minutes)
- **Unit tests for browser modules** follow
  `tests/ffast/renderers/web/test_web_pure_helpers.py`: a static file server
  over `ffast/renderers/web/static`, a blank page, `import()` of the module
  inside `page.evaluate`. A fake connection has `send(event, kwargs)` that
  pushes `[event, kwargs]` onto an array, and `on(event, fn)` that stores `fn`.
- **Behaviour does not change** except where this ADR says so. A step that
  seems to need another change stops and asks.
- **Line numbers drift**; find code by function name.

## Steps

### Step 1. Worker runs the function it is sent (`fix:`)

Files: `ffast/metrics/pool.py`, `ffast/metrics/executor.py`,
`ffast/session/server_session.py` (`_apply_metric_change`: delete the
`refresh_registry()` call), `tests/ffast/test_pool.py`,
`tests/ffast/session/test_server_session_user_metrics.py` (its fake executor's
`refresh_registry`).

- `WorkerProcessExecutor.run`: the lambda passes `fn` through to
  `_ship_to_worker(id, schema, fn, resolved, compute_params, parameters)`.
- The task message gets `"fn": fn`. `_worker_main(conn, policy)` calls
  `msg["fn"](**inputs, **compute_params)` and no longer unpickles a registry.
- Delete `_registry_bytes`, `_stale`, `refresh_registry` (both classes) and the
  stale branch of `_get_worker`. Keep `self._registry`; it is still needed for
  planning.
- Tests: replace the three `test_refresh_registry_*` tests with:
  - a metric replaced in the registry after the worker started is run in its
    new form by the next task, with no refresh call;
  - a new metric registered after the worker started runs;
  - replacing a metric while another task runs does not fail that task.

### Step 2. Registry revision and the retry in `generateMetric` (`fix:`)

Files: `ffast/metrics/registry.py`, `ffast/core/data_service.py`,
`ffast/session/server_session.py` (`_frames_in_view`), tests in
`tests/ffast/test_metric_generation_spine.py` and a new
`tests/ffast/test_metric_registry_revision.py`.

- Add `revision` as in decision 3.
- `generateMetric`, in outline:

  ```python
  given_key = key
  for _attempt in range(3):
      revision = default_registry.revision
      key = given_key or self.make_metric_cache_key(metric_id, params, model, dataset)
      ...                                   # existing body up to metricExecutor.run
      result = self.metricExecutor.run(metric_id, source, params or {})
      if default_registry.revision != revision:
          if given_key is not None:
              return None
          logger.info("Metric %s changed while computing; computing again", metric_id)
          continue
      if isinstance(result, MetricResult):
          self.cache[key] = result
          self.eventPush("DATA_UPDATED", key)
          return key
      logger.error("Metric %s failed: %r", metric_id, result)
      return None
  logger.warning("Metric %s kept changing while computing; gave up", metric_id)
  return None
  ```

  The early returns before the run (server fetch, missing predictions) keep
  their behaviour; the server fetch returns `key` instead of `True`.
- `_frames_in_view.value_of`: look the result up under `make_metric_cache_key`;
  on a miss, call `generateMetric(metric_id, params, model, parent)` without a
  key and read the cache under the key it returns.
- Tests:
  - every way of changing the registry raises `revision`;
  - with an injected fake `MetricExecutor` whose first `run` registers and then
    unregisters a throwaway metric in `default_registry`, `generateMetric`
    stores only the second run's result, under the key of the final revision;
  - with a fake that changes the registry on every run, it returns `None`
    after 3 attempts and stores nothing;
  - with a given key and a change during the run, it returns `None` and stores
    nothing.

### Step 3. `request_id` replaces `key` on the wire (`fix:`)

Files: `ffast/protocol/messages.py` (`RequestMetricRequest`),
`ffast/protocol/rpc.py` (`pack_metric_result`: rename the first parameter),
`ffast/session/server_session.py` (route table row for `REQUEST_METRIC`:
`["metric_id", "request_id"]`; `_on_request_metric`), `cluster/connection.py`
(`request_metric`), `ffast/core/connection_manager.py`
(`_fetchMetricResultSync` stops passing `key` to the session; it still caches
locally under `key`), `ffast/renderers/web/static/metrics.js`, `app.js`
(`METRICS_UPDATED` handler calls `this._metricClient?.forgetInFlight()`), the
METRIC_RESULT comment in `protocol.js`. Tests:
`tests/ffast/test_protocol_messages.py`, `tests/ffast/session/test_server_session.py`,
`tests/ffast/renderers/web/test_metrics_pane.py`, and a browser unit test for
`MetricClient`.

- `_on_request_metric(self, metric_id, request_id, **kwargs)`: never takes a
  key. Look up `make_metric_cache_key(...)` in the cache; on a miss, run
  `generateMetric` without a key in the metric pool and read the cache under the
  key it returns. Reply with `pack_metric_result(request_id, metric_id, ...)`.
- `MetricClient`:
  - `_nextId` counter;
  - `_byId: Map<request_id, {resolve, timer}>`;
  - `_inFlight: Map<label, Promise>`;
  - `request()` returns the in-flight promise for an identical label, else
    sends a new request and removes the label when it settles (only if the map
    still holds that same promise);
  - `forgetInFlight()` clears `_inFlight` only; replies still resolve through
    `_byId`;
  - `_onResult` resolves `_byId.get(args[0])`;
  - delete `cacheKey` and the module comment's description of the server key
    format.
- Tests:
  - a `REQUEST_METRIC` with `key` is rejected;
  - the reply echoes `request_id`;
  - a user metric's result is stored under a key that contains `__f`, and
    nothing is stored under the request id;
  - browser unit test: ids are unique; two identical calls send one message;
    after `forgetInFlight()` an identical call sends a new message; each reply
    resolves its own request.

### Step 4. One change step (`fix:`)

Files: `ffast/session/server_session.py` (`_change_metrics`,
`_apply_metric_change`), `ffast/core/data_service.py` (`invalidate_metrics`
docstring), `tests/ffast/session/test_server_session_user_metrics.py`.

- One `asyncio.to_thread` call runs the change and then `invalidate_metrics`.
  Save, delete and add-session all go through it.
- Test: with a fake `UserMetrics` whose change records that it ran, the
  invalidation runs in the same thread call (for example, record
  `threading.get_ident()` in both and compare).

### Step 5. The inner cache is bounded (`fix:`)

Files: `ffast/metrics/cache.py`, a new `tests/ffast/test_metric_cache.py`.

- Implement decision 6 with `collections.OrderedDict`.
- Tests:
  - adding past `max_bytes` drops the oldest entry;
  - `get` makes an entry the newest;
  - an entry larger than `max_bytes` is not stored;
  - `clear()` resets the total.

### Step 6. All prediction fields at load (`fix:`)

Files: `ffast/core/loading_coordinator.py` (`_extractPredictionFields`),
`tests/ffast/test_loading_coordinator.py` or `tests/ffast/test_dataset_fields.py`
(whichever already loads a prediction with extra keys).

- `wanted = declared ∪ source.availableFieldKeys()`, per kind (`info`, `atoms`).
  Keep the early return when both are empty.
- Test: load a prediction whose extxyz has a per-atom key and a per-frame key
  that no registered metric declares; both are in
  `data.predictionFields[(model_fp, dataset_fp)]`.

### Step 7. Shape change refused for an inheriting transform (`fix:`)

Files: `ffast/config/user_metrics.py` (`_check_dependents`),
`tests/ffast/test_user_metrics.py`.

- Import `_TransformFn` from `ffast.metrics.transforms` inside the method, as
  `_ExprFn` is imported now.
- Test: a user metric read by a compiled transform with `shape = "inherit"`;
  saving it with another shape raises `MetricError` naming the transform's id,
  and the registry and the file are unchanged.

### Step 8. Documents (`docs:`)

- This ADR: change the status line to built, and add an "As built" section
  listing the commits and any change from the plan.
- `docs/adr/README.md`: in the row under "Metrics and analysis", change
  "(accepted)" to "(built)".
- `CONTEXT.md`, the "Cache Key" entry: the key is built only by the server and
  no longer crosses the RPC boundary; replies are matched by `request_id`.
- `docs/research/architecture-review-2026-10-14/04-metric-change-and-result-keys.md`:
  status line `decided, see ADR 0061`.

## As built

Branch `metric-keys`, one commit per step, no changes from the plan except where
noted.

| Step | Commit |
|---|---|
| 1. Worker runs the function it is sent | 6543ce9 |
| 2. Registry revision, retry in `generateMetric` | 668fd25 |
| 3. `request_id` replaces `key` on the wire | 8760b44, 4fd33fe (real `DataService` test) |
| 4. One change step | ec5afe5 |
| 5. Bounded inner cache | e64a7cc |
| 6. All prediction fields at load | cb422cd |
| 7. Shape change refused for an inheriting transform | 1c862fc |
| 8. Documents | CONTEXT.md in the last `metric-keys` commit; this ADR, the README row and the review brief are edited in the main folder, uncommitted |

Differences from the plan:

- Step 2: `generateMetric` is split in two. The retry loop stays in
  `generateMetric`; one attempt is `_generate_metric_once`, which returns the key,
  `None`, or a marker meaning "the table changed".
- Step 3: the session test with a fake data service checks the wire (reply echoes
  `request_id`, a `key` is rejected). A second test, with the real `DataService`,
  real cache and a real formula metric, checks that the stored key contains `__f`,
  that nothing is stored under the request id, and that an edit gives a new key.
- Step 4: `_change_and_invalidate` runs both parts; its test counts `to_thread`
  calls.

Two fixes found when the build was reviewed, after step 8:

- 3fb87e4: a task whose function cannot be pickled returns a `MetricFailure`
  for that metric instead of raising out of `MetricExecutor.run`. Before step 1
  such a function broke the whole pool at start; now it would have escaped as an
  exception from one run.
- b6d23d1: the revision is checked again after the result is stored. A change
  that landed between the first check and the store had already dropped old
  results, so the stored one stayed for ever under the old formula's key (never
  shown, only wasted memory). It is now deleted and the run starts again.

# One metric result store on the server

Status: Accepted 2026-11-04. Built 2026-11-04 (5598145..16310b7, with review
fixes 9807e3a, 32b8d5c, 16310b7). Checked in a real browser by a script on
`examples/data` (each metric step computed once on Basic Errors; the Zero
Model gives one "Load prediction…" pop-up that opens the browser on the
dataset); not yet tried by a person. Plan:
`docs/implementation/2026-11-04-metric-result-store.md`.

## The problem

"Give me this metric over the whole dataset, computing it if it is not
stored" is written out four times in `ffast/session/server_session.py` (a
plot's `REQUEST_METRIC`, colouring later, scene colouring, a subset following a
plot's zoom), and Try computes the saved metrics a draft formula reads a fifth
way. The copies disagree:

- Only two avoid computing the same result twice at the same time.
- Only two say why a metric failed.
- A metric that needs a prediction nobody has made gets three answers: refused
  at once (only when no model is chosen), nothing, or a whole-dataset
  prediction started quietly with a reply that says "failed" and no reason.
- Every result is kept twice: in `env.cache`, which has no size limit, and in
  the executor's `MetricCache`, which has one and writes big results to disk.
  Both hold the same object, so when `MetricCache` drops a 4 GB result to save
  memory, `env.cache` still holds it. The memory limit frees nothing.
- A failure's reason is kept until it is read once; the next request computes
  the metric again and fails again.

Found by the architecture review of 3 November 2026
(`docs/research/architecture-review-2026-11-03/`, problem 1).

## The decision

1. **One metric result store** (`MetricResultStore`,
   `ffast/metrics/result_store.py`), built once per server as `env.results`.
   It answers "this metric, these parameters, this model, this dataset" with a
   metric result or a reason why there is none. It has two calls:
   - `peek(...)`: the stored result, or nothing. Never computes. Safe from any
     thread. Scene building uses it.
   - `await get(...)`: the result, computing it if needed, or a reason. One
     computation per result however many ask at the same time.

   Plots, colouring later, a subset following a plot's zoom, and Try's
   references to saved metrics all go through it. Try's own draft result is
   never stored.
2. **A missing prediction is refused, never started.** When a metric needs a
   prediction the (dataset, model) pair does not have, the store answers
   "needs a prediction" at once. It never starts a whole-dataset prediction.
   Making a prediction stays a deliberate user action, as atom colouring
   already did.
3. **A reason has a kind**, not only text, so the page can act on it:
   `needs_prediction`, `failed` (the metric ran and raised, or a dependency
   did), `missing_input` (the data has no such field), `temporary` (stopped,
   worker died, could not be sent, no memory freed in time), `object_gone`
   (the dataset or model was deleted). `METRIC_RESULT` carries the kind next
   to the existing `error` text.
4. **The page offers to load a prediction.** A `needs_prediction` reply for a
   chosen model shows an ADR 0066 pop-up with a "Load prediction…" button,
   once per (dataset, model) per window. The button opens the file browser in
   prediction mode with that dataset chosen. The panel says "needs a
   prediction" in place. With no model chosen, the panel keeps today's "Select
   a prediction" and no pop-up.
5. **One cache for metric results.** Results leave `env.cache`. They live only
   in the executor's bounded `MetricCache`; the store keeps a small index from
   the server's key (ADR 0061) to the cache's own key. A result the cache
   dropped is computed again, or read back from disk if it was written there.
6. **A permanent failure is remembered like a result**: `failed` and
   `missing_input`, under the same key, until the dataset or model is deleted,
   a subset's frames change, or the metric is edited (which gives a new key
   anyway). `temporary`, `needs_prediction` and `object_gone` are never
   remembered; they are checked again on every request.
7. **A computation stops when its last waiter leaves.** When every request
   waiting for a result is gone (its panel or window closed), the store asks
   the computation to stop. Asking again starts it over. While it runs it is a
   Running Task in every window's Tasks list.
8. **The desktop client and the command line are not changed.** The step
   "compute one metric, give the result or the failure, store nothing" is split
   out of `DataService.generateMetric`. The store uses that step;
   `generateMetric` keeps its other jobs (fetch from the server, queue a
   prediction, store in `env.cache`) for the desktop and the CLI, and goes
   when the desktop goes.
9. **Nothing else stores or names metric results on the server.** The four
   copies, `_COMPUTING`, `_compute_once`, the module-level compute pool and
   `DataService.metric_failures` are deleted in the same change. A test fails
   if server code calls `generateMetric` or reads `metric_failures` again.

## Alternatives

- **Start the prediction and wait for it** (decision 2). A plot click could
  start hours of GPU work on a large dataset.
- **Start it and send the result later.** Needs a new "result later" message,
  and still starts heavy work the user did not ask for.
- **A blocking dialog instead of a pop-up** (decision 4). The reply arrives
  without the user asking (opening a tab, restoring a session), two datasets
  give two dialogs in a row, and they come back on every reload. A dialog
  would fit only a click whose sole purpose needs the prediction; it can be
  added for that case later.
- **Text-only reasons** (decision 3). The page would have to match wording,
  which breaks silently when the wording changes.
- **Keep two caches behind the store** (decision 5). The memory limit would
  still free nothing.
- **Never remember failures** (decision 6). On 1.77M structures a failure that
  comes after reading every input is repeated on every redraw and window.
- **Always finish a computation** (decision 7). Switching through five
  datasets would queue five whole-dataset computations, each holding memory
  and one of the compute threads.
- **The desktop uses the store too** (decision 8). Needs a second way to
  compute ("ask the server") inside the store and a rewrite of the desktop's
  `DataWatcher`, for a client being retired.

## Consequences

- A result that `MetricCache` dropped is no longer kept in `env.cache`, so it
  may be computed again (or read back from disk). This is the memory limit
  working.
- The server and the desktop compute metrics through two paths until the
  desktop is removed. Both share the compute step, so the rules about inputs
  and failures stay the same.
- `MetricFailure` gains a `kind`. A worker builds failures too, so the field
  has a default and stays picklable.
- Tests stop faking `metric_failures` and the server's key layout; they fake
  the store's four inputs instead.

## Not now

- A "Run model on this dataset" button next to "Load prediction…".
- A blocking dialog when the user's own click asked for a prediction metric.
- Cancel from the Tasks list stopping a metric computation.

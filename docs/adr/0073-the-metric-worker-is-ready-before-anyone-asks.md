# The metric worker is ready before anyone asks

Status: Accepted 2026-10-30. Builds on ADR 0072 (the timing log that found
this).

## The problem

Opening the "Basic Errors" tab on the example data took about 1.1 s. The
timing log showed why: the tab sends 14 REQUEST_METRIC messages. The first one
took about 730 ms on the server; the other 13 took 1–4 ms each, but waited
about 740 ms behind it, because the server reads one message at a time.

Measured inside the server:

- The metric worker (the one separate process that runs metric code) started
  inside that first request: about 200 ms.
- The first density panel then imported `scipy.stats` inside the worker:
  about 500 ms. The KDE itself took under 1 ms.
- The worker exits after 100 tasks (`max_tasks_per_worker`), and its
  replacement started inside the next task. One tab open is about 21 worker
  tasks, so about every fifth tab open, energy shift or new subset paid the
  ~700 ms again. A loop of 205 calls showed 660–710 ms at calls 0, 100 and
  200, and 1.9 ms for the rest.

On a cluster, imports read from a shared file system and are likely slower.
That part is an assumption until the cluster timing log confirms it.

## The decision

1. **The worker imports `scipy.stats` and the built-in metrics as it boots**,
   before it tells the server it is ready (`_PRELOAD` in
   `ffast/metrics/pool.py`). An import that fails is left for the metric that
   needs it to report.
2. **The server starts the worker when the server starts**
   (`MetricExecutor.start()`, called in `server.py`). It returns without
   waiting; the worker boots while the user loads data.
3. **A retiring worker keeps serving until its replacement is ready.** After
   100 tasks the server boots a replacement. Each task checks, without
   waiting, whether the replacement has said it is ready; once it has, the
   old worker is told to exit and the replacement takes over. The worker no
   longer exits by itself after 100 tasks.

Result on the example data: "Basic Errors" opens in about 340 ms instead of
1.1 s, and the server answers all 14 metrics within 30 ms of the first one
arriving.

## Alternatives

- **Handle read-only messages at the same time** (the plan before measuring).
  Tried: the click took 1.18 s instead of 1.10 s. Every metric still runs on
  the one worker, one task at a time, so the wait moved from "waiting to be
  read" to "waiting for the worker". Worth revisiting only if a cluster log
  shows messages waiting behind work that does not use the worker.
- **Start the worker when the first dataset loads.** No idle process before
  there is data, but a tab opened right after loading could still wait.
- **Only start the replacement early, and switch at once.** Tried first: the
  next task arrived 2 ms later and waited ~660 ms for the replacement to boot.
  Inside one tab open that is the usual case.
- **Raise the 100-task limit.** Restarts become rarer, but each one still
  makes a click wait.

## Consequences

- The server keeps one worker process (with scipy loaded) running from the
  start, even if no metric is ever asked for.
- For a few hundred milliseconds after each retirement two workers exist.
- A metric module that needs another slow import pays it on first use, as
  before, until it is added to `_PRELOAD`.
- The message-at-a-time reading in `server.py` is unchanged.

# FFAST's own metric code runs in the server; outside code runs in workers

Status: Accepted 2026-11-02. Reverses part of the Metric Worker Pool rule
(CONTEXT.md; ADR 0046: "metric code never runs in the long-lived server").
Changes ADR 0073: the server starts a worker only when there is outside code.
Plan: `docs/implementation/2026-11-02-basic-errors-under-a-minute.md`.

## The problem

MeluXina, 2026-11-02, `shard_03_pred.extxyz` (1,773,605 structures, ~530M
force components, prediction SO3LR). Opening "Basic Errors" took **3 min 15 s**
until the last panel was on screen (run 9, job 5414321). The goal is under a
minute.

Where the time went (handoff `docs/handoffs/2026-11-02-basic-errors-under-a-minute.md`):

- Every metric step from every request goes through **one** worker process,
  one step at a time (`WorkerProcessExecutor._lock`). The 14 requests of the
  tab queue behind each other; the first ones waited ~31 s before starting.
- Each step copies its inputs into shared memory first: 7.5 s for one force
  metric (4 GB of force arrays).
- `force_parity_density` alone took 34.8 s on one core of a 256-core node.

## The decision

1. **Code FFAST ships runs in the server process; outside code runs in a
   worker.** FFAST's code means every function whose module is `ffast` or
   under it: built-in metrics, transform and keep-box metrics, and every
   formula (Expression metric) whatever its origin, because FFAST's own
   evaluator runs a formula. **Outside code** is Python that did not ship with
   FFAST: a project's metric module, a plugin outside the `ffast` package. The
   Try button follows the same rule.
2. **Several steps run at the same time, as memory allows.** Before an
   in-server step starts, it reserves an estimate of the memory it will need
   (its `memory_mb` hint, else twice the size of its array inputs). Steps start
   while the reservations fit under 90% of the memory available when the
   first of them started. A step that does not fit **waits**; it is never
   refused. A step bigger than the whole budget runs alone. Steps on outside
   code reserve memory the same way, including their shared-memory copy.
   At most 16 steps run at once.
3. **No time limit for in-server steps; they stop when their answer is no
   longer wanted.** A step stops at its next checkpoint once the dataset or
   prediction it reads has been deleted. Checkpoints sit between the steps of
   a metric and between the chunks of a chunked built-in. Leaving a page does
   not stop a step: its result still goes to the cache (ADR 0077).
4. **One shared chunk pool per server.** Chunked built-ins (the force parity
   density and its worst dots first) split their work over one thread pool
   sized to the cores the job may use, at most 32. Step threads never run
   inside the chunk pool, so a step waiting for its chunks cannot block them.
   BLAS/OpenMP libraries are held to one thread in the server, so they do not
   add threads on top.
5. **Outside code gets a small pool of workers that grows and shrinks.** At
   start, one worker boots only if the registry holds outside code. When every
   worker is busy, another boots, up to `max(2, min(8, cores // 4))`. Extra
   workers idle for 5 minutes exit; one stays. Each worker keeps the boot
   handshake, retirement after 100 tasks, the hard time limit and crash
   replacement.

## Why

- A worker protects against a crash, a hang or an endless loop. FFAST's own
  code and formulas have none of those risks that a test would not catch.
- A worker gave little protection against running out of memory: the server
  already holds the dataset and a cache of a quarter of the machine's memory,
  so it is the process the system kills first. And the worker adds memory
  (the shared-memory copy).
- Admission by memory budget is how Dask and Ray run many tasks on one
  machine; one shared pool sized to the cores is how Intel TBB, Java's
  ForkJoinPool and Rayon share them. Checkpoints that stop work when it is no
  longer wanted are how databases cancel a running query.

## Consequences and risks

- **A built-in or formula that runs out of memory now ends the server**, not
  a worker. The page shows "connection lost" and reconnects; loaded data must
  be loaded again. On a 16 GB laptop with a 4 GB force array the memory budget
  runs steps about one at a time, as before; it is never worse than today,
  but it is not a guarantee.
- **A slow in-server step cannot be killed.** It runs to the end unless its
  data is deleted. One NumPy operation (e.g. a 4 GB subtraction) cannot stop
  midway; it stops right after.
- **"Custom metric" no longer says where a metric runs.** Use **outside code**
  (CONTEXT.md) for "needs a worker".
- The server log says, for each step, how long it waited for memory and how
  long it computed, so a slow step stays visible.
- A session with no outside code never starts a worker process.

## Considered and rejected

- **All non-built-in metrics in workers** (by origin, not by kind of code).
  A user formula over 4 GB of forces would pay the shared-memory copy and wait
  for a free worker, for no safety gain.
- **Refuse a step that may not fit in memory.** The estimate is a guess per
  metric, so it would refuse steps that fit. Waiting makes a bad guess cost
  order, not an answer.
- **Fall back to a worker for a step that may not fit.** It dies just the
  same and needs more memory.
- **A 300 s soft limit with a failure shown while the work goes on.** The page
  would say "failed" for an answer that is still coming.
- **A Cancel button on a progress row.** One computation serves every page
  that asked for it (ADR 0077), and one tab starts ~10 metrics. Who may cancel
  shared work is a separate decision; add it when users ask.
- **numexpr or numba for multi-core array maths.** New dependencies, and no
  clean help for `bincount`. Worth measuring if formulas turn out slow.
- **Start metrics as soon as a prediction loads (prefetch).** Only if this
  ADR still leaves Basic Errors over a minute.

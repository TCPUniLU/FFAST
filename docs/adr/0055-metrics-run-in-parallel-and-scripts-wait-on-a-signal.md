Status: Accepted

# Metrics run in parallel, and scripts wait on a signal

Two seams move here, found while chasing one symptom:
`examples/headless/headless.py` printed nothing and took twelve seconds to do it.

## What was wrong

`WorkerProcessExecutor` held **one** worker and **one** duplex pipe, with no
serialisation. The TaskManager drives it from several threads at once —
`newTask(..., threaded=True)` runs each task through `asyncio.to_thread`
(`ffast/core/tasks.py`) — so those threads shared the pipe. Once the worker was
warm they crossed wires:

- `_await_ready` had every thread poll for a `_READY` sentinel of which exactly
  one is ever sent. A late thread instead received another thread's *result*,
  concluded "worker failed to start", and terminated the worker — taking down
  everyone else with `EOFError` ("died unexpectedly").
- The reply loop kept only messages matching its own `task_id` and **discarded**
  the rest, so a thread could throw away the answer another thread was waiting
  for. The robbed thread then waited out the hard time limit.

From cold the bug hid: each thread raced to spawn its own worker, so the pipes
never crossed — and the extra workers leaked, since `shutdown()` only knew about
the last one assigned.

Separately, `Environment.waitForTasks` polled: check three counters, else
`time.sleep(dt)` with `dt=5`. The headless example spent ten of its twelve
seconds asleep in front of finished work — 0.16 s of loading and 1.6 s of
metrics.

## What we picked

**A pool of workers, each checked out for the whole exchange.** `_checkout`
takes a warm idle worker or spawns one, bounded by a semaphore at
`PoolPolicy.max_workers`; `_checkin` parks it again, or retires it if it
crashed, timed out, or hit `max_tasks_per_worker`. A worker's pipe is therefore
owned by exactly one thread at a time, and N metrics genuinely run at once.

A global lock was tried first and rejected: it fixed the corruption by
serialising every metric, which is the opposite of what the workload wants.

Default size is `len(os.sched_getaffinity(0)) - 2` where the platform exposes
affinity — `os.cpu_count()` reports the machine rather than the job's
allocation, which oversubscribes a batch node.

**A gate that waits on a signal.** `waitForTasks` blocks on `WorkGate`
(`ffast/core/work_gate.py`), a condition variable woken by `TASK_DONE` and by
the headless loop whenever an iteration ends with nothing pending. A 1 s
watchdog re-checks for completion paths that signal nothing at all.

`dt` is gone from the signature. A caller-supplied poll interval was never the
right shape: the fallback belongs to the gate, not to every call site.

**Giving up is measured in progress, not seconds.** `waitForTasks(
stall_timeout_s=...)` raises `TimeoutError` naming the outstanding work once the
work fingerprint — queued count, running task IDs with their progress and
message, generation queue size — has held still that long. An absolute deadline
cannot fit both a 100-frame and a million-frame dataset; "stopped moving" can.
Off by default.

To make that rule mean something locally, `LoadingCoordinator._progress` (until
now only used by remote loads) narrates the local load, and
`ffast.io.xyz.read_ase_or_explain` streams frames with `iread` and reports a
running count when handed a reporter. Without a reporter the read is unchanged.

## What we gave up

- **A metric is still opaque while it computes.** Nothing publishes progress
  between a metric task starting and its result arriving, so a single metric
  slower than the stall window reads as stalled. The window must exceed the
  slowest expected metric; a genuinely stuck computation is bounded instead by
  `PoolPolicy.max_runtime_s`.
- **`max_workers` is not yet configuration.** It is a policy field with a
  sensible default; nothing plumbs it through user config.
- **The waiter is woken by the loop, not by `TASK_DONE`.** Subscribing to that
  event looks like the obvious wiring and does not work: every `EventClass`
  drains its own queue, and `headlessEventLoop` drains the environment's before
  the TaskManager's, so the finished task is still in `runningTasks` when the
  handler runs and the waiter goes straight back to sleep. The subscription was
  written, measured as dead weight, and deleted; the loop signals once an
  iteration ends with nothing outstanding, which also covers completion paths
  that raise no event at all. The cost is at most one loop iteration (0.1 s) at
  the end of a wait — unmeasurable against a 3.6 s run, and cheaper than
  reordering an event bus every listener shares.
- **Scripts must guard `__main__`.** Spawned workers re-import the main module,
  so a script with work at module level cannot start a worker at all. The
  example and the documented snippet in `docs/usage.md` now carry the guard.

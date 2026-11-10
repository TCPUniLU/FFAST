Status: Proposed

# Scripts wait for an idle loop, not a sleep

## The problem

`HeadlessEnvironment.waitForTasks` checked three counters (task queue, running
tasks, generation queue). If work remained it called `time.sleep(dt)`, with
`dt=5` by default. A script waited up to 5 s after its work had finished.

Measured 2026-11-10 on a Mac, `examples/headless/headless.py`: **11.3 s wall
time, 0.9 s CPU.** About 10 s was sleeping (two waits of 5 s). `ffast-cli`
passed `dt=0.5` and lost up to 0.5 s per wait.

A second, hidden problem: the check ignored the event queues. A task can
finish, leave `runningTasks`, and push an event whose handler queues a
follow-up task. For a moment nothing looks pending. The 5 s sleep mostly hid
this. A fast waiter would return too early.

## Decision

1. **The loop decides when work is done, not the waiter.** At the end of each
   iteration of `headlessEventLoop`, the loop checks that everything is empty:
   the task queue, `runningTasks`, `data.generationQueue`, and the
   `eventQueue` of every `EventClass` the loop drains. If so, the iteration is
   marked *idle* and waiters wake.
2. **A waiter only trusts an idle iteration that started after it began
   waiting.** The loop counts iterations. `waitForTasks` records the count on
   entry and returns at the first idle iteration with a higher number. This
   closes the race above and the gap inside `handleTaskQueue` (between
   `q.get()` and `newTask`).
3. **`WorkGate`** (`ffast/core/work_gate.py`) holds this logic. It has no FFAST
   imports and is built on `threading.Condition`.
4. **A 1 s watchdog stays,** only to reprint progress when `verbose=True` and
   to notice `quitReady`. It never decides that work is done.
5. **`headlessQuit()` wakes waiters;** `waitForTasks` returns once `quitReady`
   is set, as before.
6. **`dt` is still accepted but ignored,** with a `DeprecationWarning`. The
   three `ffast-cli` calls stop passing it.
7. **Stall timeout is opt-in:** `waitForTasks(stall_timeout_s=None)`. When set,
   it raises `TimeoutError` naming the outstanding work once the work
   fingerprint (queue size, each running task's id, progress and message,
   generation queue size) has not changed for that long. Default off.
8. **Not in scope:** the worker pool, `__main__` guards, and the server's
   behaviour. The server never calls `waitForTasks`; it only gets one cheap
   idle check per loop iteration (`len()`/`qsize()` only, no copies).

## What the August work on `2ffast` taught us

Subscribing the waiter to `TASK_DONE` does not work. Every `EventClass` drains
its own queue, and the loop drains the environment's before the TaskManager's,
so the finished task is still in `runningTasks` when the handler runs and the
waiter goes back to sleep. The loop must signal after both queues are drained.

## Consequences

- A wait ends at most about two loop iterations (~0.2 s) after work settles:
  a task can finish during the loop's 0.1 s pause, and the next iteration
  must first handle its `TASK_DONE` before it can report idle.
- `dt` has no effect. Old scripts keep working but get a warning.
- The idle check runs every loop iteration, also on the server.

## Timing

`examples/headless/headless.py`, `/usr/bin/time -p`, same Mac, 2026-11-10:

| | wall time |
|---|---|
| Before | 11.3 s (0.9 s CPU) |
| After, run 1 | 1.07 s |
| After, run 2 | 1.06 s |
| After, run 3 | 1.12 s |

The example prints the same results as before. `ffast-cli metrics run`
prints the same value (energy MAE 0.0218) in about 1.2 s.

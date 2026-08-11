"""Environment.waitForTasks must return when the work finishes.

It used to sleep a fixed interval between checks, so a script paid the full
interval no matter how quickly the task completed.
"""

from __future__ import annotations

import asyncio
import threading
import time

from ffast.core.environment import startHeadlessEnvironment


def _ensure_event_loop():
    """Other suite tests may close the process-global loop; the TaskManager
    grabs asyncio.get_event_loop() at construction and raises without one."""
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def test_wait_for_tasks_returns_when_the_task_finishes():
    _ensure_event_loop()
    env = startHeadlessEnvironment()
    try:
        def quick_task(taskID=None):
            time.sleep(0.1)

        # Scripts submit through the queue; the headless loop thread drains it.
        env.tm.queueTask(quick_task, name="quick", threaded=True)

        start = time.monotonic()
        env.waitForTasks()
        elapsed = time.monotonic() - start

        # The task takes 0.1s; the old fixed-sleep loop always cost 5s.
        assert elapsed < 1.0, f"waitForTasks took {elapsed:.2f}s for a 0.1s task"
    finally:
        env.headlessQuit()


def test_wait_for_tasks_gives_up_on_a_stuck_task_and_names_it():
    """A task that never finishes must fail the script, not hang it."""
    _ensure_event_loop()
    env = startHeadlessEnvironment()
    try:
        stop = threading.Event()

        def stuck_task(taskID=None):
            stop.wait(30)  # Never completes within the stall window

        env.tm.queueTask(stuck_task, name="stuck_load", threaded=True)

        start = time.monotonic()
        try:
            env.waitForTasks(stall_timeout_s=0.5)
        except TimeoutError as exc:
            assert "stuck_load" in str(exc), f"outstanding work not named: {exc}"
        else:
            raise AssertionError("expected TimeoutError for a task that never finishes")
        finally:
            stop.set()

        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"took {elapsed:.2f}s to give up"
    finally:
        env.headlessQuit()


def test_work_fingerprint_survives_mixed_task_id_types():
    """Remote tasks carry string IDs (registerPhantomTask), local ones ints.

    Sorting the two together raised TypeError, killing any stall-guarded wait
    that had a load and a metric in flight.
    """
    _ensure_event_loop()
    env = startHeadlessEnvironment()
    try:
        env.tm.runningTasks[7] = {"progress": None, "progressMessage": "N/A", "name": "load"}
        env.tm.runningTasks["remote_1"] = {"progress": 0.5, "progressMessage": "…", "name": "remote"}

        env._workFingerprint()          # Must not raise
        assert "remote" in env._describePendingWork()
    finally:
        env.tm.runningTasks.clear()
        env.headlessQuit()

"""HeadlessEnvironment.waitForTasks waits on the loop's idle signal (ADR 0094)."""
from __future__ import annotations

import asyncio
import threading
import time
import warnings

import pytest

from ffast.core.environment import startHeadlessEnvironment


@pytest.fixture
def env():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    env = startHeadlessEnvironment()
    # Let the loop finish its first start-up iterations.
    env.waitForTasks()
    yield env
    env.headlessQuit()
    env.join(5)


def _sleeper(seconds, done_flag=None):
    def work(taskID=None):
        time.sleep(seconds)
        if done_flag is not None:
            done_flag.set()
    return work


def test_a_short_task_is_waited_for_without_a_poll_delay(env):
    done = threading.Event()
    env.tm.queueTask(_sleeper(0.3, done), threaded=True, name="short")
    t0 = time.monotonic()
    env.waitForTasks()
    assert done.is_set()
    assert time.monotonic() - t0 < 1.0


def test_a_task_started_by_an_event_of_the_first_is_waited_for(env):
    """The first task finishes and leaves runningTasks; its TASK_DONE event is
    still queued, and its handler queues the second task."""
    second_done = threading.Event()
    first_id = {}

    def first(taskID=None):
        first_id["id"] = taskID
        time.sleep(0.05)

    def on_done(taskID):
        if taskID == first_id.get("id"):
            env.tm.queueTask(_sleeper(0.4, second_done), threaded=True, name="second")

    env.eventSubscribe("TASK_DONE", on_done)
    env.tm.queueTask(first, threaded=True, name="first")
    env.waitForTasks()
    assert second_done.is_set()


def test_quit_from_another_thread_unblocks_a_waiting_call(env):
    release = threading.Event()

    def stuck(taskID=None):
        release.wait(10)

    env.tm.queueTask(stuck, threaded=True, name="stuck")
    returned = threading.Event()

    def waiter():
        env.waitForTasks()
        returned.set()

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    assert not returned.wait(0.5)
    t0 = time.monotonic()
    env.headlessQuit()
    try:
        assert returned.wait(2)
        assert time.monotonic() - t0 < 1.0
    finally:
        release.set()
        t.join(2)


def test_dt_warns_and_does_not_slow_the_wait(env):
    done = threading.Event()
    env.tm.queueTask(_sleeper(0.2, done), threaded=True, name="short")
    t0 = time.monotonic()
    with pytest.warns(DeprecationWarning, match="dt"):
        env.waitForTasks(dt=5)
    assert done.is_set()
    assert time.monotonic() - t0 < 2.0


def test_no_warning_without_dt(env):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        env.waitForTasks()


def test_stall_timeout_names_a_task_that_never_reports_progress(env):
    release = threading.Event()

    def stuck(taskID=None):
        release.wait(10)

    env.tm.queueTask(stuck, threaded=True, name="never-ending-task")
    try:
        with pytest.raises(TimeoutError, match="never-ending-task"):
            env.waitForTasks(stall_timeout_s=0.5)
    finally:
        release.set()


def test_a_quick_task_is_noticed_without_waiting_for_the_next_turn(env):
    """New work and a finished task wake the loop; before, each turn slept
    0.1 s, so a 10 ms task took 0.1-0.2 s to be waited for."""
    times = []
    for _ in range(5):
        t0 = time.monotonic()
        env.tm.queueTask(_sleeper(0.01), threaded=True, name="quick")
        env.waitForTasks()
        times.append(time.monotonic() - t0)
    assert sorted(times)[2] < 0.06, times


def test_an_idle_loop_does_not_spin(env):
    """With nothing to do the loop still pauses 0.1 s per turn."""
    start = env._workGate._iteration
    time.sleep(0.5)
    assert env._workGate._iteration - start <= 8

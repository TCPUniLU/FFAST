"""WorkGate: waits for an idle loop iteration that started after the wait began."""

import threading
import time

import pytest

from ffast.core.work_gate import WorkGate


def _in_thread(fn):
    t = threading.Thread(target=fn, daemon=True)
    t.start()
    return t


def _never():
    return False


def test_returns_soon_after_an_idle_mark_from_another_thread():
    gate = WorkGate()
    entered = threading.Event()
    done = threading.Event()

    def waiter():
        entered.set()
        gate.wait(should_stop=_never)
        done.set()

    _in_thread(waiter)
    assert entered.wait(2)
    # Keep marking (as the loop does) until the waiter returns.
    t0 = time.monotonic()
    while not done.is_set() and time.monotonic() - t0 < 2:
        gate.mark_iteration(True)
        done.wait(0.02)
    assert done.is_set()
    assert time.monotonic() - t0 < 0.2


def test_ignores_an_idle_mark_from_before_the_wait():
    gate = WorkGate()
    gate.mark_iteration(True)
    done = threading.Event()

    def waiter():
        gate.wait(should_stop=_never, watchdog_s=0.05)
        done.set()

    _in_thread(waiter)
    assert not done.wait(0.3)
    gate.mark_iteration(True)
    assert done.wait(1)


def test_does_not_return_on_busy_marks():
    gate = WorkGate()
    done = threading.Event()

    def waiter():
        gate.wait(should_stop=_never, watchdog_s=0.05)
        done.set()

    _in_thread(waiter)
    for _ in range(5):
        gate.mark_iteration(False)
        assert not done.wait(0.05)
    gate.mark_iteration(True)
    assert done.wait(1)


def test_returns_when_should_stop_becomes_true_with_no_marks():
    gate = WorkGate()
    stop = threading.Event()
    done = threading.Event()

    def waiter():
        gate.wait(should_stop=stop.is_set, watchdog_s=0.05)
        done.set()

    _in_thread(waiter)
    assert not done.wait(0.2)
    stop.set()
    assert done.wait(1)


def test_wake_makes_a_waiter_recheck_should_stop_at_once():
    gate = WorkGate()
    stop = threading.Event()
    done = threading.Event()

    def waiter():
        gate.wait(should_stop=stop.is_set, watchdog_s=30)
        done.set()

    _in_thread(waiter)
    assert not done.wait(0.1)
    stop.set()
    gate.wake()
    assert done.wait(1)


def test_on_tick_runs_every_watchdog_interval():
    gate = WorkGate()
    stop = threading.Event()
    ticks = []

    def waiter():
        gate.wait(should_stop=stop.is_set, on_tick=lambda: ticks.append(1),
                  watchdog_s=0.05)

    t = _in_thread(waiter)
    deadline = time.monotonic() + 2
    while len(ticks) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    t.join(1)
    assert len(ticks) >= 3


def test_stall_raises_with_the_description_when_nothing_changes():
    gate = WorkGate()
    with pytest.raises(TimeoutError, match="task X stuck"):
        gate.wait(should_stop=_never, stall_timeout_s=0.2,
                  fingerprint=lambda: "same", describe=lambda: "task X stuck",
                  watchdog_s=0.05)


def test_no_stall_while_the_fingerprint_keeps_changing():
    gate = WorkGate()
    counter = {"n": 0}

    def fp():
        counter["n"] += 1
        return counter["n"]

    done = threading.Event()
    errors = []

    def waiter():
        try:
            gate.wait(should_stop=_never, stall_timeout_s=0.2, fingerprint=fp,
                      describe=lambda: "x", watchdog_s=0.05)
        except TimeoutError as e:  # pragma: no cover
            errors.append(e)
        done.set()

    _in_thread(waiter)
    assert not done.wait(0.6)  # outlives the stall window without raising
    gate.mark_iteration(True)
    assert done.wait(1)
    assert errors == []


def test_stall_timeout_without_a_fingerprint_is_a_value_error():
    gate = WorkGate()
    with pytest.raises(ValueError):
        gate.wait(should_stop=_never, stall_timeout_s=1.0)

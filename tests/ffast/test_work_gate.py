"""Tests for the WorkGate: block until queued work has settled.

Scripted callers (``Environment.waitForTasks``) used to poll on a fixed sleep,
so a script paid the full interval even when the work finished immediately.
The gate waits on a signal instead, with a watchdog for completion paths that
never signal.
"""

from __future__ import annotations

import threading
import time

from ffast.core.work_gate import WorkGate


def test_wait_returns_as_soon_as_work_settles():
    """The wait ends on the signal, not on the watchdog tick."""
    pending = {"n": 1}
    gate = WorkGate(lambda: pending["n"] > 0, watchdog_s=5.0)

    def finish():
        time.sleep(0.05)
        pending["n"] = 0
        gate.notify()

    threading.Thread(target=finish, daemon=True).start()

    start = time.monotonic()
    gate.wait()
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, f"waited {elapsed:.2f}s — polled instead of waiting on the signal"


def test_wait_returns_without_a_signal_after_the_watchdog():
    """A completion path that never signals costs one tick, not a hang."""
    pending = {"n": 1}
    gate = WorkGate(lambda: pending["n"] > 0, watchdog_s=0.1)

    def finish_silently():
        time.sleep(0.05)
        pending["n"] = 0  # No gate.notify() — the missed-signal case

    threading.Thread(target=finish_silently, daemon=True).start()

    start = time.monotonic()
    gate.wait()
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, f"waited {elapsed:.2f}s — watchdog did not re-check"


def test_wait_returns_immediately_when_nothing_is_pending():
    gate = WorkGate(lambda: False, watchdog_s=5.0)
    start = time.monotonic()
    gate.wait()
    assert time.monotonic() - start < 0.05


def test_on_tick_fires_while_work_is_outstanding_and_not_after():
    """Verbose progress printing hangs off the tick, so it must fire per wake-up."""
    pending = {"n": 1}
    gate = WorkGate(lambda: pending["n"] > 0, watchdog_s=0.05)
    ticks = []

    def finish():
        time.sleep(0.2)
        pending["n"] = 0
        gate.notify()

    threading.Thread(target=finish, daemon=True).start()
    gate.wait(on_tick=lambda: ticks.append(time.monotonic()))

    assert len(ticks) >= 2, f"expected repeated ticks while waiting, got {len(ticks)}"

    settled = time.monotonic()
    assert all(t <= settled for t in ticks)

    # Nothing pending: no tick at all.
    quiet = []
    WorkGate(lambda: False).wait(on_tick=lambda: quiet.append(1))
    assert quiet == []


def test_wait_raises_when_work_stops_making_progress():
    """A stuck job must fail loudly rather than block a script forever."""
    gate = WorkGate(
        lambda: True,                       # Never settles
        fingerprint=lambda: "unchanged",    # ...and never moves
        describe=lambda: "1 task running: load_dataset",
        watchdog_s=0.05,
    )

    start = time.monotonic()
    try:
        gate.wait(stall_timeout_s=0.2)
    except TimeoutError as exc:
        assert "load_dataset" in str(exc), f"outstanding work not reported: {exc}"
    else:
        raise AssertionError("expected TimeoutError for work that never progresses")

    elapsed = time.monotonic() - start
    assert 0.2 <= elapsed < 1.5, f"gave up after {elapsed:.2f}s"


def test_slow_but_progressing_work_never_trips_the_stall_timeout():
    """Size-independence: a long job that keeps reporting is not stuck."""
    ticks = {"n": 0}
    pending = {"busy": True}

    def fingerprint():
        return ticks["n"]

    gate = WorkGate(lambda: pending["busy"], fingerprint=fingerprint, watchdog_s=0.02)

    def slow_job():
        for _ in range(6):          # 0.6s total, far beyond the 0.2s stall window
            time.sleep(0.1)
            ticks["n"] += 1         # Progress: the fingerprint moves
            gate.notify()
        pending["busy"] = False
        gate.notify()

    threading.Thread(target=slow_job, daemon=True).start()
    gate.wait(stall_timeout_s=0.2)  # Must not raise

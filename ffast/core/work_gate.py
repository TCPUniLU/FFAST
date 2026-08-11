"""Block a caller until queued work has settled.

Scripted callers have no event loop to react in, so they need to wait for the
task layer rather than react to it.  A gate waits on a condition that whoever
finishes the work signals, instead of sleeping a fixed interval and hoping the
work is done by the time it looks again.

The watchdog exists because not every completion path signals: a task can die
off the normal path, and cache-served work can drain a queue without producing
an event.  A missed signal should cost one watchdog tick, never a hang.

Giving up is measured in progress, not in seconds elapsed.  A dataset can take
two seconds or twenty minutes depending on its size, so no absolute deadline
fits every caller; what does generalise is that work which has stopped moving
is stuck.  The gate therefore watches a caller-supplied fingerprint of the work
state and only gives up once that fingerprint has held still for the whole
stall window.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable


class WorkGate:
    """Waits until ``is_pending()`` reports that nothing is outstanding.

    Args:
        is_pending: True while work is outstanding.
        fingerprint: Snapshot of the work state — anything comparable with
            ``!=``.  It must change whenever work moves forward (a task
            finishes, a progress tick arrives, a queue shrinks).  Required for
            stall detection.
        describe: Human-readable summary of what is still outstanding, used in
            the TimeoutError message.
        watchdog_s: How often to re-check ``is_pending`` when no signal arrives.
            Bounds the cost of a completion path that never signals.
    """

    def __init__(
        self,
        is_pending: Callable[[], bool],
        fingerprint: Callable[[], Any] | None = None,
        describe: Callable[[], str] | None = None,
        watchdog_s: float = 1.0,
    ) -> None:
        self._is_pending = is_pending
        self._fingerprint = fingerprint
        self._describe = describe
        self._watchdog_s = watchdog_s
        self._condition = threading.Condition()

    def notify(self) -> None:
        """Signal that work may have finished. Safe to call from any thread."""
        with self._condition:
            self._condition.notify_all()

    def wait(
        self,
        on_tick: Callable[[], None] | None = None,
        stall_timeout_s: float | None = None,
    ) -> None:
        """Block until no work is pending.

        ``on_tick`` is called once per wake-up — on a signal or a watchdog tick,
        so it fires at least every ``watchdog_s`` while work is outstanding.

        With ``stall_timeout_s`` set, raises TimeoutError once the work
        fingerprint has not changed for that long.  Work that keeps reporting
        progress never trips it, however long it runs.

        Raises:
            TimeoutError: work stopped progressing for ``stall_timeout_s``.
            ValueError: ``stall_timeout_s`` given without a fingerprint.
        """
        if stall_timeout_s is not None and self._fingerprint is None:
            raise ValueError("stall_timeout_s needs a fingerprint to detect progress")

        # Cap the wait so stalls are noticed on time even when nothing signals.
        interval = self._watchdog_s
        if stall_timeout_s is not None:
            interval = min(interval, stall_timeout_s / 2)

        last_change = time.monotonic()
        last_seen = self._fingerprint() if self._fingerprint is not None else None

        with self._condition:
            while self._is_pending():
                if on_tick is not None:
                    on_tick()
                self._condition.wait(interval)

                if stall_timeout_s is None:
                    continue

                current = self._fingerprint()
                if current != last_seen:
                    last_seen = current
                    last_change = time.monotonic()
                elif time.monotonic() - last_change >= stall_timeout_s:
                    if not self._is_pending():
                        return  # Finished right as we gave up
                    outstanding = self._describe() if self._describe else "unknown work"
                    raise TimeoutError(
                        f"No progress for {stall_timeout_s:.0f}s. Still outstanding: "
                        f"{outstanding}"
                    )

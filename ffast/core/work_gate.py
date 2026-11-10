"""Block a script until the event loop reports an idle iteration.

The loop calls ``mark_iteration(idle)`` once per iteration. A waiter returns at
the first idle iteration that is numbered after the moment it began waiting, so
an idle mark from before the wait cannot end it early (ADR 0094).

A short watchdog wakes the waiter now and then, only to reprint progress, to
notice that the caller should stop, and to check for a stall. It never decides
that work is done. This module imports nothing from FFAST.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable


class WorkGate:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._iteration = 0
        self._last_idle = 0

    def mark_iteration(self, idle: bool) -> None:
        """Called by the loop once per iteration. Wakes waiters if ``idle``."""
        with self._condition:
            self._iteration += 1
            if idle:
                self._last_idle = self._iteration
                self._condition.notify_all()

    def wake(self) -> None:
        """Wake every waiter so it re-checks ``should_stop``. Any thread."""
        with self._condition:
            self._condition.notify_all()

    def wait(
        self,
        *,
        should_stop: Callable[[], bool],
        on_tick: Callable[[], None] | None = None,
        stall_timeout_s: float | None = None,
        fingerprint: Callable[[], Any] | None = None,
        describe: Callable[[], str] | None = None,
        watchdog_s: float = 1.0,
    ) -> None:
        """Return at the first idle iteration after entry, or when ``should_stop()``.

        ``on_tick`` runs on every wake-up, so at least once per ``watchdog_s``.
        With ``stall_timeout_s``, raise TimeoutError once ``fingerprint()`` has
        not changed for that long; the message holds ``describe()``.

        Raises:
            ValueError: ``stall_timeout_s`` given without ``fingerprint``.
            TimeoutError: no progress for ``stall_timeout_s``.
        """
        if stall_timeout_s is not None and fingerprint is None:
            raise ValueError("stall_timeout_s needs a fingerprint to detect progress")

        interval = watchdog_s
        if stall_timeout_s is not None:
            interval = min(interval, stall_timeout_s / 2)

        last_change = time.monotonic()
        last_seen = fingerprint() if fingerprint is not None else None

        with self._condition:
            entered_at = self._iteration
            while True:
                if should_stop() or self._last_idle > entered_at:
                    return
                if on_tick is not None:
                    on_tick()
                self._condition.wait(interval)

                if stall_timeout_s is None:
                    continue
                current = fingerprint()
                if current != last_seen:
                    last_seen = current
                    last_change = time.monotonic()
                elif time.monotonic() - last_change >= stall_timeout_s:
                    if should_stop() or self._last_idle > entered_at:
                        return  # finished just as we gave up
                    outstanding = describe() if describe else "unknown work"
                    raise TimeoutError(
                        f"No progress for {stall_timeout_s:g}s. "
                        f"Still outstanding: {outstanding}"
                    )

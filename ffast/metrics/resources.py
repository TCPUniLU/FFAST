"""Cores and memory a server's metric steps share (ADR 0079)."""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

_MIB = 2**20


def cores() -> int:
    """The cores this process may use: a Slurm job's CPUs where the platform
    reports them, else every core of the machine; at least 1."""
    getter = getattr(os, "sched_getaffinity", None)
    if getter is not None:
        try:
            return max(1, len(getter(0)))
        except OSError:
            pass
    return max(1, os.cpu_count() or 1)


def available_memory() -> int:
    """Bytes of memory a new step could use now. Linux: ``MemAvailable``.
    Else psutil if it imports, else half of the machine's memory."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        import psutil
        return int(psutil.virtual_memory().available)
    except Exception:
        pass
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 2
    except (ValueError, OSError, AttributeError):
        return 8 * 2**30


class Reservation:
    """Bytes held in a ``MemoryGate``; leaving the ``with`` gives them back."""

    def __init__(self, gate: "MemoryGate", nbytes: int) -> None:
        self._gate = gate
        self.nbytes = nbytes
        self._released = False

    def __enter__(self) -> "Reservation":
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._gate._release(self.nbytes)


class MemoryGate:
    """Lets steps start while their estimated memory fits (ADR 0079 decision 2).

    The cap is ``share`` of the memory available when the gate goes from empty
    to busy. A step that does not fit waits; one that arrives when nothing is
    reserved always starts, even if it is bigger than the cap.
    """

    def __init__(self, available: Callable[[], int] = available_memory, share: float = 0.9) -> None:
        self._available = available
        self._share = share
        self._cond = threading.Condition()
        self._reserved = 0
        self._cap = 0

    def reserve(self, nbytes: int, stopped: Callable[[], bool] = lambda: False) -> Reservation | None:
        """Wait until ``nbytes`` fit, then hold them. None if ``stopped()``
        turned true while waiting."""
        nbytes = max(int(nbytes), _MIB)
        with self._cond:
            while True:
                if self._reserved == 0:
                    self._cap = self._share * self._available()
                    break
                if self._reserved + nbytes <= self._cap:
                    break
                if stopped():
                    return None
                self._cond.wait(0.5)
            self._reserved += nbytes
        return Reservation(self, nbytes)

    def _release(self, nbytes: int) -> None:
        with self._cond:
            self._reserved -= nbytes
            self._cond.notify_all()


# ── one chunk pool for the whole server (ADR 0079 decision 4) ────────────────

_CHUNK_POOL: ThreadPoolExecutor | None = None
_CHUNK_POOL_LOCK = threading.Lock()


def chunk_pool() -> ThreadPoolExecutor:
    """The threads chunked built-ins share: the cores the job may use, at most
    32. Step threads never run in it, so a step waiting for its chunks cannot
    block them."""
    global _CHUNK_POOL
    with _CHUNK_POOL_LOCK:
        if _CHUNK_POOL is None:
            _CHUNK_POOL = ThreadPoolExecutor(
                max_workers=min(32, cores()), thread_name_prefix="ffast-chunk")
        return _CHUNK_POOL


def map_chunks(fn: Callable[[Any], Any], starts) -> list:
    """``[fn(start) for start in starts]`` on the chunk pool, results in the
    order of ``starts``. Stops at the next result once the step's data was
    deleted (``stop.check``), cancelling chunks not yet started. Call it from a
    step thread, never from inside a chunk."""
    from ffast.metrics import stop

    pool = chunk_pool()
    futures = [pool.submit(fn, s) for s in starts]
    try:
        out = []
        for f in futures:
            stop.check()
            out.append(f.result())
        return out
    except BaseException:
        for f in futures:
            f.cancel()
        raise

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
    """Bytes of memory a new step could use now. Linux: ``MemAvailable``, or
    less when a cgroup (a Slurm job, a container) limits this process. Else
    psutil (macOS, Windows), else half of the machine's memory."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    free = int(line.split()[1]) * 1024
                    left = cgroup_headroom()
                    if left is None:
                        left = slurm_headroom()
                    return free if left is None else min(free, left)
    except (OSError, ValueError, IndexError):
        pass
    try:
        import psutil
        free = int(psutil.virtual_memory().available)
        left = slurm_headroom()
        return free if left is None else min(free, left)
    except Exception:
        pass
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 2
    except (ValueError, OSError, AttributeError):
        return 8 * 2**30


def total_memory() -> int:
    """Bytes of RAM in the machine, whatever is using it now. Linux:
    ``MemTotal``, else psutil, else the page count. Job limits (a cgroup, a
    Slurm allocation) are not applied here."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        import psutil
        return int(psutil.virtual_memory().total)
    except Exception:
        pass
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 16 * 2**30


def _read_int(path: str) -> int | None:
    """The number in a cgroup file; None when missing or "max" (no limit)."""
    try:
        with open(path) as f:
            text = f.read().strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


# Finding this process's children makes psutil read every process under
# /proc: 44 ms a call on a MeluXina node, before each group of a parse, was
# 7.9 s of a 31 s parse of 48 GB (2026-11-21). An answer this recent is
# reused; a group takes about 0.1 s and the parser's own estimate still
# applies to what it allocates.
HEADROOM_REUSE_S = 0.5
_headroom_memo = None             # (when, allocation in MiB, answer)
_clock = time.monotonic


def slurm_headroom() -> int | None:
    """Fallback when a Slurm allocation's cgroup cannot be inspected.

    Slurm exports memory in MiB. Subtract this server's process tree; shared
    pages may be counted twice, which is conservative. A visible cgroup is
    preferred because it also accounts for unrelated tasks in the job. An
    answer younger than ``HEADROOM_REUSE_S`` for the same allocation is reused.
    """
    global _headroom_memo
    try:
        amount = int(os.environ.get("SLURM_MEM_PER_NODE", "0"))
        if not amount:
            cpus = int(os.environ.get("SLURM_CPUS_PER_TASK") or
                       os.environ.get("SLURM_CPUS_ON_NODE", "1"))
            amount = int(os.environ.get("SLURM_MEM_PER_CPU", "0")) * cpus
        if amount <= 0:
            return None
        now = _clock()
        memo = _headroom_memo
        if memo is not None and memo[1] == amount and 0 <= now - memo[0] < HEADROOM_REUSE_S:
            return memo[2]
        answer = _walk_headroom(amount)
        _headroom_memo = (now, amount, answer)
        return answer
    except (ValueError, OSError, ImportError):
        return None


def _walk_headroom(amount: int) -> int:
    """The allocation (MiB) minus this server's process tree, in bytes."""
    import psutil
    process = psutil.Process()
    used = process.memory_info().rss
    for child in process.children(recursive=True):
        try:
            used += child.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return max(0, amount * _MIB - used)


def _inactive_file(path: str, key: str) -> int:
    """File cache the kernel can drop; a cgroup counts it as used."""
    try:
        with open(path) as f:
            for line in f:
                name, _, value = line.partition(" ")
                if name == key:
                    return int(value)
    except (OSError, ValueError):
        pass
    return 0


def cgroup_headroom(proc: str = "/proc/self/cgroup", root: str = "/sys/fs/cgroup") -> int | None:
    """Bytes left under the tightest cgroup memory limit above this process,
    or None when none is set. A Slurm job's limit lives here; MemAvailable
    shows the whole node, so a job past its limit is killed with the node
    still showing memory free."""
    try:
        with open(proc) as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    best = None
    for line in lines:
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        _, controllers, path = parts
        steps = [p for p in path.strip("/").split("/") if p]
        if controllers == "":                            # cgroup v2: every level may set memory.max
            dirs = [os.path.join(root, *steps[:k]) for k in range(len(steps), -1, -1)]
            files = ("memory.max", "memory.current", "memory.stat", "inactive_file")
        elif "memory" in controllers.split(","):         # cgroup v1
            dirs = [os.path.join(root, "memory", *steps), os.path.join(root, "memory")]
            files = ("memory.limit_in_bytes", "memory.usage_in_bytes", "memory.stat", "total_inactive_file")
        else:
            continue
        for d in dirs:
            limit = _read_int(os.path.join(d, files[0]))
            used = _read_int(os.path.join(d, files[1]))
            if limit is None or used is None or limit >= 1 << 60:   # v1 writes "no limit" as ~2**63
                continue
            left = max(0, limit - used + _inactive_file(os.path.join(d, files[2]), files[3]))
            best = left if best is None else min(best, left)
    return best


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

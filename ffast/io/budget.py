"""Memory shared by dataset storage, cached batches and active work (ADR 0102).

The limit is admission control, not a promise about arbitrary Python code's
RSS. External operations must declare/estimate their working set too.
"""

from __future__ import annotations

import logging
import os
import threading
import weakref
from collections import OrderedDict
from contextlib import contextmanager

from ffast.io.extxyz_arrays import NotEnoughMemory
from ffast.metrics.resources import (
    available_memory,
    cgroup_headroom,
    slurm_headroom,
    total_memory,
)

MiB = 2**20
GiB = 2**30


def _setting(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        result = int(value)
    except ValueError:
        raise ValueError(
            f"{name} must be a nonnegative number of bytes"
        ) from None
    if result < 0:
        raise ValueError(f"{name} must be a nonnegative number of bytes")
    return result


class Budget:
    def __init__(self, ram_bytes=None, parsed_bytes=None, result_bytes=None):
        # The automatic limit must not change with what else is running, or
        # the same file would load by a different path on each start (ADR 0106).
        job_limit = cgroup_headroom()
        if job_limit is None:
            job_limit = slurm_headroom()
        if (
            os.environ.get("SLURM_JOB_ID")
            and job_limit is None
            and ram_bytes is None
            and "FFAST_MEMORY_BYTES" not in os.environ
        ):
            # Whole-node jobs (--mem=0) expose no limit. Start with the
            # workstation rule instead of refusing to run.
            logging.getLogger("FFAST").warning(
                "The Slurm memory allocation is not visible; using 80%% of the "
                "node's total memory. Set FFAST_MEMORY_BYTES or resources.ram_bytes "
                "within the job's allocation if this node is shared."
            )
        if job_limit is not None:
            # A constrained cluster job may use most of what is left of its
            # allocation, never the whole of it.
            free = min(available_memory(), job_limit)
            automatic = max(MiB, min(int(free * 0.85), free - 512 * MiB))
        else:
            total = total_memory()
            automatic = max(MiB, min(int(total * 0.80), total - 512 * MiB))
        self.limit = int(
            ram_bytes
            if ram_bytes is not None
            else _setting("FFAST_MEMORY_BYTES", automatic)
        )
        if job_limit is not None:
            self.limit = min(self.limit, free)
        if self.limit <= 0:
            raise ValueError("FFAST_MEMORY_BYTES must be positive")
        self.parsed_limit = int(
            parsed_bytes
            if parsed_bytes is not None
            else _setting("FFAST_PARSED_CACHE_BYTES", 100 * GiB)
        )
        # The parsed cache may grow until this much of its disk would be
        # left free. A quarter of free disk kept the forces of an 83 GB file
        # (22 GB) out on a laptop with 45 GB free, so every force plot
        # parsed the text again (ADR 0112).
        self.keep_free = int(_setting("FFAST_CACHE_KEEP_FREE_BYTES", 20 * GiB))
        self.result_limit = int(
            result_bytes
            if result_bytes is not None
            else _setting("FFAST_RESULT_CACHE_BYTES", 20 * GiB)
        )
        self.lock = threading.RLock()
        self.used = 0
        self.pending = 0
        self.cache_bytes = 0
        self.cache = OrderedDict()
        self._arrays = {}
        self._evictors = []

    @property
    def block_bytes(self):
        return max(1024, min(16 * MiB, self.limit // 16))

    def _room(self, size):
        while self.cache and self.used + self.cache_bytes + size > self.limit:
            _, (_, n) = self.cache.popitem(last=False)
            self.cache_bytes -= n
        for ref in list(self._evictors):
            evict = ref()
            if evict is None:
                self._evictors.remove(ref)
                continue
            while self.used + self.cache_bytes + size > self.limit and evict():
                pass
        # Check current headroom as well: another process can take memory.
        if self.used + self.cache_bytes + size > self.limit or (
            size > 0 and self.pending + size > available_memory()
        ):
            raise NotEnoughMemory(
                f"This operation needs {size / MiB:.1f} MiB of working memory; "
                f"FFAST's shared RAM budget is {self.limit / MiB:.1f} MiB "
                f"({self.used / MiB:.1f} MiB in use). The dataset remains open."
            )

    def fits(self, size):
        """Whether ``size`` more bytes stay within the limit. Looks at this
        budget only, never at the memory free right now, so the answer depends
        on the machine and not on what else is open (ADR 0106)."""
        with self.lock:
            return self.used + int(size) <= self.limit

    def check(self, size):
        with self.lock:
            self._room(int(size))

    def register_cache(self, evict):
        with self.lock:
            self._evictors.append(weakref.WeakMethod(evict))

    @contextmanager
    def reserve(self, size):
        size = int(size)
        with self.lock:
            self._room(size)
            self.used += size
            self.pending += size
        lease = _Lease(size)
        try:
            yield lease
        finally:
            with self.lock:
                self.used -= lease.remaining
                self.pending -= lease.remaining

    def retain(self, array, lease=None):
        """Account for an owned numpy allocation until its last view dies."""
        import numpy as np

        base = array
        while isinstance(getattr(base, "base", None), np.ndarray):
            base = base.base
        key = id(base)
        with self.lock:
            if key in self._arrays:
                return array
            size = base.nbytes
            credit = min(size, lease.remaining) if lease is not None else 0
            self._room(size - credit)
            self.used += size - credit
            if lease is not None:
                lease.remaining -= credit
                self.pending -= credit
            self._arrays[key] = weakref.ref(base)

        def released():
            with self.lock:
                self._arrays.pop(key, None)
                self.used -= size

        weakref.finalize(base, released)
        return array

    def cached(self, key):
        with self.lock:
            hit = self.cache.get(key)
            if hit is not None:
                self.cache.move_to_end(key)
                return hit[0]
        return None

    def keep(self, key, value, size):
        # Cache at most a quarter of RAM; processing buffers have priority.
        with self.lock:
            old = self.cache.pop(key, None)
            if old:
                self.cache_bytes -= old[1]
            if size > self.limit // 4:
                return
            while self.cache and self.cache_bytes + size > self.limit // 4:
                _, (_, n) = self.cache.popitem(last=False)
                self.cache_bytes -= n
            try:
                self._room(size)
            except NotEnoughMemory:
                return
            self.cache[key] = (value, size)
            self.cache_bytes += size


class _Lease:
    def __init__(self, size):
        self.remaining = size


_budget = None
_lock = threading.Lock()


def budget():
    global _budget
    with _lock:
        if _budget is None:
            _budget = Budget()
        return _budget


def configure(*, ram_bytes=None, parsed_bytes=None, result_bytes=None):
    """Configure before loading data; replacing a live budget is refused."""
    global _budget
    with _lock:
        if _budget is not None and _budget.used:
            raise ValueError(
                "Memory budgets must be configured before loading data"
            )
        _budget = Budget(ram_bytes, parsed_bytes, result_bytes)
    return _budget

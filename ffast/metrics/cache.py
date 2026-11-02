from __future__ import annotations

import atexit
import hashlib
import inspect
import logging
import os
import shutil
import tempfile
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ffast.metrics.models import MetricResult

logger = logging.getLogger(__name__)


def _array_fingerprint(arr: np.ndarray) -> str:
    # Shape and type too: (frames, atoms, 3) and its flat (atoms, 3) copy hold
    # the same bytes but give a metric different answers.
    arr = np.ascontiguousarray(arr)
    digest = hashlib.sha256(f"{arr.dtype.str}{arr.shape}".encode())
    digest.update(arr.tobytes())
    return digest.hexdigest()[:16]


def function_hash(fn: Callable) -> str:
    try:
        src = inspect.getsource(fn)
    except (TypeError, OSError):
        # Callable instances / partials (e.g. compiled transform metrics) have no
        # retrievable source. Prefer a self-described stable source; never fall
        # back to repr() of a plain instance (its 0x address would defeat caching).
        impl = getattr(fn, "implementation_source", None)
        src = impl() if callable(impl) else repr(fn)
    return hashlib.sha256(src.encode()).hexdigest()[:16]


def formula_token(registry, metric_id: str) -> str | None:
    """Short identity of the formulas behind a metric, or None when it has none.

    Formula metrics (user, project, transform) describe themselves by
    ``implementation_source``; a built-in's code cannot change while the server
    runs. A formula built on other formulas takes theirs in too, so editing one
    changes the token of everything above it (ADR 0057 rule 5).
    """
    # A box id is a formula once compiled (ADR 0065). Compile it here, so a key
    # built before its first run carries the same token as one built after.
    if hasattr(registry, "ensure"):
        registry.ensure(metric_id)
    parts: dict[str, str] = {}

    def walk(mid: str) -> None:
        if mid in parts or not registry.has(mid):
            return
        schema, fn = registry.get(mid)
        parts[mid] = function_hash(fn) if hasattr(fn, "implementation_source") else ""
        for ref in schema.inputs.values():
            walk(ref)

    walk(metric_id)
    seen = [f"{m}:{h}" for m, h in sorted(parts.items()) if h]
    if not seen:
        return None
    return hashlib.sha256("|".join(seen).encode()).hexdigest()[:8]


def _value_fingerprint(v: Any) -> str:
    if hasattr(v, "__array__"):
        return _array_fingerprint(np.asarray(v))
    return hashlib.sha256(str(v).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class CacheKey:
    metric_id: str
    implementation_hash: str
    compute_params: frozenset
    input_fingerprints: frozenset


def _memory_bytes() -> int:
    """This machine's memory, or 0 when it cannot be read."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return 0


# A quarter of the machine's memory, at least 1 GiB; change here, there is no
# setting (ADR 0061). A fixed 1 GiB could not keep the 4 GB force difference
# of 1.77M structures, so every force metric recomputed it (2026-11-02).
DEFAULT_MAX_BYTES = max(1 << 30, _memory_bytes() // 4)


# A smaller result is cheap to compute again, so it is not written to disk.
SPILL_MIN_BYTES = 100 << 20


def _memory_backed(path, mounts: str | None = None) -> bool:
    """Whether ``path`` lies on a file system kept in memory (tmpfs, ramfs):
    a file there takes the memory the cache let go of. On a MeluXina compute
    node /tmp is tmpfs (2026-11-02). Without a mount table (macOS): no."""
    if mounts is None:
        try:
            mounts = Path("/proc/mounts").read_text()
        except OSError:
            return False
    target = os.path.abspath(path)
    best, kind = "", ""
    for line in mounts.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        point = parts[1]
        inside = target == point or target.startswith(point.rstrip("/") + "/")
        if inside and len(point) > len(best):
            best, kind = point, parts[2]
    return kind in ("tmpfs", "ramfs")


def _size(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


class MetricCache:
    """Computed metric results, bounded by the memory their arrays hold.

    Least recently used entries are dropped first when a new one would take the
    total past ``max_bytes``. Several threads share one cache, so every access
    holds a lock.

    A result of ``spill_min_bytes`` or more that does not fit in memory (too
    large, or pushed out by newer ones) is written to a file in ``spill_dir``
    and read back from it, instead of computed again: computing the 4.2 GB
    force difference of 1.77M structures again took ~25 s (2026-11-02). The
    files take at most ``spill_max_bytes`` (default: half the disk's free
    space when the first is written), the oldest deleted first, and go when
    the cache is cleared or the process ends. ``spill_dir`` defaults to a
    new folder in the temporary folder (``TMPDIR``). The user is told once
    per metric, as a warning every window shows.
    """

    def __init__(
        self,
        max_bytes: int = DEFAULT_MAX_BYTES,
        spill_dir: str | os.PathLike | None = None,
        spill_min_bytes: int = SPILL_MIN_BYTES,
        spill_max_bytes: int | None = None,
    ) -> None:
        self._max_bytes = max_bytes
        self._store: OrderedDict[CacheKey, tuple[MetricResult, int]] = OrderedDict()
        self._total = 0
        self._lock = threading.Lock()
        self._spill_dir = Path(spill_dir) if spill_dir is not None else None
        self._spill_min = spill_min_bytes
        self._spill_max = spill_max_bytes
        # key -> (result without its values, file, bytes), oldest first
        self._disk: OrderedDict[CacheKey, tuple[MetricResult, Path, int]] = OrderedDict()
        self._disk_total = 0
        self._warned: set[tuple[str, str]] = set()

    def make_key(
        self,
        metric_id: str,
        fn: Callable,
        compute_params: dict[str, Any],
        resolved_inputs: dict[str, Any],
        known: dict[str, Any] | None = None,
    ) -> CacheKey:
        """``known`` gives inputs a fingerprint already (a source name, a
        dependency's key); the rest are hashed by content."""
        known = known or {}
        impl_hash = function_hash(fn)
        input_fps = frozenset(
            (k, known[k] if k in known else _value_fingerprint(v))
            for k, v in resolved_inputs.items()
        )
        return CacheKey(
            metric_id=metric_id,
            implementation_hash=impl_hash,
            compute_params=frozenset(compute_params.items()),
            input_fingerprints=input_fps,
        )

    def get(self, key: CacheKey) -> MetricResult | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is not None:
                self._store.move_to_end(key)
                return entry[0]
            spilled = self._disk.get(key)
            if spilled is None:
                return None
            self._disk.move_to_end(key)
            meta, path, _ = spilled
        try:
            # Read as needed, not all at once into memory.
            values = np.load(path, mmap_mode="r", allow_pickle=False)
        except (OSError, ValueError):
            return None         # deleted meanwhile: computed again
        return meta.model_copy(update={"values": values})

    def put(self, key: CacheKey, result: MetricResult) -> None:
        size = int(np.asarray(result.values).nbytes)
        out: list[tuple[CacheKey, MetricResult, int]] = []
        with self._lock:
            old = self._store.pop(key, None)
            if old is not None:
                self._total -= old[1]
            stale = self._disk.pop(key, None)
            if stale is not None:
                self._disk_total -= stale[2]
            if size > self._max_bytes:
                out.append((key, result, size))
            else:
                self._store[key] = (result, size)
                self._total += size
                while self._total > self._max_bytes:
                    k, (r, dropped) = self._store.popitem(last=False)
                    self._total -= dropped
                    out.append((k, r, dropped))
        if stale is not None:
            stale[1].unlink(missing_ok=True)
        # Written outside the lock: a 4 GB write must not hold up lookups.
        for k, r, n in out:
            self._spill(k, r, n)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._total = 0
            files = [path for _, path, _ in self._disk.values()]
            self._disk.clear()
            self._disk_total = 0
        for path in files:
            path.unlink(missing_ok=True)

    # ── disk ──────────────────────────────────────────────────────────────

    def _spill(self, key: CacheKey, result: MetricResult, size: int) -> None:
        if size < self._spill_min:
            return
        path = None
        try:
            directory = self._directory()
            if _memory_backed(directory):
                self._warn("dropped", result.metric_id,
                           f"{result.metric_id} ({_size(size)}) does not fit in the metric "
                           f"result cache in memory ({_size(self._max_bytes)}), and the folder "
                           f"for results that do not fit ({directory}) is memory-backed: metrics "
                           f"that use it compute it again, which is slow. Set "
                           f"FFAST_METRIC_CACHE_DIR to a folder on disk to keep it there.")
                return
            path = directory / f"{uuid.uuid4().hex}.npy"
            np.save(path, np.asarray(result.values), allow_pickle=False)
        except Exception as exc:
            if path is not None:
                path.unlink(missing_ok=True)
            self._warn("dropped", result.metric_id,
                       f"{result.metric_id} ({_size(size)}) does not fit in the metric result "
                       f"cache in memory ({_size(self._max_bytes)}) and could not be written to "
                       f"disk ({exc}): metrics that use it compute it again, which is slow.")
            return
        gone: list[tuple[MetricResult, Path, int]] = []
        with self._lock:
            limit = self._disk_limit(directory)
            self._disk[key] = (result.model_copy(update={"values": None}), path, size)
            self._disk_total += size
            while self._disk_total > limit and self._disk:
                _, entry = self._disk.popitem(last=False)
                self._disk_total -= entry[2]
                gone.append(entry)
        for meta, old_path, old_size in gone:
            old_path.unlink(missing_ok=True)
            self._warn("dropped", meta.metric_id,
                       f"{meta.metric_id} ({_size(old_size)}) does not fit in the metric result "
                       f"cache, in memory ({_size(self._max_bytes)}) or on disk "
                       f"({_size(limit)}): metrics that use it compute it again, which is slow. "
                       f"A server with more memory or disk keeps it.")
        if all(entry[1] != path for entry in gone):
            self._warn("disk", result.metric_id,
                       f"{result.metric_id} ({_size(size)}) does not fit in the metric result "
                       f"cache in memory ({_size(self._max_bytes)}): it was moved to disk, in "
                       f"{directory}. Reading it back is slower than memory but faster than "
                       f"computing it again. A server with more memory keeps it in memory.")

    def _directory(self) -> Path:
        if self._spill_dir is None:
            parent = os.environ.get("FFAST_METRIC_CACHE_DIR") or None
            if parent:
                Path(parent).mkdir(parents=True, exist_ok=True)
            self._spill_dir = Path(tempfile.mkdtemp(prefix="ffast-metric-cache-", dir=parent))
            atexit.register(shutil.rmtree, self._spill_dir, True)
        self._spill_dir.mkdir(parents=True, exist_ok=True)
        return self._spill_dir

    def _disk_limit(self, directory: Path) -> int:
        if self._spill_max is None:
            self._spill_max = shutil.disk_usage(directory).free // 2
        return self._spill_max

    def _warn(self, kind: str, metric_id: str, text: str) -> None:
        with self._lock:
            if (kind, metric_id) in self._warned:
                return
            self._warned.add((kind, metric_id))
        logger.warning(text)

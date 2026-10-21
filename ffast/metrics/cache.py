from __future__ import annotations

import hashlib
import inspect
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from ffast.metrics.models import MetricResult


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


DEFAULT_MAX_BYTES = 1 << 30   # 1 GiB; change here, there is no setting (ADR 0061)


class MetricCache:
    """Computed metric results, bounded by the memory their arrays hold.

    Least recently used entries are dropped first when a new one would take the
    total past ``max_bytes``. Several threads share one cache, so every access
    holds a lock.
    """

    def __init__(self, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self._max_bytes = max_bytes
        self._store: OrderedDict[CacheKey, tuple[MetricResult, int]] = OrderedDict()
        self._total = 0
        self._lock = threading.Lock()

    def make_key(
        self,
        metric_id: str,
        fn: Callable,
        compute_params: dict[str, Any],
        resolved_inputs: dict[str, Any],
    ) -> CacheKey:
        impl_hash = function_hash(fn)
        input_fps = frozenset(
            (k, _value_fingerprint(v)) for k, v in resolved_inputs.items()
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
            if entry is None:
                return None
            self._store.move_to_end(key)
            return entry[0]

    def put(self, key: CacheKey, result: MetricResult) -> None:
        size = int(np.asarray(result.values).nbytes)
        with self._lock:
            old = self._store.pop(key, None)
            if old is not None:
                self._total -= old[1]
            if size > self._max_bytes:
                return
            self._store[key] = (result, size)
            self._total += size
            while self._total > self._max_bytes:
                _, (_, dropped) = self._store.popitem(last=False)
                self._total -= dropped

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._total = 0

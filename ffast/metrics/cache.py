from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from ffast.metrics.models import MetricResult


def _array_fingerprint(arr: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(arr).tobytes()).hexdigest()[:16]


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


class MetricCache:
    def __init__(self) -> None:
        self._store: dict[CacheKey, MetricResult] = {}

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
        return self._store.get(key)

    def put(self, key: CacheKey, result: MetricResult) -> None:
        self._store[key] = result

    def clear(self) -> None:
        self._store.clear()

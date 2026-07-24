from __future__ import annotations

import logging
from typing import Callable

from ffast.metrics.models import MetricSchema, ParameterSchema
from ffast.metrics.graph import MetricGraph

logger = logging.getLogger("FFAST")


class MetricRegistry:
    def __init__(self) -> None:
        self._metrics: dict[str, tuple[MetricSchema, Callable]] = {}
        self._graph = MetricGraph()

    def metric(
        self,
        func: Callable | None = None,
        *,
        id: str | None = None,
        namespace: str | None = None,
        inputs: dict[str, str] | None = None,
        shape=None,  # Dim | tuple[Dim, ...] | str (str = legacy)
        unit: str | None = None,
        label: str | None = None,
        description: str | None = None,
        parameters: dict[str, dict] | None = None,
        optional_inputs: list[str] | None = None,
        tests: list[dict] | None = None,
        hints: dict | None = None,
    ) -> Callable:
        """Register a metric.

        Usable fully-declared (``@metric(id=..., inputs=..., shape=..., unit=...)``)
        or with anything omitted inferred from the function signature, type
        annotations and docstring (``@metric`` / ``@metric(unit=units.force)``).
        Explicit arguments always win over inference. See
        ``ffast.metrics.signature`` for the inference rules.
        """
        from ffast.metrics.signature import infer_schema

        def register(fn: Callable) -> Callable:
            spec = infer_schema(
                fn,
                id=id,
                namespace=namespace,
                inputs=inputs,
                shape=shape,
                unit=unit,
                label=label,
                description=description,
                parameters=parameters,
                optional_inputs=optional_inputs,
                tests=tests,
                hints=hints,
            )
            mid = spec["id"]
            if "." not in mid:
                raise ValueError(
                    f"Metric id '{mid}' must contain at least one dot to separate "
                    f"namespace and name (set METRIC_NAMESPACE or pass id=/namespace=)"
                )
            if mid in self._metrics:
                raise ValueError(f"Metric with id '{mid}' is already registered")
            decl = MetricSchema.model_validate(spec)
            self._metrics[mid] = (decl, fn)
            return fn

        # bare @metric          -> func is the function
        # @metric(...) / @metric() -> func is None, return the decorator
        if func is not None and callable(func):
            return register(func)
        return register

    def get(self, id: str) -> tuple[MetricSchema, Callable]:
        if id not in self._metrics:
            raise KeyError(f"Metric with id '{id}' is not registered")
        return self._metrics[id]

    def has(self, id: str) -> bool:
        return id in self._metrics

    def list_metrics(self) -> list[str]:
        return list(self._metrics.keys())

    def freeze(self, validate_refs: bool = True) -> list[tuple[str, str]]:
        """Build dependency DAG, validate all refs and shapes. Returns errors list.

        Call once at server startup. Logs all errors; raises if any found.
        """
        from ffast.metrics.inputs import ALL_VALID_REFS
        valid_refs = ALL_VALID_REFS if validate_refs else None
        errors = self._graph.freeze(self._metrics, valid_refs=valid_refs)
        for mid, msg in errors:
            logger.error("MetricRegistry.freeze [%s]: %s", mid, msg)
        return errors


_default_registry = MetricRegistry()
default_registry = _default_registry
metric = _default_registry.metric

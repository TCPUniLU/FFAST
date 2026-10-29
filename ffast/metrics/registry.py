from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Callable

from ffast.metrics.models import MetricSchema, ParameterSchema
from ffast.metrics.graph import MetricGraph

logger = logging.getLogger("FFAST")

# Set while this thread registers derived ids (see MetricRegistry.derived).
# Module-level, not on the registry, so the registry stays picklable.
_deriving = threading.local()


class MetricRegistry:
    def __init__(self) -> None:
        self._metrics: dict[str, tuple[MetricSchema, Callable]] = {}
        self._graph = MetricGraph()
        # Goes up by one every time the table changes (ADR 0061). A caller that
        # read it before a run and sees another value after knows the formulas
        # may have changed under it, and throws the run away.
        self.revision = 0

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
            if not getattr(_deriving, "on", False):
                self.revision += 1
            return fn

        # bare @metric          -> func is the function
        # @metric(...) / @metric() -> func is None, return the decorator
        if func is not None and callable(func):
            return register(func)
        return register

    def get(self, id: str) -> tuple[MetricSchema, Callable]:
        if id not in self._metrics and not self.ensure(id):
            from ffast.metrics.retired import retired_message
            raise KeyError(retired_message(id) or f"Metric with id '{id}' is not registered")
        return self._metrics[id]

    def has(self, id: str) -> bool:
        return id in self._metrics

    @contextmanager
    def derived(self):
        """Registrations made by this thread inside are derived ids, such as a
        box setting over a registered base (ADR 0065). They add an id and change
        no result that exists, so they leave ``revision`` alone; a change to the
        base still raises it."""
        _deriving.on = True
        try:
            yield
        finally:
            _deriving.on = False

    def ensure(self, id: str) -> bool:
        """Whether ``id`` is registered, registering it first when it is a box
        id such as ``ffast.force_difference[structure;component;mae]``
        (ADR 0065): any setting of a base metric works without being created."""
        if id in self._metrics:
            return True
        from ffast.metrics.boxes import ensure_metric
        return ensure_metric(id, self)

    # -- changing the table on a running server (ADR 0057 rule 5) -------------
    # The table is frozen at start-up, then user metrics are added, replaced and
    # removed while it runs. Callers re-run ``freeze()`` afterwards and tell the
    # result cache.

    def unregister(self, id: str) -> tuple[MetricSchema, Callable]:
        """Remove a metric; returns what was removed so a caller can restore it."""
        if id not in self._metrics:
            raise KeyError(f"Metric with id '{id}' is not registered")
        entry = self._metrics.pop(id)
        self.revision += 1
        return entry

    def restore(self, id: str, entry: tuple[MetricSchema, Callable]) -> None:
        """Put back an entry taken out by ``unregister`` (rollback of a failed edit)."""
        self._metrics[id] = entry
        self.revision += 1

    def dependents_of(self, id: str) -> list[str]:
        """Every metric built on ``id``, directly or through others (sorted).

        Found by walking the metrics' declared inputs, so it is right before
        and after ``freeze()`` and includes Transform Metrics.
        """
        direct: dict[str, set[str]] = {}
        for mid, (schema, _fn) in self._metrics.items():
            for ref in schema.inputs.values():
                direct.setdefault(ref, set()).add(mid)
        found: set[str] = set()
        todo = [id]
        while todo:
            for dep in direct.get(todo.pop(), ()):
                if dep not in found and dep != id:
                    found.add(dep)
                    todo.append(dep)
        return sorted(found)

    def direct_dependents_of(self, id: str) -> list[str]:
        """The metrics that name ``id`` as one of their inputs (sorted)."""
        return sorted(
            mid for mid, (schema, _fn) in self._metrics.items()
            if id in schema.inputs.values()
        )

    def list_metrics(self) -> list[str]:
        return list(self._metrics.keys())

    def freeze(self, validate_refs: bool = True) -> list[tuple[str, str]]:
        """Build dependency DAG, validate all refs and shapes. Returns errors list.

        Call once at server startup. Logs all errors; raises if any found.
        """
        from ffast.metrics.inputs import ALL_VALID_REFS
        valid_refs = ALL_VALID_REFS if validate_refs else None
        # A metric may take a box setting as its input (ADR 0065).
        for schema, _fn in list(self._metrics.values()):
            for ref in schema.inputs.values():
                self.ensure(ref)
        errors = self._graph.freeze(self._metrics, valid_refs=valid_refs)
        for mid, msg in errors:
            logger.error("MetricRegistry.freeze [%s]: %s", mid, msg)
        return errors


_default_registry = MetricRegistry()
default_registry = _default_registry
metric = _default_registry.metric

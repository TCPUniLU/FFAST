from __future__ import annotations

import hashlib
import traceback as tb
from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from ffast.metrics.cache import MetricCache, function_hash
from ffast.metrics.dims import shape_to_str
from ffast.metrics.models import MetricFailure, MetricResult
from ffast.metrics.registry import MetricRegistry


class MetricExecutor(ABC):
    @abstractmethod
    def run(self, id: str, inputs: dict[str, Any], parameters: dict[str, Any]) -> MetricResult | MetricFailure:
        ...


class InProcessExecutor(MetricExecutor):
    def __init__(self, registry: MetricRegistry, cache: MetricCache | None = None) -> None:
        self._registry = registry
        self._cache = cache if cache is not None else MetricCache()

    def _execute_one(
        self,
        id: str,
        inputs: dict[str, Any],
        parameters: dict[str, Any],
        intermediate: dict[str, MetricResult],
    ) -> MetricResult | MetricFailure:
        """Run a single metric, using intermediate cache for metric-dep results."""
        schema, fn = self._registry.get(id)

        compute_params = {
            key: parameters.get(key, p.default)
            for key, p in schema.parameters.items()
            if p.role == "compute"
        }

        resolved: dict[str, Any] = {}
        for input_key, input_ref in schema.inputs.items():
            if self._registry.has(input_ref):
                if input_ref in intermediate:
                    dep = intermediate[input_ref]
                else:
                    dep = self._execute_one(input_ref, inputs, parameters, intermediate)
                    if isinstance(dep, MetricResult):
                        intermediate[input_ref] = dep
                if isinstance(dep, MetricFailure):
                    return MetricFailure(
                        metric_id=id,
                        traceback=f"Dependency '{input_ref}' failed:\n{dep.traceback}",
                        parameters=parameters,
                    )
                resolved[input_key] = dep.values
            else:
                val = inputs.get(input_key)
                if val is None and input_key not in inputs:
                    if input_key in schema.optional_inputs:
                        resolved[input_key] = None
                    else:
                        return MetricFailure(
                            metric_id=id,
                            traceback=f"Missing raw input '{input_key}' (symbolic ref '{input_ref}')",
                            parameters=parameters,
                        )
                elif val is None:
                    if input_key in schema.optional_inputs:
                        resolved[input_key] = None
                    else:
                        return MetricFailure(
                            metric_id=id,
                            traceback=f"Input '{input_key}' (ref '{input_ref}') is unavailable for this dataset",
                            parameters=parameters,
                        )
                else:
                    resolved[input_key] = np.asarray(val)

        # also resolve optional inputs not in schema.inputs (bare optional_inputs)
        for opt_key in schema.optional_inputs:
            if opt_key not in resolved:
                raw_opt = inputs.get(opt_key, None)
                resolved[opt_key] = np.asarray(raw_opt) if raw_opt is not None else None

        cache_key = self._cache.make_key(id, fn, compute_params, resolved)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            raw = fn(**resolved, **compute_params)
        except Exception:
            return MetricFailure(metric_id=id, traceback=tb.format_exc(), parameters=parameters)

        arr = np.asarray(raw)
        checksum = hashlib.sha256(np.ascontiguousarray(arr).tobytes()).hexdigest()
        result = MetricResult(
            metric_id=id,
            shape=shape_to_str(schema.shape),
            dtype=str(arr.dtype),
            unit=schema.unit,
            compute_parameters=compute_params,
            implementation_hash=function_hash(fn),
            checksum=checksum,
            values=arr,
        )
        self._cache.put(cache_key, result)
        return result

    def run(self, id: str, inputs: dict[str, Any], parameters: dict[str, Any]) -> MetricResult | MetricFailure:
        """Run a single metric (and its deps recursively). Transitional bridge compatibility."""
        return self._execute_one(id, inputs, parameters, {})

    def run_batch(
        self,
        metric_ids: list[str],
        inputs: dict[str, Any],
        parameters: dict[str, Any],
    ) -> dict[str, MetricResult | MetricFailure]:
        """Run multiple metrics sharing intermediate results.

        Uses compute_plan() for topological ordering — each intermediate metric
        runs exactly once even when depended on by multiple requested metrics.

        Returns {metric_id: result} for all requested metric_ids.
        """
        plan = self._registry.compute_plan(metric_ids)
        intermediate: dict[str, MetricResult] = {}
        results: dict[str, MetricResult | MetricFailure] = {}

        for mid in plan:
            result = self._execute_one(mid, inputs, parameters, intermediate)
            if isinstance(result, MetricResult):
                intermediate[mid] = result
            results[mid] = result

        return {mid: results[mid] for mid in metric_ids if mid in results}

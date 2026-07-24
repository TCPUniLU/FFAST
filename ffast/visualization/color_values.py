"""Value-driven atom coloring (ADR 0016) — resolve per-atom color values.

Extracted from ``scene_builder.build_scene`` so the symbolic-ref→array
resolution and metric execution that drive atom coloring live in one cohesive,
unit-testable module rather than inline in the 700-line scene orchestrator.

The active color *source* (``state.parameters['ffast.atom_color']['source']``)
is one of:

- ``element`` / unset    → ``None`` (renderer falls back to element colors)
- ``displacement``       → per-atom RMS displacement over the trajectory
- ``metric:<metric_id>`` → run the metric over the current frame's inputs

Resolution is frame-scoped: refs map to the *currently displayed* frame
(``idx``) — distinct from ``ffast/metrics/input_resolver``, which resolves whole-dataset
arrays for 2D panels. Any failure (missing prediction, metric error, shape
mismatch) returns ``None`` so coloring degrades to element colors (ADR 0016).
"""
from __future__ import annotations

import logging

import numpy as np

from ffast.metrics.execution import InputSource

logger = logging.getLogger(__name__)


class _FrameInputSource(InputSource):
    """Metric Execution Context input source backed by frame-scoped symbolic-ref
    resolution (mirrors ``input_resolver._ResolverSource`` for the panel path —
    ADR 0046).

    Wraps ``_resolve_symbolic_ref`` (which raises ``KeyError`` for a missing or
    unavailable ref) so ``get`` never raises: any resolution failure surfaces as
    ``(True, None)``, which the plan builder treats as an unavailable input —
    unavailable *required* inputs then flow through to a ``MetricFailure``,
    which ``resolve_atom_color_values`` maps to ``None`` → element-color
    fallback (ADR 0016).
    """

    def __init__(self, resolve) -> None:
        self._resolve = resolve

    def get(self, metric_id, local_key, ref):
        try:
            return True, self._resolve(ref)
        except Exception:
            return True, None


def _resolve_symbolic_ref(ref, ds, idx, raw_positions, z, get_prediction, state, prediction_ref):
    """Map a metric's symbolic input ref to a current-frame array (ADR 0016).

    ``prediction_ref`` is the resolved prediction for *this* coloring (the
    ``ffast.atom_color`` stage's own ``prediction_ref`` parameter if set, else
    the view's global ``state.prediction_ref`` — same override pattern as
    ``ffast.force_arrows``, scene_builder.py's "Option B").
    """
    if ref == "frame.positions":
        return raw_positions
    if ref in ("frame.elements", "reference.elements"):
        return z
    if ref == "reference.forces":
        return np.asarray(ds.getForces(idx), dtype=np.float64)
    if ref == "prediction.forces":
        if get_prediction is None or not prediction_ref:
            raise KeyError(ref)
        pred = get_prediction(state.dataset_ref, prediction_ref)
        if pred is None:
            raise KeyError(ref)
        return np.asarray(pred.forces[idx], dtype=np.float64)
    if ref == "reference.masses":
        if hasattr(ds, "getMasses"):
            return np.asarray(ds.getMasses(), dtype=np.float64)
        # Fall back to standard atomic masses from the elements (Z), so
        # mass-dependent metrics (e.g. acceleration error) work on datasets that
        # don't carry per-atom masses.
        try:
            from ase.data import atomic_masses
            return np.asarray(atomic_masses[np.asarray(z, dtype=int)], dtype=np.float64)
        except Exception as exc:
            logger.warning(
                "atom-color: reference.masses unavailable — no getMasses and ASE "
                "mass fallback failed: %s", exc,
            )
            raise KeyError(ref)
    # Dataset Fields (ADR 0023). Only a reference Atom Field is per-atom and thus
    # colorable; Frame Fields are per-frame (shape N_frames, filtered out of the
    # color picker) and prediction fields are not sourced in this path → fall
    # back to element colors via KeyError.
    from ffast.metrics.inputs import parse_field_ref
    parsed = parse_field_ref(ref)
    if parsed is not None:
        side, kind, key = parsed
        if side == "reference" and kind == "atoms":
            val = ds.getAtomField(key, indices=idx)
            if val is None:
                raise KeyError(ref)
            return np.asarray(val, dtype=np.float64)
        raise KeyError(ref)
    raise KeyError(ref)


def resolve_atom_color_values(state, ds, idx, raw_positions, z, get_prediction, executor=None):
    """Per-atom values for the active atom-color source, or None for element.

    ``executor`` is the injected ``MetricExecutor`` (ADR 0046) — the server
    threads its ``DataService.metricExecutor`` in via ``build_scene``; a
    metric-coloring request with no executor injected falls back to element
    colors, same as any other resolution failure.

    Returns ``(values (N,) float64, label, unit)``. Any failure (missing
    prediction, metric error, shape mismatch) returns None so the renderer falls
    back to element colors (ADR 0016).
    """
    params = state.parameters.get("ffast.atom_color", {})
    source = params.get("source", "element")
    # Per-stage override: an explicit "prediction_ref" key (even None, meaning
    # ground truth) wins; absent key falls back to the view's global prediction.
    prediction_ref = params["prediction_ref"] if "prediction_ref" in params else state.prediction_ref
    logger.debug("atom-color: requested source=%r params=%s", source, params)
    if not source or source == "element":
        return None
    try:
        if source == "displacement":
            from ffast.visualization.stages.builtin.color_stages import displacement_stats
            traj = np.asarray(ds.getCoordinates(), dtype=np.float64)
            if traj.ndim != 3 or traj.shape[1] != len(raw_positions):
                logger.warning(
                    "atom-color: displacement unavailable — coordinates shape %s is "
                    "not (frames, %d atoms, 3); falling back to element colors",
                    getattr(traj, "shape", None), len(raw_positions),
                )
                return None
            d_total, _ = displacement_stats(traj)
            logger.info("atom-color: displacement resolved for %d atoms", len(d_total))
            return np.asarray(d_total, dtype=np.float64), "displacement", "Å"
        if source.startswith("metric:"):
            # Register built-in metrics (idempotent; mirrors ffast/cli/main.py).
            from ffast.metrics.builtin import (  # noqa: F401
                accel_metrics, atomic_metrics, energy_metrics, force_metrics,
            )
            from ffast.metrics.models import MetricFailure
            from ffast.metrics.registry import _default_registry as reg
            metric_id = source.split(":", 1)[1]
            if not reg.has(metric_id):
                logger.warning(
                    "atom-color: metric %r is not registered; falling back to element "
                    "colors", metric_id,
                )
                return None
            if executor is None:
                logger.warning(
                    "atom-color: no metric executor injected; falling back to "
                    "element colors"
                )
                return None
            resolve = lambda ref: _resolve_symbolic_ref(
                ref, ds, idx, raw_positions, z, get_prediction, state, prediction_ref
            )
            result = executor.run(metric_id, _FrameInputSource(resolve), params)
            if isinstance(result, MetricFailure):
                logger.warning(
                    "atom-color: metric %s failed: %s", metric_id, result.traceback,
                )
                return None
            vals = np.asarray(result.values, dtype=np.float64).ravel()
            schema, _ = reg.get(metric_id)
            from ffast.metrics.dims import shape_to_str
            if shape_to_str(schema.shape) == "N_elements":
                # Broadcast per-element values onto atoms. Per-element metrics
                # order their output by sorted unique Z (np.unique), so map each
                # atom's element to its slot the same way.
                zarr = np.asarray(z).ravel()
                unique_z = np.unique(zarr)
                if len(vals) != len(unique_z):
                    logger.warning(
                        "atom-color: per-element metric %s returned %d values for %d "
                        "elements; falling back to element colors",
                        metric_id, len(vals), len(unique_z),
                    )
                    return None
                vals = vals[np.searchsorted(unique_z, zarr)]
            if len(vals) != len(raw_positions):
                logger.warning(
                    "atom-color: metric %s returned %d values for %d atoms (shape "
                    "mismatch); falling back to element colors",
                    metric_id, len(vals), len(raw_positions),
                )
                return None
            logger.info(
                "atom-color: metric %s resolved for %d atoms (range %.4g..%.4g)",
                metric_id, len(vals),
                float(np.min(vals)) if len(vals) else 0.0,
                float(np.max(vals)) if len(vals) else 0.0,
            )
            return vals, (schema.label or metric_id), schema.unit
        logger.warning(
            "atom-color: unknown source %r; falling back to element colors", source,
        )
    except Exception as exc:
        logger.warning("atom-color: color source %r failed: %s", source, exc)
        return None
    return None

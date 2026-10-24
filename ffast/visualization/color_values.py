"""Value-driven atom coloring (ADR 0016) — resolve per-atom color values.

Extracted from ``scene_builder.build_scene`` so the symbolic-ref→array
resolution and metric execution that drive atom coloring live in one cohesive,
unit-testable module rather than inline in the 700-line scene orchestrator.

The active color *source* (``state.parameters['ffast.atom_color']['source']``)
is one of:

- ``element`` / unset    → ``None`` (renderer falls back to element colors)
- ``displacement``       → per-atom RMS displacement over the trajectory
- ``metric:<metric_id>`` → the metric over the whole dataset, as the 2D plots
  compute and store it, cut to the frame on screen; the colour range then
  covers every frame. When the whole dataset can't be computed from what is
  loaded (a prediction not made for every frame), the metric runs on the
  current frame's inputs only.

The frame-only path maps refs to the *currently displayed* frame (``idx``) —
distinct from ``ffast/metrics/input_resolver``, which resolves whole-dataset
arrays for 2D panels. Any failure (missing prediction, metric error, shape
mismatch) returns ``None`` so coloring degrades to element colors (ADR 0016).
"""
from __future__ import annotations

import logging

import numpy as np

from ffast.loaders.dataset import stackFrames
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
    if ref == "offsets":
        # One frame: its atoms start at 0 (ADR 0058 step 6, flat inputs).
        return np.array([0, len(raw_positions)])
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


def frame_colour_label(label: str, shape: str) -> str:
    """The name of a colouring as the 3D view shows it. A per-element metric
    colours by the frame on screen, while its 2D plots average all frames, so
    in 3D its name says "this frame": "Force MAE (by element)" becomes
    "Force MAE (by element, this frame)"."""
    if shape != "N_elements":
        return label
    if label.endswith(")"):
        return label[:-1] + ", this frame)"
    return label + " (this frame)"


def _metric_parameters(params, schema):
    """The colouring parameters the metric declares; the rest (source,
    colour map, limits) would only split its stored result from the plots'."""
    return {k: v for k, v in params.items() if k in schema.parameters}


def _whole_dataset_values(get_metric, metric_id, params, shape, ds, idx, z, dataset_ref,
                          prediction_ref):
    """``(values of frame idx, (lowest, highest) over every frame)`` from the
    metric over the whole dataset, or None when that result isn't available.

    A per-atom result has a row per atom, frame after frame, or one row per
    atom of a structure when it already averages over frames (every structure
    has the same atoms). A per-element result has one value per element of
    the dataset, in sorted order."""
    if get_metric is None:
        return None
    result = get_metric(metric_id, params, dataset_ref, prediction_ref)
    if result is None:
        return None
    vals = np.asarray(result.values, dtype=np.float64).ravel()
    zarr = np.asarray(z).ravel()
    if shape == "(N_frames, N_elements)":
        # A row per structure, a column per element of the dataset (ADR 0064
        # amendment): the frame on screen reads its row.
        unique_z = np.unique(np.asarray(ds.getAllElements()))
        if len(unique_z) == 0 or len(vals) % len(unique_z):
            return None
        row = vals.reshape(-1, len(unique_z))[idx]
        frame = row[np.searchsorted(unique_z, zarr)]
    elif shape == "N_elements":
        unique_z = np.unique(np.asarray(ds.getAllElements()))
        if len(vals) != len(unique_z):
            return None
        frame = vals[np.searchsorted(unique_z, zarr)]
    elif shape == "N_atoms":
        offsets = np.asarray(ds.getOffsets())
        if len(vals) == offsets[-1]:
            frame = vals[offsets[idx]:offsets[idx + 1]]
        elif len(vals) == len(zarr):
            frame = vals
        else:
            return None
    else:
        return None
    finite = vals[np.isfinite(vals)]
    span = (float(finite.min()), float(finite.max())) if finite.size else None
    return frame, span


def resolve_atom_color_values(state, ds, idx, raw_positions, z, get_prediction, executor=None,
                              get_metric=None):
    """Per-atom values for the active atom-color source, or None for element.

    ``executor`` is the injected ``MetricExecutor`` (ADR 0046) — the server
    threads its ``DataService.metricExecutor`` in via ``build_scene``; a
    metric-coloring request with no executor injected falls back to element
    colors, same as any other resolution failure.

    ``get_metric(metric_id, params, dataset_ref, prediction_ref)`` gives the
    metric's result over the whole dataset, or None when it can't be computed
    from what is loaded; the colouring then runs on this frame alone.

    Returns ``(values (N,) float64, label, unit, span)``: ``span`` is the
    ``(lowest, highest)`` value over every frame, or None when only this frame
    was computed. Any failure (missing prediction, metric error, shape
    mismatch) returns None so the renderer falls back to element colors
    (ADR 0016).
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
            try:
                traj = np.asarray(stackFrames(ds.getAllCoordinates(), ds.getOffsets()),
                                  dtype=np.float64)
            except ValueError:
                logger.warning(
                    "atom-color: displacement unavailable — structures differ in size; "
                    "falling back to element colors")
                return None
            if traj.shape[1] != len(raw_positions):
                logger.warning(
                    "atom-color: displacement unavailable — coordinates shape %s is "
                    "not (frames, %d atoms, 3); falling back to element colors",
                    traj.shape, len(raw_positions),
                )
                return None
            d_total, _ = displacement_stats(traj)
            logger.info("atom-color: displacement resolved for %d atoms", len(d_total))
            return np.asarray(d_total, dtype=np.float64), "displacement", "Å", None
        if source.startswith("metric:"):
            # Register built-in metrics (idempotent; mirrors ffast/cli/main.py).
            from ffast.metrics.builtin import (  # noqa: F401
                accel_metrics, atomic_metrics, energy_metrics, force_metrics,
            )
            from ffast.metrics.models import MetricFailure
            from ffast.metrics.registry import _default_registry as reg
            metric_id = source.split(":", 1)[1]
            if not reg.ensure(metric_id):
                from ffast.metrics.retired import retired_message
                logger.warning(
                    "atom-color: %s; falling back to element colors",
                    retired_message(metric_id) or f"metric {metric_id!r} is not registered",
                )
                return None
            from ffast.metrics.dims import shape_to_str
            schema, _ = reg.get(metric_id)
            shape = shape_to_str(schema.shape)
            whole = _whole_dataset_values(
                get_metric, metric_id, _metric_parameters(params, schema), shape, ds, idx, z,
                state.dataset_ref, prediction_ref,
            )
            if whole is not None:
                vals, span = whole
                if len(vals) == len(raw_positions):
                    logger.info("atom-color: metric %s resolved from the whole dataset",
                                metric_id)
                    return vals, schema.label or metric_id, schema.unit, span
            from ffast.metrics.expr import metric_colour_problem
            problem = metric_colour_problem(metric_id, reg)
            if problem:
                # ADR 0064: by=all / by=element would mean "this frame" here.
                logger.warning("atom-color: %s: %s; falling back to element colors",
                               metric_id, problem)
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
            if shape in ("N_elements", "(N_frames, N_elements)"):
                # Broadcast per-element values onto atoms. Per-element metrics
                # order their output by sorted unique Z (np.unique), so map each
                # atom's element to its slot the same way. A table (ADR 0064
                # amendment) run on this one frame is its row: one value per
                # element of the frame, in the same order.
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
            label = frame_colour_label(schema.label or metric_id, shape)
            return vals, label, schema.unit, None
        logger.warning(
            "atom-color: unknown source %r; falling back to element colors", source,
        )
    except Exception as exc:
        logger.warning("atom-color: color source %r failed: %s", source, exc)
        return None
    return None

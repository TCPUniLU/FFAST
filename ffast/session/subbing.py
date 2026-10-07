"""Which frames a plot's view covers: subbing in the browser.

While SUB is ticked on a browser plot, the frames inside the plot's visible
range are a live SubDataset that follows the zoom, as on the desktop. The
browser sends the view (the panel kind, its metrics and their params, and the
x and y ranges) with ``DECLARE_SUBSET``; this works out the parent's
configuration indices with the desktop's rules (``UI/panels.py``,
``PanelKind.sub_indices``):

- timeline, overlay_timeline: x is the frame index, so the frames on screen.
- scatter: the points inside the box, over the full arrays (drawing may thin
  them; subbing does not). Per-atom metrics (force components) map each
  component back to its frame.
- density: the curve has no frame index, so the per-frame values it was drawn
  from (its ``src`` input) are filtered by the x range. The x axis is
  ``|value|``, minus the energy shift when the curve is shifted.
"""
from __future__ import annotations

import math

import numpy as np

FRAME_KINDS = ("timeline", "overlay_timeline")


def frames_in_x_range(x_range, n_frames: int) -> list[int]:
    """Frames whose index lies inside ``x_range``, within ``0..n_frames-1``."""
    lo = max(0, math.ceil(min(x_range)))
    hi = min(n_frames - 1, math.floor(max(x_range)))
    return list(range(lo, hi + 1))


def _span(r):
    return min(r), max(r)


def points_in_box(x, y, x_range, y_range) -> np.ndarray:
    """Indices of the points inside the box."""
    x = np.asarray(x, dtype=float).ravel()
    y = np.asarray(y, dtype=float).ravel()
    (x0, x1), (y0, y1) = _span(x_range), _span(y_range)
    return np.flatnonzero((x >= x0) & (x <= x1) & (y >= y0) & (y <= y1))


def values_in_range(values, x_range, shift: float | None = None) -> np.ndarray:
    """Indices of the values whose size, after ``shift``, lies in ``x_range``."""
    v = np.asarray(values, dtype=float).ravel()
    if shift is not None:
        v = v - shift
    v = np.abs(v)
    x0, x1 = _span(x_range)
    return np.flatnonzero((v >= x0) & (v <= x1))


def flat_to_frames(flat, *, n_atoms: int | None = None, offsets=None) -> np.ndarray:
    """Frames of flat force-component indices: frames of different sizes use
    their atom ``offsets``; same-sized frames divide by components per frame."""
    flat = np.asarray(flat, dtype=int)
    if offsets is not None:
        components = np.asarray(offsets) * 3
        return np.unique(np.searchsorted(components[1:], flat, side="right"))
    return np.unique(flat // (int(n_atoms) * 3))


def source_metric(registry, metric_id: str) -> str | None:
    """The per-frame metric a reduced metric (a density) was drawn from: its
    ``src`` input, else the first input that is itself a metric."""
    if not registry.has(metric_id):
        return None
    inputs = registry.get(metric_id)[0].inputs
    src = inputs.get("src")
    if src and registry.has(src):
        return src
    return next((ref for ref in inputs.values() if registry.has(ref)), None)


def _named_input(registry, metric_id: str, key: str) -> str | None:
    if not registry.has(metric_id):
        return None
    ref = registry.get(metric_id)[0].inputs.get(key)
    return ref if ref and registry.has(ref) else None


def _per_atom(registry, metric_id: str) -> bool:
    if not registry.has(metric_id):
        return False
    shape = registry.get(metric_id)[0].shape
    dims = shape if isinstance(shape, tuple) else (shape,)
    return any(getattr(d, "name", str(d)) == "N_atoms" for d in dims)


def frames_in_view(view: dict, *, n_frames: int, value_of, registry, dataset=None) -> list[int]:
    """The parent frames a plot's ``view`` covers.

    ``value_of(metric_id, params)`` gives a metric's values for the series, or
    None. ``dataset`` (the parent) is needed only to map force components of
    a per-atom scatter to frames.
    """
    kind = view["kind"]
    metrics = view.get("metrics") or {}
    params = view.get("params") or {}
    x_range, y_range = view["x"], view.get("y")

    if kind in FRAME_KINDS:
        return frames_in_x_range(x_range, n_frames)

    if kind == "scatter":
        xs = value_of(metrics["x"], params.get(metrics["x"], {}))
        ys = value_of(metrics["y"], params.get(metrics["y"], {}))
        if xs is None or ys is None or y_range is None:
            return []
        flat = points_in_box(xs, ys, x_range, y_range)
        if _per_atom(registry, metrics["x"]):
            if getattr(dataset, "isVariable", False):
                return flat_to_frames(flat, offsets=dataset.molecule_offsets).tolist()
            return flat_to_frames(flat, n_atoms=dataset.getNAtoms()).tolist()
        return np.unique(flat).tolist()

    if kind == "density":
        value = metrics["value"]
        src = source_metric(registry, value)
        values = value_of(src, {}) if src else None
        if values is None:
            return []
        shift = None
        if params.get(value, {}).get("shifted"):
            shift_mid = _named_input(registry, value, "shift")
            sv = value_of(shift_mid, {}) if shift_mid else None
            if sv is not None and np.asarray(sv).size:
                shift = float(np.asarray(sv).ravel()[0])
        return values_in_range(values, x_range, shift).tolist()

    raise ValueError(f"{kind} plots have no frames to make a subset from")

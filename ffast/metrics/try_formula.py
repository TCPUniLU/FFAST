"""Try a formula on a whole dataset without saving it (ADR 0057 rule 12).

Evaluates the formula over every frame, in chunks of frames so a long run can
report progress and stop cleanly when cancelled. Chunking gives the same answer
as one pass because a formula is element-wise. Nothing is registered and
nothing is cached: this is a look at the numbers, so that bad values (infinity,
not-a-number) are found *before* saving, where they would fail the metric.

Pure: the caller supplies ``resolve(ref)`` (full arrays for one dataset and
prediction) and the per-structure atom counts; this module knows nothing of
the environment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from ffast.metrics import dims
from ffast.metrics.dims import shape_to_str
from ffast.metrics.expr import _ExprFn, analyze_expr, resolve_ref_shape

CHUNK_FRAMES = 2000
BINS = 20
MAX_BAD_FRAMES = 20


class TryError(ValueError):
    """The formula cannot be tried here; the message is shown to the user."""


@dataclass
class TryResult:
    shape: str | None = None
    cancelled: bool = False
    count: int = 0                      # values computed, bad ones included
    bad_count: int = 0
    min: float | None = None
    mean: float | None = None
    max: float | None = None
    bad_frames: list[int] = field(default_factory=list)   # first few, in order
    histogram: dict | None = None       # {"edges": [...], "counts": [...]}

    def to_dict(self) -> dict:
        return {
            "shape": self.shape, "cancelled": self.cancelled, "count": self.count,
            "bad_count": self.bad_count, "min": self.min, "mean": self.mean,
            "max": self.max, "bad_frames": self.bad_frames, "histogram": self.histogram,
        }


def _lead(shape):
    return shape[0] if shape else dims.scalar


def try_formula(expr: str, variables: dict[str, str], registry,
                resolve: Callable[[str], object], n_atoms: np.ndarray, *,
                chunk_frames: int = CHUNK_FRAMES,
                on_progress: Callable[[int, int], None] | None = None,
                cancelled: Callable[[], bool] = lambda: False) -> TryResult:
    """Evaluate ``expr`` over all frames.

    ``variables`` maps each variable to its input ref; ``resolve(ref)`` gives
    that input's full array (``None`` if unavailable here); ``n_atoms`` is the
    atom count of every frame. ``on_progress(done_frames, total_frames)`` is
    called after each chunk; ``cancelled()`` is asked before each.
    """
    try:
        inputs, out_shape, var_parts = analyze_expr(expr, variables, registry=registry)
    except ValueError as exc:
        raise TryError(str(exc)) from None
    fn = _ExprFn(expr, inputs, var_parts)
    shapes = {n: tuple(resolve_ref_shape(r, registry)) for n, r in inputs.items()}

    n_atoms = np.asarray(n_atoms)
    n_frames = len(n_atoms)
    atom_offsets = np.concatenate([[0], np.cumsum(n_atoms)])

    full: dict[str, np.ndarray] = {}
    for name, ref in inputs.items():
        if ref == "n_atoms":
            full[name] = n_atoms
            continue
        value = resolve(ref)
        if value is None:
            raise TryError(f"{ref} is not available for this dataset and prediction")
        full[name] = np.asarray(value)

    leads = {_lead(sh) for sh in shapes.values() if sh != (dims.scalar,)}
    out_lead = _lead(out_shape)
    chunkable = leads <= {dims.N_frames, dims.N_atoms} and out_lead in (dims.N_frames, dims.N_atoms)

    result = TryResult(shape=shape_to_str(out_shape))
    finite_parts: list[np.ndarray] = []
    total = 0
    bad_total = 0
    bad_frames: list[int] = []
    sums = 0.0
    lo, hi = None, None

    def take(out, frame0):
        """Fold one chunk's output into the running statistics."""
        nonlocal total, bad_total, sums, lo, hi
        arr = np.asarray(out, dtype=float)
        flat_axes = tuple(range(1, arr.ndim))
        bad_elem = ~np.isfinite(arr)
        # Which entries (rows of the leading axis) hold a bad value.
        bad_rows = np.flatnonzero(bad_elem.any(axis=flat_axes) if flat_axes else bad_elem)
        total += arr.size
        bad_total += int(bad_elem.sum())
        good = arr[~bad_elem]
        if good.size:
            finite_parts.append(good)
            sums += float(good.sum())
            lo = float(good.min()) if lo is None else min(lo, float(good.min()))
            hi = float(good.max()) if hi is None else max(hi, float(good.max()))
        if len(bad_frames) < MAX_BAD_FRAMES and bad_rows.size:
            if out_lead == dims.N_atoms:
                frames = np.searchsorted(atom_offsets, atom_offsets_base + bad_rows, side="right") - 1
            elif out_lead == dims.N_frames:
                frames = frame0 + bad_rows
            else:
                frames = np.array([], dtype=int)
            for f in dict.fromkeys(int(x) for x in frames):
                if f not in bad_frames and len(bad_frames) < MAX_BAD_FRAMES:
                    bad_frames.append(f)

    atom_offsets_base = 0
    if not chunkable:
        # No frame axis to cut along (a scalar, or per-element result): one pass.
        if cancelled():
            result.cancelled = True
            return result
        take(fn.evaluate(**full), 0)
        if on_progress:
            on_progress(n_frames, n_frames)
    else:
        for f0 in range(0, n_frames, chunk_frames):
            if cancelled():
                result.cancelled = True
                return result
            f1 = min(n_frames, f0 + chunk_frames)
            a0, a1 = int(atom_offsets[f0]), int(atom_offsets[f1])
            kwargs = {}
            for name, arr in full.items():
                lead = _lead(shapes[name])
                if lead == dims.N_frames:
                    kwargs[name] = arr[f0:f1]
                elif lead == dims.N_atoms:
                    kwargs[name] = arr[a0:a1]
                else:
                    kwargs[name] = arr           # scalar: the same in every chunk
            atom_offsets_base = a0
            take(fn.evaluate(**kwargs), f0)
            if on_progress:
                on_progress(f1, n_frames)

    result.count = total
    result.bad_count = bad_total
    result.bad_frames = bad_frames
    if finite_parts:
        values = np.concatenate([p.ravel() for p in finite_parts])
        result.min, result.max = lo, hi
        result.mean = sums / values.size
        counts, edges = np.histogram(values, bins=BINS)
        result.histogram = {"edges": edges.tolist(), "counts": counts.tolist()}
    return result

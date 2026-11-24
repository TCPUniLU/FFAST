"""Bounded kernels for metrics over indexed arrays (ADR 0102)."""

from __future__ import annotations

import ast
import copy
import math

import numpy as np

from ffast.io.batched import BatchedArray, asarray, blocks, mapped
from ffast.io.budget import budget
from ffast.io.result_array import store_result

NOT_HANDLED = object()


def frame_map(kernel, offsets, kwargs, tail=()):
    offsets = np.asarray(offsets)
    n = len(offsets) - 1
    per_row = sum(
        v.dtype.itemsize * math.prod(v.shape[1:])
        for v in kwargs.values()
        if hasattr(v, "shape") and v.shape and v.shape[0] == offsets[-1]
    )
    target = max(1, budget().block_bytes // max(1, 4 * per_row))

    def read(a, b):
        out = np.empty((b - a, *tail), dtype=float)
        start = a
        while start < b:
            end = (
                int(
                    np.searchsorted(
                        offsets, offsets[start] + target, side="right"
                    )
                )
                - 1
            )
            end = max(start + 1, min(end, b))
            lo, hi = offsets[start], offsets[end]
            local = {
                k: (
                    np.asarray(v[lo:hi])
                    if hasattr(v, "shape")
                    and v.shape
                    and v.shape[0] == offsets[-1]
                    else v
                )
                for k, v in kwargs.items()
            }
            local["offsets"] = offsets[start : end + 1] - lo
            result = kernel(**local)
            from ffast.metrics.models import WithWarnings

            if isinstance(result, WithWarnings):
                result = result.values
            out[start - a : end - a] = result
            start = end
        return out

    return BatchedArray((n, *tail), float, read)


def expression_reduction(fn, node, ns, value, parts, gap):
    from ffast.metrics.expr import (
        _by_key,
        _keyword,
        _OFFSETS,
        _ELEMENTS,
        _IGNORED,
        MISSING_PREFIX,
        PAIR,
        _check_same_atoms,
    )

    by = _by_key(_keyword(node, "by"))
    where_node = _keyword(node, "where")
    where = fn._eval(where_node, ns) if where_node is not None else None
    offsets = np.asarray(ns.get(_OFFSETS, [0, len(value)]), dtype=np.int64)
    elements = ns.get(_ELEMENTS)
    columns = np.unique(elements) if by in ("element", PAIR) else None

    if by in ("structure", PAIR):
        # Independent frame reductions reuse the existing exact kernel, with
        # complete frames and offsets rebased to the batch.
        n = len(offsets) - 1
        tail = value.shape[1:]
        shape = (n, *((len(columns),) if by == PAIR else ()), *tail)
        gap_shape = shape[:-parts] if parts else shape
        ignored_seen = set()

        def calculate_part(a, b):
            lo, hi = int(offsets[a]), int(offsets[b])
            local = {
                **ns,
                _OFFSETS: offsets[a : b + 1] - lo,
                "_frame_base": a,
                "_row_base": lo,
                "_stream_ignored_seen": ignored_seen,
            }
            local["_stream_value"] = np.asarray(value[lo:hi])
            if elements is not None:
                local[_ELEMENTS] = np.asarray(elements[lo:hi])
            if columns is not None:
                local["_stream_columns"] = columns
            temporary = copy.copy(fn)
            temporary.var_parts = {**fn.var_parts, "_stream_value": parts}
            temporary.gap_inputs = frozenset()
            substitute = copy.deepcopy(node)
            substitute.args[0] = ast.Name(id="_stream_value", ctx=ast.Load())
            if gap is not None:
                local[MISSING_PREFIX + "_stream_value"] = np.asarray(
                    gap[lo:hi]
                )
            if where is not None:
                w, wk, wg = where
                local["_stream_where"] = (
                    np.asarray(w[lo:hi])
                    if hasattr(w, "shape") and w.ndim
                    else w
                )
                if wg is not None:
                    local[MISSING_PREFIX + "_stream_where"] = np.asarray(
                        wg[lo:hi]
                    )
                for keyword in substitute.keywords:
                    if keyword.arg == "where":
                        keyword.value = ast.Name(
                            id="_stream_where", ctx=ast.Load()
                        )
            out, k, missing = temporary._reduce(substitute, local)
            missing = (
                np.zeros(out.shape[:-k] if k else out.shape, bool)
                if missing is None
                else missing
            )
            return out, missing

        last = [None, None]

        def calculate(a, b):
            if last[0] == (a, b):
                return last[1]
            values, missing = [], []
            start = a
            rows = max(
                1,
                budget().block_bytes
                // max(1, value.dtype.itemsize * math.prod(tail) + 8),
            )
            while start < b:
                end = (
                    int(
                        np.searchsorted(
                            offsets, offsets[start] + rows, side="right"
                        )
                    )
                    - 1
                )
                end = max(start + 1, min(end, b))
                result, gaps = calculate_part(start, end)
                values.append(result)
                missing.append(gaps)
                start = end
            result = (np.concatenate(values), np.concatenate(missing))
            last[:] = [(a, b), result]
            return result

        out = BatchedArray(shape, float, lambda a, b: calculate(a, b)[0])
        missing = BatchedArray(
            gap_shape, bool, lambda a, b: calculate(a, b)[1]
        )
        return out, parts, missing

    if by == "atom":
        _check_same_atoms(offsets, elements)
        groups = int(offsets[1] - offsets[0]) if len(offsets) > 1 else 0
    elif by == "element":
        groups = len(columns)
    else:
        groups = 1
    budget().check(max(1, groups) * max(1, math.prod(value.shape[1:])) * 40)
    shape = (groups, *value.shape[1:])
    counts = np.zeros(groups)
    sums = np.zeros(shape)
    squares = np.zeros(shape)
    extrema = np.full(shape, np.inf if node.func.id == "min" else -np.inf)
    bad = np.zeros(shape, bool)

    def chunks(note=True):
        for a, b, v in blocks(value):
            v = np.asarray(v, dtype=float)
            keep = (
                np.ones(b - a, bool)
                if gap is None
                else ~np.asarray(gap[a:b], dtype=bool)
            )
            if where is not None:
                w, _, wg = where
                w = np.asarray(
                    w[a:b] if hasattr(w, "shape") and w.ndim else w,
                    dtype=float,
                )
                w = np.broadcast_to(w, (b - a,))
                if note:
                    bad_rows = ~np.isfinite(v)
                    if parts:
                        bad_rows = bad_rows.any(axis=tuple(range(-parts, 0)))
                    ignored = np.flatnonzero(keep & (w == 0) & bad_rows) + a
                    if ignored.size:
                        frames = []
                        if _OFFSETS in ns and len(value) == offsets[-1]:
                            frames = (
                                np.searchsorted(offsets, ignored, side="right")
                                - 1
                            )
                        elif _OFFSETS in ns and len(value) == len(offsets) - 1:
                            frames = ignored
                        ns[_IGNORED].append(
                            (len(ignored), [int(f) for f in frames])
                        )
                keep &= (w != 0) | ~np.isfinite(w)
                if wg is not None:
                    keep &= ~np.asarray(wg[a:b])
                v = np.where(
                    (~np.isfinite(w)).reshape((-1,) + (1,) * parts), np.nan, v
                )
            if by == "atom":
                index = np.arange(a, b) % groups
            elif by == "element":
                index = np.searchsorted(columns, np.asarray(elements[a:b]))
            else:
                index = np.zeros(b - a, dtype=np.intp)
            yield v, keep, index

    for v, keep, index in chunks():
        mask = keep.reshape((-1,) + (1,) * parts)
        np.add.at(counts, index, keep)
        np.add.at(sums, index, np.where(mask, v, 0))
        np.add.at(squares, index, np.where(mask, v * v, 0))
        np.logical_or.at(bad, index, mask & ~np.isfinite(v))
        extreme = np.minimum if node.func.id == "min" else np.maximum
        extreme.at(
            extrema,
            index,
            np.where(mask, v, np.inf if node.func.id == "min" else -np.inf),
        )
    number = counts.reshape((-1,) + (1,) * parts)
    with np.errstate(all="ignore"):
        name = node.func.id
        if name == "sum":
            out = sums
        elif name == "mean":
            out = sums / number
        elif name == "rms":
            out = np.sqrt(squares / number)
        elif name in ("min", "max"):
            out = np.where(bad, np.nan, extrema)
        else:
            mean = sums / number
            deviation = np.zeros(shape)
            for v, keep, index in chunks(note=False):
                mask = keep.reshape((-1,) + (1,) * parts)
                np.add.at(
                    deviation, index, np.where(mask, (v - mean[index]) ** 2, 0)
                )
            out = np.sqrt(deviation / number)
    empty = counts == 0
    if by == "all":
        out, empty = out[0], empty[0]
    return out, parts, (empty if np.any(empty) else None)


def smooth(value, window):
    from ffast.metrics.transforms import _smooth

    value = asarray(value, dtype=float)
    if value.ndim != 2:          # a table (frames × elements) keeps its columns
        value = value.ravel()
    w = max(1, min(int(window), len(value)))
    return BatchedArray(
        (max(0, len(value) - w + 1), *value.shape[1:]),
        float,
        lambda a, b: _smooth(np.asarray(value[a : b + w - 1]), w),
    )


def kde(value, kind="mirror"):
    from ffast.metrics import transforms as t

    value = asarray(value, dtype=float).ravel()

    def samples():
        for _, _, chunk in blocks(value):
            sample = chunk[np.isfinite(chunk)]
            if kind != "value":
                sample = np.abs(sample)
            if kind == "mirror":
                sample = np.concatenate((sample, -sample))
            yield sample

    n, mean, m2 = 0, 0.0, 0.0
    lo, hi = np.inf, -np.inf
    small = []
    for sample in samples():
        if not sample.size:
            continue
        m = sample.size
        mu = float(sample.mean())
        delta = mu - mean
        m2 += float(np.sum((sample - mu) ** 2)) + delta * delta * n * m / (
            n + m
        )
        mean += delta * m / (n + m)
        n += m
        lo, hi = min(lo, sample.min()), max(hi, sample.max())
        if n < t._EXACT_KDE_MAX:
            small.append(sample)
        else:
            small.clear()
    if kind == "mirror":
        bounds = (0.0, max(abs(lo), abs(hi)) * 1.05)
    elif kind == "abs":
        bounds = lo * 0.95, hi * 1.05
    else:
        bounds = lo - (hi - lo) * 0.05, hi + (hi - lo) * 0.05
    if n < t._EXACT_KDE_MAX:
        sample = np.concatenate(small) if small else np.array([])
        return t._kde_xy(sample, lambda s: bounds)
    if n < 2 or math.sqrt(m2 / n) < 1e-10:
        x = np.linspace(0, max(float(hi), 1e-10) if n else 1.0, 200)
        return np.vstack((x, np.zeros_like(x)))
    from scipy.signal import fftconvolve

    sigma = math.sqrt(m2 / (n - 1)) * n ** (-1 / 5)
    left, right = lo - 5 * sigma, hi + 5 * sigma
    bins = int(min(1 << 22, max(256, math.ceil((right - left) / sigma * 8))))
    budget().check(bins * 64)
    step = (right - left) / (bins - 1)
    counts = np.zeros(bins)
    for sample in samples():
        x = (sample - left) / step
        i = np.minimum(x.astype(np.int64), bins - 2)
        w = x - i
        counts += np.bincount(i, 1 - w, bins) + np.bincount(i + 1, w, bins)
    half = int(math.ceil(5 * sigma / step))
    kernel = np.exp(-0.5 * (np.arange(-half, half + 1) * step / sigma) ** 2)
    kernel /= kernel.sum()
    density = fftconvolve(counts, kernel, mode="same") / (n * step)
    x = np.linspace(*bounds, 200)
    return np.vstack(
        (
            x,
            np.interp(
                x, left + step * np.arange(bins), np.maximum(density, 0)
            ),
        )
    )


def parity(true, pred, bins=300):
    t, p = asarray(true).ravel(), asarray(pred).ravel()
    lo, hi = np.inf, -np.inf
    for a, b, x in blocks(t):
        x, y = x.astype(np.float64), np.asarray(p[a:b], dtype=np.float64)
        keep = np.isfinite(x) & np.isfinite(y)
        if keep.any():
            lo = min(lo, x[keep].min(), y[keep].min())
            hi = max(hi, x[keep].max(), y[keep].max())
    if not np.isfinite(lo):
        lo, hi = 0.0, 1.0
    if hi <= lo:
        hi = lo + 1
    width = (hi - lo) / bins
    counts = np.zeros(bins * bins, dtype=np.int64)
    for a, b, x in blocks(t):
        x, y = x.astype(np.float64), np.asarray(p[a:b], dtype=np.float64)
        keep = np.isfinite(x) & np.isfinite(y)
        col = np.minimum(((x[keep] - lo) / width).astype(np.int64), bins - 1)
        row = np.minimum(((y[keep] - lo) / width).astype(np.int64), bins - 1)
        counts += np.bincount(row * bins + col, minlength=bins * bins)
    return np.vstack(
        (lo + (np.arange(bins) + 0.5) * width, counts.reshape(bins, bins))
    )


def worst(true, pred, offsets=None, *, components=True, k=50_000):
    t, p = asarray(true).ravel(), asarray(pred).ravel()
    indices, errors = np.zeros(0, dtype=np.int64), np.zeros(0)
    # The true and predicted values travel with their errors: reading the
    # k kept ones back afterwards took one read each, 100,000 reads and
    # 12 s for the energies of 7.1M structures from the parsed cache.
    true_kept, pred_kept = np.zeros(0), np.zeros(0)
    for a, b, x in blocks(t):
        x, y = x.astype(np.float64), np.asarray(p[a:b], dtype=np.float64)
        err = np.abs(y - x)
        idx = np.flatnonzero(np.isfinite(err))
        if len(idx) > k:
            idx = idx[np.argpartition(-err[idx], k - 1)[:k]]
        indices = np.concatenate((indices, idx + a))
        errors = np.concatenate((errors, err[idx]))
        true_kept = np.concatenate((true_kept, x[idx]))
        pred_kept = np.concatenate((pred_kept, y[idx]))
        if len(indices) > k:
            keep = np.argpartition(-errors, k - 1)[:k]
            indices, errors = indices[keep], errors[keep]
            true_kept, pred_kept = true_kept[keep], pred_kept[keep]
    order = np.argsort(-errors, kind="stable")
    indices = indices[order]
    values = [true_kept[order], pred_kept[order]]
    if components:
        offsets = np.asarray(
            offsets if offsets is not None else [0, len(t) // 3]
        )
        atoms = indices // 3
        frames = np.searchsorted(offsets[1:], atoms, side="right")
        values += [frames, atoms - offsets[frames], indices % 3]
    else:
        values += [indices]
    return np.column_stack(values).astype(float)


def execute(fn, kwargs, params):
    """Return NOT_HANDLED only when no safe streaming kernel is registered."""
    from ffast.metrics.expr import _ExprFn
    from ffast.metrics.transforms import _TransformFn

    if not any(isinstance(v, BatchedArray) for v in kwargs.values()):
        return NOT_HANDLED
    if isinstance(fn, (_ExprFn, _TransformFn)):
        return fn(**kwargs, **params)
    name = getattr(fn, "__name__", "")
    if name in ("energy_reference", "force_reference"):
        return kwargs["reference"]
    if name in ("energy_prediction", "force_prediction"):
        return kwargs["predicted"]
    if name in ("energy_difference", "force_difference"):
        return kwargs["predicted"] - kwargs["reference"]
    if name == "energy_difference_shifted":
        v = kwargs["energy_difference"]
        return v - v.mean()
    if name in (
        "force_parity_density",
        "energy_parity_density",
        "force_parity_outliers",
        "energy_parity_outliers",
    ):
        t, p = kwargs["reference"], kwargs["predicted"]
        if params.get("shifted"):
            p = p - float(kwargs["shift"])
        if name.endswith("density"):
            return parity(t, p)
        return worst(
            t, p, kwargs.get("offsets"), components=name.startswith("force")
        )
    if name in (
        "force_rmse_smoothed",
        "force_mae_per_structure_smoothed",
        "energy_error_smoothed",
    ):
        v = kwargs["src"]
        if name == "energy_error_smoothed" and params.get("shifted"):
            v = v - float(kwargs["shift"])
        out = smooth(v, params.get("window", 1))
        return np.abs(out) if name == "energy_error_smoothed" else out
    if name.endswith("_density") and "src" in kwargs:
        v = kwargs["src"]
        if params.get("shifted"):
            v = v - float(kwargs["shift"])
        return kde(v)
    if name in ("accel_difference",):
        source = next(
            v for v in kwargs.values() if isinstance(v, BatchedArray)
        )
        return BatchedArray(
            source.shape,
            float,
            lambda a, b: fn(
                **{
                    k: (
                        np.asarray(v[a:b])
                        if hasattr(v, "shape")
                        and v.shape
                        and v.shape[0] == len(source)
                        else v
                    )
                    for k, v in kwargs.items()
                },
                **params,
            ),
        )
    if name in ("gyradius", "force_net_residual"):
        offsets = np.asarray(kwargs.get("offsets"))
        tail = () if name == "gyradius" else (3,)
        result = frame_map(
            lambda **local: fn(**local, **params), offsets, kwargs, tail
        )
        if name == "gyradius" and kwargs.get("periodic"):
            from ffast.metrics.models import WithWarnings
            from ffast.metrics.builtin.structure_metrics import (
                PERIODIC_WARNING,
            )

            return WithWarnings(store_result(result), [PERIODIC_WARNING])
        return result
    if name == "force_mae_per_structure_per_element_kde":
        from ffast.io.result_array import result_writer

        # One pass over the forces for every element: each structure's MAE
        # per element is written to one stored column per element, and each
        # curve reads its column (twice above the exact-KDE size), never the
        # forces again. One pass per element took ~70 s each on 463M atoms.
        offsets = np.asarray(kwargs["offsets"])
        unique = np.unique(kwargs["elements"])
        width = len(unique)

        def kernel(force_difference, elements, offsets):
            frames = len(offsets) - 1
            slot = np.searchsorted(unique, np.asarray(elements))
            frame = np.repeat(np.arange(frames), np.diff(offsets))
            cell = frame * width + slot
            total = np.bincount(cell, np.abs(force_difference).sum(axis=1),
                                frames * width).reshape(frames, width)
            count = np.bincount(cell, None, frames * width).reshape(frames, width)
            with np.errstate(all="ignore"):
                return total / (3 * count)

        values = frame_map(kernel, offsets, kwargs, (width,))
        writers = [result_writer((len(offsets) - 1,), float) for _ in unique]
        try:
            for _, _, chunk in blocks(values):
                for column, writer in enumerate(writers):
                    writer.append(chunk[:, column])
            columns = [writer.finish() for writer in writers]
        except BaseException:
            for writer in writers:
                writer.abort()
            raise
        return np.stack([kde(column, "abs") for column in columns])
    return NOT_HANDLED

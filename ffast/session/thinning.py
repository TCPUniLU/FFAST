"""The points a plot can show: a thinned view of a large result (ADR 0099).

The server twin of the page's ``thin.js``, with the same rules, so a large
result thinned here and a small one thinned in the page look alike:

- a line keeps, in each pixel column, its first, lowest, highest and last
  point, and its first gap (a value that is not a number), so every peak, dip
  and break is still drawn;
- dots keep one point per cell of a ``cell`` px grid, so a lone outlier stays.

Both thin the whole data coarsely and the range shown finely, so a pan shows
the rest at once. They return the kept points' indices in the full data
(ascending), or None when the data is small enough to send whole. A test
runs both versions on the same data and expects the same indices.
"""
from __future__ import annotations

import math

import numpy as np
from ffast.io.batched import BatchedArray, asarray, blocks

# A series with no more values than this is sent and drawn whole (thin.js).
THIN_ABOVE = 20000


def _js_round(v: float) -> int:
    """``Math.round``: halves go up (Python's ``round`` goes to even)."""
    return int(math.floor(v + 0.5))


def _first_per_group(groups: np.ndarray, where: np.ndarray) -> np.ndarray:
    """For each group (a run of equal ``groups`` values, in order) the first
    index where ``where`` holds; groups with none are left out."""
    idx = np.flatnonzero(where)
    if not idx.size:
        return idx
    _, first = np.unique(groups[idx], return_index=True)
    return idx[first]


def _line_columns(xs, ys, start, stop, x0, x1, columns) -> np.ndarray:
    """The points of a line in ``[start, stop)`` to keep for ``columns``
    pixel columns over x from ``x0`` to ``x1`` (thin.js ``lineColumns``)."""
    x = xs[start:stop]
    y = ys[start:stop]
    span = x1 - x0
    if span > 0:
        # Same order of operations as the page, so the columns match exactly.
        col = np.minimum(columns - 1, np.floor(((x - x0) / span) * columns))
    else:
        col = np.zeros(x.shape)
    # x never decreases, so each column is one run; number the runs.
    run = np.concatenate(([0], np.cumsum(col[1:] != col[:-1])))
    starts = np.flatnonzero(np.concatenate(([True], col[1:] != col[:-1])))
    ends = np.concatenate((starts[1:], [len(x)])) - 1
    finite = np.isfinite(y)
    keep = [starts, ends, _first_per_group(run, ~finite)]
    if finite.any():
        low = np.where(finite, y, np.inf)
        high = np.where(finite, y, -np.inf)
        run_min = np.minimum.reduceat(low, starts)
        run_max = np.maximum.reduceat(high, starts)
        has = np.isfinite(run_min)[run]            # runs that are not all gaps
        keep.append(_first_per_group(run, finite & has & (low == run_min[run])))
        keep.append(_first_per_group(run, finite & has & (high == run_max[run])))
    return np.concatenate(keep) + start


def thin_line(xs, ys, x_range, columns) -> np.ndarray | None:
    """The indices of a line's points to draw, ascending, or None to draw all.

    ``xs`` ascending; ``x_range`` the x range shown, or None for all;
    ``columns`` the plot's width in pixels.
    """
    if isinstance(xs, BatchedArray) or isinstance(ys, BatchedArray):
        return _stream_line(asarray(xs), asarray(ys), x_range, columns)
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    n = min(len(xs), len(ys))
    xs, ys = xs[:n], ys[:n]
    columns = max(1, _js_round(columns))
    if n <= THIN_ABOVE or n <= 4 * columns or np.any(xs[1:] < xs[:-1]):
        return None
    keep = [_line_columns(xs, ys, 0, n, xs[0], xs[n - 1], columns)]
    if x_range is not None:
        a, b = sorted(x_range)
        # One point beyond each edge, so the line runs to the plot's border.
        start = max(0, int(np.searchsorted(xs, a, side="left")) - 1)
        stop = min(n, int(np.searchsorted(xs, b, side="left")) + 2)
        if stop - start <= 4 * columns:
            keep.append(np.arange(start, stop))
        else:
            keep.append(_line_columns(xs, ys, start, stop, a, b, columns))
    return np.unique(np.concatenate(keep)).astype(np.int32)


def _grid_cells(xs, ys, box_x, box_y, cols, rows) -> np.ndarray:
    """One point per cell of a ``cols`` x ``rows`` grid over the box, the
    first in index order; points outside it (and NaN) are left out."""
    x0, x1 = box_x
    y0, y1 = box_y
    with np.errstate(invalid="ignore"):
        inside = (xs >= x0) & (xs <= x1) & (ys >= y0) & (ys <= y1)
    src = np.flatnonzero(inside)
    sx, sy = x1 - x0, y1 - y0
    if sx > 0:
        c = np.minimum(cols - 1, np.floor(((xs[src] - x0) / sx) * cols))
    else:
        c = np.zeros(src.shape)
    if sy > 0:
        r = np.minimum(rows - 1, np.floor(((ys[src] - y0) / sy) * rows))
    else:
        r = np.zeros(src.shape)
    cell = (r * cols + c).astype(np.int64)
    # Written last to first, so each cell ends up holding its first point:
    # one pass, where np.unique sorted 1.77M cells (214 ms).
    first = np.full(cols * rows, -1, dtype=np.int64)
    first[cell[::-1]] = src[::-1]
    return first[first >= 0]


def _extent(values) -> tuple[float, float] | None:
    """Lowest and highest value, NaN left out (infinities count, as in the page)."""
    with np.errstate(invalid="ignore"):
        if not np.any(~np.isnan(values)):
            return None
        return float(np.nanmin(values)), float(np.nanmax(values))


def thin_points(xs, ys, x_range, y_range, width, height, cell=2) -> np.ndarray | None:
    """The indices of dots to draw, ascending, or None to draw all: one per
    cell of ``cell`` px over a plot of ``width`` x ``height`` px. ``x_range``
    and ``y_range`` are what is shown; None for all."""
    if isinstance(xs, BatchedArray) or isinstance(ys, BatchedArray):
        return _stream_points(asarray(xs), asarray(ys), x_range, y_range, width, height, cell)
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    n = min(len(xs), len(ys))
    xs, ys = xs[:n], ys[:n]
    if n <= THIN_ABOVE:
        return None
    cols = max(1, _js_round(width / cell))
    rows = max(1, _js_round(height / cell))
    ex, ey = _extent(xs), _extent(ys)
    if ex is None or ey is None:
        return None
    keep = [_grid_cells(xs, ys, ex, ey, cols, rows)]
    if x_range is not None or y_range is not None:
        bx = tuple(sorted(x_range)) if x_range is not None else ex
        by = tuple(sorted(y_range)) if y_range is not None else ey
        keep.append(_grid_cells(xs, ys, bx, by, cols, rows))
    return np.unique(np.concatenate(keep)).astype(np.int32)


def _search(values, target):
    a, b = 0, len(values)
    while a < b:
        mid = (a + b) // 2
        if values[mid] < target:
            a = mid + 1
        else:
            b = mid
    return a


def _stream_line(xs, ys, x_range, columns):
    n = min(len(xs), len(ys))
    columns = max(1, _js_round(columns))
    if n <= THIN_ABOVE or n <= 4 * columns:
        return None
    previous = -np.inf
    for _, _, x in blocks(xs):
        if len(x) and (x[0] < previous or np.any(x[1:] < x[:-1])):
            return None
        if len(x):
            previous = x[-1]

    def scan(start, end, x0, x1):
        kept = np.zeros(0, dtype=np.int64)
        for offset, _, x in blocks(xs[start:end]):
            offset += start
            y = np.asarray(ys[offset:offset + len(x)])
            candidates = _line_columns(x, y, 0, len(x), x0, x1, columns) + offset
            candidates = np.unique(np.concatenate((kept, candidates)))
            cx, cy = np.asarray(xs[candidates]), np.asarray(ys[candidates])
            kept = candidates[_line_columns(cx, cy, 0, len(cx), x0, x1, columns)]
        return kept

    keep = [scan(0, n, xs[0], xs[n - 1])]
    if x_range is not None:
        a, b = sorted(x_range)
        start, end = max(0, _search(xs, a) - 1), min(n, _search(xs, b) + 2)
        keep.append(np.arange(start, end) if end - start <= 4 * columns else scan(start, end, a, b))
    return np.unique(np.concatenate(keep)).astype(np.int32)


def _stream_points(xs, ys, x_range, y_range, width, height, cell):
    n = min(len(xs), len(ys))
    if n <= THIN_ABOVE:
        return None
    cols, rows = max(1, _js_round(width / cell)), max(1, _js_round(height / cell))

    def extent(value):
        lo, hi = np.inf, -np.inf
        found = False
        for _, _, v in blocks(value[:n]):
            v = v[~np.isnan(v)]
            if v.size:
                lo, hi, found = min(lo, v.min()), max(hi, v.max()), True
        return (lo, hi) if found else None

    ex, ey = extent(xs), extent(ys)
    if ex is None or ey is None:
        return None

    def scan(bx, by):
        from ffast.io.budget import budget
        budget().check(cols * rows * 16)
        first = np.full(cols * rows, -1, dtype=np.int64)
        for a, b, x in blocks(xs[:n]):
            y = np.asarray(ys[a:b])
            idx = _grid_cells(x, y, bx, by, cols, rows)
            c = (np.minimum(cols - 1, np.floor((x[idx] - bx[0]) / (bx[1] - bx[0]) * cols))
                 if bx[1] > bx[0] else np.zeros(len(idx)))
            r = (np.minimum(rows - 1, np.floor((y[idx] - by[0]) / (by[1] - by[0]) * rows))
                 if by[1] > by[0] else np.zeros(len(idx)))
            grid = (r * cols + c).astype(np.int64)
            empty = first[grid] < 0
            first[grid[empty]] = idx[empty] + a
        return first[first >= 0]

    keep = [scan(ex, ey)]
    if x_range is not None or y_range is not None:
        keep.append(scan(tuple(sorted(x_range)) if x_range is not None else ex,
                         tuple(sorted(y_range)) if y_range is not None else ey))
    return np.unique(np.concatenate(keep)).astype(np.int32)

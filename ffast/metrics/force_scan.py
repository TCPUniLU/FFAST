"""Shared bounded passes for the canonical force summaries (ADR 0104).

The public metric graph/results stay unchanged. One summary pass reads each
force pair once and computes its difference once; a requested parity histogram
uses the resulting bounds in a second pass. No full difference array is kept.
"""

from __future__ import annotations

import hashlib
import logging
import sys
import threading
from collections import OrderedDict, deque
from concurrent.futures import wait
from contextlib import closing
from contextvars import ContextVar, copy_context
from dataclasses import dataclass

import numpy as np

from ffast.io.batched import BatchedArray
from ffast.io.budget import budget
from ffast.io.extxyz_arrays import NotEnoughMemory
from ffast.io.result_array import ResultWriter
from ffast.metrics import stop
from ffast.metrics.segments import segmentSums

log = logging.getLogger(__name__)
NOT_HANDLED = object()
K = 50_000
_BASE = "ffast.force_difference"
_width = ContextVar("ffast_force_scan_width", default=1)
_EXPRESSIONS = {
    _BASE + "[dataset;component;mae]": ("mae", "mean(mean_parts(abs(D)), by=all)"),
    _BASE + "[dataset;component;rmse]": ("rmse", "sqrt(mean(mean_parts(D ** 2), by=all))"),
    _BASE + "[structure;component;mae]": ("frame_mae", "mean(mean_parts(abs(D)), by=structure)"),
    _BASE + "[structure;component;rmse]": ("frame_rmse", "sqrt(mean(mean_parts(D ** 2), by=structure))"),
}
# 3D colouring by an error of each atom needs only its lowest and highest
# value over every atom. The summary pass has each atom's error in hand, so it
# keeps those ranges; a colouring after the plots then reads no forces.
_COLOUR_EXPRESSIONS = {
    _BASE + "[atom+structure;component;mae]": ("mae", "mean_parts(abs(D))"),
    _BASE + "[atom+structure;component;rmse]": ("rmse", "sqrt(mean_parts(D ** 2))"),
    _BASE + "[atom+structure;length]": ("length", "norm(D)"),
}
# The Atomic Errors tab: the same pass sums each atom's error per element and
# per (structure, element), so the per-element table and distribution read
# no forces after the Basic Errors plots, and the reverse (2026-11-21).
_ELEMENT_EXPRESSIONS = {
    _BASE + "[element;component;mae]": ("element_mae", "mean(mean_parts(abs(D)), by=element)"),
    _BASE + "[element;component;rmse]": ("element_rmse", "sqrt(mean(mean_parts(D ** 2), by=element))"),
}
_ELEMENT_KDE = "ffast.force_mae_per_structure_per_element_kde"
# Above this many elements the (structure, element) table is not kept: a
# batch's table would outgrow its workspace. The KDE then runs on its own.
_TABLE_ELEMENTS = 32


def colour_role(metric_id, fn):
    """Which per-atom range of the summary answers colouring by ``metric_id``,
    or None. Only the exact built-in formula qualifies."""
    if metric_id not in _COLOUR_EXPRESSIONS:
        return None
    from ffast.metrics.expr import _ExprFn
    name, expression = _COLOUR_EXPRESSIONS[metric_id]
    if (type(fn) is _ExprFn and fn.expr == expression and fn.var_refs == {"D": _BASE}
            and not fn.gap_inputs and fn.var_parts == {"D": 1}):
        return name
    return None


def role(metric_id, fn):
    if metric_id in _EXPRESSIONS:
        from ffast.metrics.expr import _ExprFn
        if type(fn) is not _ExprFn:
            return None
        name, expression = _EXPRESSIONS[metric_id]
        refs = {"D": _BASE}
        if name.startswith("frame_"):
            refs["_offsets"] = "offsets"
        if (fn.expr == expression and fn.var_refs == refs and not fn.gap_inputs
                and fn.var_parts == {"D": 1}):
            return name
    if metric_id in _ELEMENT_EXPRESSIONS:
        from ffast.metrics.expr import _ExprFn
        name, expression = _ELEMENT_EXPRESSIONS[metric_id]
        if (type(fn) is _ExprFn and fn.expr == expression
                and fn.var_refs == {"D": _BASE, "_elements": "reference.elements"}
                and not fn.gap_inputs and fn.var_parts == {"D": 1}):
            return name
        return None
    # Looking for an optional optimization must not register built-ins in a
    # different/global registry used by an unrelated headless caller.
    f = sys.modules.get("ffast.metrics.builtin.force_metrics")
    if f is None:
        return None
    if metric_id == "ffast.force_parity_outliers" and fn is f.force_parity_outliers:
        return "outliers"
    if metric_id == "ffast.force_parity_density" and fn is f.force_parity_density:
        return "density"
    if metric_id == "ffast.force_net_residual" and fn is f.force_net_residual:
        return "net"
    a = sys.modules.get("ffast.metrics.builtin.atomic_metrics")
    if metric_id == _ELEMENT_KDE and a is not None and fn is a.force_mae_per_structure_per_element_kde:
        return "element_kde"
    return None


def _batches(offsets, target):
    a, n = 0, len(offsets) - 1
    while a < n:
        b = int(np.searchsorted(offsets, offsets[a] + target, side="right")) - 1
        b = min(n, max(a + 1, b))
        yield a, b, int(offsets[a]), int(offsets[b])
        a = b


def _difference(predicted, reference):
    return np.subtract(predicted, reference)


# ── Forces held in RAM ────────────────────────────────────────────────────────
# Each metric run on its own sums a whole array at once, and NumPy 2 adds it by
# halving it again and again (pairwise summation). A pass in pieces gives the
# same bits only when its pieces are NumPy's halves and their sums are added
# back in NumPy's order. Each structure's sum is the same whatever the piece,
# as long as the pieces use the same method as the whole array (a reshape when
# all structures have the same size, else reduceat).

def _split(m):
    """Where NumPy's pairwise sum cuts ``m`` values: half, rounded down to 8."""
    h = m // 2
    return h - h % 8


def _leaves(n, size):
    """``[(start, end)]``: the pieces of NumPy's pairwise sum of ``n`` values
    no longer than ``size``, in order. NumPy itself does not cut 128 or fewer."""
    out, todo = [], [(0, n)]
    while todo:
        a, b = todo.pop()
        if b - a <= max(size, 128):
            out.append((a, b))
        else:
            h = a + _split(b - a)
            todo += [(h, b), (a, h)]
    return out


def _tree_total(sums, n, size):
    """The sum of ``n`` values from the sums of their ``_leaves(n, size)``,
    added as NumPy's pairwise sum adds them."""
    sums = iter(sums)

    def walk(m):
        if m <= max(size, 128):
            return next(sums)
        h = _split(m)
        left = walk(h)
        return left + walk(m - h)

    return walk(n)


_PAIRWISE = []


def _pairwise():
    """True when this NumPy adds a long array as one pairwise sum, as the
    pieces assume. Otherwise (a future NumPy that adds in another order)
    forces in RAM take each metric's own path."""
    if not _PAIRWISE:
        # Values of one size: any change of order shows in the last bits.
        rng = np.random.default_rng(0)
        values = [rng.random(n) for n in (100_003, 70_001, 1_000_003)]
        _PAIRWISE.append(all(
            _tree_total([v[a:b].sum() for a, b in _leaves(len(v), 1000)], len(v), 1000) == v.sum()
            for v in values))
    return _PAIRWISE[0]


def _frame_sums(values, local, equal):
    """Each structure's sum as ``segmentSums`` of the whole array makes it:
    a reshape when every structure has the same size (``equal``), else
    reduceat, whatever these structures' own sizes."""
    if equal:
        return segmentSums(values, local)
    counts = np.diff(local)
    out = np.zeros((counts.size,) + values.shape[1:], dtype=np.float64)
    filled = counts > 0
    if filled.any():
        out[filled] = np.add.reduceat(values[:local[-1]], local[:-1][filled], axis=0)
    return out


def _exact_batch(leaf, reference, predicted, offsets, equal):
    """One piece of forces in RAM: the atoms of ``leaf`` for the dataset sums,
    and the whole structures that start in it."""
    start, end = leaf
    starts = offsets[:-1]
    a = int(np.searchsorted(starts, start, side="left"))
    b = len(starts) if end == offsets[-1] else int(np.searchsorted(starts, end, side="left"))
    stop_at = max(end, int(offsets[b])) if b > a else end
    t, p = _read_pair(reference, predicted, start, stop_at)
    with np.errstate(all="ignore"):
        # Whole columns, never rows of three: a row's sum or mean adds x, y
        # and z in this same order, so the bits match, but numpy makes one
        # small call per row (3x the work), and a sum per structure over rows
        # of three kept 32 threads waiting on each other.
        x, y, z = (_difference(p[:, j], t[:, j]) for j in range(3))
        atom_abs = (np.abs(x) + np.abs(y) + np.abs(z)) / 3
        atom_ss = x * x + y * y + z * z
        atom_sq = atom_ss / 3
        local = offsets[a:b + 1] - start
        first = int(local[0]) if b > a else 0
        local = local - first
        sizes = np.diff(local)
        frame_abs = _frame_sums(atom_abs[first:], local, equal) / sizes
        frame_rms = np.sqrt(_frame_sums(atom_sq[first:], local, equal) / sizes)
        net = np.stack([_frame_sums(c[first:], local, equal) for c in (x, y, z)], axis=1)
    bad = tuple(bool((~np.isfinite(v) & (sizes > 0)).any()) for v in (frame_abs, frame_rms))
    m = end - start
    t, p = t[:m], p[:m]
    atom_abs, atom_sq, atom_ss = atom_abs[:m], atom_sq[:m], atom_ss[:m]
    lo, hi = _finite_extent(t, p)
    if m:
        atom_ranges = np.array([[v.min(), v.max()] for v in (atom_abs, atom_sq, atom_ss)])
    else:
        atom_ranges = np.full((3, 2), [np.inf, -np.inf])
    none = np.empty(0)
    return _Partial(a, b, m, atom_abs.sum(), atom_sq.sum(),
                    frame_abs, frame_rms, bad,
                    lo, hi, np.empty(0, dtype=np.int64), none, none, none, atom_ranges, net=net)


def _all_finite(t, p):
    """True when no value of ``t`` or ``p`` is NaN or infinite: a NaN carries
    into the lowest and highest value, and an infinity is one of them."""
    if not t.size:
        return True
    return bool(np.isfinite(np.minimum(t.min(), p.min())) and np.isfinite(np.maximum(t.max(), p.max())))


def _finite_extent(t, p):
    """The lowest and highest value over the pairs where both are finite,
    or (inf, -inf) when there are none. Plain min and max when every value
    is finite: the masked copies took 4x as long."""
    if not t.size:
        return np.inf, -np.inf
    if _all_finite(t, p):
        return min(t.min(), p.min()), max(t.max(), p.max())
    finite_pair = np.isfinite(t) & np.isfinite(p)
    if not finite_pair.any():
        return np.inf, -np.inf
    return (min(t[finite_pair].min(), p[finite_pair].min()),
            max(t[finite_pair].max(), p[finite_pair].max()))


def _parallelism(maximum=4):
    from ffast.metrics.resources import cores

    owner = budget()
    # Each lane declares ten arithmetic blocks plus two admission/headroom
    # blocks. Leave capacity for source parsing and other active metrics.
    headroom = min(owner.limit - owner.used, owner.limit // 2)
    return min(maximum, cores(), max(1, headroom // (12 * owner.block_bytes)))


def _map_batches(fn, batches):
    """Bounded read-ahead and arithmetic on the server's existing chunk pool.

    A lane reads a pair then reduces it immediately. While it computes, other
    lanes can read subsequent pairs. Only ``width`` tasks/results exist at a
    time; merge in source order for stable floating-point sums and top-k ties.
    The caller closes this iterator before releasing its workspace or writers.
    """
    width = _width.get()
    if width == 1:
        for batch in batches:
            stop.check()
            yield fn(batch)
        return
    from ffast.metrics.resources import chunk_pool

    batches = iter(batches)
    pending = deque()
    aborted = threading.Event()

    def run(batch):
        if aborted.is_set():
            raise stop.Stopped(stop.STOPPED)
        stop.check()
        value = fn(batch)
        stop.check()
        return value

    def submit():
        batch = next(batches, None)
        if batch is not None:
            # ContextVars (cancellation and profiling ancestry) do not follow
            # ThreadPoolExecutor submissions automatically.
            pending.append(chunk_pool().submit(copy_context().run, run, batch))

    try:
        for _ in range(width):
            submit()
        while pending:
            stop.check()
            future = pending[0]
            value = future.result()
            pending.popleft()
            yield value
            del value, future
            submit()
    finally:
        aborted.set()
        for future in pending:
            future.cancel()
        # In-flight reads cannot be killed. Keep their declared RAM reservation
        # until they exit, including on cancellation, merge errors and failures.
        wait(pending)


def _read_pair(reference, predicted, start, end):
    stop.check()
    t = np.asarray(reference[start:end])
    stop.check()
    p = np.asarray(predicted[start:end])
    stop.check()
    return t, p


@dataclass
class _Partial:
    first: int
    last: int
    atoms: int
    absolute_sum: float
    squared_sum: float
    frame_abs: np.ndarray
    frame_rms: np.ndarray
    bad: tuple
    lo: float
    hi: float
    indices: np.ndarray
    errors: np.ndarray
    true: np.ndarray
    predicted: np.ndarray
    atom_ranges: np.ndarray   # (lowest, highest) of each atom's MAE, MSE, squared length
    # Per element: sums of each atom's MAE and MSE, and the atom count, or None
    # when an atom's element is not in the dataset's list (never expected).
    element_sums: np.ndarray | None = None
    # Each structure's MAE per element (NaN where it has none of that element).
    element_table: np.ndarray | None = None
    # Each structure's net force error: the sum of its atoms' errors (x, y, z).
    net: np.ndarray | None = None
    # The force parity counts on a fine grid, a ``_Fine``, or None.
    parity: object = None


# ── Parity counts in the summary pass ─────────────────────────────────────────
# The parity density bins every component between the lowest and highest
# value, which are known only after every force was read: it took a second
# full pass (63 s of 126 s for Basic Errors on an 83 GB file, 2026-11-21).
# The summary pass instead counts each (true, predicted) pair on a fine grid
# whose bins are a power of two wide, at fixed places: bin i holds
# [i w, (i + 1) w). Doubling w merges bins 2i and 2i + 1 exactly, so the grid
# widens with the data and no count is lost or moved. At the end whole fine
# bins are added into plot bins, so every count is exact; the plot's edges
# are fine-bin edges, not the exact lowest and highest value.
_FINE = 1200


@dataclass
class _Fine:
    width: float          # a power of two
    start: int            # absolute index of row/column 0
    low: int              # lowest and highest occupied absolute index
    high: int
    counts: np.ndarray    # (_FINE, _FINE) int64, [predicted bin][true bin]


def _fine_width(lo, hi, at_least=0.0):
    """The smallest power of two, at least ``at_least``, whose grid holds
    [lo, hi] in ``_FINE - 1`` bins, or None for values too large to bin."""
    scale = max(abs(lo), abs(hi))
    if not np.isfinite(hi - lo) or not np.isfinite(scale):
        return None
    # x / w must stay far inside int64, and w must be a normal number.
    floor = max(scale * 2.0 ** -40, 2.0 ** -1000)
    wanted = max((hi - lo) / (_FINE - 2), floor, at_least)
    width = 2.0 ** int(np.ceil(np.log2(wanted)))
    while np.floor(hi / width) - np.floor(lo / width) + 1 > _FINE - 1:
        width *= 2
    return width


def _fine_batch(t, p, lo, hi, state):
    """The finite pairs of one batch counted on a fine grid: the merged
    grid's own when they fit it (``state`` holds its width and start), else
    one of their own."""
    if not np.isfinite(lo):
        return None
    shared = state.get("grid")
    width = start = None
    if shared is not None:
        w, s = shared
        if np.floor(lo / w) >= s and np.floor(hi / w) < s + _FINE:
            width, start = w, s
    if width is None:
        width = _fine_width(lo, hi, shared[0] if shared is not None else 0.0)
        if width is None:
            return None
        start = int(np.floor(lo / width))
    keep = np.isfinite(t) & np.isfinite(p)
    col = np.floor(t[keep] / width).astype(np.int64) - start
    row = np.floor(p[keep] / width).astype(np.int64) - start
    counts = np.bincount(row * _FINE + col, minlength=_FINE * _FINE).reshape(_FINE, _FINE)
    return _Fine(width, start, int(np.floor(lo / width)), int(np.floor(hi / width)), counts)


def _regroup(fine, groups, size):
    """``fine.counts`` with row and column i added into ``groups[i]`` of a
    ``size`` x ``size`` grid. ``groups`` does not decrease; groups outside
    [0, size) hold no counts."""
    inside = (groups >= 0) & (groups < size)
    index = np.flatnonzero(inside)
    out = np.zeros((size, size), dtype=fine.counts.dtype)
    if not index.size:
        return out
    target = groups[index]
    starts = np.flatnonzero(np.r_[True, np.diff(target) != 0])
    block = fine.counts[np.ix_(index, index)]
    block = np.add.reduceat(np.add.reduceat(block, starts, axis=0), starts, axis=1)
    out[np.ix_(target[starts], target[starts])] = block
    return out


def _coarsen(fine, width, start):
    """``fine`` on the grid of ``width`` (a power of two, not finer) starting
    at absolute index ``start``."""
    factor = int(round(width / fine.width))
    groups = (fine.start + np.arange(_FINE)) // factor - start
    return _regroup(fine, groups, _FINE)


def _merge_fine(total, part, state):
    """``total`` and ``part`` counted together, exactly, on one grid."""
    if part is None:
        return total
    if total is None:
        state["grid"] = (part.width, part.start)
        return part
    if part.width == total.width and part.start == total.start:
        total.counts += part.counts
        total.low, total.high = min(total.low, part.low), max(total.high, part.high)
        return total
    width = max(total.width, part.width)
    while True:
        low = min(v.low // int(round(width / v.width)) for v in (total, part))
        high = max(v.high // int(round(width / v.width)) for v in (total, part))
        if high - low + 1 <= _FINE:
            break
        width *= 2
    # Centre the occupied bins, so later batches fit without regrouping.
    start = low - (_FINE - (high - low + 1)) // 2
    counts = _coarsen(total, width, start) + _coarsen(part, width, start)
    state["grid"] = (width, start)
    return _Fine(width, start, low, high, counts)


def parity_from_fine(fine, bins=300):
    """The parity density (``histogram``'s layout: row 0 the bin centres,
    then counts [predicted bin][true bin]) from fine counts, exactly. Each
    plot bin is a whole number of fine bins, so its edges are fine-bin
    edges: the plot reaches past the lowest and highest value by less than
    one fine bin, and has ``bins`` to ``2 * bins - 1`` bins."""
    span = fine.high - fine.low + 1
    if span >= bins:
        merge = span // bins
        first, count = fine.low, -(-span // merge)
    else:
        # Too few occupied fine bins (all values nearly equal): one fine bin
        # per plot bin, the empty ones shared out on both sides.
        merge, first, count = 1, fine.low - (bins - span) // 2, bins
    groups = (fine.start + np.arange(_FINE) - first) // merge
    counts = _regroup(fine, groups, count)
    width = merge * fine.width
    centres = (first + np.arange(count) * merge) * fine.width + width / 2
    return budget().retain(np.vstack((centres, counts)))


def _slots(z, columns):
    """Each atom's column in ``columns`` (sorted atomic numbers), or None when
    an atom's element is not among them. A lookup table, not a search: the
    search took 35 of 91 ms per 2M atoms (2026-11-21)."""
    if not len(z):
        return np.zeros(0, dtype=np.intp)
    low, high = int(z.min()), int(z.max())
    if low < columns[0] or high > columns[-1]:
        return None
    if columns[-1] - columns[0] <= 4096:
        lookup = np.full(int(columns[-1] - columns[0]) + 1, -1, dtype=np.intp)
        lookup[columns - columns[0]] = np.arange(len(columns))
        slot = lookup[z - columns[0]]
        return None if (slot < 0).any() else slot
    slot = np.searchsorted(columns, z)
    return None if (columns[np.minimum(slot, len(columns) - 1)] != z).any() else slot


def _element_batch(a, b, start, end, elements, columns, table, atom_abs, atom_sq, sizes):
    """Per element: sums of each atom's MAE and MSE and the atom count, and
    with ``table`` each structure's MAE per element; None, None when an
    atom's element is not in ``columns``."""
    z = np.asarray(elements[start:end])
    width = len(columns)
    slot = None if len(z) != end - start else _slots(z, columns)
    if slot is None:
        return None, None
    if not table:
        return np.stack((np.bincount(slot, atom_abs, width), np.bincount(slot, atom_sq, width),
                         np.bincount(slot, None, width))), None
    # One group per (structure, element); the element sums are the table's
    # column sums, so the atoms are grouped once, not twice.
    frames = b - a
    cell = np.repeat(np.arange(frames), sizes) * width + slot
    shape = (frames, width)
    total = np.bincount(cell, atom_abs, frames * width).reshape(shape)
    squares = np.bincount(cell, atom_sq, frames * width).reshape(shape)
    count = np.bincount(cell, None, frames * width).reshape(shape)
    sums = np.stack((total.sum(axis=0), squares.sum(axis=0), count.sum(axis=0)))
    with np.errstate(all="ignore"):
        return sums, total / count


def _summary_batch(batch, reference, predicted, offsets, elements=None, columns=None, table=False,
                   parity=None):
    a, b, start, end = batch
    t, p = _read_pair(reference, predicted, start, end)
    with np.errstate(all="ignore"):
        delta = _difference(p, t)
        absolute = np.abs(delta)
        atom_abs = absolute.mean(axis=1)
        # The same arithmetic as mean_parts and norm: a sum over x, y, z,
        # then a division by 3 for the mean.
        atom_ss = np.square(delta).sum(axis=1)
        atom_sq = atom_ss / 3
        local = offsets[a:b + 1] - start
        sizes = np.diff(local)
        frame_abs = segmentSums(atom_abs, local) / sizes
        frame_rms = np.sqrt(segmentSums(atom_sq, local) / sizes)
        # The same arithmetic as force_net_residual, structure by structure.
        net = segmentSums(delta, local)
    bad = tuple(bool((~np.isfinite(v) & (sizes > 0)).any())
                for v in (frame_abs, frame_rms))
    lo, hi = np.inf, -np.inf
    finite_pair = np.isfinite(t) & np.isfinite(p)
    if finite_pair.any():
        lo = min(t[finite_pair].min(), p[finite_pair].min())
        hi = max(t[finite_pair].max(), p[finite_pair].max())
    fine = None if parity is None else _fine_batch(t, p, lo, hi, parity)
    if len(atom_abs):
        atom_ranges = np.array([[v.min(), v.max()] for v in (atom_abs, atom_sq, atom_ss)])
    else:
        atom_ranges = np.full((3, 2), [np.inf, -np.inf])
    element_sums = element_table = None
    if columns is not None:
        element_sums, element_table = _element_batch(
            a, b, start, end, elements, columns, table, atom_abs, atom_sq, sizes)
    flat = absolute.ravel()
    selected = np.flatnonzero(np.isfinite(flat))
    if len(selected) > K:
        selected = selected[np.argpartition(-flat[selected], K - 1)[:K]]
    return _Partial(a, b, end - start, atom_abs.sum(), atom_sq.sum(),
                    frame_abs, frame_rms, bad, lo, hi, selected + 3 * start,
                    flat[selected], t.ravel()[selected], p.ravel()[selected], atom_ranges,
                    element_sums, element_table, net, fine)


@dataclass
class Summary:
    mae: np.ndarray
    rmse: np.ndarray
    frame_mae: object
    frame_rmse: object
    outliers: np.ndarray
    extent: tuple
    bad_frames: tuple
    batches: int
    atoms: int
    workers: int
    # {"mae" | "rmse" | "length": (lowest, highest)} over every atom, or None
    # when an atom's error is not finite: the colour pass decides then.
    atom_ranges: dict | None = None
    # The dataset's elements in order of atomic number, and per element the
    # force MAE and RMSE over its atoms; None without the elements.
    element_columns: np.ndarray | None = None
    element_mae: np.ndarray | None = None
    element_rmse: np.ndarray | None = None
    # One column per element: each structure's MAE over its atoms of that
    # element (NaN without one). None when it was not kept.
    element_table: list | None = None
    # Each structure's net force error (structures x 3), the value of
    # force_net_residual; None when storage refused it.
    net: np.ndarray | None = None
    # The force parity density counted in this pass (``histogram``'s layout,
    # fine bins regrouped by their centre); None leaves it to a second pass.
    density: np.ndarray | None = None


def _in_memory(owner, size):
    """Whether a value the pass keeps for other tabs stays in RAM. These are
    extras: beyond a small share of one block they go to result files, so
    they never take the RAM the pass and the plots need."""
    return size <= min(128 * 2**20, owner.limit // 8) and (
        size <= owner.block_bytes // 16 or owner.result_limit == 0)


def _net_storage(owner, n):
    """``("memory", array)``, ``("files", [writer])`` or None: where each
    structure's net force error goes. None leaves it to its own pass."""
    try:
        if _in_memory(owner, n * 24):
            with owner.reserve(n * 24) as lease:
                return "memory", owner.retain(np.empty((n, 3)), lease)
        return "files", [ResultWriter((n, 3), float)]
    except NotEnoughMemory:
        return None


def _exact_storage(owner, shape):
    """``("memory", array)`` of ``shape`` in RAM, or None when it does not fit."""
    size = int(np.prod(shape)) * 8
    try:
        with owner.reserve(size) as lease:
            return "memory", owner.retain(np.empty(shape), lease)
    except NotEnoughMemory:
        return None


def _table_storage(owner, n, width):
    """``("memory", array)``, ``("files", writers)`` or None: where each
    structure's per-element MAE goes. None leaves it to its own pass."""
    size = n * width * 8
    if _in_memory(owner, size):
        try:
            with owner.reserve(size) as lease:
                return "memory", owner.retain(np.full((n, width), np.nan), lease)
        except NotEnoughMemory:
            return None
    writers = []
    try:
        for _ in range(width):
            writers.append(ResultWriter((n,), float))
    except NotEnoughMemory:
        for writer in writers:
            writer.abort()
        return None
    except BaseException:
        for writer in writers:
            writer.abort()
        raise
    return "files", writers


def _drop_table(table):
    if table is not None and table[0] == "files":
        for writer in table[1]:
            writer.abort()


def scan(reference, predicted, offsets, elements=None, exact=False):
    """One pass, complete frames and bounded workspaces; check cancellation.
    With ``elements`` (every atom's atomic number) it also sums the errors
    per element and keeps each structure's MAE per element.

    ``exact`` (forces in RAM): the pieces follow NumPy's pairwise sum, so every
    value has the bits of the metric run on its own. It keeps no outliers,
    parity counts or element sums: those take their own exact passes."""
    owner = budget()
    n = len(offsets) - 1
    writers = []
    columns = table = None
    if elements is not None and not exact:
        columns = np.asarray(np.unique(elements))
        if columns.size == 0:
            columns = None
        elif columns.size <= _TABLE_ELEMENTS:
            table = _table_storage(owner, n, columns.size)
    element_sums = None if columns is None else np.zeros((3, columns.size))
    # Forces in RAM: each metric run on its own keeps its result in RAM, and
    # what reads it next sums a contiguous array; a column of a wider array
    # or a result file is summed in another order. So the pass keeps them
    # the same way, or leaves them to their own pass when they do not fit.
    net = _exact_storage(owner, (n, 3)) if exact else _net_storage(owner, n)
    # Preserve downstream streaming for large frame series, just as the
    # expression evaluator does. Small arrays (or admitted disk-free results)
    # stay resident; the two O(frames) arrays otherwise own ordinary result files.
    small = n * 16 <= min(128 * 2**20, owner.limit // 8)
    memory = exact or (small and (n * 8 <= owner.block_bytes or owner.result_limit == 0))
    if exact:
        values = [_exact_storage(owner, (n,)) for _ in range(2)]
        if values[0] is None or values[1] is None:
            raise NotEnoughMemory("Each structure's force errors do not fit the RAM budget")
        values = [v[1] for v in values]
    elif memory:
        with owner.reserve(n * 16) as lease:
            values = owner.retain(np.empty((n, 2)), lease)
    else:
        if owner.result_limit < n * 16:
            raise NotEnoughMemory("Shared force summaries do not fit result storage")
        try:
            for _ in range(2):
                writers.append(ResultWriter((n,), float))
        except BaseException:
            for writer in writers:
                writer.abort()
            raise
    count = 0
    absolute_sum = squared_sum = 0.0
    lo, hi = np.inf, -np.inf
    indices = np.empty(0, dtype=np.int64)
    errors = true_values = pred_values = np.empty(0)
    bad_frames = [False, False]
    atom_lo, atom_hi = np.full(3, np.inf), np.full(3, -np.inf)
    batches = 0
    try:
        # Multiple temporaries and top-k indices fit the producer's reserved
        # workspace; batches never split an individual structure.
        target = max(1, owner.block_bytes // 48)
        # The merged parity grid's width and start, for the lanes to bin on.
        parity_state = {}
        fine = None
        fine_ok = not exact
        leaf_sums = ([], [])
        if exact:
            counts = np.diff(offsets)
            equal = bool(counts.size and counts[0] > 0 and (counts == counts[0]).all())
            if not _pairwise():
                raise NotEnoughMemory("This NumPy sums in an unknown order")
            fn = lambda leaf: _exact_batch(leaf, reference, predicted, offsets, equal)
            batches_in = _leaves(int(offsets[-1]), target)
        else:
            fn = lambda batch: _summary_batch(batch, reference, predicted, offsets,
                                              elements, columns, table is not None, parity_state)
            batches_in = _batches(offsets, target)
        with closing(_map_batches(fn, batches_in)) as partials:
            for part in partials:
                stop.check()
                if exact:
                    leaf_sums[0].append(part.absolute_sum)
                    leaf_sums[1].append(part.squared_sum)
                else:
                    absolute_sum += part.absolute_sum
                    squared_sum += part.squared_sum
                bad_frames[0] |= part.bad[0]
                bad_frames[1] |= part.bad[1]
                if exact:
                    values[0][part.first:part.last] = part.frame_abs
                    values[1][part.first:part.last] = part.frame_rms
                elif memory:
                    values[part.first:part.last, 0] = part.frame_abs
                    values[part.first:part.last, 1] = part.frame_rms
                else:
                    writers[0].append(part.frame_abs)
                    writers[1].append(part.frame_rms)
                count += part.atoms
                lo, hi = min(lo, part.lo), max(hi, part.hi)
                if fine_ok:
                    if part.parity is None and np.isfinite(part.lo):
                        # Values too large to bin: the density makes its own pass.
                        fine_ok, fine = False, None
                    else:
                        fine = _merge_fine(fine, part.parity, parity_state)
                part.parity = None
                # np.minimum keeps a NaN, so a non-finite atom is seen below.
                atom_lo = np.minimum(atom_lo, part.atom_ranges[:, 0])
                atom_hi = np.maximum(atom_hi, part.atom_ranges[:, 1])
                if element_sums is not None:
                    if part.element_sums is None:
                        element_sums = None
                        _drop_table(table)
                        table = None
                    else:
                        element_sums += part.element_sums
                if net is not None:
                    if net[0] == "memory":
                        net[1][part.first:part.last] = part.net
                    else:
                        net[1][0].append(part.net)
                if table is not None:
                    if table[0] == "memory":
                        table[1][part.first:part.last] = part.element_table
                    else:
                        for column, writer in enumerate(table[1]):
                            writer.append(np.ascontiguousarray(part.element_table[:, column]))
                indices = np.concatenate((indices, part.indices))
                errors = np.concatenate((errors, part.errors))
                true_values = np.concatenate((true_values, part.true))
                pred_values = np.concatenate((pred_values, part.predicted))
                if len(indices) > K:
                    keep = np.argpartition(-errors, K - 1)[:K]
                    indices, errors = indices[keep], errors[keep]
                    true_values, pred_values = true_values[keep], pred_values[keep]
                batches += 1
        stop.check()
        if exact:
            absolute_sum = _tree_total(leaf_sums[0], int(offsets[-1]), target)
            squared_sum = _tree_total(leaf_sums[1], int(offsets[-1]), target)
        if exact:
            for v in values:
                v.flags.writeable = False
            frame_mae, frame_rmse = values
        elif memory:
            values.flags.writeable = False
            frame_mae, frame_rmse = values[:, 0], values[:, 1]
        else:
            frame_mae, frame_rmse = (writer.finish() for writer in writers)
        order = np.argsort(-errors, kind="stable")
        indices = indices[order]
        atoms = indices // 3
        frames = np.searchsorted(offsets[1:], atoms, side="right")
        outliers = np.column_stack((true_values[order], pred_values[order], frames,
                                    atoms - offsets[frames], indices % 3)).astype(float)
        outliers = owner.retain(outliers)
        outliers.flags.writeable = False
        if not np.isfinite(lo):
            lo, hi = 0.0, 1.0
        if hi <= lo:
            hi = lo + 1.0
        # The fine grid (11.5 MB) is not kept: only the plot's 300 x 300.
        density = None if fine is None else parity_from_fine(fine)
        fine = None
        if density is not None:
            density.flags.writeable = False
        with np.errstate(all="ignore"):
            mae = np.asarray(absolute_sum / count if count else np.nan)
            rmse = np.asarray(np.sqrt(squared_sum / count) if count else np.nan)
        mae.flags.writeable = rmse.flags.writeable = False
        atom_ranges = None
        if count and np.isfinite(atom_lo).all() and np.isfinite(atom_hi).all():
            # sqrt is monotone and correctly rounded: sqrt of the extremes is
            # the extreme of the sqrt, as the colour pass computes it.
            atom_ranges = {
                "mae": (float(atom_lo[0]), float(atom_hi[0])),
                "rmse": (float(np.sqrt(atom_lo[1])), float(np.sqrt(atom_hi[1]))),
                "length": (float(np.sqrt(atom_lo[2])), float(np.sqrt(atom_hi[2]))),
            }
        element_mae = element_rmse = element_table = None
        if element_sums is not None:
            with np.errstate(all="ignore"):
                element_mae = element_sums[0] / element_sums[2]
                element_rmse = np.sqrt(element_sums[1] / element_sums[2])
            element_mae.flags.writeable = element_rmse.flags.writeable = False
            if table is not None:
                if table[0] == "memory":
                    table[1].flags.writeable = False
                    element_table = [table[1][:, j] for j in range(len(columns))]
                else:
                    element_table = [writer.finish() for writer in table[1]]
                table = None
        else:
            columns = None
        net_values = None
        if net is not None:
            if net[0] == "memory":
                net_values = net[1]
                net_values.flags.writeable = False
            else:
                net_values = net[1][0].finish()
            net = None
        result = Summary(mae, rmse, frame_mae, frame_rmse, outliers, (lo, hi),
                         tuple(bad_frames), batches, count, _width.get(), atom_ranges,
                         columns, element_mae, element_rmse, element_table, net_values, density)
        log.info("force_batch_scan: summary, %d batches, %d atoms, %d lanes, one difference per batch",
                 batches, count, _width.get())
        return result
    except BaseException:
        for writer in writers:
            writer.abort()
        _drop_table(table)
        _drop_table(net)
        raise


def _histogram_batch(batch, reference, predicted, extent, bins):
    _, _, start, end = batch
    t, p = _read_pair(reference, predicted, start, end)
    lo, hi = extent
    width = (hi - lo) / bins
    if _all_finite(t, p):
        t, p = t.ravel(), p.ravel()     # the same values in the same order
    else:
        keep = np.isfinite(t) & np.isfinite(p)
        t, p = t[keep], p[keep]
    col = np.minimum(((t - lo) / width).astype(np.int64), bins - 1)
    row = np.minimum(((p - lo) / width).astype(np.int64), bins - 1)
    return np.bincount(row * bins + col, minlength=bins * bins)


def histogram(reference, predicted, offsets, extent, bins=300):
    """Second pass reuses global bounds; no force differences are evaluated."""
    lo, hi = extent
    width = (hi - lo) / bins
    counts = np.zeros(bins * bins, dtype=np.int64)
    batches = 0
    fn = lambda batch: _histogram_batch(batch, reference, predicted, extent, bins)
    with closing(_map_batches(fn, _batches(offsets, max(1, budget().block_bytes // 48)))) as partials:
        for part in partials:
            stop.check()
            counts += part
            batches += 1
    stop.check()
    log.info("force_batch_scan: histogram, %d batches, %d lanes, no difference calculation",
             batches, _width.get())
    return budget().retain(np.vstack((lo + (np.arange(bins) + .5) * width,
                                      counts.reshape(bins, bins))))


class _Group:
    def __init__(self, offsets, uses, workspace, exact=False):
        self.offsets, self.uses, self.workspace = offsets, set(uses), workspace
        # Forces in RAM: pieces on many cores, every value bit for bit as
        # each metric run on its own (see ``scan``).
        self.exact = exact
        self.condition = threading.Condition()
        self.members = {}
        self.states, self.results, self.errors = {}, {}, {}
        # Values a 3D colouring by per-atom MAE collected before any summary
        # pass: {"mae": dataset MAE, "frame_mae": each structure's MAE}.
        self.from_colour = {}
        self.invalid = False
        self.disabled = False

    def stopped(self, phase):
        with self.condition:
            return self.invalid or not any(
                not lease.stopped() for lease in self.members.values()
                if phase == "summary" or "density" in lease.roles)

    def _blocks(self, lanes):
        """Workspace blocks for ``lanes`` lanes. Forces read from disk: ten
        blocks a lane (copies read in, then the arithmetic). Forces in RAM
        are read in place: a lane's piece holds about 80 bytes an atom at
        its peak, so four times 48 bytes an atom of its piece, which is at
        most one block's worth of atoms and at most every atom there is."""
        if not self.exact:
            return 10 * lanes
        block = budget().block_bytes
        piece = min(max(1, block // 48), max(1, int(self.offsets[-1])))
        return max(1, -(-4 * 48 * piece * lanes // block))

    def start(self, lease):
        """Begin the summary pass now, on its own thread, when none ran yet.
        Forces in RAM: the pass reads the reference and predicted forces, so
        it need not wait while the plan builds the difference table first."""
        with self.condition:
            if self.states.get("summary") or self.disabled or self.invalid:
                return

        def run():
            try:
                self.get("summary", lease)
            except BaseException as exc:   # kept by the group for its consumers
                log.debug("force_batch_scan: early pass ended: %r", exc)

        threading.Thread(target=copy_context().run, args=(run,), daemon=True,
                         name="ffast-force-pass").start()

    def get(self, phase, lease):
        with self.condition:
            while self.states.get(phase) == "running":
                if lease.stopped():
                    raise stop.Stopped(stop.STOPPED)
                self.condition.wait(.05)
            if lease.stopped() or self.invalid:
                raise stop.Stopped(stop.STOPPED)
            if self.disabled:
                return NOT_HANDLED
            if phase in self.errors:
                raise self.errors[phase]
            if phase in self.results:
                return self.results[phase]
            self.states[phase] = "running"
        callback = lambda: self.stopped(phase)
        token = stop.running_under(callback)
        # Forces in RAM are not read from disk: as many lanes as the chunk
        # pool has threads, within the memory budget.
        width_token = _width.set(_parallelism(32) if self.exact else _parallelism())
        try:
            with self.workspace(callback, blocks=self._blocks(_width.get())):
                value = (scan(lease.reference, lease.predicted, self.offsets, lease.elements(),
                              exact=self.exact)
                         if phase == "summary"
                         else histogram(lease.reference, lease.predicted, self.offsets,
                                        self.results["summary"].extent))
            with self.condition:
                self.results[phase] = value
                self.states[phase] = "done"
                self.condition.notify_all()
            return value
        except BaseException as exc:
            with self.condition:
                if isinstance(exc, (NotEnoughMemory, MemoryError)):
                    self.disabled = True
                else:
                    self.errors[phase] = exc
                self.states[phase] = "failed"
                self.condition.notify_all()
            if self.disabled:
                log.info("force_batch_scan: %s unavailable (%s); using individual kernels", phase, exc)
                return NOT_HANDLED
            raise
        finally:
            _width.reset(width_token)
            stop.reset(token)


class Lease:
    def __init__(self, manager, key, group, reference, predicted, stopped, roles,
                 elements=lambda: None):
        self.manager, self.key, self.group = manager, key, group
        self.reference, self.predicted, self.stopped = reference, predicted, stopped
        self.roles = roles
        # Called only by the pass itself: resolving every atom's element may
        # build an array, so a lease answered by a finished pass never does.
        self.elements = elements

    def select(self, metric_id, fn):
        name = role(metric_id, fn)
        if name is None:
            return NOT_HANDLED
        if self.group.exact and name in _OWN_PATH_IN_RAM:
            return NOT_HANDLED
        with self.group.condition:
            early = (None if "summary" in self.group.results or self.group.invalid
                     else self.group.from_colour.get(name))
        if early is not None:
            if self.stopped():
                raise stop.Stopped(stop.STOPPED)
            return early
        result = self.group.get("summary", self)
        if result is NOT_HANDLED:
            return NOT_HANDLED
        if name == "density":
            if result.density is not None:
                if self.stopped():
                    raise stop.Stopped(stop.STOPPED)
                return result.density
            return self.group.get("histogram", self)
        if name.startswith("element_"):
            return self._element(name, fn, result)
        if name == "net":
            if result.net is None:
                return NOT_HANDLED
            if self.stopped():
                raise stop.Stopped(stop.STOPPED)
            return result.net
        value = getattr(result, name)
        if name in ("mae", "rmse", "frame_mae", "frame_rmse"):
            bad = (result.bad_frames[name == "frame_rmse"] if name.startswith("frame_")
                   else result.atoms > 0 and not np.isfinite(value).all())
            if bad:
                raise ValueError(f"expr {fn.expr!r} produced non-finite values (inf/nan) — "
                                 "check for division by zero or log of a non-positive value in the data")
        if self.stopped():
            raise stop.Stopped(stop.STOPPED)
        return value

    def _element(self, name, fn, result):
        if result.element_mae is None:
            return NOT_HANDLED
        if name == "element_kde":
            if result.element_table is None:
                return NOT_HANDLED
            from ffast.metrics.streaming import kde
            value = np.stack([kde(column, "abs") for column in result.element_table])
        else:
            value = getattr(result, name)
            if not np.isfinite(value).all():
                raise ValueError(f"expr {fn.expr!r} produced non-finite values (inf/nan) — "
                                 "check for division by zero or log of a non-positive value in the data")
        if self.stopped():
            raise stop.Stopped(stop.STOPPED)
        return value

    def ahead(self):
        """A step of this plan is about to be computed (not served from the
        cache): forces in RAM start the pass now, alongside that step."""
        if self.group.exact and self.roles - _OWN_PATH_IN_RAM:
            self.group.start(self)

    def close(self):
        self.manager.release(self)


class FrameMAE:
    """Each structure's and the dataset's force MAE, summed from the per-atom
    MAE blocks a colouring reads in row order. A block may cut a structure;
    its two parts are added. Published only when the whole colouring
    finished and every structure with atoms has a finite value."""

    def __init__(self, scans, key, offsets, source, sums):
        self.scans, self.key, self.offsets, self.source = scans, key, offsets, source
        self.sums = sums
        self.total = 0.0
        self.next = 0

    def add(self, start, end, values):
        if start != self.next:
            raise ValueError("FrameMAE: blocks must come in row order")
        self.next = end
        values = np.asarray(values, dtype=np.float64).reshape(-1)
        first = int(np.searchsorted(self.offsets, start, side="right")) - 1
        last = int(np.searchsorted(self.offsets, end, side="left"))
        local = np.clip(self.offsets[first:last + 1], start, end) - start
        self.sums[first:last] += segmentSums(values, local)
        self.total += values.sum()

    def finish(self):
        offsets = self.offsets
        count = int(offsets[-1])
        if self.next != count:
            return
        sizes = np.diff(offsets)
        with np.errstate(all="ignore"):
            frame_mae = np.divide(self.sums, sizes, out=self.sums)
            mae = np.asarray(self.total / count if count else np.nan)
        if not np.isfinite(frame_mae[sizes > 0]).all() or (count and not np.isfinite(mae)):
            return
        frame_mae.flags.writeable = mae.flags.writeable = False
        self.scans._publish(self.key, offsets, self.source,
                            {"frame_mae": frame_mae, "mae": mae})


def _elements_of(source, root, atoms):
    """Every atom's atomic number from ``source``, or None when it has none
    that fits the forces."""
    for local in ("_elements", "elements"):
        found, value = source.get(root, local, "reference.elements")
        if found and value is not None:
            break
    else:
        return None
    if (getattr(value, "shape", None) != (atoms,)
            or not np.issubdtype(np.dtype(value.dtype), np.integer)):
        return None
    return value


# Forces in RAM: their own kernels pick ties and add per element in an order
# pieces cannot repeat, and they are fast already.
_OWN_PATH_IN_RAM = frozenset(("outliers", "element_mae", "element_rmse", "element_kde"))


def _in_ram(key):
    return key[-2] == "ram"


class ForceScans:
    """At most two idle summaries; live consumers share cancellation/lifetime."""

    def __init__(self, workspace):
        self.workspace = workspace
        self.lock = threading.RLock()
        self.groups = OrderedDict()
        self.owner = None

    def acquire(self, plan, source, registry):
        roles = {role(step.metric_id, registry.get(step.metric_id)[1]) for step in plan.steps}
        roles.discard(None)
        if not roles:
            return None
        found = self._identify(plan, source, registry)
        if found is None or (_in_ram(found[0]) and not _pairwise()):
            return None
        key, offsets, reference, predicted = found
        root = plan.root_ids[0]
        elements = ((lambda: _elements_of(source, root, len(reference)))
                    if key[-1] is not None else (lambda: None))
        with self.lock:
            group = self._group(key, offsets, source, exact=_in_ram(key))
            lease = Lease(self, key, group, reference, predicted, source.stopped, roles, elements)
            with group.condition:
                group.members[id(lease)] = lease
                group.uses.update(source.uses())
        return lease

    def _group(self, key, offsets, source, exact=False):
        """The live group for ``key``, made when missing. Holds ``self.lock``."""
        owner = budget()
        if self.owner is not owner:
            owner.register_cache(self.evict)
            self.owner = owner
        group = self.groups.get(key)
        if group is None or group.invalid or group.errors:
            group = _Group(offsets, source.uses(), self.workspace, exact)
            self.groups[key] = group
        self.groups.move_to_end(key)
        return group

    def colour_collector(self, plan, source, registry, metric_id):
        """A ``FrameMAE`` that turns the per-atom MAE a colouring reads into
        the structure and dataset MAE the plots ask for, or None when the
        plots could not use them or already have them."""
        if colour_role(metric_id, registry.get(metric_id)[1]) != "mae":
            return None
        found = self._identify(plan, source, registry)
        if found is None or _in_ram(found[0]):
            # Summed from blocks that may cut a structure: not the bits of
            # the metric run on its own, which forces in RAM must keep.
            return None
        key, offsets = found[0], found[1]
        with self.lock:
            group = self.groups.get(key)
            if group is not None and not group.invalid:
                with group.condition:
                    if "summary" in group.results or group.from_colour:
                        return None
        n = len(offsets) - 1
        # As the summary pass: per-structure values beyond this size go to
        # result files there, so they are not collected here.
        if n * 8 > min(128 * 2**20, budget().limit // 8):
            return None
        try:
            sums = budget().retain(np.zeros(n))
        except NotEnoughMemory:
            return None
        return FrameMAE(self, key, offsets, source, sums)

    def _publish(self, key, offsets, source, values):
        with self.lock:
            group = self._group(key, offsets, source)
            with group.condition:
                if "summary" not in group.results:
                    group.from_colour.update(values)

    def element_values(self, plan, source, registry, metric_id):
        """The per-element force MAE or RMSE of a summary already made for
        the same forces and elements, or None. Never reads or scans: a 3D
        colouring by element error after the plots then reads no forces."""
        name = role(metric_id, registry.get(metric_id)[1])
        if name not in ("element_mae", "element_rmse"):
            return None
        found = self._identify(plan, source, registry)
        if found is None or found[0][-1] is None:
            return None
        with self.lock:
            group = self.groups.get(found[0])
            if group is None:
                return None
            with group.condition:
                summary = None if group.invalid else group.results.get("summary")
        if summary is None or summary.element_mae is None:
            return None
        value = getattr(summary, name)
        # A bad value is the colour pass's own question, as for the ranges.
        return value if np.isfinite(value).all() else None

    def colour_range(self, plan, source, registry, metric_id):
        """``(lowest, highest)`` of a per-atom force error from a summary
        already made for the same forces, or None. Never reads or scans."""
        if colour_role(metric_id, registry.get(metric_id)[1]) is None:
            return None
        found = self._identify(plan, source, registry)
        if found is None:
            return None
        with self.lock:
            group = self.groups.get(found[0])
            if group is None:
                return None
            with group.condition:
                summary = None if group.invalid else group.results.get("summary")
        if summary is None or summary.atom_ranges is None:
            return None
        return summary.atom_ranges[colour_role(metric_id, registry.get(metric_id)[1])]

    def _identify(self, plan, source, registry):
        """``(key, offsets, reference, predicted)`` of the one force pair
        ``plan`` reads, or None when the shared pass cannot answer it."""
        from ffast.metrics.execution import RawInput

        f = sys.modules.get("ffast.metrics.builtin.force_metrics")
        if (f is None or not registry.has(_BASE)
                or registry.get(_BASE)[1] is not f.force_difference):
            return None
        pairs, first = set(), None
        for step in plan.steps:
            fn = registry.get(step.metric_id)[1]
            if fn not in (f.force_difference, f.force_parity_density, f.force_parity_outliers):
                continue
            r, p = (step.bindings.get(k) for k in ("reference", "predicted"))
            if not isinstance(r, RawInput) or not isinstance(p, RawInput):
                continue
            reference, predicted = r.value, p.value
            if (not r.identity or not p.identity or reference is None or predicted is None
                    or not all(isinstance(v, (BatchedArray, np.ndarray)) for v in (reference, predicted))
                    or reference.shape != predicted.shape or reference.ndim != 2
                    or reference.shape[1] != 3 or reference.dtype != np.float64
                    or predicted.dtype != np.float64):
                return None
            pairs.add((r.identity, p.identity))
            if first is None:
                first = r, p
        if first is None or len(pairs) > 1:
            # A lease answers for one reference/prediction pair. A plan that
            # mixes pairs takes the normal path, which reads each step's own inputs.
            return None
        r, p = first
        reference, predicted = r.value, p.value
        found, offsets = source.get(plan.root_ids[0], "_offsets", "offsets")
        if not found or offsets is None:
            found, offsets = source.get(plan.root_ids[0], "offsets", "offsets")
        offsets = np.asarray([0, len(reference)] if offsets is None else offsets, dtype=np.int64)
        if (offsets.ndim != 1 or not len(offsets) or offsets[0] != 0
                or offsets[-1] != len(reference) or (np.diff(offsets) < 0).any()):
            return None
        identity = source.identity("offsets")
        if identity is None:
            identity = hashlib.sha256(offsets.tobytes()).hexdigest()
        # The elements are named here, not read: the pass reads them a batch
        # at a time. Unnamed elements are left out of the pass, as they are
        # for forces in RAM (whose element sums keep their own path).
        in_ram = not any(isinstance(v, BatchedArray) for v in (reference, predicted))
        key = (r.identity, p.identity, reference.shape, identity, registry.revision,
               "ram" if in_ram else "batched",
               None if in_ram else source.identity("reference.elements"))
        return key, offsets, reference, predicted

    def release(self, lease):
        gone = []
        with self.lock:
            with lease.group.condition:
                lease.group.members.pop(id(lease), None)
                lease.group.condition.notify_all()
            if (not lease.group.members and (lease.group.errors or lease.group.invalid or lease.group.disabled)
                    and self.groups.get(lease.key) is lease.group):
                gone.append(self.groups.pop(lease.key))
            idle = [key for key, group in self.groups.items() if not group.members]
            for key in idle[:-2]:
                gone.append(self.groups.pop(key))
        gone.clear()

    def evict(self):
        if not self.lock.acquire(blocking=False):
            return False
        group = None
        try:
            for key, candidate in self.groups.items():
                if not candidate.members:
                    group = self.groups.pop(key)
                    break
        finally:
            self.lock.release()
        return group is not None

    def forget(self, fingerprints=None):
        gone = []
        wanted = None if fingerprints is None else set(fingerprints)
        with self.lock:
            for key, group in list(self.groups.items()):
                if wanted is None or any(fp in wanted for pair in group.uses for fp in pair):
                    with group.condition:
                        group.invalid = True
                        group.condition.notify_all()
                    gone.append(self.groups.pop(key))
        gone.clear()

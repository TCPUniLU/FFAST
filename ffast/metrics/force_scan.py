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
    # Looking for an optional optimization must not register built-ins in a
    # different/global registry used by an unrelated headless caller.
    f = sys.modules.get("ffast.metrics.builtin.force_metrics")
    if f is None:
        return None
    if metric_id == "ffast.force_parity_outliers" and fn is f.force_parity_outliers:
        return "outliers"
    if metric_id == "ffast.force_parity_density" and fn is f.force_parity_density:
        return "density"
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


def _summary_batch(batch, reference, predicted, offsets):
    a, b, start, end = batch
    t, p = _read_pair(reference, predicted, start, end)
    with np.errstate(all="ignore"):
        delta = _difference(p, t)
        absolute = np.abs(delta)
        atom_abs = absolute.mean(axis=1)
        atom_sq = np.square(delta).mean(axis=1)
        local = offsets[a:b + 1] - start
        sizes = np.diff(local)
        frame_abs = segmentSums(atom_abs, local) / sizes
        frame_rms = np.sqrt(segmentSums(atom_sq, local) / sizes)
    bad = tuple(bool((~np.isfinite(v) & (sizes > 0)).any())
                for v in (frame_abs, frame_rms))
    lo, hi = np.inf, -np.inf
    finite_pair = np.isfinite(t) & np.isfinite(p)
    if finite_pair.any():
        lo = min(t[finite_pair].min(), p[finite_pair].min())
        hi = max(t[finite_pair].max(), p[finite_pair].max())
    flat = absolute.ravel()
    selected = np.flatnonzero(np.isfinite(flat))
    if len(selected) > K:
        selected = selected[np.argpartition(-flat[selected], K - 1)[:K]]
    return _Partial(a, b, end - start, atom_abs.sum(), atom_sq.sum(),
                    frame_abs, frame_rms, bad, lo, hi, selected + 3 * start,
                    flat[selected], t.ravel()[selected], p.ravel()[selected])


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


def scan(reference, predicted, offsets):
    """One pass, complete frames and bounded workspaces; check cancellation."""
    owner = budget()
    n = len(offsets) - 1
    writers = []
    # Preserve downstream streaming for large frame series, just as the
    # expression evaluator does. Small arrays (or admitted disk-free results)
    # stay resident; the two O(frames) arrays otherwise own ordinary result files.
    small = n * 16 <= min(128 * 2**20, owner.limit // 8)
    memory = small and (n * 8 <= owner.block_bytes or owner.result_limit == 0)
    if memory:
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
    batches = 0
    try:
        # Multiple temporaries and top-k indices fit the producer's reserved
        # workspace; batches never split an individual structure.
        target = max(1, owner.block_bytes // 48)
        fn = lambda batch: _summary_batch(batch, reference, predicted, offsets)
        with closing(_map_batches(fn, _batches(offsets, target))) as partials:
            for part in partials:
                stop.check()
                absolute_sum += part.absolute_sum
                squared_sum += part.squared_sum
                bad_frames[0] |= part.bad[0]
                bad_frames[1] |= part.bad[1]
                if memory:
                    values[part.first:part.last, 0] = part.frame_abs
                    values[part.first:part.last, 1] = part.frame_rms
                else:
                    writers[0].append(part.frame_abs)
                    writers[1].append(part.frame_rms)
                count += part.atoms
                lo, hi = min(lo, part.lo), max(hi, part.hi)
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
        if memory:
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
        with np.errstate(all="ignore"):
            mae = np.asarray(absolute_sum / count if count else np.nan)
            rmse = np.asarray(np.sqrt(squared_sum / count) if count else np.nan)
        mae.flags.writeable = rmse.flags.writeable = False
        result = Summary(mae, rmse, frame_mae, frame_rmse, outliers, (lo, hi),
                         tuple(bad_frames), batches, count, _width.get())
        log.info("force_batch_scan: summary, %d batches, %d atoms, %d lanes, one difference per batch",
                 batches, count, _width.get())
        return result
    except BaseException:
        for writer in writers:
            writer.abort()
        raise


def _histogram_batch(batch, reference, predicted, extent, bins):
    _, _, start, end = batch
    t, p = _read_pair(reference, predicted, start, end)
    lo, hi = extent
    width = (hi - lo) / bins
    keep = np.isfinite(t) & np.isfinite(p)
    col = np.minimum(((t[keep] - lo) / width).astype(np.int64), bins - 1)
    row = np.minimum(((p[keep] - lo) / width).astype(np.int64), bins - 1)
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
    def __init__(self, offsets, uses, workspace):
        self.offsets, self.uses, self.workspace = offsets, set(uses), workspace
        self.condition = threading.Condition()
        self.members = {}
        self.states, self.results, self.errors = {}, {}, {}
        self.invalid = False
        self.disabled = False

    def stopped(self, phase):
        with self.condition:
            return self.invalid or not any(
                not lease.stopped() for lease in self.members.values()
                if phase == "summary" or "density" in lease.roles)

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
        width_token = _width.set(_parallelism())
        try:
            with self.workspace(callback, blocks=10 * _width.get()):
                value = (scan(lease.reference, lease.predicted, self.offsets) if phase == "summary"
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
    def __init__(self, manager, key, group, reference, predicted, stopped, roles):
        self.manager, self.key, self.group = manager, key, group
        self.reference, self.predicted, self.stopped = reference, predicted, stopped
        self.roles = roles

    def select(self, metric_id, fn):
        name = role(metric_id, fn)
        if name is None:
            return NOT_HANDLED
        result = self.group.get("summary", self)
        if result is NOT_HANDLED:
            return NOT_HANDLED
        if name == "density":
            return self.group.get("histogram", self)
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

    def close(self):
        self.manager.release(self)


class ForceScans:
    """At most two idle summaries; live consumers share cancellation/lifetime."""

    def __init__(self, workspace):
        self.workspace = workspace
        self.lock = threading.RLock()
        self.groups = OrderedDict()
        self.owner = None

    def acquire(self, plan, source, registry):
        from ffast.metrics.execution import RawInput

        roles = {role(step.metric_id, registry.get(step.metric_id)[1]) for step in plan.steps}
        roles.discard(None)
        f = sys.modules.get("ffast.metrics.builtin.force_metrics")
        if (not roles or f is None or not registry.has(_BASE)
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
                    or not any(isinstance(v, BatchedArray) for v in (reference, predicted))
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
        key = (r.identity, p.identity, reference.shape, identity, registry.revision)
        owner = budget()
        if self.owner is not owner:
            owner.register_cache(self.evict)
            self.owner = owner
        with self.lock:
            group = self.groups.get(key)
            if group is None or group.invalid or group.errors:
                group = _Group(offsets, source.uses(), self.workspace)
                self.groups[key] = group
            self.groups.move_to_end(key)
            lease = Lease(self, key, group, reference, predicted, source.stopped, roles)
            with group.condition:
                group.members[id(lease)] = lease
                group.uses.update(source.uses())
            return lease

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

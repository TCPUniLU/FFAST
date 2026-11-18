"""Read-only numerical arrays whose first-axis rows are fetched in batches.

No mapped-file I/O. A conversion to a full numpy array is checked against the
shared budget before allocating. Consumers can keep descriptors and read
slices, or map pointwise operations without materialising the whole input.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.lib.mixins import NDArrayOperatorsMixin

from ffast.io.budget import budget


def asarray(value, dtype=None):
    if isinstance(value, BatchedArray):
        return value.astype(dtype) if dtype is not None else value
    return np.asarray(value, dtype=dtype)


def blocks(value, block_bytes=None):
    row = max(1, math.prod(value.shape[1:]) * value.dtype.itemsize)
    per = max(1, (block_bytes or budget().block_bytes) // row)
    if isinstance(value, BatchedArray):
        per = min(per, value.batch_rows)
    for a in range(0, len(value), per):
        from ffast.metrics import stop

        stop.check()
        b = min(len(value), a + per)
        yield a, b, np.asarray(value[a:b])


class BatchedArray(NDArrayOperatorsMixin):
    __array_priority__ = 1000

    def __init__(
        self,
        shape,
        dtype,
        read,
        *,
        digest=None,
        cast_digests=None,
        unique_values=None,
        batch_rows=None,
        label="data",
    ):
        self.shape = tuple(int(n) for n in shape)
        self.dtype = np.dtype(dtype)
        self._read = read
        self.digest = digest
        self.cast_digests = dict(cast_digests or {})
        self.unique_values = unique_values
        self._batch_rows = batch_rows
        self.label = label

    @property
    def ndim(self):
        return len(self.shape)

    @property
    def size(self):
        return math.prod(self.shape)

    @property
    def nbytes(self):
        return self.size * self.dtype.itemsize

    @property
    def batch_rows(self):
        """Respect input workspace when an operation narrows its output rows."""
        own = max(
            1,
            budget().block_bytes
            // max(1, math.prod(self.shape[1:]) * self.dtype.itemsize),
        )
        return (
            min(own, self._batch_rows) if self._batch_rows is not None else own
        )

    def __len__(self):
        return self.shape[0]

    def __array__(self, dtype=None, copy=None):
        dtype = self.dtype if dtype is None else np.dtype(dtype)
        owner = budget()
        with owner.reserve(
            self.size * dtype.itemsize + owner.block_bytes
        ) as lease:
            out = owner.retain(np.empty(self.shape, dtype=dtype), lease)
            for a, b, values in blocks(self):
                out[a:b] = values
        out.flags.writeable = False
        return out

    def __getitem__(self, key):
        if not isinstance(key, tuple):
            key = (key,)
        row, tail = key[0], key[1:]
        if row is Ellipsis:
            tail = (slice(None),) * max(0, self.ndim - 1 - len(tail)) + tail
            row = slice(None)
        if np.isscalar(row):
            i = int(row)
            if i < 0:
                i += len(self)
            if i < 0 or i >= len(self):
                raise IndexError("array index out of range")
            budget().check(math.prod(self.shape[1:]) * self.dtype.itemsize)
            value = self._read(i, i + 1)[0]
            return value[tail] if tail else value
        if isinstance(row, slice):
            a, b, step = row.indices(len(self))
            n = len(range(a, b, step))
            trailing = np.empty((0, *self.shape[1:]))[
                (slice(None), *tail)
            ].shape[1:]

            def read(x, y):
                if step == 1:
                    return self._read(a + x, a + y)[(slice(None), *tail)]
                idx = np.arange(a + x * step, a + y * step, step)
                return self._take(idx)[(slice(None), *tail)]

            view = BatchedArray(
                (n, *trailing),
                self.dtype,
                read,
                label=self.label,
                batch_rows=self.batch_rows,
            )
            if view.nbytes <= budget().block_bytes:
                return read(0, n)
            return view
        idx = np.asarray(row)
        if idx.dtype == bool:
            idx = np.flatnonzero(idx)
        idx = np.asarray(idx, dtype=np.intp)
        flat = idx.reshape(-1)
        flat = np.where(flat < 0, flat + len(self), flat)
        if np.any((flat < 0) | (flat >= len(self))):
            raise IndexError("array index out of range")
        trailing = np.empty((0, *self.shape[1:]))[(slice(None), *tail)].shape[
            1:
        ]
        if idx.ndim != 1:
            budget().check(
                flat.size * math.prod(trailing) * self.dtype.itemsize
            )
            return self._take(flat)[(slice(None), *tail)].reshape(
                *idx.shape, *trailing
            )
        view = BatchedArray(
            (len(flat), *trailing),
            self.dtype,
            lambda a, b: self._take(flat[a:b])[(slice(None), *tail)],
            label=self.label,
            batch_rows=self.batch_rows,
        )
        return (
            view._read(0, len(flat))
            if view.nbytes <= budget().block_bytes
            else view
        )

    def _take(self, rows):
        budget().check(
            len(rows) * math.prod(self.shape[1:]) * self.dtype.itemsize
        )
        out = np.empty((len(rows), *self.shape[1:]), dtype=self.dtype)
        # Coalesce adjacent rows, while preserving order and repetitions.
        a = 0
        while a < len(rows):
            b = a + 1
            while b < len(rows) and rows[b] == rows[b - 1] + 1:
                b += 1
            out[a:b] = self._read(int(rows[a]), int(rows[b - 1]) + 1)
            a = b
        return out

    def astype(self, dtype, copy=True):
        dtype = np.dtype(dtype)
        if dtype == self.dtype:
            return self
        return BatchedArray(
            self.shape,
            dtype,
            lambda a, b: self._read(a, b).astype(dtype),
            digest=self.cast_digests.get(dtype.str),
            batch_rows=self.batch_rows,
            label=self.label,
        )

    def reshape(self, *shape):
        if len(shape) == 1 and isinstance(shape[0], (tuple, list)):
            shape = tuple(shape[0])
        shape = list(shape)
        if -1 in shape:
            i = shape.index(-1)
            shape[i] = self.size // max(
                1, math.prod(n for n in shape if n != -1)
            )
        if math.prod(shape) != self.size:
            raise ValueError("cannot reshape array")
        old_width = math.prod(self.shape[1:])
        new_width = math.prod(shape[1:])

        def read(a, b):
            if old_width == 0:
                return np.empty((b - a, *shape[1:]), dtype=self.dtype)
            start, end = a * new_width, b * new_width
            lo, hi = start // old_width, (end + old_width - 1) // old_width
            data = self._read(lo, hi).reshape(-1)
            return data[start - lo * old_width : end - lo * old_width].reshape(
                b - a, *shape[1:]
            )

        return BatchedArray(
            shape,
            self.dtype,
            read,
            digest=self.digest,
            cast_digests=self.cast_digests,
            unique_values=self.unique_values,
            batch_rows=max(
                1, self.batch_rows * old_width // max(1, new_width)
            ),
            label=self.label,
        )

    def ravel(self, order="C"):
        return self.reshape(-1)

    def copy(self, order="C"):
        budget().check(2 * self.nbytes + budget().block_bytes)
        return budget().retain(np.array(self))

    def tolist(self):
        return np.asarray(self).tolist()

    def content_digest(self):
        if self.digest is None:
            from ffast.cache.fingerprint import ChunkHasher

            h = ChunkHasher(self.shape[1:], self.dtype)
            for _, _, value in blocks(self):
                h.update(value)
            self.digest = h.hexdigest()
        return self.digest

    def __array_ufunc__(self, ufunc, method, *inputs, **kwargs):
        if method != "__call__" or kwargs.get("out") is not None:
            return NotImplemented
        arrays = [v for v in inputs if hasattr(v, "shape")]
        shape = np.broadcast_shapes(*(v.shape for v in arrays))
        if not shape:
            return ufunc(*(np.asarray(v) for v in inputs), **kwargs)

        def read(a, b):
            values = []
            for v in inputs:
                if (
                    hasattr(v, "shape")
                    and v.ndim == len(shape)
                    and v.shape[0] == shape[0]
                ):
                    values.append(np.asarray(v[a:b]))
                else:
                    values.append(v)
            return ufunc(*values, **kwargs)

        samples = [
            (
                np.empty((1,) * v.ndim, dtype=v.dtype)
                if hasattr(v, "dtype")
                else v
            )
            for v in inputs
        ]
        with np.errstate(all="ignore"):
            dtype = np.asarray(ufunc(*samples, **kwargs)).dtype
        return BatchedArray(
            shape,
            dtype,
            read,
            label=self.label,
            batch_rows=min(
                v.batch_rows for v in inputs if isinstance(v, BatchedArray)
            ),
        )

    def __array_function__(self, fn, types, args, kwargs):
        reductions = {
            np.sum: "sum",
            np.mean: "mean",
            np.min: "min",
            np.max: "max",
            np.any: "any",
            np.all: "all",
        }
        if fn in reductions:
            return getattr(args[0], reductions[fn])(*args[1:], **kwargs)
        if fn is np.reshape:
            return args[0].reshape(args[1])
        if fn is np.ravel:
            return args[0].ravel()
        if fn is np.unique:
            if kwargs or len(args) > 1:
                return np.unique(np.asarray(args[0]), *args[1:], **kwargs)
            if args[0].unique_values is not None:
                return np.asarray(args[0].unique_values, dtype=args[0].dtype)
            found = set()
            for _, _, chunk in blocks(args[0]):
                found.update(np.unique(chunk).tolist())
                budget().check(len(found) * 64)
            return np.asarray(sorted(found), dtype=args[0].dtype)
        if fn is np.isin:
            v, wanted = args[:2]
            return BatchedArray(
                v.shape,
                bool,
                lambda a, b: np.isin(v._read(a, b), wanted, **kwargs),
            )
        if fn is np.clip:
            v = args[0]
            low = args[1] if len(args) > 1 else kwargs.get("a_min")
            high = args[2] if len(args) > 2 else kwargs.get("a_max")
            return mapped(lambda x, lo, hi: np.clip(x, lo, hi), v, low, high)
        if fn is np.broadcast_to:
            value, shape = args[:2]
            return BatchedArray(
                shape,
                value.dtype,
                lambda a, b: np.broadcast_to(
                    np.asarray(value[a:b]), (b - a, *shape[1:])
                ),
                batch_rows=value.batch_rows,
            )
        if fn is np.where and len(args) == 3:
            cond, yes, no = args
            source = next(v for v in args if isinstance(v, BatchedArray))
            shape = np.broadcast_shapes(*(np.shape(v) for v in args))
            return mapped(
                lambda c, y, n: np.where(c, y, n),
                cond,
                yes,
                no,
                shape=shape,
                dtype=np.result_type(
                    getattr(yes, "dtype", type(yes)),
                    getattr(no, "dtype", type(no)),
                ),
            )
        if fn is np.shape:
            return args[0].shape
        if fn is np.ndim:
            return args[0].ndim
        if fn is np.size:
            return args[0].size if len(args) == 1 else args[0].shape[args[1]]
        return NotImplemented

    def _reduce(self, name, axis=None, **kwargs):
        if axis is not None and (
            isinstance(axis, int)
            and axis not in (0, -self.ndim)
            or isinstance(axis, tuple)
            and 0 not in axis
            and -self.ndim not in axis
        ):
            sample = getattr(
                np.empty((1, *self.shape[1:]), dtype=self.dtype), name
            )(axis=axis, **kwargs)
            return BatchedArray(
                (len(self), *sample.shape[1:]),
                sample.dtype,
                lambda a, b: getattr(self._read(a, b), name)(
                    axis=axis, **kwargs
                ),
                batch_rows=self.batch_rows,
            )
        if axis not in (None, 0):
            return getattr(np.asarray(self), name)(axis=axis, **kwargs)
        result, count = None, 0
        for _, _, chunk in blocks(self):
            value = getattr(chunk, "sum" if name == "mean" else name)(
                axis=axis, **kwargs
            )
            if result is None:
                result = value
            elif name in ("sum", "mean"):
                result = result + value
            elif name in ("min", "max"):
                result = (np.minimum if name == "min" else np.maximum)(
                    result, value
                )
            else:
                result = (np.logical_and if name == "all" else np.logical_or)(
                    result, value
                )
            count += chunk.size if axis is None else len(chunk)
        if result is None:
            return getattr(np.empty(self.shape, dtype=self.dtype), name)(
                axis=axis, **kwargs
            )
        return result / count if name == "mean" else result

    def sum(self, axis=None, **kw):
        return self._reduce("sum", axis, **kw)

    def mean(self, axis=None, **kw):
        return self._reduce("mean", axis, **kw)

    def min(self, axis=None, **kw):
        return self._reduce("min", axis, **kw)

    def max(self, axis=None, **kw):
        return self._reduce("max", axis, **kw)

    def any(self, axis=None, **kw):
        return self._reduce("any", axis, **kw)

    def all(self, axis=None, **kw):
        return self._reduce("all", axis, **kw)


def mapped(fn, *values, shape=None, dtype=None):
    """A pointwise/per-row kernel, evaluated only for requested rows."""
    source = next(v for v in values if isinstance(v, BatchedArray))
    shape = shape or source.shape
    return BatchedArray(
        shape,
        dtype or source.dtype,
        lambda a, b: fn(
            *(
                (
                    np.asarray(v[a:b])
                    if hasattr(v, "shape")
                    and v.shape
                    and v.shape[0] == shape[0]
                    else v
                )
                for v in values
            )
        ),
        batch_rows=min(
            v.batch_rows for v in values if isinstance(v, BatchedArray)
        ),
    )

"""Large numerical results written incrementally, using ordinary numpy files.

Explicit buffered reads avoid depending on mapped-file performance on Lustre.
Result files are owned by their descriptor and removed after its last user.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import weakref
from pathlib import Path

import numpy as np

from ffast.io.batched import BatchedArray, blocks
from ffast.io.budget import budget
from ffast.io.extxyz_arrays import NotEnoughMemory

_lock = threading.RLock()
_used = 0
_STALE_SECONDS = 3600  # same age rule as frame_cache._make_room


def sweep_stale_temporaries(folder):
    """Delete ``*.tmp`` files older than an hour: a crashed write left them.

    Young ones stay, since another process may be writing them right now.
    Finished and in-progress results share the name ``result-*.npy`` and a live
    result can be old, so those are never touched here.
    """
    try:
        names = list(Path(folder).glob("*.tmp"))
    except OSError:
        return
    for file in names:
        try:
            if file.is_file() and time.time() - file.stat().st_mtime > _STALE_SECONDS:
                file.unlink(missing_ok=True)
        except OSError:
            pass


class MemoryWriter:
    """The same append interface for a result that comfortably fits RAM."""

    def __init__(self, shape, dtype=float):
        size = int(np.prod(shape)) * np.dtype(dtype).itemsize
        owner = budget()
        with owner.reserve(size + 4 * owner.block_bytes) as lease:
            self.values = owner.retain(np.empty(shape, dtype=dtype), lease)
        self.rows = 0

    def append(self, values):
        from ffast.metrics.stop import check

        check()
        values = np.asarray(values, dtype=self.values.dtype)
        end = self.rows + len(values)
        if values.shape[1:] != self.values.shape[1:] or end > len(self.values):
            raise ValueError("Result batch does not fit its output shape")
        self.values[self.rows : end] = values
        self.rows = end

    def finish(self):
        if self.rows != len(self.values):
            raise ValueError("Result output is incomplete")
        return self.values

    def abort(self):
        self.values = None


def result_writer(shape, dtype=float):
    size = int(np.prod(shape)) * np.dtype(dtype).itemsize
    if size <= budget().block_bytes or budget().result_limit == 0:
        return MemoryWriter(shape, dtype)
    try:
        return ResultWriter(shape, dtype)
    except (OSError, NotEnoughMemory):
        return MemoryWriter(shape, dtype)


class ResultWriter:
    """Append rows to a numpy file without ever constructing the full array."""

    def __init__(self, shape, dtype=float):
        global _used
        from ffast.io.frame_cache import _folder
        from ffast.metrics.cache import _memory_backed

        self.shape, self.dtype = tuple(shape), np.dtype(dtype)
        self.size = int(np.prod(shape)) * self.dtype.itemsize + 4096
        self.rows = 0
        self.closed = False
        folder = Path(
            os.environ.get(
                "FFAST_RESULT_CACHE_DIR", str(_folder() / "results")
            )
        ).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        sweep_stale_temporaries(folder)
        if _memory_backed(folder):
            raise NotEnoughMemory(
                "The result directory is in RAM. Choose FFAST_RESULT_CACHE_DIR on disk."
            )
        with _lock:
            if _used + self.size > budget().result_limit:
                raise NotEnoughMemory(
                    "This result exceeds the disk result budget. The dataset remains open."
                )
            free = shutil.disk_usage(folder).free
            if self.size > free // 2:
                raise NotEnoughMemory(
                    f"This result needs {self.size / 2**30:.2f} GiB on disk, "
                    f"but only {free / 2**30:.2f} GiB is free; FFAST reserves "
                    "half the free disk space as headroom. Choose a result "
                    "directory with more space. The dataset remains open."
                )
            _used += self.size
        self.name = None
        try:
            fd, self.name = tempfile.mkstemp(
                prefix="result-", suffix=".npy", dir=folder
            )
            self.file = os.fdopen(fd, "wb")
            np.lib.format.write_array_header_1_0(
                self.file,
                {
                    "descr": np.lib.format.dtype_to_descr(self.dtype),
                    "fortran_order": False,
                    "shape": self.shape,
                },
            )
            self.offset = self.file.tell()
        except BaseException:
            self.abort()
            raise

    def append(self, values):
        from ffast.metrics import stop

        stop.check()
        values = np.asarray(values, dtype=self.dtype)
        if (
            values.shape[1:] != self.shape[1:]
            or self.rows + len(values) > self.shape[0]
        ):
            raise ValueError("Result batch does not fit its output shape")
        self.file.write(np.ascontiguousarray(values).tobytes())
        self.rows += len(values)

    def abort(self):
        global _used
        if self.closed:
            return
        self.closed = True
        if hasattr(self, "file"):
            self.file.close()
        if self.name:
            Path(self.name).unlink(missing_ok=True)
        with _lock:
            _used -= self.size

    def finish(self):
        if self.rows != self.shape[0]:
            raise ValueError("Result output is incomplete")
        self.file.close()
        name, offset, shape, dtype = (
            self.name,
            self.offset,
            self.shape,
            self.dtype,
        )
        width = int(np.prod(shape[1:])) * dtype.itemsize

        def read(a, b):
            with open(name, "rb") as f:
                f.seek(offset + a * width)
                raw = f.read((b - a) * width)
            return np.frombuffer(raw, dtype=dtype).reshape(b - a, *shape[1:])

        out = BatchedArray(shape, dtype, read, label="result")
        out.result_path = name
        weakref.finalize(out, self.abort)
        return out


def store_result(value):
    from ffast.metrics.colouring import deferring_results

    if deferring_results():
        return (
            budget().retain(value) if isinstance(value, np.ndarray) else value
        )
    force_disk = False
    if isinstance(value, np.ndarray):
        try:
            return budget().retain(value)
        except NotEnoughMemory:
            force_disk = True
            raw = value
            value = BatchedArray(raw.shape, raw.dtype, lambda a, b: raw[a:b])
    if not isinstance(value, BatchedArray) or hasattr(value, "result_path"):
        return value
    if (
        value.nbytes <= budget().block_bytes or budget().result_limit == 0
    ) and not force_disk:
        return np.asarray(value)
    try:
        writer = ResultWriter(value.shape, value.dtype)
    except (OSError, NotEnoughMemory) as disk_error:
        try:
            return np.asarray(value)
        except NotEnoughMemory as memory_error:
            raise NotEnoughMemory(
                f"Result storage is unavailable: {disk_error} "
                f"RAM fallback also failed: {memory_error}"
            ) from None
    try:
        for _, _, chunk in blocks(value):
            writer.append(chunk)
        return writer.finish()
    except BaseException:
        writer.abort()
        raise


_UMASK = os.umask(0)
os.umask(_UMASK)


def save_array(path, value):
    """Write a session/export array with bounded temporary memory.

    The data goes to a temporary file next to ``path`` and replaces it only
    when complete. ``value`` may read from ``path`` itself, and a crash leaves
    the old file whole.
    """
    path = os.fspath(path)
    fd, temp = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(path)), suffix=".tmp"
    )
    try:
        # mkstemp makes the file owner-only; keep the permissions a plain
        # open() would give (the old file's, or the default from the umask).
        try:
            mode = os.stat(path).st_mode & 0o777
        except FileNotFoundError:
            mode = 0o666 & ~_UMASK
        os.chmod(temp, mode)
        with os.fdopen(fd, "wb") as f:
            np.lib.format.write_array_header_1_0(
                f,
                {
                    "descr": np.lib.format.dtype_to_descr(value.dtype),
                    "fortran_order": False,
                    "shape": value.shape,
                },
            )
            for _, _, chunk in blocks(value):
                f.write(np.ascontiguousarray(chunk).tobytes())
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def open_array(path):
    """Read an existing numerical numpy file through explicit buffered I/O."""
    with open(path, "rb") as f:
        version = np.lib.format.read_magic(f)
        if version == (1, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_1_0(f)
        else:
            shape, fortran, dtype = np.lib.format.read_array_header_2_0(f)
        offset = f.tell()
        size = os.fstat(f.fileno()).st_size
    if fortran or dtype.hasobject:
        raise ValueError("Disk arrays must be numeric and stored in row order")
    expected = offset + int(np.prod(shape)) * dtype.itemsize
    if size != expected:
        raise ValueError(
            f"{path} is truncated or damaged: file size {size} bytes, "
            f"expected {expected}"
        )
    width = int(np.prod(shape[1:])) * dtype.itemsize

    def read(a, b):
        with open(path, "rb") as f:
            f.seek(offset + a * width)
            raw = f.read((b - a) * width)
        return np.frombuffer(raw, dtype=dtype).reshape(b - a, *shape[1:])

    return BatchedArray(shape, dtype, read)

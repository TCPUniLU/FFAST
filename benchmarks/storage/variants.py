"""The formats that compete: today's npz batches and a Zarr sweep (ADR 0109 §2).

Every variant offers the same few operations on a store folder:
``write``, ``open`` (a handle with ``scan``, ``frame``, ``window``) and the file
walk used for disk bytes. The npz reads imitate ``IndexedStore._cached_slice``.
"""

import os
import time
import zipfile
from pathlib import Path

import numpy as np

from benchmarks.storage.source import META_COLUMNS, read_raw
from ffast.readers.indexed import _member_rows, _restore

CODECS = {"none": None, "lz4": ("lz4", 5), "zstd1": ("zstd", 1),
          "zstd3": ("zstd", 3), "zstd5": ("zstd", 5)}
CHUNKS = {"16k": 16_384, "128k": 131_072, "1m": 1_048_576}
SHARD_CHUNKS = 64  # 16k variants: 64 chunks per shard file


def zarr_names():
    return [f"zarr-{codec}-{chunk}" for codec in CODECS for chunk in CHUNKS]


def all_names():
    return ["npz", *zarr_names()]


def make(name):
    if name.startswith("npz"):
        return NpzVariant(name)
    if name.startswith("zarr-"):
        _, codec, chunk = name.split("-")
        if codec not in CODECS or chunk not in CHUNKS:
            raise ValueError(f"unknown variant {name!r}")
        return ZarrVariant(name, codec, chunk)
    raise ValueError(f"unknown variant {name!r}")


def store_files(store):
    """Every file of a store folder."""
    for folder, _, names in os.walk(store):
        for name in names:
            yield Path(folder) / name


def disk_usage(store):
    """``(bytes, allocated_bytes, n_files)`` of a store folder."""
    size = alloc = count = 0
    for path in store_files(store):
        st = path.stat()
        size += st.st_size
        alloc += st.st_blocks * 512
        count += 1
    return size, alloc, count


def _fsync_path(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Variant:
    name = ""

    def write(self, source, store):
        """Write every window of ``source`` into ``store``. The reading of a window is not
        timed, its writing and syncing are. Returns ``(wall_seconds, cpu_seconds)``."""
        raise NotImplementedError

    def open(self, source, store):
        raise NotImplementedError


class NpzVariant(Variant):
    """The batches as FFAST writes them (``IndexedStore._save``): np.savez, flushed, renamed."""

    def __init__(self, name="npz"):
        self.name = name

    def write(self, source, store):
        store = Path(store)
        store.mkdir(parents=True)
        wall = cpu = 0.0
        for index, batch in enumerate(source.batches):
            part = read_raw(source.batch_path(index))
            target = store / batch["file"]
            temp = store / (batch["file"] + ".tmp")
            w0, c0 = time.perf_counter(), time.process_time()
            with open(temp, "wb") as f:
                np.savez(f, **part)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, target)
            wall += time.perf_counter() - w0
            cpu += time.process_time() - c0
            del part
        return wall, cpu

    def open(self, source, store):
        return NpzHandle(source, Path(store))


class NpzHandle:
    def __init__(self, source, store):
        self.source, self.store = source, store

    def path(self, window):
        return self.store / self.source.batches[window]["file"]

    def rows(self, window, key, start, stop):
        """Like ``IndexedStore._cached_slice`` for a column present in the batch."""
        path = self.path(window)
        with zipfile.ZipFile(path) as archive, open(path, "rb") as f:
            out = _member_rows(archive, f, key, start, stop)
            if out is None:
                raise ValueError(f"{key} cannot be read in place from {path.name}")
            if "__restore__.npy" in archive.namelist():
                with archive.open("__restore__.npy") as member:
                    table = np.lib.format.read_array(member, allow_pickle=False)
                out = _restore({key: out}, table)[key]
        return out

    def scan_window(self, window, key):
        return self.rows(window, key, 0, None)

    def frame(self, i):
        window, l0, l1, _, _ = self.source.locate(i)
        return {key: self.rows(window, key, l0, l1)
                for key in ["positions", *self.source.force_columns]}

    def window(self, index):
        path = self.path(index)
        with zipfile.ZipFile(path) as archive, open(path, "rb") as f:
            names = [n[:-4] for n in archive.namelist()]
            part = {n: _member_rows(archive, f, n, 0, None) for n in names}
        _restore(part)
        return {k: v for k, v in part.items() if k not in META_COLUMNS}

    def reopen(self):
        names = sorted(p.name for p in self.store.glob("*.npz"))
        if not names:
            raise FileNotFoundError(f"no batches in {self.store}")
        return self.frame(0)


def configure_threads():
    """One thread for Blosc and for Zarr's own codec pool; returns what took effect."""
    import numcodecs.blosc as blosc
    import zarr

    blosc.set_nthreads(1)
    zarr.config.set({"threading.max_workers": 1, "async.concurrency": 1})
    return {"blosc_nthreads": blosc.get_nthreads(),
            "zarr_max_workers": zarr.config.get("threading.max_workers"),
            "zarr_async_concurrency": zarr.config.get("async.concurrency")}


class ZarrVariant(Variant):
    """Zarr format 3: one group, one array per column, rows appended window by window."""

    def __init__(self, name, codec, chunk):
        self.name, self.codec, self.chunk = name, codec, chunk
        self.chunk_rows = CHUNKS[chunk]
        self.sharded = chunk == "16k"

    def _create(self, group, key, spec):
        from zarr.codecs import BloscCodec

        dtype = np.dtype(spec["dtype"])
        tail = tuple(spec["tail"])
        compressors = None
        if CODECS[self.codec] is not None:
            cname, clevel = CODECS[self.codec]
            compressors = BloscCodec(cname=cname, clevel=clevel, shuffle="shuffle",
                                     typesize=dtype.itemsize)
        kwargs = {}
        if self.sharded:
            kwargs["shards"] = (self.chunk_rows * SHARD_CHUNKS, *tail)
        return group.create_array(
            node_name(key), shape=(0, *tail), chunks=(self.chunk_rows, *tail), dtype=dtype,
            compressors=compressors, fill_value=0, **kwargs)

    def write(self, source, store):
        import zarr

        configure_threads()
        store = Path(store)
        group = zarr.open_group(store, mode="w", zarr_format=3)
        group.attrs["names"] = {node_name(k): k for k in source.columns}
        group.attrs["restore"] = source.restore
        arrays = {key: self._create(group, key, spec) for key, spec in source.columns.items()}
        seen = {}
        wall = cpu = 0.0
        for index in range(len(source.batches)):
            part = read_raw(source.batch_path(index))
            w0, c0 = time.perf_counter(), time.process_time()
            for key, array in arrays.items():
                array.append(part[key], axis=0)
            for path in store_files(store):
                st = path.stat()
                stamp = (st.st_mtime_ns, st.st_size)
                if seen.get(path) != stamp:
                    _fsync_path(path)
                    seen[path] = stamp
            wall += time.perf_counter() - w0
            cpu += time.process_time() - c0
            del part
        return wall, cpu

    def open(self, source, store):
        import zarr

        configure_threads()
        return ZarrHandle(source, zarr.open_group(Path(store), mode="r"))


def node_name(key):
    """A Zarr node name for a column name: ':' is kept (Zarr 3 allows it)."""
    return key


class ZarrHandle:
    def __init__(self, source, group):
        self.source, self.group = source, group
        self.arrays = {}

    def array(self, key):
        if key not in self.arrays:
            self.arrays[key] = self.group[node_name(key)]
        return self.arrays[key]

    def scan_window(self, window, key):
        r0, r1 = self.source.frame_rows(window, key)
        return self.array(key)[r0:r1]

    def frame(self, i):
        _, _, _, g0, g1 = self.source.locate(i)
        return {key: self.array(key)[g0:g1] for key in ["positions", *self.source.force_columns]}

    def window(self, index):
        part = {}
        for key in self.source.columns:
            r0, r1 = self.source.frame_rows(index, key)
            part[key] = self.array(key)[r0:r1]
        _restore(part, np.asarray(self.source.restore) if self.source.restore else [])
        return part

    def reopen(self):
        return self.frame(0)

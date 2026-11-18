"""Indexed text storage with bounded parsed batches (ADR 0102).

The first pass learns the complete schema and hashes every column. Subsequent
reads use a shared RAM cache, an optional bounded numpy batch cache, or the
original source. The same Rust parse_into contract fills each bounded batch.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import shutil
import struct
import tempfile
import threading
import uuid
import weakref
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from ffast.io.batched import BatchedArray
from ffast.io.budget import budget
from ffast.io.extxyz_arrays import NotEnoughMemory, NotFast
from ffast.readers.contract import ReaderError

log = logging.getLogger("FFAST")
_disk_lock = threading.RLock()
_parse_lock = threading.RLock()
# Stores open in this process. Eviction must not delete their cache files.
# This guards one process only: another server sharing the folder cannot see it.
_live_stores = weakref.WeakSet()


def _protected(path, owner):
    """True if ``path`` belongs to another store that is open right now. The
    store that is saving (``owner``) may still trim its own files. Identities
    are read at eviction time because a scan changes a store's identity."""
    name = Path(path).name
    return any(s is not owner and name.startswith(s.identity)
               for s in list(_live_stores))


_FRAME_COLUMNS = ("n_atoms", "cell", "pbc", "numbers", "__restore__")


def _cache_levels(part):
    """Disk-cache priority of each column: 1 is kept first, 3 last (ING-05)."""
    return {
        k: 1 if k in _FRAME_COLUMNS or k.startswith(("energy:", "frame:"))
        else 2 if k.startswith("forces:")
        else 3
        for k in part
    }


def _compact(part):
    """Store species as uint8 on disk; ``_restore`` gives back the parsed dtype."""
    numbers = part.get("numbers")
    if (
        numbers is None
        or numbers.dtype == np.uint8
        or numbers.dtype.kind not in "iu"
        or (numbers.size and (numbers.min() < 0 or numbers.max() > 255))
    ):
        return part
    part = dict(part)
    part["numbers"] = numbers.astype(np.uint8)
    part["__restore__"] = np.asarray([["numbers", numbers.dtype.str]])
    return part


def _restore(part, restore=None):
    """Undo ``_compact`` on a loaded batch (or on one ``restore`` table)."""
    table = part.pop("__restore__", None) if restore is None else restore
    if table is not None:
        for name, dtype in table:
            if name in part:
                part[name] = part[name].astype(np.dtype(str(dtype)))
    return part


def file_state(path):
    s = os.stat(path)
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns


def _read_into(f, offset, nbytes, out):
    """Read ``nbytes`` of the file at ``offset`` straight into the writable
    uint8 array ``out``: one copy, no temporary buffer. A memory map per read
    made Basic Errors on a 10 GB file 2.6x slower: threads opening and closing
    maps at the same time wait on each other (ADR 0106)."""
    if nbytes == 0:
        return
    f.seek(offset)
    view = memoryview(out)
    done = 0
    while done < nbytes:
        got = f.readinto(view[done:])
        if not got:
            raise EOFError("A cached batch ends early")
        done += got


def _member_rows(archive, f, key, start, stop):
    """Rows ``[start, stop)`` (all rows if ``stop`` is None) of the stored
    ``key.npy`` member of an open npz archive, copied into a new array; None
    when the member is compressed or in a layout that cannot be read in place."""
    member = archive.getinfo(key + ".npy")
    if member.compress_type != zipfile.ZIP_STORED:
        return None
    f.seek(member.header_offset)
    header = f.read(30)
    if len(header) != 30 or header[:4] != b"PK\x03\x04":
        return None
    name_len, extra_len = struct.unpack_from("<HH", header, 26)
    f.seek(name_len + extra_len, os.SEEK_CUR)
    version = np.lib.format.read_magic(f)
    reader = (np.lib.format.read_array_header_1_0 if version == (1, 0)
              else np.lib.format.read_array_header_2_0)
    shape, fortran, dtype = reader(f)
    stop = shape[0] if stop is None else stop
    if fortran or dtype.hasobject or start < 0 or stop > shape[0]:
        return None
    width = int(np.prod(shape[1:])) * dtype.itemsize
    out = np.empty((stop - start, *shape[1:]), dtype=dtype)
    _read_into(f, f.tell() + start * width, (stop - start) * width,
                 out.reshape(-1).view(np.uint8))
    return out


class NoManifest(Exception):
    """A store was asked to open from its manifest only, and there is none."""


class IndexedArrays(dict):
    """The ordinary reader keys, backed by a shared indexed source."""

    def __init__(self, store):
        self.store = store
        super().__init__(n_atoms=store.counts)
        for key, info in store.info.items():
            self[key] = store.column(key)


class IndexedStore:
    def __init__(
        self,
        reader,
        path,
        contents,
        frames,
        starts,
        ends,
        counts,
        *,
        progress=None,
        workers=None,
        use_cache=True,
        scan_source=False,
        require_manifest=False,
    ):
        from ffast.io import frame_cache
        from ffast.loaders.ase import _sample_indices

        self.reader = reader
        self.path = os.path.abspath(path)
        self.state = file_state(self.path)
        self.contents = contents
        self.frames = np.asarray(frames, dtype=np.int64)
        self.starts = np.asarray(starts, dtype=np.int64)
        self.ends = np.asarray(ends, dtype=np.int64)
        self.counts = np.asarray(counts, dtype=np.int64)
        self.offsets = np.concatenate(([0], np.cumsum(self.counts)))
        self.workers = workers
        self.use_cache = bool(use_cache and budget().parsed_limit)
        self.folder = frame_cache._folder() / "batches"
        identity = (
            1,
            reader.name,
            getattr(reader, "cache_version", 1),
            self.path,
            self.state,
        )
        h = hashlib.sha256(repr(identity).encode())
        h.update(self.frames)
        self.identity = h.hexdigest()
        _live_stores.add(self)
        self.info = {}
        self.sample = {}
        self.sample_frames = set(_sample_indices(len(counts)))
        self.element_set = set()
        self.uniform = not len(self.counts) or bool((self.counts == self.counts[0]).all())
        self.first_numbers = None
        self._small_columns = {}
        self._small_parts = {}
        self._small_bytes = 0
        self._small_limit = min(128 * 2**20, budget().limit // 8)
        self._warming = False
        if scan_source:
            self._warming = True
            self._scan_source(progress)
            return
        self.ranges = self._ranges()
        if self._load_manifest():
            self.check_source()
            if progress:
                progress(len(counts), len(counts))
            return
        if require_manifest:
            _live_stores.discard(self)
            raise NoManifest(self.path)
        self._warming = True
        from ffast.readers.text import fast_threads
        with ThreadPoolExecutor(max_workers=min(4, fast_threads())) as pool:
            self._hash_pool = pool
            for a, b in self.ranges:
                from ffast.metrics.stop import check

                check()
                self.check_source()
                part = self._part(a, b)
                self._observe(part, a, b)
                self._remember_small(part, a, b)
                self._observe_elements(part, a, b)
                if progress:
                    progress(b, len(self.counts))
        del self._hash_pool
        self.check_source()
        for key, info in self.info.items():
            dtype = info["dtype"]
            info["digest"] = info["hashes"][dtype.str].hexdigest()
            info["float_digest"] = info["hashes"][
                np.dtype(np.float64).str
            ].hexdigest()
            info.pop("hashes")
        self.same_formula = all(
            np.array_equal(z, self.sample[0]) for z in self.sample.values()
        )
        self._finish_small()
        self._save_manifest()
        self._warming = False

    def _scan_source(self, progress):
        """Parse each indexed window while its text is still in the OS cache.

        One indexing window runs beside one parsing/hashing window. Both are
        bounded; this avoids a second physical pass over a file larger than RAM.
        """
        from ffast.io import frame_index
        from ffast.readers import text
        from ffast.metrics import stop
        from ffast.loaders.ase import _sample_indices

        self.identity += "-" + uuid.uuid4().hex
        temporary_identity = self.identity
        self.ranges = []
        # A scan projects the cache size from bytes (ADR 0106). The disk
        # allowance is read at the first save, and the kept level only falls.
        self._scanning = True
        self._scan_allowance = None
        self._kept_level = None
        pending = None
        try:
            with ThreadPoolExecutor(max_workers=min(4, text.fast_threads())) as hashes, \
                    ThreadPoolExecutor(max_workers=1) as parsing:
                self._hash_pool = hashes

                def consume(starts, counts, end):
                    stop.check()
                    self.check_source()
                    a, b = len(self.counts), len(self.counts) + len(counts)
                    self.starts = np.concatenate((self.starts, starts))
                    self.ends = np.concatenate((self.ends, starts[1:], [end]))
                    self.counts = np.concatenate((self.counts, counts))
                    self.offsets = np.concatenate((self.offsets, self.offsets[-1] + np.cumsum(counts)))
                    self.frames = np.arange(b, dtype=np.int64)
                    self.uniform = self.uniform and bool((counts == self.counts[0]).all())
                    self.ranges.append((a, b))
                    part = self._part(a, b)
                    self._observe(part, a, b)
                    self._remember_small(part, a, b)
                    self._observe_elements(part, a, b)
                    if progress and callable(getattr(progress, "bytes_read", None)):
                        progress.bytes_read(min(int(end), self.state[2]), self.state[2], b)

                def found(starts, counts, end):
                    nonlocal pending
                    stop.check()
                    if pending is not None:
                        pending.result()
                    context = contextvars.copy_context()
                    pending = parsing.submit(context.run, consume, starts, counts, end)

                index = frame_index.scan(self.path, on_batch=found,
                    batch_bytes=min(128 * 2**20, max(1024, budget().limit // 16)))
                if pending is not None:
                    pending.result()
            del self._hash_pool
            self.check_source()
            if not len(self.counts):
                raise ReaderError(f"{os.path.basename(self.path)} has no frames")
            h = hashlib.sha256(repr((1, self.reader.name,
                getattr(self.reader, "cache_version", 1), self.path, self.state)).encode())
            h.update(self.frames)
            self.identity = h.hexdigest()
            # Publish the parsed windows under the final selection identity.
            if self.use_cache and self.folder.exists():
                for old in self.folder.glob(temporary_identity + "-*.npz"):
                    os.replace(old, old.with_name(old.name.replace(temporary_identity, self.identity, 1)))
            for info in self.info.values():
                info["digest"] = info["hashes"][info["dtype"].str].hexdigest()
                info["float_digest"] = info["hashes"][np.dtype(np.float64).str].hexdigest()
                info.pop("hashes")
            # Fingerprints use the historical deterministic 60-frame formula
            # sample. Different atom counts settle it without reading any atoms.
            sample = np.asarray(list(_sample_indices(len(self.counts))))
            self.same_formula = bool((self.counts[sample] == self.counts[0]).all())
            if self.same_formula:
                first = np.unique(self.first_numbers, return_counts=True)
                for frame in sample:
                    part = self._part(int(frame), int(frame) + 1)
                    current = np.unique(part["numbers"], return_counts=True)
                    if not all(np.array_equal(a, b) for a, b in zip(first, current)):
                        self.same_formula = False
                        break
            self._finish_small()
            self._save_manifest()
            self._warming = False
            frame_index.save_cached(index)
            text._save_table(self.reader, self.path, (self.state[2], self.state[3]),
                             (index.starts, index.counts))
            if progress:
                progress(len(self.counts), len(self.counts))
        finally:
            if self.folder.exists():
                for path in self.folder.glob(temporary_identity + "-*.npz"):
                    path.unlink(missing_ok=True)

    def _observe_elements(self, part, a, b):
        if self.first_numbers is None:
            self.first_numbers = part["numbers"][:self.counts[a]].copy()
        if self.uniform:
            self.uniform = bool(np.all(
                part["numbers"].reshape(b - a, int(self.counts[0])) == self.first_numbers))
        for frame in self.sample_frames.intersection(range(a, b)):
            lo, hi = self.offsets[frame:frame + 2] - self.offsets[a]
            unique, counts = np.unique(part["numbers"][lo:hi], return_counts=True)
            self.sample[frame] = np.column_stack((unique, counts))
        self.element_set.update(np.unique(part["numbers"]).tolist())

    def _remember_small(self, part, a, b):
        # Retain small scalar columns once, instead of parsing all atom columns
        # on every energy reduction. Energies get admission before other fields.
        keys = sorted((k for k, v in part.items() if v.ndim == 1 and
                       k.startswith(("energy:", "frame:"))),
                      key=lambda k: (not k.startswith("energy:"), k))
        for key in keys:
            value = part[key]
            if self._small_bytes + value.nbytes > self._small_limit:
                continue
            try:
                with budget().reserve(value.nbytes) as lease:
                    kept = budget().retain(value.copy(), lease)
                self._small_parts.setdefault(key, []).append((a, b, kept))
            except NotEnoughMemory:
                continue
            self._small_bytes += value.nbytes

    def _finish_small(self):
        for key, parts in self._small_parts.items():
            info = self.info[key]
            size = len(self.counts) * info["dtype"].itemsize
            if sum(b - a for a, b, _ in parts) != len(self.counts):
                continue
            try:
                with budget().reserve(size) as lease:
                    array = np.empty(len(self.counts), dtype=info["dtype"])
                    for a, b, values in parts:
                        array[a:b] = values
                    budget().retain(array, lease)
                array.flags.writeable = False
                self._small_columns[key] = array
            except NotEnoughMemory:
                pass
        self._small_parts.clear()
        if self.use_cache and self._small_columns:
            self._save(-1, -1, self._small_columns)

    def _load_small(self):
        try:
            with np.load(self._cache_path(-1, -1), allow_pickle=False) as saved:
                for key in saved.files:
                    if key not in self.info or not key.startswith(("energy:", "frame:")):
                        continue
                    size = len(self.counts) * self.info[key]["dtype"].itemsize
                    if size > self._small_limit - sum(v.nbytes for v in self._small_columns.values()):
                        continue
                    with budget().reserve(size) as lease:
                        array = saved[key]
                        if array.shape != (len(self.counts),) or array.dtype != self.info[key]["dtype"]:
                            continue
                        budget().retain(array, lease)
                    array.flags.writeable = False
                    self._small_columns[key] = array
        except (OSError, ValueError, EOFError, zipfile.BadZipFile, NotEnoughMemory):
            pass

    def _load_manifest(self):
        if not self.use_cache:
            return False
        try:
            meta = json.loads(
                (self.folder / f"{self.identity}.json").read_text()
            )
            self.info = {
                k: {
                    **v,
                    "dtype": np.dtype(v["dtype"]),
                    "tail": tuple(v["tail"]),
                }
                for k, v in meta["columns"].items()
            }
            if not {"numbers", "positions"} <= self.info.keys():
                self.info = {}
                return False
            self.first_numbers = np.asarray(
                meta["first_numbers"], dtype=np.int64
            )
            self.uniform = bool(meta["uniform"])
            self.same_formula = bool(meta["same_formula"])
            self.element_set = set(meta["elements"])
            ranges = [tuple(map(int, pair)) for pair in meta["ranges"]]
            if (not ranges or ranges[0][0] != 0 or ranges[-1][1] != len(self.counts)
                    or any(a >= b for a, b in ranges)
                    or any(left[1] != right[0] for left, right in zip(ranges, ranges[1:]))):
                raise ValueError("Invalid parsed-cache partition")
            self.ranges = ranges
            self._load_small()
            return True
        except (OSError, ValueError, KeyError, TypeError):
            self.info = {}
            return False

    def _save_manifest(self):
        if not self.use_cache:
            return
        temp = None
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            meta = {
                "columns": {
                    k: {**v, "dtype": v["dtype"].str}
                    for k, v in self.info.items()
                },
                "first_numbers": self.first_numbers.tolist(),
                "uniform": self.uniform,
                "same_formula": self.same_formula,
                "elements": sorted(self.element_set),
                "ranges": self.ranges,
            }
            data = json.dumps(meta)
            if len(data) > min(budget().parsed_limit, budget().block_bytes):
                return
            from ffast.io.frame_cache import _entry_bytes

            with _disk_lock:
                destination = self.folder / f"{self.identity}.json"
                used = sum(
                    _entry_bytes(d)
                    for d in self.folder.parent.iterdir()
                    if d.is_dir() and d.name != "results"
                )
                old = destination.stat().st_size if destination.exists() else 0
                limit = min(
                    budget().parsed_limit,
                    shutil.disk_usage(self.folder).free // 4,
                )
                if used - old + len(data) > limit:
                    # The free-space allowance shrinks as batches are written.
                    # Preserve the tiny schema/fingerprint record by dropping
                    # an atom batch rather than repeating a full source scan.
                    candidates = sorted(self.folder.glob("*.npz"),
                        key=lambda p: (p.name.endswith("--1--1.npz"), p.stat().st_mtime_ns))
                    for candidate in candidates:
                        if _protected(candidate, self):
                            continue
                        if used - old + len(data) <= limit:
                            break
                        used -= candidate.stat().st_size
                        candidate.unlink(missing_ok=True)
                if used - old + len(data) > limit:
                    return
                fd, temp = tempfile.mkstemp(dir=self.folder, suffix=".tmp")
                with os.fdopen(fd, "w") as f:
                    f.write(data)
                os.replace(temp, destination)
        except OSError:
            if temp:
                Path(temp).unlink(missing_ok=True)

    def check_source(self):
        try:
            same = file_state(self.path) == self.state
        except OSError:
            same = False
        if not same:
            raise ReaderError(
                f"{os.path.basename(self.path)} changed or is unavailable. Reload the dataset."
            )

    def _ranges(self):
        # Bound physical reads as well as selected bytes: the Rust parser
        # merges gaps <= 1 MiB. Sparse sampling must not inflate a batch to GB.
        target = max(1024, min(256 * 2**20, budget().limit // 32))
        out, a, cost = [], 0, 0
        for i, (s, e) in enumerate(zip(self.starts, self.ends)):
            extra = int(e - s)
            if i > a:
                gap = int(s - self.ends[i - 1])
                if 0 <= gap <= 1 << 20:
                    extra += gap
            if i > a and cost + extra > target:
                out.append((a, i))
                a, cost = i, 0
                extra = int(e - s)
            cost += extra
        out.append((a, len(self.counts)))
        return out

    def _cache_path(self, a, b):
        return self.folder / f"{self.identity}-{a}-{b}.npz"

    def _cached_slice(self, a, b, key, start, stop):
        """Read only the requested rows of an uncompressed numpy zip member.

        np.savez uses ZIP_STORED. Its members are ordinary .npy files; seek
        past the ZIP and numpy headers, then read just the selected bytes into
        a new array (ADR 0106). The operating system's file cache keeps recent
        reads in RAM; FFAST keeps no copy of its own.
        """
        if not self.use_cache:
            return None
        path = self._cache_path(a, b)
        try:
            with zipfile.ZipFile(path) as archive, open(path, "rb") as f:
                try:
                    archive.getinfo(key + ".npy")
                except KeyError:
                    if "__omitted__.npy" in archive.namelist():
                        with archive.open("__omitted__.npy") as member:
                            if key in np.lib.format.read_array(member, allow_pickle=False):
                                return None
                    info = self.info[key]
                    return np.full(
                        (stop - start, *info["tail"]),
                        np.nan,
                        dtype=info["dtype"],
                    )
                out = _member_rows(archive, f, key, start, stop)
                if out is None:
                    return None
                if "__restore__.npy" in archive.namelist():
                    with archive.open("__restore__.npy") as member:
                        table = np.lib.format.read_array(member, allow_pickle=False)
                    out = _restore({key: out}, table)[key]
            os.utime(path)
            return out
        except (OSError, ValueError, EOFError, zipfile.BadZipFile):
            return None

    def _cached_batch(self, a, b):
        """Every column of one cached window, read in place from the file.
        None when the window omits a column; raises when the file is missing
        or damaged."""
        path = self._cache_path(a, b)
        with zipfile.ZipFile(path) as archive, open(path, "rb") as f:
            names = archive.namelist()
            if "__omitted__.npy" in names:
                return None
            part = {}
            for name in names:
                if name == "__restore__.npy":
                    continue
                value = _member_rows(archive, f, name[:-4], 0, None)
                if value is None:
                    raise ValueError("A cached column cannot be read in place")
                part[name[:-4]] = value
            if "__restore__.npy" in names:
                with archive.open("__restore__.npy") as member:
                    table = np.lib.format.read_array(member, allow_pickle=False)
                _restore(part, table)
        os.utime(path)
        return part

    def _save(self, a, b, part):
        if not self.use_cache or not budget().parsed_limit:
            return
        temp = None
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            size = sum(v.nbytes for v in part.values()) + 4096 * len(part)
            scanning = getattr(self, "_scanning", False) and a >= 0
            if scanning:
                # Read the allowance once: the scan fills the disk itself, and
                # a later window must not decide differently because of that.
                if self._scan_allowance is None:
                    self._scan_allowance = min(
                        budget().parsed_limit, shutil.disk_usage(self.folder).free // 4
                    )
                limit = self._scan_allowance
            else:
                limit = min(
                    budget().parsed_limit, shutil.disk_usage(self.folder).free // 4
                )
            if a >= 0:
                part = _compact(part)
                levels = _cache_levels(part)
                # Keep whole priority levels while the projected dataset
                # total fits: (1) per-frame values and species, (2) forces,
                # (3) positions and other atom columns. Level 1 is kept even
                # if nothing else fits. Omitted columns are read from the
                # original file, never filled with NaN.
                if scanning:
                    # The frame count is not known yet: scale by bytes of the
                    # file, from this window's text to the whole file.
                    window = max(1, int(self.ends[b - 1] - self.starts[a]))
                    scale = self.state[2] / window
                else:
                    scale = len(self.counts) / max(1, b - a)
                keep = 1
                for level in range(2, 4):
                    wanted = [k for k in part if levels[k] <= level]
                    projected = (sum(part[k].nbytes for k in wanted) + 4096 * len(wanted)) * scale
                    if projected > limit:
                        break
                    keep = level
                if scanning:
                    # The choice may fall in a later window, never rise.
                    if self._kept_level is not None:
                        keep = min(keep, self._kept_level)
                    self._kept_level = keep
                selected = {k: v for k, v in part.items() if levels[k] <= keep}
                if len(selected) < len(part):
                    selected["__omitted__"] = np.asarray(sorted(set(part) - set(selected)))
                    part = selected
                size = sum(v.nbytes for v in part.values()) + 4096 * len(part)
            if size > limit:
                return
            with _disk_lock:
                # Independent servers serialize eviction and publication too.
                with open(self.folder / ".lock", "a+b") as lock:
                    try:
                        import fcntl

                        fcntl.flock(lock, fcntl.LOCK_EX)
                    except ImportError:
                        pass
                    from ffast.io.result_array import sweep_stale_temporaries

                    sweep_stale_temporaries(self.folder)
                    files = sorted(
                        (
                            p
                            for p in self.folder.iterdir()
                            if p.suffix in (".npz", ".json")
                        ),
                        # Tiny identity/schema records and scalar columns
                        # prevent full rescans; evict atom batches before them.
                        key=lambda p: (p.suffix == ".json" or p.name.endswith("--1--1.npz"),
                                       p.stat().st_mtime_ns),
                    )
                    used = sum(p.stat().st_size for p in files)
                    # The existing complete-array cache shares this parsed
                    # budget. Result files have their own budget.
                    legacy = sum(
                        f.stat().st_size
                        for d in self.folder.parent.iterdir()
                        if d.is_dir() and d.name not in ("batches", "results")
                        for f in d.iterdir()
                        if f.is_file()
                    )
                    for old in files:
                        if _protected(old, self):
                            continue
                        if legacy + used + size <= limit:
                            break
                        used -= old.stat().st_size
                        old.unlink(missing_ok=True)
                    if legacy + used + size > limit:
                        return
                    fd, temp = tempfile.mkstemp(dir=self.folder, suffix=".tmp")
                    with os.fdopen(fd, "wb") as f:
                        np.savez(f, **part)
                    os.replace(temp, self._cache_path(a, b))
        except OSError as exc:
            log.debug(
                "Parsed batch cache unavailable for %s: %s", self.path, exc
            )
            if temp:
                Path(temp).unlink(missing_ok=True)

    def _part(self, a, b):
        from ffast.readers import text
        from ffast.metrics.stop import check

        check()

        self.check_source()
        key = self.identity, a, b
        hit = budget().cached(key)
        if hit is not None:
            return hit
        part = None
        if self.use_cache:
            try:
                part = self._cached_batch(a, b)
                if part is not None:
                    # Read through the operating system's file cache, which it
                    # drops first when memory is short: not kept in the RAM
                    # cache (ADR 0106).
                    return part
            except (OSError, ValueError, EOFError, zipfile.BadZipFile):
                # A damaged or cut-off batch: parse the source instead, and
                # drop the file so it is not hit again (_save rewrites it).
                part = None
                if self._cache_path(a, b).exists():
                    self._cache_path(a, b).unlink(missing_ok=True)
        if part is None:
            atoms = int(self.counts[a:b].sum())
            # Atom arrays + parser input + transient headers. A giant single
            # frame is refused before its output arrays are allocated.
            need = atoms * (
                32
                + 24 * len(self.contents.get("forces") or [])
                + 8 * len(self.contents.get("atom_fields") or [])
            )
            need += int((self.ends[a:b] - self.starts[a:b]).sum()) * 8
            with budget().reserve(need):
                if (
                    callable(getattr(self.reader, "parse_into", None))
                    and text.fast_parser_usable()
                ):
                    try:
                        threads = (
                            text.fast_threads()
                            if self.workers is None
                            else max(1, int(self.workers))
                        )
                        threads = min(
                            threads,
                            b - a,
                            max(
                                1,
                                int((self.ends[a:b] - self.starts[a:b]).sum())
                                // 2**20,
                            ),
                        )
                        part = text._read_fast_arrays(
                            self.reader,
                            self.path,
                            self.contents,
                            self.frames[a:b],
                            self.starts[a:b],
                            self.ends[a:b],
                            self.counts[a:b],
                            None,
                            threads,
                            b - a,
                            log_read=False,
                        )
                    except NotFast as exc:
                        if not getattr(self, "_warned_fallback", False):
                            log.warning(
                                "%s: read with the slower Python parser (%s)",
                                os.path.basename(self.path),
                                exc,
                                extra={"popup": False},
                            )
                            self._warned_fallback = True
                        part = self.reader.parse(
                            text._read_spans(
                                self.path, self.starts[a:b], self.ends[a:b]
                            ),
                            int(self.frames[a]),
                        )
                else:
                    if callable(getattr(self.reader, "parse_into", None)):
                        text._warn_no_fast_parser()
                    part = self.reader.parse(
                        text._read_spans(
                            self.path, self.starts[a:b], self.ends[a:b]
                        ),
                        int(self.frames[a]),
                    )
            self.check_source()
            if self._warming and (a, b) in self.ranges:
                self._save(a, b, part)
        size = sum(v.nbytes for v in part.values())
        budget().keep(key, part, size)
        return part

    def _read_ranges(self, a, b):
        """Split source reads by parser workspace, even with an old manifest.

        Storage batches favour sequential I/O during the initial scan. Metric
        readers need much smaller workspaces because many panels run at once.
        """
        atom_bytes = (
            32
            + 24 * len(self.contents.get("forces") or [])
            + 8 * len(self.contents.get("atom_fields") or [])
        )
        costs = self.counts[a:b] * atom_bytes
        costs = np.cumsum(costs + (self.ends[a:b] - self.starts[a:b]) * 8)
        start, before = a, 0
        while start < b:
            end = a + int(
                np.searchsorted(
                    costs, before + budget().block_bytes, side="right"
                )
            )
            end = max(start + 1, end)
            yield start, end
            before = int(costs[end - a - 1])
            start = end

    def _observe(self, part, a, b):
        keys = list(set(part) - {"n_atoms"} | set(self.info))
        pool = getattr(self, "_hash_pool", None)
        if pool is not None and sum(v.nbytes for v in part.values()) >= 2**20:
            list(pool.map(lambda key: self._observe_key(part, a, b, key), keys))
        else:
            for key in keys:
                self._observe_key(part, a, b, key)

    def _observe_key(self, part, a, b, key):
        from ffast.readers.text import per_atom

        value = part.get(key)
        n = (
            int(self.offsets[b] - self.offsets[a])
            if per_atom(key)
            else b - a
        )
        before = int(self.offsets[a]) if per_atom(key) else a
        info = self.info.get(key)
        if info is None:
            dtype = value.dtype
            if before:
                dtype = np.result_type(dtype, np.float64)
            info = self.info[key] = {
                "dtype": dtype,
                "tail": value.shape[1:],
                "has_nan": bool(before),
                "hashes": {},
            }
            # Retain both integer and promoted-real digests: an integer
            # header may acquire a missing/real value in a later batch.
            for d in {dtype.str, np.dtype(np.float64).str}:
                info["hashes"][d] = hashlib.md5()
                if before:
                    self._hash_missing(
                        info["hashes"][d],
                        before,
                        info["tail"],
                        np.dtype(d),
                    )
        if value is None:
            info["dtype"] = np.dtype(np.float64)
            info["has_nan"] = True
            self._hash_missing(
                info["hashes"][np.dtype(np.float64).str],
                n,
                info["tail"],
                np.float64,
            )
        else:
            if value.shape[1:] != info["tail"]:
                raise NotFast(f"column {key} changes width")
            info["dtype"] = np.result_type(info["dtype"], value.dtype)
            info["has_nan"] |= bool(np.isnan(value).any())
            for d, h in info["hashes"].items():
                h.update(np.ascontiguousarray(value, dtype=np.dtype(d)))

    @staticmethod
    def _hash_missing(h, n, tail, dtype):
        # Only the floating candidate matters for a column with missing rows.
        if np.dtype(dtype).kind not in "fc":
            return
        per = max(1, budget().block_bytes // max(8, 8 * int(np.prod(tail))))
        for a in range(0, n, per):
            h.update(np.full((min(per, n - a), *tail), np.nan, dtype=dtype))

    def column(self, key):
        from ffast.readers.text import per_atom

        info = self.info[key]
        atom = per_atom(key)
        n = int(self.offsets[-1]) if atom else len(self.counts)

        def read(a, b):
            self.check_source()
            if key in self._small_columns:
                return self._small_columns[key][a:b]
            out = np.empty((b - a, *info["tail"]), dtype=info["dtype"])
            boundaries = self.offsets if atom else None
            first = (
                max(0, int(np.searchsorted(boundaries, a, side="right")) - 1)
                if atom
                else a
            )
            for lo, hi in self.ranges:
                if hi <= first:
                    continue
                start, end = (
                    (int(boundaries[lo]), int(boundaries[hi]))
                    if atom
                    else (lo, hi)
                )
                if start >= b:
                    break
                left, right = max(a, start), min(b, end)
                if left >= right:
                    continue
                part = budget().cached((self.identity, lo, hi))
                if part is None:
                    cached = self._cached_slice(
                        lo, hi, key, left - start, right - start
                    )
                    if cached is not None:
                        out[left - a : right - a] = cached
                        continue
                    requested = (right - left) * info["dtype"].itemsize * max(1, int(np.prod(info["tail"])))
                    physical = int((self.ends[lo:hi] - self.starts[lo:hi]).sum())
                    atom_bytes = 32 + 24 * len(self.contents.get("forces") or []) + 8 * len(self.contents.get("atom_fields") or [])
                    workspace = int(self.counts[lo:hi].sum()) * atom_bytes + physical * 8
                    if requested >= 2**20 and workspace <= budget().limit // 3:
                        # Align bulk consumers on one canonical parsed window.
                        # A single native parse uses its own CPU threads; the
                        # lock coalesces reference/prediction and panel reads
                        # while keeping only one large source workspace live.
                        with _parse_lock:
                            part = self._part(lo, hi)
                        value = part.get(key)
                        out[left - a:right - a] = (np.nan if value is None else
                            value[left - start:right - start])
                        continue
                    # No binary batch: fetch only the complete source frames
                    # intersecting the requested rows, rather than re-parsing
                    # a potentially 256 MiB batch for one molecule.
                    selected_lo = (
                        max(
                            lo,
                            int(
                                np.searchsorted(boundaries, left, side="right")
                            )
                            - 1,
                        )
                        if atom
                        else left
                    )
                    selected_hi = (
                        min(
                            hi,
                            int(
                                np.searchsorted(boundaries, right, side="left")
                            ),
                        )
                        if atom
                        else right
                    )
                    selected_hi = max(selected_lo + 1, selected_hi)
                    for frame_lo, frame_hi in self._read_ranges(
                        selected_lo, selected_hi
                    ):
                        part = self._part(frame_lo, frame_hi)
                        row_lo, row_hi = (
                            (
                                int(boundaries[frame_lo]),
                                int(boundaries[frame_hi]),
                            )
                            if atom
                            else (frame_lo, frame_hi)
                        )
                        take_lo, take_hi = max(left, row_lo), min(
                            right, row_hi
                        )
                        value = part.get(key)
                        out[take_lo - a : take_hi - a] = (
                            np.nan
                            if value is None
                            else value[take_lo - row_lo : take_hi - row_lo]
                        )
                    continue
                value = part.get(key)
                if value is None:
                    out[left - a : right - a] = np.nan
                else:
                    out[left - a : right - a] = value[
                        left - start : right - start
                    ]
            return out

        out = BatchedArray(
            (n, *info["tail"]),
            info["dtype"],
            read,
            digest=info["digest"],
            cast_digests=(
                {np.dtype(np.float64).str: info["float_digest"]}
                if "float_digest" in info
                else None
            ),
            label=key,
            unique_values=(
                tuple(sorted(self.element_set)) if key == "numbers" else None
            ),
        )
        out.has_nan = info["has_nan"]
        return out

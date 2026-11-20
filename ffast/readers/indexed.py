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
import weakref
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from ffast.cache.fingerprint import SCHEME, ChunkHasher
from ffast.io import cache_space
from ffast.io.batched import BatchedArray
from ffast.io.budget import budget
from ffast.io.extxyz_arrays import NotEnoughMemory, NotFast
from ffast.readers import scan_record
from ffast.readers.contract import ReaderError

log = logging.getLogger("FFAST")
_disk_lock = threading.RLock()
_parse_lock = threading.RLock()
# The chunk digests of a finished scan, beside its manifest (ADR 0107).
CHUNKS_SUFFIX = ".chunks.npz"
SOURCE_RANGES = "source|ranges"       # ADR 0111: the sidecar arrays that are not columns
SOURCE_DIGESTS = "source|digests"


def _open_identities(folder, own):
    """The identities in ``folder`` that some process has open, ``own`` left
    out (ADR 0108)."""
    out = set()
    try:
        names = [p.name for p in Path(folder).glob("*" + cache_space.OPEN)]
    except OSError:
        return out
    for name in names:
        identity = name[:-len(cache_space.OPEN)]
        if identity != own and cache_space.is_open(Path(folder) / name):
            out.add(identity)
    return out


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


def _write_batch(path, part, prefix=""):
    """Write one compacted batch (``part``: name -> array) to ``path``: a
    temporary file in the same folder, flushed to disk, then renamed, so a
    crash leaves a whole batch or none. Uncompressed, so members can be read
    in place. Raises OSError; the temporary file is removed first."""
    fd, temp = tempfile.mkstemp(dir=Path(path).parent, prefix=prefix, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            np.savez(f, **part)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _read_batch_slice(path, key, start, stop, info, *, touch=True):
    """Rows ``[start, stop)`` of column ``key`` of the batch file ``path``,
    read in place from its uncompressed numpy zip member: only the requested
    bytes, copied into a new array (ADR 0106). None when the batch left the
    column out on purpose (``__omitted__``) or cannot be read in place. A
    column the batch never had is NaN. ``info`` is the column's facts (dtype,
    tail). ``touch`` marks the file as just used; a read-only bundle passes
    False. Raises OSError, ValueError, EOFError or BadZipFile for a damaged
    file."""
    with zipfile.ZipFile(path) as archive, open(path, "rb") as f:
        try:
            archive.getinfo(key + ".npy")
        except KeyError:
            if "__omitted__.npy" in archive.namelist():
                with archive.open("__omitted__.npy") as member:
                    if key in np.lib.format.read_array(member, allow_pickle=False):
                        return None
            return np.full((stop - start, *info["tail"]), np.nan, dtype=info["dtype"])
        out = _member_rows(archive, f, key, start, stop)
        if out is None:
            return None
        if "__restore__.npy" in archive.namelist():
            with archive.open("__restore__.npy") as member:
                table = np.lib.format.read_array(member, allow_pickle=False)
            out = _restore({key: out}, table)[key]
    if touch:
        os.utime(path)
    return out


def _read_batch(path, *, touch=True):
    """Every column of the batch file ``path``, read in place. None when the
    batch omits a column; raises when the file is missing or damaged."""
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
    if touch:
        os.utime(path)
    return part


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
        full=False,
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
            SCHEME,
        )
        h = hashlib.sha256(repr(identity).encode())
        # A full, unsampled load is named by the file alone ("all"), so a scan
        # can name its cache before it knows its frames (ADR 0107). Any other
        # selection is named by its frame list.
        full = full or scan_source
        h.update(b"all" if full else self.frames)
        self.identity = h.hexdigest()
        self._hold_cache_open()
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
        self._record = None
        self._chunk_sidecar = None
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

    def _hold_cache_open(self):
        """Name this identity's source file, mark its last use and hold its
        ``.open`` lock for as long as the store lives, so no process evicts
        its files (ADR 0108)."""
        self._open_handle = None
        if not self.use_cache:
            return
        source = self.folder / f"{self.identity}{cache_space.SOURCE}"
        for _ in range(3):         # an evictor may delete the file under us
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                try:
                    os.utime(source)
                except FileNotFoundError:
                    fd, temp = tempfile.mkstemp(dir=self.folder, prefix=f"{self.identity}-",
                                                suffix=".tmp")
                    with os.fdopen(fd, "w") as f:
                        json.dump({"path": os.path.realpath(self.path),
                                   "state": list(self.state)}, f)
                    os.replace(temp, source)
            except OSError:
                return             # the cache folder is not writable: _save copes
            handle = cache_space.hold_open(
                self.folder / f"{self.identity}{cache_space.OPEN}", needs=source)
            if handle is not None:
                self._open_handle = handle
                weakref.finalize(self, handle.close)
                return

    def _scan_source(self, progress):
        """Parse each indexed window while its text is still in the OS cache.

        One indexing window runs beside one parsing/hashing window. Both are
        bounded; this avoids a second physical pass over a file larger than RAM.
        """
        from ffast.cache import source_digest
        from ffast.io import frame_index
        from ffast.readers import text
        from ffast.metrics import stop
        from ffast.loaders.ase import _sample_indices

        # The cache is named from the start (ADR 0107): batches are written
        # under their final names, so a scan that stops leaves usable files.
        lock = self._lock_scan()
        self.ranges = []
        # A scan projects the cache size from bytes (ADR 0106). The disk
        # allowance is read at the first save, and the kept level only falls.
        self._scanning = True
        self._scan_allowance = None
        self._kept_level = None
        # The source byte ranges of the scan's windows, with their digests, in
        # order (ADR 0111); not kept once a window lacks one.
        self._source = []
        self._source_ok = True
        pending = None
        resume_at = 0
        if self.use_cache and budget().parsed_limit:
            self._record = scan_record.ScanRecord(self.folder, self.identity)
            try:
                scan_record.ScanRecord.sweep_changed(
                    self.folder, self.path, self.state, self.identity,
                    _open_identities(self.folder, self.identity))
                resume_at = self._resume()
            except OSError:
                self._record = None
        try:
            with ThreadPoolExecutor(max_workers=min(4, text.fast_threads())) as hashes, \
                    ThreadPoolExecutor(max_workers=1) as parsing:
                self._hash_pool = hashes

                def consume(starts, counts, end, window=None, digest=None):
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
                    # The finder hashes the window's bytes on a thread of its
                    # own while it is parsed here; collect the digest before
                    # the window is committed (ADR 0111).
                    source = None
                    if digest is not None and window is not None:
                        source = (int(window[0]), int(window[1]), source_digest.resolve(digest))
                    if source is None:
                        self._source_ok = False
                    else:
                        self._source.append(source)
                    if self._record is not None:
                        if self._cache_path(a, b).exists():
                            self._record.commit(self, a, b, end, source)
                        else:
                            self._record.abandon("a batch was not kept")
                    if progress and callable(getattr(progress, "bytes_read", None)):
                        progress.bytes_read(min(int(end), self.state[2]), self.state[2], b)

                def found(starts, counts, end, digest=None, window=None):
                    nonlocal pending
                    stop.check()
                    if pending is not None:
                        pending.result()
                    context = contextvars.copy_context()
                    pending = parsing.submit(context.run, consume, starts, counts, end, window, digest)

                if resume_at and progress and callable(getattr(progress, "bytes_read", None)):
                    # Report from where the scan goes on, not from zero.
                    progress.bytes_read(min(resume_at, self.state[2]), self.state[2], len(self.counts))
                index = frame_index.scan(self.path, on_batch=found, start=resume_at,
                    batch_bytes=min(128 * 2**20, max(1024, budget().limit // 16)))
                if pending is not None:
                    pending.result()
            del self._hash_pool
            self.check_source()
            if resume_at:
                # The scan saw only the frames after the committed ones.
                index = frame_index.FrameIndex(
                    self.path, np.append(self.starts, index.starts[-1]),
                    self.state[2], self.state[3], self.counts)
            if not len(self.counts):
                raise ReaderError(f"{os.path.basename(self.path)} has no frames")
            self._close_source_ranges()
            self._save_chunk_sidecar()
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
            saved = self._save_manifest()
            if self._record is not None and saved:
                self._record.finish()
            self._warming = False
            frame_index.save_cached(index)
            text._save_table(self.reader, self.path, (self.state[2], self.state[3]),
                             (index.starts, index.counts))
            if progress:
                progress(len(self.counts), len(self.counts))
        except NotFast:
            # The Python scanner takes over and restarts the whole load on its
            # validated path: this record has no use then.
            if self._record is not None:
                self._record.abandon("the fast frame finder handed the file over")
            raise
        finally:
            # Only half-written files go; committed batches stay (ADR 0107).
            if self.folder.exists():
                for path in self.folder.glob(self.identity + "-*.tmp"):
                    path.unlink(missing_ok=True)
                for path in self.folder.glob(self.identity + ".*.tmp"):
                    path.unlink(missing_ok=True)
            self._unlock_scan(lock)

    def _lock_scan(self):
        """Take this identity's scan lock, or turn the disk cache off for this
        load when another scan holds it: then this load reads without writing
        batches. None when there is nothing to lock."""
        if not (self.use_cache and budget().parsed_limit):
            return None
        try:
            import fcntl
        except ImportError:      # no flock here; FFAST targets macOS and Linux
            return None
        path = self.folder / f"{self.identity}.lock"
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            handle = open(path, "a+b")
        except OSError:
            return None          # the cache folder is not writable: _save copes
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # The holder deletes the file when it is done: a lock taken on a
            # file that was just deleted guards nothing.
            if os.fstat(handle.fileno()).st_ino != os.stat(path).st_ino:
                raise BlockingIOError
        except OSError:
            handle.close()
            log.info("%s is being scanned by another load; this one will not write cache files",
                     os.path.basename(self.path))
            self.use_cache = False
            return None
        return handle

    def _resume(self):
        """Go on from the committed windows of an interrupted scan of this
        identity: the byte to continue at, or 0 for a scan from the start. A
        record that does not fit the file or the files on disk is deleted
        with its batches, with one line in the log (ADR 0107)."""
        record = self._record
        state = record.load()
        if state is None:
            if record.exists():
                log.info("%s: a scan record could not be read; scanning from the start",
                         os.path.basename(self.path))
                record.discard()
            else:
                record.clear()          # stray temporary files only
            return 0
        try:
            return self._restore(state)
        except (OSError, ValueError, KeyError, TypeError, EOFError, zipfile.BadZipFile) as exc:
            log.info("%s: the earlier scan cannot be continued (%s); scanning from the start",
                     os.path.basename(self.path), exc)
            self._reset_scan_state()
            record.discard()
            return 0

    def _close_source_ranges(self):
        """The frames end where the last window did; blank lines after the
        last frame belong to no window. Hash them here (a few bytes) so the
        ranges reach the file's end."""
        size = self.state[2]
        if not (self._source_ok and self._source) or self._source[-1][1] >= size:
            return
        from ffast.cache.source_digest import file_range_digests
        start = self._source[-1][1]
        [digest] = file_range_digests(self.path, [(start, size)])
        self._source.append((start, size, digest))

    def _source_arrays(self):
        """``source|ranges`` and ``source|digests`` for the sidecar when the
        windows cover the whole file; else {} and one line saying why."""
        name = os.path.basename(self.path)
        if not self._source_ok:
            log.info("%s: the frame finder gave no source digests; none are kept", name)
            return {}
        ranges = np.array([(a, b) for a, b, _ in self._source], dtype=np.int64).reshape(-1, 2)
        covers = (len(ranges) and ranges[0, 0] == 0 and ranges[-1, 1] == self.state[2]
                  and (ranges[1:, 0] == ranges[:-1, 1]).all() and (ranges[:, 0] < ranges[:, 1]).all())
        if not covers:
            log.info("%s: the source windows do not cover the file; no source digests are kept", name)
            return {}
        digests = np.frombuffer(b"".join(d for _, _, d in self._source), dtype=np.uint8).reshape(-1, 32)
        return {SOURCE_RANGES: ranges, SOURCE_DIGESTS: digests}

    def _reset_scan_state(self):
        self._source = []
        self._source_ok = True
        self.starts = np.empty(0, dtype=np.int64)
        self.ends = np.empty(0, dtype=np.int64)
        self.counts = np.empty(0, dtype=np.int64)
        self.offsets = np.zeros(1, dtype=np.int64)
        self.frames = np.empty(0, dtype=np.int64)
        self.ranges = []
        self.info = {}
        self.element_set = set()
        self.first_numbers = None
        self.uniform = True
        self._kept_level = None
        self._scan_allowance = None
        self._small_parts.clear()
        self._small_bytes = 0

    def _restore(self, state):
        """Rebuild the scan's state from a progress record, checking it against
        what is on disk. Raises ValueError (or an OSError) for any mismatch."""
        from ffast.cache.fingerprint import CHUNK_ROWS
        from ffast.readers.text import per_atom

        record = self._record
        if state["scheme"] != SCHEME.decode():
            raise ValueError("it used another hash scheme")
        if state["path"] != self.path or state["state"] != list(self.state):
            raise ValueError("the file changed")
        frame_end, atom_end, byte_end = (int(state[k]) for k in ("frame_end", "atom_end", "byte_end"))
        ranges = [tuple(map(int, r)) for r in state["ranges"]]
        if (not ranges or ranges[0][0] != 0 or ranges[-1][1] != frame_end
                or any(a >= b for a, b in ranges)
                or any(x[1] != y[0] for x, y in zip(ranges, ranges[1:]))):
            raise ValueError("its windows do not line up")
        for a, b in ranges:
            with zipfile.ZipFile(self._cache_path(a, b)) as archive:
                if not archive.namelist():
                    raise ValueError("a batch is empty")
        source = self._restore_source(state, ranges, byte_end)
        frames_length, chunks_length = int(state["frames_length"]), int(state["chunks_length"])
        if (frames_length != 16 * frame_end or record.frames_bin.stat().st_size < frames_length
                or record.chunks_bin.stat().st_size < chunks_length):
            raise ValueError("its frame or chunk file is too short")
        with open(record.frames_bin, "rb") as f:
            pairs = np.frombuffer(f.read(frames_length), dtype="<i8").reshape(-1, 2)
        starts, counts = pairs[:, 0].copy(), pairs[:, 1].copy()
        if (len(starts) != frame_end or (len(starts) > 1 and (np.diff(starts) <= 0).any())
                or starts[-1] >= byte_end or (counts < 0).any()):
            raise ValueError("its frame list is inconsistent")
        offsets = np.concatenate(([0], np.cumsum(counts)))
        if int(offsets[-1]) != atom_end:
            raise ValueError("its atom count is inconsistent")
        digests = {}
        with open(record.chunks_bin, "rb") as f:
            for line in f.read(chunks_length).decode().splitlines():
                key, dtype, index, digest = json.loads(line)
                found = digests.setdefault((key, dtype), [])
                if index != len(found):
                    raise ValueError("its chunk digests are out of order")
                found.append(bytes.fromhex(digest))
        info = {}
        with np.load(self.folder / state["tail_file"], allow_pickle=False) as tails:
            for key, facts in state["columns"].items():
                rows = atom_end if per_atom(key) else frame_end
                column = {"dtype": np.dtype(facts["dtype"]), "tail": tuple(facts["tail"]),
                          "has_nan": bool(facts["has_nan"]), "hashes": {}}
                for dtype, h in facts["hashers"].items():
                    found = digests.get((key, dtype), [])
                    if (h["rows"] != rows or h["chunks"] != rows // CHUNK_ROWS
                            or len(found) != h["chunks"]):
                        raise ValueError("its chunk digests do not match the frames")
                    hasher = ChunkHasher(column["tail"], np.dtype(dtype))
                    tail = tails[h["tail"]]
                    if tail.shape[0] != rows % CHUNK_ROWS:
                        raise ValueError("an unfinished chunk is the wrong size")
                    hasher.restore(found, tail)
                    column["hashes"][dtype] = hasher
                info[key] = column
        # Per-frame columns come back from the batches: level 1 is in every one.
        self._small_parts.clear()
        self._small_bytes = 0
        for a, b in ranges:
            self._remember_small(self._small_from_batch(a, b), a, b)
        os.truncate(record.frames_bin, frames_length)
        os.truncate(record.chunks_bin, chunks_length)
        if source is not None:
            os.truncate(record.source_bin, scan_record.SOURCE_RECORD * len(source))
        self._source, self._source_ok = source or [], source is not None
        record.adopt(state, {(k, d): len(v) for (k, d), v in digests.items()})
        self.starts = starts
        self.ends = np.concatenate((starts[1:], [byte_end])).astype(np.int64)
        self.counts = counts
        self.offsets = offsets
        self.frames = np.arange(frame_end, dtype=np.int64)
        self.ranges = ranges
        self.info = info
        self.element_set = set(state["elements"])
        self.first_numbers = np.asarray(state["first_numbers"], dtype=np.dtype(state["first_numbers_dtype"]))
        self.uniform = bool(state["uniform"])
        self._kept_level = state["kept_level"]
        self._scan_allowance = state["allowance"]
        return byte_end

    def _restore_source(self, state, ranges, byte_end):
        """The source windows of the committed batches, or None when the
        record holds none (made before ADR 0111, or a window had no digest).
        Raises ValueError for a record that says it has them and does not."""
        if not state.get("source_ok", False):
            return None
        length = int(state["source_length"])
        record = self._record
        if length != scan_record.SOURCE_RECORD * len(ranges) or record.source_bin.stat().st_size < length:
            raise ValueError("its source digests do not match its windows")
        raw = record.source_bin.read_bytes()[:length]
        out = []
        for i in range(0, length, scan_record.SOURCE_RECORD):
            a, b = np.frombuffer(raw[i:i + 16], dtype="<i8").tolist()
            out.append((a, b, raw[i + 16:i + 48]))
        if (out[0][0] != 0 or out[-1][1] != byte_end
                or any(x[1] != y[0] for x, y in zip(out, out[1:])) or any(a >= b for a, b, _ in out)):
            raise ValueError("its source windows do not line up")
        return out

    def _small_from_batch(self, a, b):
        """The per-frame columns (energies and frame fields) of one cached window."""
        path = self._cache_path(a, b)
        part = {}
        with zipfile.ZipFile(path) as archive, open(path, "rb") as f:
            for name in archive.namelist():
                key = name[:-4]
                if key.startswith(("energy:", "frame:")):
                    value = _member_rows(archive, f, key, 0, None)
                    if value is None:
                        raise ValueError("a cached column cannot be read in place")
                    part[key] = value
        return part

    def _unlock_scan(self, handle):
        if handle is None:
            return
        try:
            Path(handle.name).unlink(missing_ok=True)
        finally:
            handle.close()

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
        """Write the manifest; True when it is on disk."""
        if not self.use_cache:
            return False
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
            if self._chunk_sidecar:
                meta["chunks"] = self._chunk_sidecar
            data = json.dumps(meta)
            if len(data) > min(budget().parsed_limit, budget().block_bytes):
                return False
            with _disk_lock, cache_space.space_lock(self.folder.parent):
                destination = self.folder / f"{self.identity}.json"
                old = destination.stat().st_size if destination.exists() else 0
                limit = min(
                    budget().parsed_limit,
                    shutil.disk_usage(self.folder).free // 4,
                )
                # The free-space allowance shrinks as batches are written: other
                # datasets give way, this one is never trimmed (ADR 0108).
                if not cache_space.make_room(
                        self.folder.parent, max(0, len(data) - old), limit,
                        saving=os.path.realpath(self.path), locked=True,
                        trim_own_atom_batches=True):    # a finished scan: its atom batches may go (ADR 0108)
                    return False
                fd, temp = tempfile.mkstemp(dir=self.folder, prefix=f"{self.identity}-",
                                            suffix=".tmp")
                with os.fdopen(fd, "w") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp, destination)
            return True
        except OSError:
            if temp:
                Path(temp).unlink(missing_ok=True)
            return False

    def _save_chunk_sidecar(self):
        """Keep the chunk digests of every column beside the manifest: one
        ``(n_chunks, 32)`` uint8 array per column and dtype candidate, the last
        row being the digest of the unfinished chunk. They rebuild each column
        digest, and let a later check compare chunk by chunk (ADR 0107)."""
        self._chunk_sidecar = None
        if not (self.use_cache and budget().parsed_limit):
            return
        arrays = {}
        for key, info in self.info.items():
            for d, h in info["hashes"].items():
                arrays[f"{d}|{key}"] = np.frombuffer(
                    b"".join(h.all_digests()), dtype=np.uint8).reshape(-1, 32)
        arrays.update(self._source_arrays())
        name = f"{self.identity}{CHUNKS_SUFFIX}"
        temp = None
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(dir=self.folder, prefix=f"{self.identity}-", suffix=".tmp")
            with os.fdopen(fd, "wb") as f:
                np.savez(f, **arrays)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, self.folder / name)
            self._chunk_sidecar = name
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
        try:
            return _read_batch_slice(self._cache_path(a, b), key, start, stop, self.info[key])
        except (OSError, ValueError, EOFError, zipfile.BadZipFile):
            return None

    def _cached_batch(self, a, b):
        """Every column of one cached window, read in place from the file.
        None when the window omits a column; raises when the file is missing
        or damaged."""
        return _read_batch(self._cache_path(a, b))

    def _save(self, a, b, part):
        if not self.use_cache or not budget().parsed_limit:
            return
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
            # Independent servers serialize eviction and publication through
            # the cache's one space lock (ADR 0108).
            with _disk_lock, cache_space.space_lock(self.folder.parent):
                from ffast.io.result_array import sweep_stale_temporaries

                sweep_stale_temporaries(self.folder)
                # Other datasets give way, oldest last use first; pinned and
                # open ones, and this one, are never trimmed.
                if not cache_space.make_room(
                        self.folder.parent, size, limit,
                        saving=os.path.realpath(self.path), locked=True):
                    return
                # Written, flushed to disk, then renamed: a crash leaves a
                # whole batch or none. The temporary name carries the
                # identity so a stopped scan removes only its own.
                _write_batch(self._cache_path(a, b), part, prefix=f"{self.identity}-")
        except OSError as exc:
            log.debug(
                "Parsed batch cache unavailable for %s: %s", self.path, exc
            )

    def _parse(self, a, b, path=None, reader=None):
        """Frames ``[a, b)`` parsed from the text of ``path`` (the source by
        default) with ``reader`` (the store's own by default)."""
        from ffast.readers import text

        path = self.path if path is None else path
        reader = self.reader if reader is None else reader
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
                callable(getattr(reader, "parse_into", None))
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
                        reader,
                        path,
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
                            os.path.basename(path),
                            exc,
                            extra={"popup": False},
                        )
                        self._warned_fallback = True
                    part = reader.parse(
                        text._read_spans(
                            path, self.starts[a:b], self.ends[a:b]
                        ),
                        int(self.frames[a]),
                    )
            else:
                if callable(getattr(reader, "parse_into", None)):
                    text._warn_no_fast_parser()
                part = reader.parse(
                    text._read_spans(
                        path, self.starts[a:b], self.ends[a:b]
                    ),
                    int(self.frames[a]),
                )
        return part

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
            part = self._parse(a, b)
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
                info["hashes"][d] = ChunkHasher(info["tail"], np.dtype(d))
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
                h.update(value)

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

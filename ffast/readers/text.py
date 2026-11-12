"""Level 2, the fast text contract (ADR 0095, decision 3).

A Level 2 reader gives ``find_frames(path)`` (where each frame starts, and its
atom count) and ``parse(data, first_frame)`` (whole frames' bytes to the dict
``read()`` returns). FFAST does the rest, as it did for xyz alone before:

- the frame list, kept in memory and in ``~/.ffast/frame-index``, per reader
  and file, so a second load does not call ``find_frames``;
- every n-th frame without parsing the others;
- parsing in batches, in worker processes for a big file;
- progress, and the memory check (ADR 0083);
- the read-once cache: the joined arrays kept as ``.npy`` files, opened
  memory-mapped the next time the same file is read with the same stride
  (ADR 0076).

``parse`` runs in worker processes: a staticmethod (or a top-level function
held by the class), and the class importable. A ``NotFast`` it raises passes
up for the caller to read the file another way.
"""
from __future__ import annotations

import hashlib
import json
import logging
import multiprocessing as mp
import os
import shutil
import threading
from collections import deque
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from ffast.io import extxyz_arrays as xa
from ffast.io import frame_cache, frame_index
from ffast.io.extxyz_arrays import MEMORY_FLOOR, MEMORY_SHARE, NotEnoughMemory, _gib
from ffast.metrics.resources import available_memory
from ffast.readers.contract import ReaderError

logger = logging.getLogger("FFAST")

_PER_ATOM = ("numbers", "positions")
_PER_ATOM_PREFIXES = ("forces:", "atom:")
_CORE = ("n_atoms", "numbers", "positions")
_FORMAT = 1                       # bump when the keys or layout of a kept entry change
_META = "meta.json"


def per_atom(key: str) -> bool:
    """Whether a returned array has one row per atom (else one per frame)."""
    return key in _PER_ATOM or key.startswith(_PER_ATOM_PREFIXES)


# ── the frame list, per reader and file ─────────────────────────────────────

_lock = threading.Lock()
_tables: dict = {}


def clear_memory_cache():
    with _lock:
        _tables.clear()


def _table_file(reader_class, path):
    key = hashlib.sha1(f"{reader_class.name}|{os.path.abspath(path)}".encode()).hexdigest()
    return frame_index.CACHE_DIR.expanduser() / f"reader-{key}.npz"


def get_table(reader_class, path):
    """``(starts, counts)`` of ``path``: ``starts`` has one more entry than
    ``counts``, the last being where the last frame ends. From memory, from
    disk, else from the reader's ``find_frames``."""
    path = os.path.abspath(path)
    st = os.stat(path)
    ident = (st.st_size, st.st_mtime_ns)
    memo = (reader_class.name, path)
    with _lock:
        hit = _tables.get(memo)
        if hit is not None and hit[0] == ident:
            return hit[1]
    table = _load_table(reader_class, path, ident)
    if table is None:
        starts, counts = reader_class.find_frames(path)
        table = (np.asarray(starts, dtype=np.int64), np.asarray(counts, dtype=np.int64))
        if len(table[0]) != len(table[1]) + 1:
            raise ValueError("find_frames() must return starts with one more entry than counts")
        _save_table(reader_class, path, ident, table)
    with _lock:
        _tables[memo] = (ident, table)
    return table


def _load_table(reader_class, path, ident):
    try:
        with np.load(_table_file(reader_class, path)) as z:
            if (int(z["size"]), int(z["mtime_ns"])) != ident:
                return None
            return z["starts"], z["counts"]
    except Exception:
        return None


def _save_table(reader_class, path, ident, table):
    try:
        target = _table_file(reader_class, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp.npz")
        np.savez(tmp, starts=table[0], counts=table[1], size=ident[0], mtime_ns=ident[1])
        os.replace(tmp, target)
    except Exception as exc:
        logger.warning("Could not save the frame list of %s: %s", path, exc, extra={"popup": False})


# ── the read-once cache ─────────────────────────────────────────────────────

def _kept_key(reader_class, path, step):
    st = os.stat(path)
    version = getattr(reader_class, "cache_version", 1)
    text = (f"{_FORMAT}|reader|{reader_class.name}|{version}|{os.path.abspath(path)}"
            f"|{st.st_size}|{st.st_mtime_ns}|{step}")
    return hashlib.sha1(text.encode()).hexdigest()


def _kept_load(reader_class, path, step):
    """``(arrays, n_file_frames)`` kept by an earlier read, or None."""
    try:
        entry = frame_cache._folder() / _kept_key(reader_class, path, step)
        meta = json.loads((entry / _META).read_text())
        arrays = {key: np.load(entry / file, mmap_mode="r", allow_pickle=False)
                  for key, file in meta["arrays"].items()}
    except (OSError, ValueError, KeyError):
        return None
    os.utime(entry / _META)                    # most recently used
    return arrays, int(meta["n_file_frames"])


def _kept_save(reader_class, path, step, arrays, n_file_frames):
    name = os.path.basename(path)
    tmp = None
    try:
        folder = frame_cache._folder()
        folder.mkdir(parents=True, exist_ok=True)
        size = sum(np.asarray(a).nbytes for a in arrays.values())
        limit = frame_cache.max_bytes(folder)
        if size > limit:
            logger.info("Not keeping the arrays of %s: %.1f GB is more than the %.1f GB allowed.",
                        name, size / 2**30, limit / 2**30)
            return
        frame_cache._make_room(folder, limit - size)
        entry = folder / _kept_key(reader_class, path, step)
        tmp = folder / f".{entry.name}.{os.getpid()}.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        files = {}
        for i, (key, value) in enumerate(arrays.items()):
            files[key] = f"a{i}.npy"
            np.save(tmp / files[key], np.asarray(value), allow_pickle=False)
        (tmp / _META).write_text(json.dumps({
            "path": os.path.abspath(path), "reader": reader_class.name, "step": step,
            "n_file_frames": n_file_frames, "arrays": files}))   # last: no meta, never opened
        shutil.rmtree(entry, ignore_errors=True)
        os.replace(tmp, entry)
    except Exception as exc:
        logger.warning("Could not keep the arrays of %s for the next load: %s", name, exc,
                       extra={"popup": False})
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def _regular_step(frames):
    """``s`` when ``frames`` are ``0, s, 2s, ...`` (a stride), else None."""
    if len(frames) == 0 or frames[0] != 0:
        return None
    if len(frames) == 1:
        return 1
    step = frames[1] - frames[0]
    return step if step > 0 and all(b - a == step for a, b in zip(frames, frames[1:])) else None


# ── reading ─────────────────────────────────────────────────────────────────

def _read_spans(path, starts, ends):
    """The bytes of the frames at [starts[i], ends[i]). Frames side by side are
    one read; frames a few apart (a stride) are read straight through and
    picked out, which beats a seek per frame on a disk; frames far apart get a
    seek each."""
    if len(starts) == 1 or (ends[:-1] == starts[1:]).all():
        with open(path, "rb") as f:
            f.seek(int(starts[0]))
            return f.read(int(ends[-1]) - int(starts[0]))
    with open(path, "rb") as f:
        if ends[-1] - starts[0] <= xa.READ_THROUGH * int((ends - starts).sum()):
            base = int(starts[0])
            f.seek(base)
            buf = f.read(int(ends[-1]) - base)
            return b"".join(buf[int(a) - base:int(b) - base] for a, b in zip(starts, ends))
        parts = []
        for a, b in zip(starts.tolist(), ends.tolist()):
            f.seek(a)
            parts.append(f.read(b - a))
        return b"".join(parts)


def _parse_job(reader_class, path, starts, ends, first_frame):
    """Worker: read the frames' bytes and give them to the reader's ``parse``."""
    return reader_class.parse(_read_spans(path, starts, ends), first_frame)


class _Joined:
    """The joined arrays, made at their final size from the first batch; each
    batch is copied into its place as it arrives, so a load never holds the
    batches and their join at once. A key a batch lacks is NaN there (ADR
    0058, ADR 0064); a key only later batches have is NaN before."""

    def __init__(self, atoms, frames):
        self.atoms, self.frames = atoms, frames
        self.arrays: dict = {}
        self.a = self.f = 0

    def add(self, part, n_atoms, n_frames):
        if len(part["n_atoms"]) != n_frames or int(np.sum(part["n_atoms"])) != n_atoms:
            raise ValueError(
                f"parse() of frames {self.f:,}+ gave {len(part['n_atoms'])} frames and "
                f"{int(np.sum(part['n_atoms'])):,} atoms; the frame list counted {n_frames} and "
                f"{n_atoms:,}")
        for key in _CORE:
            if key not in part:
                raise ValueError(f"parse() must return the key '{key}'")
        for key in set(self.arrays) | set(part):
            atoms = per_atom(key)
            start, n = (self.a, n_atoms) if atoms else (self.f, n_frames)
            rows = self.atoms if atoms else self.frames
            if key not in self.arrays:
                sample = np.asarray(part[key])
                shape = (rows,) + sample.shape[1:]
                self.arrays[key] = (np.empty(shape, dtype=sample.dtype) if start == 0
                                    else np.full(shape, np.nan))
            target = self.arrays[key]
            if key not in part:
                if not np.issubdtype(target.dtype, np.floating):
                    target = self.arrays[key] = target.astype(np.float64)
                target[start:start + n] = np.nan
            else:
                value = np.asarray(part[key])
                if not np.can_cast(value.dtype, target.dtype, casting="same_kind"):
                    target = self.arrays[key] = target.astype(np.result_type(target.dtype, value.dtype))
                elif value.dtype.kind == "f" and target.dtype.kind in "iu":
                    target = self.arrays[key] = target.astype(np.float64)   # as np.concatenate would
                target[start:start + n] = value
        self.a += n_atoms
        self.f += n_frames


def read_level2(reader_class, path, *, stride=0, select=None, progress=None, workers=None,
                batch=None, use_cache=True):
    """The kept frames of ``path`` through a Level 2 reader: ``(arrays,
    contents, frames, n_file_frames)``."""
    name = os.path.basename(path)
    reader = reader_class(path)
    contents = dict(reader.contents() or {})
    starts, counts = get_table(reader_class, path)
    n_file = len(counts)
    if select is not None:
        frames = [int(f) for f in select(n_file)]
    else:
        step = int(stride) if stride and int(stride) > 1 else 1
        frames = list(range(0, n_file, step))
    total = len(frames)
    if total == 0:
        raise ReaderError(f"{name} has no frames")

    step = _regular_step(frames)
    if use_cache and step is not None:
        kept = _kept_load(reader_class, path, step)
        if kept is not None:
            logger.info("%s: opened the arrays kept from an earlier load", name)
            if progress:
                progress(total, total)
            return kept[0], contents, frames, n_file

    index = np.asarray(frames, dtype=np.intp)
    spans_start, spans_end = starts[index], starts[index + 1]
    kept_counts = counts[index]
    atoms = int(kept_counts.sum())
    sizes = spans_end - spans_start
    workers = xa.worker_count() if workers is None else workers
    if total < xa.PARALLEL_MIN_FRAMES:
        workers = 1
    if batch is None:
        batch = max(xa.MIN_BATCH, min(xa.MAX_BATCH, -(-total // (workers * 4))))
        batch = max(1, min(batch, int(xa.BATCH_BYTES * total // max(1, int(sizes.sum())))))
    jobs = [(a, min(a + batch, total)) for a in range(0, total, batch)]
    workers = min(workers, len(jobs))

    # Sizing: with workers, one frame (the batches all go to the pool); else the
    # first batch, parsed here and kept as the first batch (no frame twice).
    a0, b0 = jobs[0]
    parallel = workers > 1 and len(jobs) > 1
    if parallel:
        b0 = a0 + 1
    first = _parse_job(reader_class, path, spans_start[a0:b0], spans_end[a0:b0], frames[a0])
    atoms0 = max(1, int(kept_counts[a0:b0].sum()))
    per_atom_bytes = sum(np.asarray(v).nbytes for k, v in first.items() if per_atom(k)) / atoms0
    per_frame_bytes = sum(np.asarray(v).nbytes for k, v in first.items() if not per_atom(k)) / (b0 - a0)
    final = int(atoms * per_atom_bytes + total * per_frame_bytes)
    peak = max(xa._batch_peak(spans_start[a:b], spans_end[a:b]) for a, b in jobs)
    free = available_memory()
    workers, fits, need = xa._plan(final, peak, workers, free)
    if not fits:
        raise NotEnoughMemory(
            f"{name} needs about {_gib(need)} of memory to load ({atoms:,} atoms in {total:,} "
            f"frames), but {_gib(free)} is free. Close other programs, or load a smaller file.")
    logger.info("Reading %s: %s frames, %d batches, %d worker process(es), about %s of %s free",
                name, f"{total:,}", len(jobs), workers, _gib(need), _gib(free))

    joined = _Joined(atoms, total)

    def keep(part, a, b):
        joined.add(part, int(kept_counts[a:b].sum()), b - a)
        left = available_memory()
        if b < total and left < MEMORY_FLOOR:
            raise NotEnoughMemory(
                f"Stopped reading {name} after {b:,} of {total:,} frames: only {_gib(left)} "
                f"of memory is left. Close other programs, or load a smaller file.")
        if progress:
            progress(b, total)

    def job(a, b):
        return (reader_class, path, spans_start[a:b], spans_end[a:b], frames[a])

    if parallel:
        del first
        rest = jobs
    else:
        keep(first, a0, b0)
        del first
        rest = jobs[1:]
    if not parallel:
        for a, b in rest:
            keep(_parse_job(*job(a, b)), a, b)
    else:
        window = workers * xa.WINDOW_PER_WORKER
        with ProcessPoolExecutor(workers, mp_context=mp.get_context("spawn")) as pool:
            pending = deque()
            try:
                for a, b in rest:
                    pending.append((pool.submit(_parse_job, *job(a, b)), a, b))
                    if len(pending) >= window:
                        future, fa, fb = pending.popleft()
                        keep(future.result(), fa, fb)
                while pending:
                    future, fa, fb = pending.popleft()
                    keep(future.result(), fa, fb)
            except BaseException:
                for future, _, _ in pending:
                    future.cancel()
                raise
    if joined.a != atoms or joined.f != total:
        raise ValueError(f"read {joined.a:,} atoms in {joined.f:,} frames, the frame list "
                         f"counted {atoms:,} in {total:,}")

    arrays = joined.arrays
    if use_cache and step is not None:
        _kept_save(reader_class, path, step, arrays, n_file)
        kept = _kept_load(reader_class, path, step)   # None: not kept, or cannot be opened
        if kept is not None:
            arrays = kept[0]
    return arrays, contents, frames, n_file

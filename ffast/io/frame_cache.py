"""The arrays a file's frames were read into, kept on disk (ADR 0083).

The first load of a large .xyz/.extxyz file parses its text; this keeps the
result as .npy files in ``~/.ffast/frame-arrays``, so the next load of the
same file (same size and modification time, same stride) opens them instead.
They are opened read-only and memory-mapped: the operating system reads them
as they are used and can drop them again under memory pressure.

The most recently used entries are kept up to ``max_bytes()``; older ones are
deleted when a new one is saved. A failed save is a warning, not an error.
"""
import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path

import numpy as np

from ffast.io.extxyz_arrays import FrameArrays

logger = logging.getLogger("FFAST")

# FFAST_FRAME_ARRAYS_DIR lets the tests point servers they spawn at a temp folder.
CACHE_DIR = Path(os.environ.get("FFAST_FRAME_ARRAYS_DIR", "~/.ffast/frame-arrays"))
# Bump when the reader changes the arrays it makes: older entries are then
# never opened, and leave as the oldest.
FORMAT = 1
MAX_BYTES = 20 * 2**30
_META = "meta.json"


def max_bytes(folder):
    """At most MAX_BYTES, and at most a quarter of the disk's free space."""
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        return MAX_BYTES
    return min(MAX_BYTES, free // 4)


def _key(index):
    st = os.stat(index.path)
    step = getattr(index, "step", 1)
    text = f"{FORMAT}|{os.path.abspath(index.path)}|{st.st_size}|{st.st_mtime_ns}|{step}"
    return hashlib.sha1(text.encode()).hexdigest()


def _folder():
    return Path(CACHE_DIR).expanduser()


def load(index):
    """The cached FrameArrays of ``index``, or None."""
    try:
        entry = _folder() / _key(index)
        meta = json.loads((entry / _META).read_text())
    except (OSError, ValueError):
        return None
    try:
        def arr(name):
            return np.load(entry / name, mmap_mode="r", allow_pickle=False)
        arrays = FrameArrays(
            properties=meta["properties"],
            essential=tuple(tuple(e) for e in meta["essential"]),
            extras={k: tuple(v) for k, v in meta["extras"].items()},
            counts=arr("counts.npy"),
            numbers=arr("numbers.npy"),
            positions=arr("positions.npy"),
            cells=arr("cells.npy"),
            pbc=arr("pbc.npy"),
            columns={k: arr(f) for k, f in meta["columns"].items()},
            missing={k: arr(f) for k, f in meta["missing"].items()},
            values={k: arr(f) for k, f in meta["values"].items()},
        )
    except (OSError, ValueError, KeyError) as exc:
        logger.warning("The saved arrays of %s could not be opened (%s); reading the file.",
                       os.path.basename(index.path), exc, extra={"popup": False})
        return None
    os.utime(entry / _META)                       # most recently used
    return arrays


def save(index, arrays):
    """Keep ``arrays`` for the next load of ``index``; make room by deleting
    the least recently used entries."""
    name = os.path.basename(index.path)
    tmp = None
    try:
        folder = _folder()
        folder.mkdir(parents=True, exist_ok=True)
        size = _nbytes(arrays)
        limit = max_bytes(folder)
        if size > limit:
            logger.info("Not keeping the arrays of %s: %.1f GB is more than the %.1f GB allowed.",
                        name, size / 2**30, limit / 2**30)
            return
        _make_room(folder, limit - size)
        entry = folder / _key(index)
        tmp = folder / f".{entry.name}.{os.getpid()}.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        meta = {"path": os.path.abspath(index.path), "step": getattr(index, "step", 1),
                "properties": arrays.properties, "essential": arrays.essential,
                "extras": arrays.extras, "columns": {}, "missing": {}, "values": {}}
        for field in ("counts", "numbers", "positions", "cells", "pbc"):
            np.save(tmp / f"{field}.npy", np.asarray(getattr(arrays, field)), allow_pickle=False)
        for group in ("columns", "missing", "values"):
            for i, (key, value) in enumerate(getattr(arrays, group).items()):
                file = f"{group}_{i}.npy"
                np.save(tmp / file, np.asarray(value), allow_pickle=False)
                meta[group][key] = file
        (tmp / _META).write_text(json.dumps(meta))      # last: an entry without it is never opened
        shutil.rmtree(entry, ignore_errors=True)
        os.replace(tmp, entry)
    except Exception as exc:
        logger.warning("Could not keep the arrays of %s for the next load: %s", name, exc,
                       extra={"popup": False})
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def _nbytes(arrays):
    groups = [arrays.columns, arrays.missing, arrays.values]
    return sum(np.asarray(a).nbytes for a in
               [arrays.counts, arrays.numbers, arrays.positions, arrays.cells, arrays.pbc]
               + [v for g in groups for v in g.values()])


def _entry_bytes(entry):
    return sum(f.stat().st_size for f in entry.iterdir() if f.is_file())


def _make_room(folder, budget):
    """Delete least recently used entries until the rest fit in ``budget``.
    Half-written entries (no meta.json) older than an hour go too."""
    entries = []
    for entry in folder.iterdir():
        if not entry.is_dir():
            continue
        meta = entry / _META
        try:
            used = meta.stat().st_mtime
        except OSError:
            if time.time() - entry.stat().st_mtime > 3600:
                shutil.rmtree(entry, ignore_errors=True)
            continue
        entries.append((used, entry, _entry_bytes(entry)))
    entries.sort()
    total = sum(n for _, _, n in entries)
    for _, entry, n in entries:
        if total <= budget:
            break
        shutil.rmtree(entry, ignore_errors=True)
        total -= n

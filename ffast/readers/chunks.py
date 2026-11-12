"""The work FFAST does for every reader (ADR 0095, decision 4): stride,
progress, the memory check (ADR 0083) and error wrapping.

A reader only answers ``read(frames)``. FFAST asks for the frames it keeps in
increasing chunks, so a reader that can only read front to back still works.
The memory need is estimated from ``n_frames()`` and the first chunk.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass

import numpy as np

from ffast.io.extxyz_arrays import MEMORY_FLOOR, MEMORY_SHARE, NotEnoughMemory, _gib
from ffast.metrics.resources import available_memory
from ffast.readers.contract import ReaderError

logger = logging.getLogger("FFAST")

CHUNK_BYTES = 16 * 2**20     # about what one later chunk holds
FIRST_CHUNK_FRAMES = 8       # the first chunk is small: it sizes the others and the memory need


@dataclass
class Loaded:
    arrays: dict        # the joined arrays, in the keys of ``read()``
    contents: dict      # the reader's ``contents()``
    frames: list        # the file's frame numbers that were kept
    n_file_frames: int  # the file's frame count, before the stride
    reader_name: str


@contextmanager
def reader_errors(reader_name: str, path: str):
    """A ReaderError and NotEnoughMemory pass as they are. Any other exception
    becomes "<reader> reader could not read <file>: <message>", its traceback
    logged without a pop-up."""
    try:
        yield
    except (ReaderError, NotEnoughMemory):
        raise
    except Exception as exc:
        name = os.path.basename(path)
        logger.error("%s reader failed on %s", reader_name, path, exc_info=True,
                     extra={"popup": False})
        raise ReaderError(f"{reader_name} reader could not read {name}: {exc}") from exc


def read_arrays(reader_class, path, *, stride=0, select=None, progress=None,
                chunk_bytes=CHUNK_BYTES, first_frames=FIRST_CHUNK_FRAMES) -> Loaded:
    """Every ``stride``-th frame of ``path`` through ``reader_class``, joined.

    ``stride`` of 0, 1 or less keeps every frame. ``select(n_file_frames)``,
    when given, names the frames instead (increasing); it may raise a
    ReaderError, for a file that does not fit what it is read for. ``progress(done, total)``
    follows each chunk. NotEnoughMemory before reading on, when the first
    chunk predicts the arrays do not fit, and while reading, when free memory
    falls below ``MEMORY_FLOOR``.
    """
    reader_name = reader_class.name
    file_name = os.path.basename(path)
    with reader_errors(reader_name, path):
        reader = reader_class(path)
        n_file = int(reader.n_frames())
        contents = dict(reader.contents() or {})
        if select is not None:
            frames = [int(f) for f in select(n_file)]
        else:
            step = int(stride) if stride and int(stride) > 1 else 1
            frames = list(range(0, n_file, step))
        total = len(frames)
        if total == 0:
            raise ReaderError(f"{file_name} has no frames")

        parts: dict[str, list] = {}
        done, size = 0, max(1, first_frames)
        while done < total:
            chunk = frames[done:done + size]
            got = reader.read(chunk)
            _check_chunk(got, len(chunk), reader_name, None if done == 0 else parts)
            for key, value in got.items():
                parts.setdefault(key, []).append(np.asarray(value))
            if done == 0:
                per_frame = _bytes_per_frame(got, len(chunk))
                _check_memory(file_name, got, len(chunk), total, per_frame)
                size = max(1, int(chunk_bytes // max(1, per_frame)))
            done += len(chunk)
            free = available_memory()
            if done < total and free < MEMORY_FLOOR:
                raise NotEnoughMemory(
                    f"Stopped reading {file_name} after {done:,} of {total:,} frames: only "
                    f"{_gib(free)} of memory is left. Close other programs, or load a smaller file.")
            if progress:
                progress(done, total)

        return Loaded(_join(parts), contents, frames, n_file, reader_name)


def _bytes_per_frame(got: dict, n: int) -> float:
    return sum(np.asarray(v).nbytes for v in got.values()) / n


def _check_chunk(got, n_frames, reader_name, so_far):
    """What the join relies on: a dict with ``n_atoms`` of one value per frame,
    and the same keys in every chunk."""
    if not isinstance(got, dict) or "n_atoms" not in got:
        raise ValueError("read() must return a dict with the key 'n_atoms' "
                         "(the atom count of each frame)")
    if len(got["n_atoms"]) != n_frames:
        raise ValueError(f"read() was asked for {n_frames} frames but 'n_atoms' has "
                         f"{len(got['n_atoms'])} values")
    if so_far is not None and set(got) != set(so_far):
        changed = sorted(set(got) ^ set(so_far))
        raise ValueError(f"read() returned different keys in different chunks: {', '.join(changed)}")


def _check_memory(file_name, got, n_chunk, total, per_frame):
    """Refuse, before reading on, a load whose joined arrays will not fit.
    Joining one key at a time costs the arrays plus the largest key."""
    final = per_frame * total
    largest = max((np.asarray(v).nbytes for v in got.values()), default=0) / n_chunk * total
    need = final + largest
    free = available_memory()
    if need > MEMORY_SHARE * free:
        atoms = int(np.sum(got["n_atoms"])) / n_chunk * total
        raise NotEnoughMemory(
            f"{file_name} needs about {_gib(need)} of memory to load ({int(atoms):,} atoms in "
            f"{total:,} frames), but {_gib(free)} is free. Close other programs, or load a "
            f"smaller file.")


def _join(parts: dict) -> dict:
    """One key at a time, freeing its chunks as it goes."""
    joined = {}
    for key in list(parts):
        pieces = parts.pop(key)
        joined[key] = pieces[0] if len(pieces) == 1 else np.concatenate(pieces)
        del pieces
    return joined

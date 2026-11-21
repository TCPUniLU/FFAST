"""The progress record of a one-pass scan (ADR 0107).

A scan names its cache when it starts. After each window is committed (its
batch file written, flushed and renamed into place) this record says how far
the scan got, so a reload can go on from there instead of starting again.

Files, all in the batch folder and all named by the scan's identity:

``<id>.frames.bin``    appended little-endian int64 pairs ``(start, count)``,
                       one per frame
``<id>.chunks.bin``    appended text lines, one JSON list per completed chunk:
                       ``[column, dtype, chunk index, hex digest]``
``<id>.<n>.tail.npz``  for each column and dtype candidate, the rows of the one
                       unfinished chunk after ``n`` frames (uncompressed). Each
                       commit writes a new file and the record names it, so a
                       crash cannot pair an old record with a newer tail
``<id>.progress.json`` the committed ends, the valid length of both ``.bin``
                       files, the tail file, the batch ranges and the
                       per-column facts; written last, by replacing the old
                       record

A crash at any point leaves the old record (whose lengths ignore bytes appended
since, and which names its own tail) or the new one. The ``.bin`` files are
appended before the record that counts them, so a record never points past
what is on disk.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import numpy as np

from ffast.cache.fingerprint import SCHEME
from ffast.io import cache_space

log = logging.getLogger("FFAST")

SUFFIXES = (".frames.bin", ".chunks.bin", ".tail.npz", ".progress.json")


def is_state_file(name):
    """True for a file that belongs to a running or interrupted scan's record."""
    return name.endswith(SUFFIXES)


def _replace(path, data, mode="wb"):
    """Write ``data`` to a temporary name, flush it to disk, then rename."""
    temp = path.with_name(path.name + ".tmp")
    try:
        with open(temp, mode) as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _append(path, data):
    with open(path, "ab") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
        return f.tell()


class ScanRecord:
    def __init__(self, folder, identity):
        self.folder = Path(folder)
        self.identity = identity
        self.frames_bin = self.folder / f"{identity}.frames.bin"
        self.chunks_bin = self.folder / f"{identity}.chunks.bin"
        self.progress = self.folder / f"{identity}.progress.json"
        self.alive = True
        self._written = {}          # (column, dtype) -> chunk digests already on disk
        self._frames_length = 0
        self._chunks_length = 0

    def paths(self):
        return (self.progress, self.frames_bin, self.chunks_bin,
                *self.folder.glob(self.identity + ".*.tail.npz"))

    def clear(self):
        """Delete the record's files (not the batches)."""
        for path in self.paths():
            path.unlink(missing_ok=True)
        for path in self.folder.glob(self.identity + ".*.tmp"):
            path.unlink(missing_ok=True)
        self._written.clear()
        self._frames_length = self._chunks_length = 0

    def load(self):
        """The record as a dict; None when there is none or it cannot be read."""
        try:
            record = json.loads(self.progress.read_text())
        except (OSError, ValueError):
            return None
        return record if isinstance(record, dict) else None

    def exists(self):
        return any(path.exists() for path in self.paths())

    def adopt(self, record, written):
        """Go on appending after the committed lengths of a loaded ``record``;
        ``written`` is how many digests of each (column, dtype) are on disk."""
        self._frames_length = int(record["frames_length"])
        self._chunks_length = int(record["chunks_length"])
        self._written = dict(written)

    def discard(self):
        """Delete the record and every batch of the identity (not the manifest)."""
        self.clear()
        for path in self.folder.glob(self.identity + "-*.npz"):
            path.unlink(missing_ok=True)

    @staticmethod
    def sweep_changed(folder, path, state, keep, live=()):
        """Delete the records, and their batches, of scans of ``path`` made
        when the file was in another state: a partial cache of a changed
        source is fully invalid (spec 4.3). ``keep`` and the ``live``
        identities are left alone."""
        folder = Path(folder)
        try:
            records = list(folder.glob("*.progress.json"))
        except OSError:
            return
        for progress in records:
            identity = progress.name[:-len(".progress.json")]
            if identity == keep or identity in live:
                continue
            other = ScanRecord(folder, identity)
            old = other.load()
            if old and old.get("path") == path and old.get("state") != list(state):
                try:
                    other.discard()
                    cache_space.remove_markers(folder, identity)
                except OSError:
                    pass

    def abandon(self, why):
        """Stop recording: the scan goes on without being resumable."""
        if self.alive:
            log.debug("Scan of %s is no longer resumable: %s", self.identity[:12], why)
        self.alive = False
        try:
            self.clear()
        except OSError:
            pass

    @staticmethod
    def snapshot(store):
        """What ``commit`` reads of ``store``, as it is now: the scan goes on
        to the next window while this one's batch is still being written,
        and its record must describe this window (ADR 0112)."""
        from types import SimpleNamespace

        def frozen(h):
            return SimpleNamespace(digests=(lambda d=h.digests(): d),
                                   tail=(lambda t=np.array(h.tail(), copy=True): t),
                                   rows=h.rows)

        info = {key: {"hashes": {d: frozen(h) for d, h in value["hashes"].items()},
                      "dtype": value["dtype"], "tail": tuple(value["tail"]),
                      "has_nan": value["has_nan"]}
                for key, value in store.info.items()}
        return SimpleNamespace(
            path=store.path, state=store.state, starts=store.starts, counts=store.counts,
            offsets=store.offsets, info=info, ranges=list(store.ranges),
            element_set=set(store.element_set), first_numbers=store.first_numbers,
            uniform=store.uniform, _kept_level=store._kept_level,
            _scan_allowance=store._scan_allowance)

    def commit(self, store, a, b, end):
        """Record that frames ``[0, b)`` and the bytes before ``end`` are done.
        Call after the batch of window ``[a, b)`` is in place."""
        if not self.alive:
            return
        try:
            self._commit(store, a, b, end)
        except OSError as exc:
            self.abandon(f"{type(exc).__name__}: {exc}")

    def _commit(self, store, a, b, end):
        pairs = np.column_stack((store.starts[a:b], store.counts[a:b])).astype("<i8")
        self._frames_length = _append(self.frames_bin, pairs.tobytes())
        lines = []
        tails = {}
        columns = {}
        for key, info in store.info.items():
            hashers = {}
            for d, h in info["hashes"].items():
                done = self._written.get((key, d), 0)
                digests = h.digests()
                for index in range(done, len(digests)):
                    lines.append(json.dumps([key, d, index, digests[index].hex()]))
                self._written[(key, d)] = len(digests)
                name = f"t{len(tails)}"
                tails[name] = h.tail()
                hashers[d] = {"tail": name, "chunks": len(digests), "rows": h.rows}
            columns[key] = {
                "dtype": info["dtype"].str,
                "tail": list(info["tail"]),
                "has_nan": bool(info["has_nan"]),
                "hashers": hashers,
            }
        if lines:
            self._chunks_length = _append(self.chunks_bin, ("\n".join(lines) + "\n").encode())
        elif not self.chunks_bin.exists():
            self._chunks_length = _append(self.chunks_bin, b"")
        tail_npz = self.folder / f"{self.identity}.{int(b)}.tail.npz"
        temp = tail_npz.with_name(tail_npz.name + ".tmp")
        try:
            with open(temp, "wb") as f:
                np.savez(f, **tails)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, tail_npz)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        record = {
            "scheme": SCHEME.decode(),
            "path": store.path,
            "state": list(store.state),
            "tail_file": tail_npz.name,
            "byte_end": int(end),
            "frame_end": int(b),
            "atom_end": int(store.offsets[b]),
            "frames_length": self._frames_length,
            "chunks_length": self._chunks_length,
            "ranges": [list(map(int, r)) for r in store.ranges],
            "columns": columns,
            "elements": sorted(int(z) for z in store.element_set),
            "first_numbers": store.first_numbers.tolist(),
            "first_numbers_dtype": store.first_numbers.dtype.str,
            "uniform": bool(store.uniform),
            "kept_level": store._kept_level,
            "allowance": store._scan_allowance,
        }
        _replace(self.progress, json.dumps(record).encode())
        # Only now is the previous tail no longer the record's.
        for old in self.folder.glob(self.identity + ".*.tail.npz"):
            if old != tail_npz:
                old.unlink(missing_ok=True)

    def finish(self):
        """The scan is done and its manifest written: the record is not needed."""
        self.alive = False
        try:
            self.clear()
        except OSError:
            pass

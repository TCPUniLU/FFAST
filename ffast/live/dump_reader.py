"""Incremental reader for the LAMMPS dump FFAST's include file sets up (ADR 0087).

The scorer and the live-run view both use it. It reads only bytes it has not
read yet, returns a frame only once every line its header announces is
present (a half-written last frame is "not yet"), notices when the file is
replaced, and carries the units of the first frame forward (LAMMPS writes
``ITEM: UNITS`` once).

Only the layout FFAST generates is read::

    ITEM: UNITS / ITEM: TIME / ITEM: TIMESTEP / ITEM: NUMBER OF ATOMS /
    ITEM: BOX BOUNDS … (three lines) / ITEM: ATOMS id type element x y z

The header items may come in any order before ``ATOMS``. Other column layouts
and the binary format are refused.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

COLUMNS = ("id", "type", "element", "x", "y", "z")
_HEAD_BYTES = 64
_ONE_VALUE = {b"UNITS": "units", b"TIME": "time", b"TIMESTEP": "step", b"NUMBER OF ATOMS": "n"}


class DumpError(ValueError):
    """The dump is not in the layout FFAST generates, or breaks its rules."""


class DumpReplaced(DumpError):
    """The file at the dump's path is no longer the one being read."""


@dataclass(frozen=True, eq=False)
class DumpFrame:
    index: int                 # 0-based position in the dump
    step: int
    time: float | None         # elapsed simulated time from ``ITEM: TIME``
    units: str                 # LAMMPS unit style, from the first frame
    ids: np.ndarray            # (n,) int64 atom IDs, sorted
    types: np.ndarray          # (n,) int64 LAMMPS atom types
    elements: tuple[str, ...]
    positions: np.ndarray      # (n, 3) float64, Å for metal and real
    box: np.ndarray            # (3, 2 or 3) box bounds as written
    offset: int                # byte offset of the frame in the file
    length: int                # bytes the frame takes


def _parse_frame(buf: bytes, start: int, base: int) -> tuple[dict, int] | None:
    """Parse one frame at ``buf[start:]``; None if it is not complete yet.

    ``base`` is the file offset of ``buf[0]``, for error messages.
    """
    pos = start
    fields: dict = {}

    def line() -> bytes | None:
        nonlocal pos
        end = buf.find(b"\n", pos)
        if end < 0:
            return None
        text = buf[pos:end]
        pos = end + 1
        return text

    while True:
        head = line()
        if head is None:
            return None
        if not head.startswith(b"ITEM: "):
            at = base + pos - len(head) - 1
            raise DumpError(f"unexpected line at byte {at}: {head[:80].decode(errors='replace')!r}")
        item = head[6:]
        if item in _ONE_VALUE:
            value = line()
            if value is None:
                return None
            fields[_ONE_VALUE[item]] = value.strip()
        elif item.startswith(b"BOX BOUNDS"):
            rows = [line() for _ in range(3)]
            if any(r is None for r in rows):
                return None
            fields["box"] = rows
        elif item.startswith(b"ATOMS"):
            cols = tuple(item.decode(errors="replace").split()[1:])
            if cols != COLUMNS:
                raise DumpError(
                    f"dump columns are {' '.join(cols)!r}; FFAST reads only "
                    f"{' '.join(COLUMNS)!r} (the layout its include file writes)"
                )
            if "n" not in fields:
                raise DumpError("ITEM: ATOMS before ITEM: NUMBER OF ATOMS")
            n = int(fields["n"])
            body_start = pos
            for _ in range(n):
                if line() is None:
                    return None
            fields["body"] = buf[body_start:pos]
            return fields, pos
        else:
            raise DumpError(f"unexpected header line {head[:80].decode(errors='replace')!r}")


class DumpReader:
    """Follow one dump file; see the module docstring."""

    def __init__(self, path: str | os.PathLike, max_bytes: int = 16 << 20) -> None:
        path = Path(path)
        if any(ch.isspace() for ch in str(path)):
            raise ValueError(
                f"dump path {str(path)!r} contains a space; LAMMPS cannot write a dump there"
            )
        self.path = path
        self.max_bytes = int(max_bytes)
        self._identity: tuple[int, int] | None = None
        self._head = b""
        self._pending = b""
        self._pending_at = 0          # file offset of _pending[0]
        self._offsets: list[int] = []
        self._lengths: list[int] = []
        self._units: str | None = None
        self._n_atoms: int | None = None
        self._ids: np.ndarray | None = None

    # ── public ──────────────────────────────────────────────────────────────
    @property
    def units(self) -> str | None:
        return self._units

    def __len__(self) -> int:
        return len(self._offsets)

    def poll(self) -> list[DumpFrame]:
        """New complete frames since the last call (at most ``max_bytes`` read)."""
        self._read(self.max_bytes)
        return self._take_frames()

    def finish(self) -> tuple[list[DumpFrame], int]:
        """The producer has ended: read to the end of the file.

        Returns the remaining complete frames and the number of bytes left
        over after the last complete frame (an incomplete tail).
        """
        frames: list[DumpFrame] = []
        while True:
            got = self._read(None)
            frames += self._take_frames()
            if not got:
                return frames, len(self._pending)

    def frame_at(self, k: int) -> DumpFrame:
        """Frame ``k`` (0-based) of the frames returned so far, read again from disk."""
        if not 0 <= k < len(self._offsets):
            raise IndexError(f"frame {k} not read yet ({len(self._offsets)} frames)")
        with open(self.path, "rb") as fh:
            fh.seek(self._offsets[k])
            buf = fh.read(self._lengths[k])
        parsed = _parse_frame(buf, 0, self._offsets[k])
        if parsed is None:
            raise DumpReplaced(f"{self.path}: frame {k} is no longer complete on disk")
        return self._make_frame(parsed[0], k, self._offsets[k], self._lengths[k], check=False)

    # ── internals ───────────────────────────────────────────────────────────
    def _read(self, limit: int | None) -> int:
        """Append new bytes to the pending buffer; return how many were read."""
        try:
            fh = open(self.path, "rb")
        except FileNotFoundError:
            if self._identity is not None:
                raise DumpReplaced(f"{self.path} disappeared")
            return 0
        with fh:
            st = os.fstat(fh.fileno())
            identity = (st.st_dev, st.st_ino)
            end = self._pending_at + len(self._pending)
            if self._identity is None:
                self._identity = identity
            elif identity != self._identity:
                raise DumpReplaced(f"{self.path} was replaced by another file")
            if st.st_size < end:
                raise DumpReplaced(f"{self.path} got shorter ({st.st_size} < {end} bytes read)")
            head = fh.read(_HEAD_BYTES)
            if head[:len(self._head)] != self._head:
                raise DumpReplaced(f"{self.path} was rewritten from the start")
            self._head = head
            fh.seek(end)
            data = fh.read(limit if limit is not None else -1)
        self._pending += data
        return len(data)

    def _take_frames(self) -> list[DumpFrame]:
        frames: list[DumpFrame] = []
        start = 0
        buf = self._pending
        while start < len(buf):
            parsed = _parse_frame(buf, start, self._pending_at)
            if parsed is None:
                break
            fields, end = parsed
            offset = self._pending_at + start
            k = len(self._offsets)
            frames.append(self._make_frame(fields, k, offset, end - start, check=True))
            self._offsets.append(offset)
            self._lengths.append(end - start)
            start = end
        self._pending = buf[start:]
        self._pending_at += start
        return frames

    def _make_frame(self, f: dict, k: int, offset: int, length: int, check: bool) -> DumpFrame:
        units = f["units"].decode() if "units" in f else None
        if check:
            if self._units is None:
                if units is None:
                    raise DumpError(
                        "the first frame has no ITEM: UNITS; the include file sets "
                        "'dump_modify … units yes'"
                    )
                self._units = units
            elif units is not None and units != self._units:
                raise DumpError(f"frame {k} says units {units!r}, the first frame {self._units!r}")
        n = int(f["n"])
        tokens = f["body"].split()
        if len(tokens) != 6 * n:
            raise DumpError(f"frame {k} at byte {offset}: {len(tokens)} values for {n} atoms × 6 columns")
        table = np.array(tokens, dtype=object).reshape(n, 6)
        ids = table[:, 0].astype(np.int64)
        if check:
            if self._ids is None:
                if np.any(np.diff(ids) <= 0):
                    raise DumpError("atom IDs are not sorted; the include file sets 'sort id'")
                self._n_atoms, self._ids = n, ids
            elif n != self._n_atoms or not np.array_equal(ids, self._ids):
                raise DumpError(
                    f"frame {k} (step {int(f['step'])}) has other atoms than the first frame: "
                    "atoms added or removed during the run"
                )
        box = np.array([r.split() for r in f["box"]], dtype=np.float64)
        return DumpFrame(
            index=k,
            step=int(f["step"]),
            time=float(f["time"]) if "time" in f else None,
            units=self._units or units or "",
            ids=ids,
            types=table[:, 1].astype(np.int64),
            elements=tuple(e.decode() for e in table[:, 2]),
            positions=np.array([float(v) for v in table[:, 3:].ravel()]).reshape(n, 3),
            box=box,
            offset=offset,
            length=length,
        )

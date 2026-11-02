"""Read an extxyz file's frames straight into arrays, no Atoms objects.

The numbers come out bit-identical to ASE's reader (ase.io.extxyz), so a
dataset's fingerprint does not change. Anything this reader does not handle
raises NotFast, and the caller reads the file with ASE instead.
"""
import logging
import os
import re
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
import multiprocessing as mp

import numpy as np

logger = logging.getLogger("FFAST")

# key, then =value (quoted, braced, bracketed or bare); anything else is junk.
_PAIR = re.compile(
    r'([A-Za-z_]\w*+)'
    r'(?:=([^\s"\'{\[]\S*+|"(?:[^"\\]++|\\.)*+"|\'(?:[^\'\\]++|\\.)*+\'|\{[^}]*+\}|\[[^\]]*+\]))?'
    r'(?=\s|$)'
    r'|(\S+)')
_UNESCAPE = re.compile(r'\\(.)')
_TOKENS = re.compile(r'[^\s,]+')
_BOOL = {'T': True, 'F': False, 'true': True, 'false': False,
         'True': True, 'False': False, 'TRUE': True, 'FALSE': False}

PARALLEL_MIN_FRAMES = 100_000   # below this, worker start-up costs more than it saves
MIN_BATCH, MAX_BATCH = 2_000, 50_000
WINDOW_PER_WORKER = 2


class NotFast(Exception):
    """This file needs ASE's reader; the message says why."""


class PropertiesDiffer(NotFast):
    def __init__(self, frame, first, other):
        super().__init__(f"Frame {frame:,} lists the columns {other}, but frame 0 lists {first}.")
        self.frame, self.first, self.other = frame, first, other


@dataclass
class FrameArrays:
    properties: str
    counts: np.ndarray            # (N,) int64
    numbers: np.ndarray           # (atoms,) int64
    positions: np.ndarray         # (atoms, 3) float64
    cells: np.ndarray             # (N, 3, 3) float64
    pbc: np.ndarray               # (N, 3) bool
    columns: dict = field(default_factory=dict)   # Properties name -> (atoms,) or (atoms, k)
    values: dict = field(default_factory=dict)    # header key -> (N,), numeric in every frame
    first_frame: int = 0          # where this batch starts in the file

    def __len__(self):
        return len(self.counts)


def _value(raw):
    """A header value as ASE leaves it, for the cases used here: int, float,
    a list of tokens, or a string."""
    if raw is None:
        return True
    if raw[0] in "\"'{[":
        raw = raw[1:-1]
        if "\\" in raw:
            raw = _UNESCAPE.sub(r'\1', raw)
    return raw


def _number(text):
    """ASE's order: int, then float; None when neither (bools, words, lists).
    A list ("1 2", "1,2") fails both, as ASE makes it an array, not a number."""
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return None


def _layout(properties):
    """[(name, type, first column, column count)] and the column total."""
    parts = properties.split(":")
    if len(parts) % 3:
        raise NotFast(f"Properties={properties} is not name:type:count triples")
    layout, col = [], 0
    for name, kind, n in zip(parts[::3], parts[1::3], parts[2::3]):
        n = int(n)
        layout.append((name, kind, col, n))
        col += n
    names = {name for name, *_ in layout}
    if "pos" not in names or not ({"species", "Z"} & names):
        raise NotFast(f"Properties={properties} has no positions or elements")
    for name, kind, _, _ in layout:
        if kind == "S" and name != "species" or kind == "L" or kind not in "RISL":
            raise NotFast(f"Properties={properties}: column {name} of type {kind}")
    return layout, col


def _cell(raw):
    toks = _TOKENS.findall(raw)
    if len(toks) != 9:
        raise NotFast("Lattice is not 9 numbers")
    return np.array(toks, dtype=float).reshape(3, 3)


def _pbc(raw):
    try:
        flags = [_BOOL[t] for t in _TOKENS.findall(raw)]
    except KeyError:
        raise NotFast(f"pbc={raw!r}") from None
    if len(flags) == 1:
        flags *= 3
    if len(flags) != 3:
        raise NotFast(f"pbc={raw!r}")
    return flags


def parse_block(data, first_frame=0):
    """Parse whole frames held in ``data`` (bytes)."""
    from ase.data import atomic_numbers

    lines = data.split(b"\n")
    counts, rows, cells, pbcs, atom_lines = [], [], [], [], []
    properties = None
    i, n_lines = 0, len(lines)
    while i < n_lines:
        head = lines[i].strip()
        if not head:
            i += 1
            continue
        n = int(head)
        header = lines[i + 1].decode()
        props = cell = pbc = None
        row = {}
        for key, raw, junk in _PAIR.findall(header):
            if junk:
                raise NotFast(f"frame {first_frame + len(counts):,}: cannot read {junk!r} in its header")
            raw = raw or None
            if key == "Properties":
                props = _value(raw)
            elif key == "Lattice":
                cell = _cell(_value(raw))
            elif key == "pbc":
                pbc = _pbc(_value(raw))
            elif raw is not None and key.lower() != "uid":
                v = _value(raw)
                num = _number(v) if not v.startswith("_JSON ") else None
                if num is not None:
                    row[key] = num
        if props is None:
            raise NotFast("no Properties= in the header (plain xyz)")
        if properties is None:
            properties = props
        elif props != properties:
            raise PropertiesDiffer(first_frame + len(counts), properties, props)
        counts.append(n)
        rows.append(row)
        cells.append(cell if cell is not None else np.zeros((3, 3)))
        pbcs.append(pbc if pbc is not None else [cell is not None] * 3)
        atom_lines.extend(lines[i + 2:i + 2 + n])
        i += 2 + n

    layout, ncol = _layout(properties)
    tokens = b" ".join(atom_lines).split()
    total = sum(counts)
    if len(tokens) != total * ncol:
        raise NotFast(f"frames {first_frame:,}+: atom lines do not have {ncol} columns each")
    table = np.array(tokens).reshape(total, ncol) if total else np.empty((0, ncol), dtype="S1")

    columns, numbers, positions = {}, None, None
    for name, kind, col, n in layout:
        block = table[:, col:col + n]
        if name == "species":
            symbols, inverse = np.unique(block[:, 0], return_inverse=True)
            try:
                z = np.array([atomic_numbers[s.decode().capitalize()] for s in symbols], dtype=np.int64)
            except KeyError as exc:
                raise NotFast(f"unknown element {exc}") from None
            numbers = z[inverse.reshape(-1)] if total else np.empty(0, dtype=np.int64)
            continue
        arr = block.astype(np.float64 if kind == "R" else np.int32)
        arr = arr[:, 0] if n == 1 else arr
        if name == "pos":
            positions = np.ascontiguousarray(arr, dtype=np.float64)
        elif name == "Z" and numbers is None:
            numbers = arr.astype(np.int64)
        else:
            columns[name] = np.ascontiguousarray(arr)

    keys = set(rows[0]) if rows else set()
    for row in rows[1:]:
        keys &= row.keys()
    values = {k: np.array([row[k] for row in rows]) for k in keys}

    return FrameArrays(
        properties=properties,
        counts=np.array(counts, dtype=np.int64),
        numbers=numbers,
        positions=positions.reshape(-1, 3),
        cells=np.array(cells, dtype=np.float64).reshape(-1, 3, 3),
        pbc=np.array(pbcs, dtype=bool).reshape(-1, 3),
        columns=columns,
        values=values,
        first_frame=first_frame,
    )


def _parse_range(path, start, stop, first_frame):
    """Worker: read bytes [start, stop) of ``path`` and parse them."""
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read(stop - start)
    return parse_block(data, first_frame)


def join(parts):
    """One FrameArrays from batches, in order."""
    first = parts[0]
    keys = set(first.values)
    for p in parts[1:]:
        keys &= p.values.keys()
    def cat(arrs):
        return np.concatenate(arrs) if len(arrs) > 1 else arrs[0]
    return FrameArrays(
        properties=first.properties,
        counts=cat([p.counts for p in parts]),
        numbers=cat([p.numbers for p in parts]),
        positions=cat([p.positions for p in parts]),
        cells=cat([p.cells for p in parts]),
        pbc=cat([p.pbc for p in parts]),
        columns={k: cat([p.columns[k] for p in parts]) for k in first.columns},
        values={k: cat([p.values[k] for p in parts]) for k in keys},
    )


def worker_count():
    env = os.environ.get("FFAST_READ_WORKERS")
    if env:
        return max(1, int(env))
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 1


def read_frames(index, *, workers=None, batch=None, progress=None):
    """Every frame of an indexed file as FrameArrays; NotFast if ASE must read it.

    Batches of frames are parsed in worker processes when the file is large,
    and joined in file order. ``progress(done, total)`` follows each batch."""
    n = len(index)
    if n == 0:
        raise NotFast("the file has no frames")
    workers = worker_count() if workers is None else workers
    if n < PARALLEL_MIN_FRAMES:
        workers = 1
    if batch is None:
        batch = max(MIN_BATCH, min(MAX_BATCH, -(-n // (workers * 4))))
    jobs = [(index.path, int(index.starts[a]), int(index.starts[min(a + batch, n)]), a)
            for a in range(0, n, batch)]
    workers = min(workers, len(jobs))
    logger.info("Reading %s: %s frames, %d batches, %d worker process(es)",
                os.path.basename(index.path), f"{n:,}", len(jobs), workers)
    parts = []

    def keep(part):
        if parts and part.properties != parts[0].properties:
            raise PropertiesDiffer(part.first_frame, parts[0].properties, part.properties)
        parts.append(part)
        if progress:
            progress(part.first_frame + len(part), n)

    if workers <= 1:
        for job in jobs:
            keep(_parse_range(*job))
        return join(parts)
    # At most a few batches per worker in flight, so finished batches waiting
    # to be joined do not pile up in memory.
    window = workers * WINDOW_PER_WORKER
    with ProcessPoolExecutor(workers, mp_context=mp.get_context("spawn")) as pool:
        pending = deque()
        for job in jobs:
            pending.append(pool.submit(_parse_range, *job))
            if len(pending) >= window:
                keep(pending.popleft().result())
        while pending:
            keep(pending.popleft().result())
    return join(parts)

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
_PLAIN_PROPERTIES = "species:S:1:pos:R:3"   # ASE's columns when a header lists none
_MEANINGFUL = {"Properties", "Lattice", "pbc"}   # words that mean something even without "="
_BOOL = {'T': True, 'F': False, 'true': True, 'false': False,
         'True': True, 'False': False, 'TRUE': True, 'FALSE': False}

PARALLEL_MIN_FRAMES = 100_000   # below this, worker start-up costs more than it saves
MIN_BATCH, MAX_BATCH = 2_000, 50_000
WINDOW_PER_WORKER = 2
# Parsing a batch holds ~6x its bytes in the file (one Python string per
# number), measured 2026-11-03 on md22_stachyose: 650 bytes per atom while
# parsing, 57 in the joined arrays.
PARSE_PEAK_PER_BYTE = 7
# Of frame text per batch: ~0.2 GB while it is parsed. Small batches let more
# workers fit beside the arrays: omol25 biomolecules, every 7th frame, 7.4 GB
# free, read with 1 worker in 242 s at 64 MB, with 3 in 110 s at 16 MB.
BATCH_BYTES = 16 * 2**20
# Frames of a stride are read straight through while the file between them is
# at most this many times their own bytes. Measured 2026-11-03 on a USB SSD
# (omol25 biomolecules, 9 KB frames, every 7th): straight through 671 MB/s, a
# seek per frame 339 us. Reading 8 frames at once was 2x faster in a bare read
# test but no faster in a full load (133 s vs 130 s), so it was not kept.
READ_THROUGH = 16
MEMORY_SHARE = 0.9          # of the free memory a load may plan to use
MEMORY_FLOOR = 512 * 2**20  # a load stops when free memory falls below this


class NotFast(Exception):
    """This file needs ASE's reader; the message says why."""


class NotEnoughMemory(Exception):
    """The file's arrays do not fit in the free memory. Not NotFast: ASE's
    reader needs more memory, not less."""


class PropertiesDiffer(NotFast):
    def __init__(self, frame, first, other):
        super().__init__(f"Frame {frame:,} lists the columns {other}, but frame 0 lists {first}.")
        self.frame, self.first, self.other = frame, first, other


@dataclass
class FrameArrays:
    properties: str               # frame 0's Properties=, for messages
    essential: tuple              # (name, type, count) of the columns every frame must list
    extras: dict                  # name -> (type, count) of per-atom numbers a frame may lack
    counts: np.ndarray            # (N,) int64
    numbers: np.ndarray           # (atoms,) int64
    positions: np.ndarray         # (atoms, 3) float64
    cells: np.ndarray             # (N, 3, 3) float64
    pbc: np.ndarray               # (N, 3) bool
    columns: dict = field(default_factory=dict)   # Properties name -> (atoms,) or (atoms, k); NaN where a frame lacks an extra
    missing: dict = field(default_factory=dict)   # extra name -> (atoms,) bool, True where its frame lacks it; only columns some frame lacks
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


def _is_extra(name, kind, n):
    """A per-atom number nothing in the loader needs (a Dataset Field at most):
    a frame may lack it. Elements, positions, forces and every wider column
    must be listed by every frame."""
    return n == 1 and kind in "RI" and name not in ("species", "Z", "pos")


def _signature(layout):
    """((name, type, count) every frame must list, {name: (type, count)} it may)."""
    essential = tuple((name, kind, n) for name, kind, _, n in layout if not _is_extra(name, kind, n))
    extras = {name: (kind, n) for name, kind, _, n in layout if _is_extra(name, kind, n)}
    return essential, extras


def _merge_extras(seen, extras, frame, first, props):
    """Add a frame's extras to ``seen``; the same name with another type is a different file."""
    for name, spec in extras.items():
        if seen.setdefault(name, spec) != spec:
            raise PropertiesDiffer(frame, first, props)


def _stack(parts, masks, atoms):
    """Columns of consecutive runs, one array per name, and where each is
    missing. A run without the column gets NaN (as float64) and True in the
    mask, so the gap is told apart from a NaN in the data (ADR 0076)."""
    names = {}
    for cols in parts:
        names.update(dict.fromkeys(cols))
    out, missing = {}, {}
    for name in names:
        present = [cols[name] for cols in parts if name in cols]
        if len(present) == len(parts) and not any(name in m for m in masks):
            out[name] = np.concatenate(present) if len(parts) > 1 else present[0]
            continue
        tail = present[0].shape[1:]
        out[name] = np.concatenate([
            cols[name].astype(np.float64) if name in cols else np.full((n,) + tail, np.nan)
            for cols, n in zip(parts, atoms)])
        missing[name] = np.concatenate([
            m[name] if name in m else np.full(n, name not in cols)
            for cols, m, n in zip(parts, masks, atoms)])
    return out, missing


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


def _parse_atoms(atom_lines, counts, layout, ncol, first_frame):
    """Elements, positions and the other columns of one run of frames that share a layout."""
    from ase.data import atomic_numbers

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
    return numbers, positions, columns


def parse_block(data, first_frame=0):
    """Parse whole frames held in ``data`` (bytes).

    Frames may list different per-atom numbers (``_is_extra``); the others
    must match frame 0's."""
    lines = data.split(b"\n")
    counts, rows, cells, pbcs = [], [], [], []
    runs = []                     # [props, layout, ncol, atom_lines, counts] per stretch of one layout
    layouts = {}                  # props -> (layout, ncol, essential, extras)
    first_props = first_essential = None
    seen_extras = {}
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
        # A plain xyz comment (no key=value): ASE makes each word a flag set
        # to True, so it holds no number, cell or pbc.
        plain = "=" not in header and not _MEANINGFUL & set(header.split())
        for key, raw, junk in () if plain else _PAIR.findall(header):
            if junk:
                raise NotFast(f"frame {first_frame + len(counts):,}: cannot read {junk!r} in its header")
            raw = raw or None
            if raw is None and key in _MEANINGFUL:
                raise NotFast(f"frame {first_frame + len(counts):,}: {key} without a value in its header")
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
            props = _PLAIN_PROPERTIES
        if props not in layouts:
            layout, ncol = _layout(props)
            essential, extras = _signature(layout)
            frame = first_frame + len(counts)
            if first_props is None:
                first_props, first_essential = props, essential
            elif essential != first_essential:
                raise PropertiesDiffer(frame, first_props, props)
            _merge_extras(seen_extras, extras, frame, first_props, props)
            layouts[props] = (layout, ncol)
        if not runs or runs[-1][0] != props:
            runs.append([props, *layouts[props], [], []])
        runs[-1][3].extend(lines[i + 2:i + 2 + n])
        runs[-1][4].append(n)
        counts.append(n)
        rows.append(row)
        cells.append(cell if cell is not None else np.zeros((3, 3)))
        pbcs.append(pbc if pbc is not None else [cell is not None] * 3)
        i += 2 + n

    parsed, done = [], 0
    for props, layout, ncol, atom_lines, run_counts in runs:
        parsed.append(_parse_atoms(atom_lines, run_counts, layout, ncol, first_frame + done))
        done += len(run_counts)
    numbers = np.concatenate([p[0] for p in parsed]) if len(parsed) > 1 else parsed[0][0]
    positions = np.concatenate([p[1] for p in parsed]) if len(parsed) > 1 else parsed[0][1]
    columns, missing = _stack([p[2] for p in parsed], [{}] * len(parsed), [len(p[0]) for p in parsed])

    keys = set(rows[0]) if rows else set()
    for row in rows[1:]:
        keys &= row.keys()
    values = {k: np.array([row[k] for row in rows]) for k in keys}

    return FrameArrays(
        properties=first_props,
        essential=first_essential,
        extras=seen_extras,
        counts=np.array(counts, dtype=np.int64),
        numbers=numbers,
        positions=positions.reshape(-1, 3),
        cells=np.array(cells, dtype=np.float64).reshape(-1, 3, 3),
        pbc=np.array(pbcs, dtype=bool).reshape(-1, 3),
        columns=columns,
        missing=missing,
        values=values,
        first_frame=first_frame,
    )


def _parse_range(path, start, stop, first_frame):
    """Worker: read bytes [start, stop) of ``path`` and parse them."""
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read(stop - start)
    return parse_block(data, first_frame)


def _parse_spans(path, starts, ends, first_frame):
    """Worker: read the frames at bytes [starts[i], ends[i]) and parse them.
    Frames side by side are one read. Frames a few apart (a stride) are read
    straight through and picked out, which beats a seek per frame on a disk;
    frames far apart get a seek each."""
    if len(starts) == 1 or (ends[:-1] == starts[1:]).all():
        return _parse_range(path, int(starts[0]), int(ends[-1]), first_frame)
    with open(path, "rb") as f:
        if ends[-1] - starts[0] <= READ_THROUGH * int((ends - starts).sum()):
            base = int(starts[0])
            f.seek(base)
            buf = f.read(int(ends[-1]) - base)
            data = b"".join(buf[int(a) - base:int(b) - base] for a, b in zip(starts, ends))
            del buf
        else:
            parts = []
            for a, b in zip(starts.tolist(), ends.tolist()):
                f.seek(a)
                parts.append(f.read(b - a))
            data = b"".join(parts)
    return parse_block(data, first_frame)


def join(parts):
    """One FrameArrays from batches, in order."""
    filled = _Filled(parts[0], sum(len(p.numbers) for p in parts), sum(len(p) for p in parts))
    for p in parts:
        filled.add(p)
    return filled.result()


class _Filled:
    """The joined arrays, made once at their final size from frame 0's
    columns; each batch is copied into its place as it arrives, so a load
    never holds the batches and their join at once. Per-atom numbers a frame
    may lack (extras) and header values are small and joined at the end."""

    def __init__(self, first, atoms, frames):
        self.first = first
        self.atoms, self.frames = atoms, frames
        self.counts = np.empty(frames, dtype=np.int64)
        self.numbers = np.empty(atoms, dtype=first.numbers.dtype)
        self.positions = np.empty((atoms, 3), dtype=np.float64)
        self.cells = np.empty((frames, 3, 3), dtype=np.float64)
        self.pbc = np.empty((frames, 3), dtype=bool)
        self.wide = {name: np.empty((atoms,) + col.shape[1:], dtype=col.dtype)
                     for name, col in first.columns.items() if name not in first.extras}
        self.extra_cols, self.extra_masks, self.extra_atoms = [], [], []
        self.values, self.extras = [], {}
        self.atom_at = self.frame_at = 0

    def add(self, part):
        a, f = self.atom_at, self.frame_at
        na, nf = len(part.numbers), len(part)
        if a + na > self.atoms or f + nf > self.frames:
            raise NotFast(f"frames {part.first_frame:,}+ have more atoms than the frame index counted")
        self.counts[f:f + nf] = part.counts
        self.numbers[a:a + na] = part.numbers
        self.positions[a:a + na] = part.positions
        self.cells[f:f + nf] = part.cells
        self.pbc[f:f + nf] = part.pbc
        for name, out in self.wide.items():
            out[a:a + na] = part.columns[name]
        self.extra_cols.append({k: v for k, v in part.columns.items() if k not in self.wide})
        self.extra_masks.append(part.missing)
        self.extra_atoms.append(na)
        self.values.append(part.values)
        self.extras.update(part.extras)
        self.atom_at, self.frame_at = a + na, f + nf

    def result(self):
        if self.atom_at != self.atoms or self.frame_at != self.frames:
            raise NotFast(f"read {self.atom_at:,} atoms in {self.frame_at:,} frames, "
                          f"the frame index counted {self.atoms:,} in {self.frames:,}")
        def cat(arrs):
            return np.concatenate(arrs) if len(arrs) > 1 else arrs[0]
        keys = set(self.values[0])
        for v in self.values[1:]:
            keys &= v.keys()
        columns, missing = _stack(self.extra_cols, self.extra_masks, self.extra_atoms)
        columns.update(self.wide)
        return FrameArrays(
            properties=self.first.properties,
            essential=self.first.essential,
            extras=self.extras,
            counts=self.counts,
            numbers=self.numbers,
            positions=self.positions,
            cells=self.cells,
            pbc=self.pbc,
            columns=columns,
            missing=missing,
            values={k: cat([v[k] for v in self.values]) for k in keys},
        )


def _bytes_per_atom_and_frame(first):
    """What one atom and one frame take in the joined arrays, from frame 0."""
    per_atom = first.numbers.itemsize + 3 * 8
    for col in first.columns.values():
        per_atom += col.itemsize * int(np.prod(col.shape[1:], dtype=np.int64))
    per_frame = 8 + 9 * 8 + 3 + 8 * len(first.values)
    return per_atom, per_frame


def _batch_peak(starts, ends):
    """Memory one worker holds for a batch: its text while parsed, plus the
    file read straight through around a stride's frames (``_parse_spans``)."""
    wanted = int((ends - starts).sum())
    through = int(ends[-1] - starts[0])
    return PARSE_PEAK_PER_BYTE * wanted + (through if through <= READ_THROUGH * wanted else 0)


def _plan(final, batch_peak, workers, free):
    """Workers that fit beside the joined arrays (each parses one batch at a
    time); NotEnoughMemory's numbers when even one does not."""
    budget = MEMORY_SHARE * free
    while workers > 1 and final + workers * batch_peak > budget:
        workers -= 1
    need = final + workers * batch_peak
    return workers, need <= budget, need


def _gib(n):
    return f"{n / 2**30:.1f} GB"


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
    and copied into the joined arrays in file order. ``progress(done, total)``
    follows each batch. NotEnoughMemory, before reading, when the arrays and
    one batch do not fit in the free memory, and while reading, when free
    memory falls below ``MEMORY_FLOOR``."""
    from ffast.metrics.resources import available_memory

    n = len(index)
    if n == 0:
        raise NotFast("the file has no frames")
    name = os.path.basename(index.path)
    starts, ends = index.spans()
    sizes = ends - starts
    workers = worker_count() if workers is None else workers
    if n < PARALLEL_MIN_FRAMES:
        workers = 1
    if batch is None:
        batch = max(MIN_BATCH, min(MAX_BATCH, -(-n // (workers * 4))))
        batch = max(1, min(batch, int(BATCH_BYTES * n // max(1, int(sizes.sum())))))
    jobs = [(index.path, starts[a:a + batch], ends[a:a + batch], a) for a in range(0, n, batch)]
    workers = min(workers, len(jobs))

    atoms = int(index.atom_counts().sum())
    first = _parse_range(index.path, int(starts[0]), int(ends[0]), 0)
    per_atom, per_frame = _bytes_per_atom_and_frame(first)
    final = atoms * per_atom + n * per_frame
    batch_peak = max(_batch_peak(s, e) for _, s, e, _ in jobs)
    free = available_memory()
    workers, fits, need = _plan(final, batch_peak, workers, free)
    if not fits:
        raise NotEnoughMemory(
            f"{name} needs about {_gib(need)} of memory to load ({atoms:,} atoms in {n:,} frames), "
            f"but {_gib(free)} is free. Close other programs, or load a smaller file.")
    logger.info("Reading %s: %s frames, %d batches, %d worker process(es), about %s of %s free",
                name, f"{n:,}", len(jobs), workers, _gib(need), _gib(free))

    filled = None
    seen_extras = {}

    def keep(part):
        nonlocal filled
        if filled is None:
            filled = _Filled(part, atoms, n)
        elif part.essential != filled.first.essential:
            raise PropertiesDiffer(part.first_frame, filled.first.properties, part.properties)
        _merge_extras(seen_extras, part.extras, part.first_frame, filled.first.properties,
                      part.properties)
        filled.add(part)
        done = part.first_frame + len(part)
        left = available_memory()
        if done < n and left < MEMORY_FLOOR:
            raise NotEnoughMemory(
                f"Stopped reading {name} after {done:,} of {n:,} frames: only {_gib(left)} "
                f"of memory is left. Close other programs, or load a smaller file.")
        if progress:
            progress(done, n)

    if workers <= 1:
        for job in jobs:
            keep(_parse_spans(*job))
        return filled.result()
    # At most a few batches per worker in flight, so finished batches waiting
    # to be copied do not pile up in memory.
    window = workers * WINDOW_PER_WORKER
    with ProcessPoolExecutor(workers, mp_context=mp.get_context("spawn")) as pool:
        pending = deque()
        try:
            for job in jobs:
                pending.append(pool.submit(_parse_spans, *job))
                if len(pending) >= window:
                    keep(pending.popleft().result())
            while pending:
                keep(pending.popleft().result())
        except BaseException:
            for future in pending:
                future.cancel()
            raise
    return filled.result()

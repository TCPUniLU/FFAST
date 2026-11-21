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
  (ADR 0076);
- each frame's energies, when the reader has ``energies(path, starts)``:
  read only when asked for (sampling by energy, ADR 0096), split across
  worker processes, and kept next to the frame list.

``parse`` runs in worker processes: a staticmethod (or a top-level function
held by the class), and the class importable. A ``NotFast`` it raises passes
up for the caller to read the file another way.

The fast parser (ADR 0100), optional: a reader may also give a staticmethod

    parse_into(path, starts, ends, counts, first_frame, arrays, threads) -> dict

FFAST uses it when the reader has it and the compiled package ``ffast_xyz``
imports with the ``CONTRACT`` FFAST expects (``fast_parser_usable``). FFAST
makes the arrays at full size and calls ``parse_into`` once per group of kept
frames (1/50 of them, at most ``GROUP_BYTES`` of text); between calls it moves
the progress, lets a cancel through and checks the memory floor.

- ``starts``, ``ends`` (``uint64``): the byte spans of the group's frames;
  ``counts`` (``int64``): their atoms; ``first_frame``: the file's number of
  the group's first frame, for messages; ``threads``: how many to use.
- ``arrays``: views of FFAST's arrays, already sliced to this group, keyed as
  ``parse`` keys them: ``numbers`` (``int64``, atoms) and ``positions``
  (atoms×3) to fill; ``cell`` (frames×3×3) to fill when ``contents()`` says
  ``cell``; ``forces:<name>`` (atoms×3) and ``atom:<name>`` (atoms) for each
  name ``contents()`` lists, already NaN, filled where a frame has them.
- Returns, every entry optional::

      {"frame":  {key: (frames, values)},   # values int64 or float64
       "text":   {key: (frames, texts)},    # numbers only Python can read
       "atom_new": {key: (atoms, values)},  # a per-atom key not in ``arrays``
       "filled": [keys of ``arrays`` this group had]}

  ``frames`` and ``atoms`` are row numbers within the group (``int64``).
  ``frame`` keys are the per-frame keys ``parse`` gives (``energy:<name>``,
  ``frame:<name>``). FFAST joins them as ``parse``'s batches join: a key every
  kept frame has with only whole numbers stays ``int64``, else ``float64``
  with NaN where a frame lacks it; ``frame:`` keys are always ``float64``. ``text`` values go through
  ``extxyz_arrays._number`` (int, then float; neither: not stored).
  ``atom_new`` values are ``float64``, NaN in the rows they do not cover. A
  key of ``arrays`` no group lists in ``filled`` is dropped, as ``parse`` would
  not have given it; without ``filled`` every key counts as filled.
- Raises ``NotFast("reason")`` to hand the file to ``parse``; the reason is
  logged. A ``NotFast`` from ``parse`` then passes up as above.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import weakref
from collections import deque

import numpy as np

from ffast.io import cache_space
from ffast.io import extxyz_arrays as xa
from ffast.io import frame_cache, frame_index
from ffast.io.extxyz_arrays import MEMORY_FLOOR, MEMORY_SHARE, NotEnoughMemory, NotFast, _gib
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
_scan_parse_lock = threading.Lock()
_tables: dict = {}


def clear_memory_cache():
    with _lock:
        _tables.clear()
        _energies.clear()


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


# ── each frame's energies, for sampling by energy (ADR 0096) ────────────────

# More workers were slower on a 30 GB file (MeluXina, 2026-11-12): the disk is
# the limit. 1 worker 77 s, 8 28 s, 32 21 s, 128 26 s, 255 46 s.
ENERGY_WORKERS = 32
ENERGY_PARALLEL_MIN_FRAMES = xa.PARALLEL_MIN_FRAMES
_energies: dict = {}


def _energies_file(reader_class, path):
    return _table_file(reader_class, path).with_name(_table_file(reader_class, path).stem + "-energies.npz")


def get_energies(reader_class, path, *, workers=None, use_cache=True):
    """Each frame's energies, ``{name: one float per frame}``, NaN where a
    frame has none; None when the reader has no ``energies``. From memory, from
    disk, else the reader's ``energies`` over the frame list, split across
    worker processes for a big file. Saved next to the frame list."""
    if not callable(getattr(reader_class, "energies", None)):
        return None
    path = os.path.abspath(path)
    st = os.stat(path)
    ident = (st.st_size, st.st_mtime_ns)
    memo = (reader_class.name, path)
    if use_cache:
        with _lock:
            hit = _energies.get(memo)
            if hit is not None and hit[0] == ident:
                return hit[1]
        found = _load_energies(reader_class, path, ident)
    else:
        found = None
    if found is None:
        starts = get_table(reader_class, path)[0][:-1]
        found = _read_energies(reader_class, path, starts, workers)
        if use_cache:
            _save_energies(reader_class, path, ident, found)
    for column in found.values():
        column.flags.writeable = False      # shared by every caller
    with _lock:
        _energies[memo] = (ident, found)
    return found


def _read_energies(reader_class, path, starts, workers):
    n = len(starts)
    if workers is None and callable(getattr(reader_class, "parse_into", None)) and fast_parser_usable():
        workers = 1       # a reader with the fast parser reads energies on its own threads (ADR 0100)
    if workers is None:
        workers = min(xa.worker_count(), ENERGY_WORKERS)
    if workers <= 1 or n < max(ENERGY_PARALLEL_MIN_FRAMES, 2):
        pieces, parts = [starts], [reader_class.energies(path, starts)]
    else:
        pieces = [p for p in np.array_split(starts, workers * 4) if len(p)]
        with xa.worker_pool(workers) as pool:
            parts = list(pool.map(reader_class.energies, [path] * len(pieces), pieces))
    names = []
    for part in parts:
        for name in part:
            if name not in names:
                names.append(name)
    out = {}
    for name in names:
        columns = []
        for piece, part in zip(pieces, parts):
            column = np.asarray(part[name], dtype=np.float64) if name in part else np.full(len(piece), np.nan)
            if column.shape != (len(piece),):
                raise ValueError(f"{reader_class.name} energies() gave {column.shape[0] if column.ndim else 0} "
                                 f"values of '{name}' for {len(piece)} frames: one value per frame")
            columns.append(column)
        out[name] = np.concatenate(columns) if columns else np.empty(0)
    return out


def _load_energies(reader_class, path, ident):
    try:
        with np.load(_energies_file(reader_class, path)) as z:
            if (int(z["size"]), int(z["mtime_ns"])) != ident:
                return None
            names = json.loads(str(z["names"]))
            return {name: z[f"e{i}"] for i, name in enumerate(names)}
    except Exception:
        return None


def _save_energies(reader_class, path, ident, energies):
    try:
        target = _energies_file(reader_class, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp.npz")
        np.savez(tmp, names=json.dumps(list(energies)), size=ident[0], mtime_ns=ident[1],
                 **{f"e{i}": v for i, v in enumerate(energies.values())})
        os.replace(tmp, target)
    except Exception as exc:
        logger.warning("Could not save the energies of %s: %s", path, exc, extra={"popup": False})


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
    except (OSError, ValueError):
        return None
    # Open for as long as the arrays live: no process evicts an open copy.
    handle = cache_space.hold_open(entry / cache_space.OPEN, needs=entry / _META)
    if handle is None:
        return None
    try:
        arrays = _read_kept(entry, meta["arrays"])
    except (OSError, ValueError, KeyError):
        handle.close()
        return None
    _seal(arrays, meta.get("digests") or {})
    # The lock lasts as long as any of the arrays is alive (a dict cannot be
    # weakly referenced, its arrays can).
    alive = [len(arrays)]

    def release():
        alive[0] -= 1
        if alive[0] <= 0:
            handle.close()
    for value in arrays.values():
        weakref.finalize(value, release)
    if not arrays:
        handle.close()
    os.utime(entry / _META)                    # most recently used
    return arrays, int(meta["n_file_frames"])


def _read_kept(entry, files):
    """Each kept array read whole into memory, several files at once. Not a
    memory map: on a network file system the map read its pages one fault at
    a time, and reopening a 23 GB copy on MeluXina's Lustre took longer than
    parsing the text again (2026-11-21)."""
    from concurrent.futures import ThreadPoolExecutor

    def read(file):
        return np.load(entry / file, allow_pickle=False)
    with ThreadPoolExecutor(max_workers=max(1, min(8, len(files)))) as pool:
        return dict(zip(files, pool.map(read, files.values())))


def _seal(arrays, digests):
    """Make kept arrays read-only, as the kept copy is, and give the
    fingerprint code the digests stored with the copy."""
    from ffast.cache import fingerprint
    for key, value in arrays.items():
        if isinstance(value, np.ndarray):
            value.flags.writeable = False
            owner = fingerprint._owner(value)
            if owner.flags.writeable:
                owner.flags.writeable = False
            if key in digests:
                fingerprint.remember_digest(value, digests[key])


def _write_kept(folder, files, arrays):
    """Write each array to its file and take its column digest, several
    arrays at once (writing and hashing both let go of the GIL)."""
    from concurrent.futures import ThreadPoolExecutor

    from ffast.cache import fingerprint

    def one(key):
        value = np.asarray(arrays[key])
        np.save(folder / files[key], value, allow_pickle=False)
        return fingerprint.array_digest(value)
    with ThreadPoolExecutor(max_workers=max(1, min(8, len(files)))) as pool:
        return dict(zip(files, pool.map(one, files)))


def _kept_save(reader_class, path, step, arrays, n_file_frames):
    """Keep ``arrays`` for the next load. The column digest of each array,
    or None when nothing was kept."""
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
        source = os.path.realpath(path)
        total = limit + cache_space.used(folder, "batches")
        if not cache_space.make_room(folder, size, total, saving=source):
            logger.info("Not keeping the arrays of %s: the rest of the parsed cache is pinned or open.",
                        name)
            return
        entry = folder / _kept_key(reader_class, path, step)
        tmp = folder / f".{entry.name}.{os.getpid()}.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        files = {key: f"a{i}.npy" for i, key in enumerate(arrays)}
        digests = _write_kept(tmp, files, arrays)
        (tmp / _META).write_text(json.dumps({
            "path": source, "reader": reader_class.name, "step": step,
            "n_file_frames": n_file_frames, "arrays": files,
            "digests": digests}))                            # last: no meta, never opened
        with cache_space.space_lock(folder):
            if cache_space.is_open(entry / cache_space.OPEN):
                shutil.rmtree(tmp, ignore_errors=True)       # the same copy is open: keep it
            else:
                shutil.rmtree(entry, ignore_errors=True)
                os.replace(tmp, entry)
        return digests
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


def _one_pass_possible(reader_class, stride, select):
    """Whether a full, unsampled XYZ load can find frames and parse in one pass."""
    from ffast.io.budget import budget
    return (reader_class.name == "xyz" and select is None and int(stride or 0) <= 1
            and budget().limit >= 16 * 2**20
            and fast_parser_usable() and frame_index._fast_finder() is not None)


def can_scan_and_read(reader_class, path, *, stride=0, select=None, memory_mode="auto", **kwargs):
    """Whether a load goes straight to the one-pass scan (ADR 0103, 0106).

    In memory-saving mode every full, unsampled XYZ load does, whatever the
    size, and a frame index already on disk does not change that. In auto mode
    a file larger than twice the budget does, and so does a smaller one with
    no frame index yet: it is found and parsed in one read and kept in memory
    when the first window says it fits (``_read_whole_one_pass``). With an
    index on disk, the parse is the only read anyway. Every other reader
    keeps its contract.
    """
    from ffast.io.budget import budget
    if memory_mode not in ("auto", "saving") or not _one_pass_possible(reader_class, stride, select):
        return False
    if memory_mode == "saving" or os.path.getsize(path) > 2 * budget().limit:
        return True
    return not frame_index.has_cached_or_running_index(path)


def _read_one_pass(reader_class, path, contents, progress, workers, use_cache):
    """``(arrays, contents, frames, n_file_frames)`` from one pass over the
    file, or None when the fast parser hands it over (NotFast)."""
    from ffast.readers.indexed import IndexedArrays, IndexedStore, NoManifest
    with _scan_parse_lock:
        # A finished scan left a manifest under the same name: open it, do not
        # scan again. Another load that waited on this lock finds it too.
        if use_cache and frame_index.has_cached_or_running_index(path):
            table, counts = get_table(reader_class, path)
            n = len(counts)
            try:
                store = IndexedStore(reader_class, path, contents, list(range(n)), table[:-1], table[1:],
                                     counts,
                                     progress=progress, workers=workers, use_cache=use_cache,
                                     require_manifest=True, full=True)
                return IndexedArrays(store), contents, list(range(n)), n
            except NoManifest:
                pass
        try:
            store = IndexedStore(reader_class, path, contents, [], [], [], [],
                                 progress=progress, workers=workers, use_cache=use_cache,
                                 scan_source=True)
        except NotFast:
            # Unusual count syntax keeps the Python scanner's errors.
            return None
        return IndexedArrays(store), contents, list(range(len(store.counts))), len(store.counts)


# ── one read into memory (2026-11-21) ───────────────────────────────────────
# A load in memory used to find every frame (one read of the file), then parse
# them (a second read): on MeluXina's Lustre a 90 GB file spent 45 s finding
# and 55 s parsing, and the node kept none of it for the second read. Now each
# window is parsed right after its frames are found, while its text is still in
# memory, and whether the arrays fit is estimated from the first window.
WHOLE_WINDOW_BYTES = 128 * 2**20   # text found and parsed at a time (as the memory-saving scan)
WHOLE_MARGIN = 1.25                # the projected arrays times this must fit the budget
WHOLE_FIRST_GUESS = 1.05           # the arrays are made this much larger than projected


class _DoesNotFit(Exception):
    """The arrays projected from the first window do not fit the budget."""


class _ParseFailed(Exception):
    """A window's parse failed; carries its error past the frame finder."""

    def __init__(self, error):
        super().__init__(str(error))
        self.error = error


class _Growing:
    """Arrays that grow as windows are parsed: made from the first window's
    projection, enlarged in place when a later window needs more, cut to size
    at the end. Per-atom forces and fields start as NaN, as in
    ``_read_fast_arrays``."""

    def __init__(self, contents):
        self.fields = ([f"forces:{k}" for k in contents.get("forces") or []]
                       + [f"atom:{k}" for k in contents.get("atom_fields") or []])
        self.has_cell = bool(contents.get("cell"))
        self.arrays: dict = {}
        self.atoms = self.frames = 0           # filled so far
        self.atom_room = self.frame_room = 0

    def _make(self, key, rows):
        if key == "numbers":
            return np.empty(rows, dtype=np.int64)
        if key in ("positions",) or key.startswith("forces:"):
            shape = (rows, 3)
        elif key == "cell":
            shape = (rows, 3, 3)
        else:
            shape = (rows,)
        return np.empty(shape) if key in ("positions", "cell") else np.full(shape, np.nan)

    def keys(self):
        return ["numbers", "positions"] + (["cell"] if self.has_cell else []) + self.fields

    def room(self, atoms, frames):
        """Make room for ``atoms`` more atoms and ``frames`` more frames."""
        if not self.arrays:
            self.atom_room, self.frame_room = atoms, frames
            self.arrays = {k: self._make(k, frames if k == "cell" else atoms) for k in self.keys()}
            return
        need_a, need_f = self.atoms + atoms, self.frames + frames
        if need_a > self.atom_room:
            self.atom_room = max(need_a, int(self.atom_room * 1.5))
            for key, value in self.arrays.items():
                if key != "cell":
                    self.arrays[key] = self._grow(key, value, self.atom_room, self.atoms)
        if need_f > self.frame_room:
            self.frame_room = max(need_f, int(self.frame_room * 1.5))
            if "cell" in self.arrays:
                self.arrays["cell"] = self._grow("cell", self.arrays["cell"], self.frame_room,
                                                 self.frames)

    def _grow(self, key, value, rows, filled):
        bigger = self._make(key, rows)
        bigger[:filled] = value[:filled]
        return bigger

    def add_atom_key(self, key, rows):
        self.arrays[key] = np.full((self.atom_room,) + rows.shape[1:], np.nan)

    def views(self, atoms, frames):
        a0, f0 = self.atoms, self.frames
        return {k: (v[f0:f0 + frames] if k == "cell" else v[a0:a0 + atoms])
                for k, v in self.arrays.items()}

    def nbytes(self):
        """Bytes of what is filled so far."""
        per_atom = sum(v[:1].nbytes for k, v in self.arrays.items() if k != "cell")
        per_frame = self.arrays["cell"][:1].nbytes if "cell" in self.arrays else 0
        return self.atoms * per_atom + self.frames * per_frame

    def result(self):
        """The arrays cut to what was filled (a copy when much room is left over)."""
        out = {}
        for key, value in self.arrays.items():
            rows = self.frames if key == "cell" else self.atoms
            kept = value[:rows]
            out[key] = kept.copy() if value.shape[0] > rows * 1.1 + 16 else kept
        return out


def _read_whole_one_pass(reader_class, path, contents, progress, workers, use_cache):
    """``(arrays, contents, frames, n_file_frames)`` from one read of the file,
    held in memory; None when the fast parser hands the file over (the
    caller then takes the usual path). Raises ``_DoesNotFit`` after the first
    window when the arrays projected from it do not fit the budget."""
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    from ffast.io.budget import budget
    from ffast.metrics import stop
    name = os.path.basename(path)
    if use_cache:
        kept = _kept_load(reader_class, path, 1)
        if kept is not None:
            logger.info("%s: opened the arrays kept from an earlier load", name)
            if reader_class.name == "xyz":
                for value in kept[0].values():
                    budget().retain(value)
            return kept[0], contents, list(range(kept[1])), kept[1]

    owner = budget()
    size = os.path.getsize(path)
    threads = fast_threads() if workers is None else max(1, int(workers))
    grow = _Growing(contents)
    values = _Values(0)
    filled: set = set()
    counts_seen: list = []
    reserved = []                           # the budget lease, once the projection is known
    state = {"text": 0}

    def consume(starts, counts, end):
        stop.check()
        n_frames, n_atoms = len(counts), int(np.sum(counts))
        ends = np.append(starts[1:], end)
        text_bytes = int(end) - int(starts[0])
        if not grow.arrays:
            # First window: a guess at the whole file, refined below.
            share = size / max(1, text_bytes)
            grow.room(max(n_atoms, int(n_atoms * share * WHOLE_FIRST_GUESS)),
                      max(n_frames, int(n_frames * share * WHOLE_FIRST_GUESS)))
        else:
            grow.room(n_atoms, n_frames)
        views = grow.views(n_atoms, n_frames)
        got = reader_class.parse_into(path, starts.astype(np.uint64), ends.astype(np.uint64),
                                      np.asarray(counts, dtype=np.int64), grow.frames, views,
                                      threads) or {}
        values.add(grow.frames, got)
        filled.update(got["filled"] if "filled" in got else grow.fields)
        for key, (rows, column) in (got.get("atom_new") or {}).items():
            if not per_atom(key):
                raise ValueError(f"parse_into() gave '{key}' as a new per-atom key")
            column = np.asarray(column, dtype=np.float64)
            if key not in grow.arrays:
                grow.add_atom_key(key, column)
            grow.arrays[key][grow.atoms + np.asarray(rows, dtype=np.int64)] = column
            filled.add(key)
        grow.atoms += n_atoms
        grow.frames += n_frames
        counts_seen.append(np.asarray(counts, dtype=np.int64))
        state["text"] += text_bytes
        projected = int(grow.nbytes() * size / max(1, state["text"]) * WHOLE_MARGIN)
        if not reserved:
            if not owner.fits(projected):
                raise _DoesNotFit(f"its arrays would need about {_gib(projected)}, more than the "
                                  f"{_gib(owner.limit - owner.used)} the memory budget has left")
            reserved.append(owner.reserve(projected))
            reserved.append(reserved[0].__enter__())
        left = available_memory()
        if end < size and left < MEMORY_FLOOR:
            raise NotEnoughMemory(
                f"Stopped reading {name} after {grow.frames:,} frames: only {_gib(left)} "
                f"of memory is left. Close other programs, or load a smaller file.")
        if progress and callable(getattr(progress, "bytes_read", None)):
            progress.bytes_read(min(int(end), size), size, grow.frames)

    pending = None
    try:
        with _scan_parse_lock, ThreadPoolExecutor(max_workers=1) as parsing:
            def found(starts, counts, end):
                nonlocal pending
                stop.check()
                if pending is not None:
                    try:
                        pending.result()
                    except BaseException as exc:
                        raise _ParseFailed(exc) from exc
                pending = parsing.submit(contextvars.copy_context().run, consume, starts, counts, end)

            logger.info("Reading %s in one pass with the fast parser: %d thread(s)", name, threads)
            try:
                index = frame_index.scan(path, on_batch=found, batch_bytes=min(
                    WHOLE_WINDOW_BYTES, max(1024, owner.limit // 16)))
            except _ParseFailed as failed:
                if isinstance(failed.error, NotFast):
                    return None            # the parser handed the file over: the usual path
                raise failed.error from None
            except NotFast:
                # The frame finder handed the file over: the usual path, which
                # hands it to the Python parser and its messages.
                if pending is not None:
                    try:
                        pending.result()
                    except NotFast:
                        pass
                return None
            if pending is not None:
                try:
                    pending.result()
                except NotFast:
                    return None            # the last window's parser handed the file over
        total = grow.frames
        if total == 0:
            raise ReaderError(f"{name} has no frames")
        arrays = grow.result()
        for key in grow.fields:
            if key not in filled:
                del arrays[key]
        arrays["n_atoms"] = np.concatenate(counts_seen)
        values.frames = total
        arrays.update(values.result())

        # The frame index of this read serves the next load and frame access.
        frame_index.save_cached(index)
        with frame_index._lock:
            frame_index._memory[os.path.abspath(path)] = index
        table = (np.asarray(index.starts, dtype=np.int64), np.asarray(index.counts, dtype=np.int64))
        ident = (index.size, index.mtime_ns)
        _save_table(reader_class, path, ident, table)
        with _lock:
            _tables[(reader_class.name, os.path.abspath(path))] = (ident, table)

        if use_cache:
            digests = _kept_save(reader_class, path, 1, arrays, total)
            if digests is not None:
                _seal(arrays, digests)
        lease = reserved[1] if reserved else None
        if reader_class.name == "xyz":
            for value in arrays.values():
                owner.retain(value, lease)
        return arrays, contents, list(range(total)), total
    finally:
        if reserved:
            reserved[0].__exit__(None, None, None)


def read_level2(reader_class, path, *, stride=0, select=None, progress=None, workers=None,
                batch=None, use_cache=True, on_handover=None, memory_mode="auto"):
    """The kept frames of ``path`` through a Level 2 reader: ``(arrays,
    contents, frames, n_file_frames)``. ``on_handover(text)`` gets the load's
    status text when the fast parser hands the file over (ADR 0100)."""
    name = os.path.basename(path)
    reader = reader_class(path)
    contents = dict(reader.contents() or {})
    tried_one_pass = False
    if can_scan_and_read(reader_class, path, stride=stride, select=select, memory_mode=memory_mode):
        tried_one_pass = True
        from ffast.io.budget import budget
        done = None
        if memory_mode == "auto" and os.path.getsize(path) <= 2 * budget().limit:
            try:
                done = _read_whole_one_pass(reader_class, path, contents, progress, workers,
                                            use_cache)
            except _DoesNotFit as exc:
                logger.info("%s: %s; reading it in memory-saving mode", name, exc)
                done = _read_one_pass(reader_class, path, contents, progress, workers, use_cache)
            except NotEnoughMemory:
                # Free memory fell short while reading: start again in
                # memory-saving mode, as the two-pass path does.
                logger.info("%s: not enough free memory for full arrays; loading in "
                            "memory-saving mode", name)
                done = _read_one_pass(reader_class, path, contents, progress, workers, use_cache)
        else:
            done = _read_one_pass(reader_class, path, contents, progress, workers, use_cache)
        if done is not None:
            return done
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

    if memory_mode not in ("auto", "saving"):
        raise ReaderError("Memory mode must be 'auto' or 'saving'")
    from ffast.io.budget import budget
    index = np.asarray(frames, dtype=np.intp)
    kept_counts = counts[index]
    fields = len(contents.get("atom_fields") or [])
    forces = len(contents.get("forces") or [])
    estimate = int(kept_counts.sum()) * (32 + 24 * forces + 8 * fields)
    estimate += total * (80 + 8 * (len(contents.get("energies") or [])
                                  + len(contents.get("frame_fields") or [])))
    if reader_class.name == "xyz":
        owner = budget()
        physical = int(_physical_sizes(starts[index], starts[index + 1]).sum())
        workspace = min(GROUP_BYTES, max(1024, owner.limit // 8), physical) * 3
        # Compared with the limit only, not with the memory free now, so the
        # choice depends on the machine and not on what else is open (ADR 0106).
        fits = owner.fits(estimate + workspace)
        if memory_mode == "saving" or not fits:
            saved = _read_saving(reader_class, path, contents, frames, n_file, starts, counts,
                                 stride, select, progress, workers, use_cache, tried_one_pass)
            if saved is not None:
                return saved

    try:
        return _read_full(reader_class, path, contents, frames, n_file, starts, counts,
                          use_cache, progress, workers, batch, on_handover)
    except NotEnoughMemory:
        # Free memory fell short of what the plan needed: start again in
        # memory-saving mode instead of failing the load.
        if reader_class.name != "xyz" or memory_mode != "auto":
            raise
        saved = _read_saving(reader_class, path, contents, frames, n_file, starts, counts,
                             stride, select, progress, workers, use_cache, tried_one_pass)
        if saved is None:
            raise
        logger.info("%s: not enough free memory for full arrays; loaded in memory-saving mode", name)
        return saved


def _read_saving(reader_class, path, contents, frames, n_file, starts, counts, stride, select,
                 progress, workers, use_cache, tried_one_pass):
    """A memory-saving load of an XYZ file: one pass for a full, unsampled load
    (ADR 0106), the indexed store for a strided or sampled one."""
    from ffast.io.budget import budget
    from ffast.readers.indexed import IndexedArrays, IndexedStore
    name = os.path.basename(path)
    if not tried_one_pass and _one_pass_possible(reader_class, stride, select):
        done = _read_one_pass(reader_class, path, contents, progress, workers, use_cache)
        if done is not None:
            return done
    index = np.asarray(frames, dtype=np.intp)
    logger.info("Reading %s in memory-saving mode: %s frames, shared RAM limit %.1f MiB",
                name, f"{len(frames):,}", budget().limit / 2**20)
    store = IndexedStore(reader_class, path, contents, frames, starts[index],
                         starts[index + 1], counts[index], progress=progress,
                         workers=workers, use_cache=use_cache,
                         full=select is None and int(stride or 0) <= 1)
    return IndexedArrays(store), contents, frames, n_file


def _read_full(reader_class, path, contents, frames, n_file, starts, counts, use_cache,
               progress, workers, batch, on_handover):
    """The kept frames as ordinary arrays held in memory."""
    from ffast.io.budget import budget
    name = os.path.basename(path)
    total = len(frames)
    step = _regular_step(frames)
    if use_cache and step is not None:
        kept = _kept_load(reader_class, path, step)
        if kept is not None:
            logger.info("%s: opened the arrays kept from an earlier load", name)
            if progress:
                progress(total, total)
            if reader_class.name == "xyz":
                for value in kept[0].values():
                    budget().retain(value)
            return kept[0], contents, frames, n_file

    index = np.asarray(frames, dtype=np.intp)
    spans_start, spans_end = starts[index], starts[index + 1]
    kept_counts = counts[index]

    arrays = None
    if callable(getattr(reader_class, "parse_into", None)):
        if not fast_parser_usable():
            _warn_no_fast_parser()
        else:
            try:
                arrays = _read_fast(reader_class, path, contents, frames, spans_start, spans_end,
                                    kept_counts, progress, workers, batch)
            except NotFast as exc:
                note = f"{name}: read with the slower Python parser ({exc})"
                logger.warning("%s", note, extra={"popup": False})
                if on_handover:
                    on_handover(note)
    if arrays is None:
        arrays = _read_python(reader_class, path, frames, spans_start, spans_end, kept_counts,
                              progress, workers, batch)

    if use_cache and step is not None:
        digests = _kept_save(reader_class, path, step, arrays, n_file)
        if digests is not None:
            # The arrays in memory stay in use, read-only like the copy, with
            # its digests (a reopen of the copy gives the same).
            _seal(arrays, digests)
    if reader_class.name == "xyz":
        for value in arrays.values():
            budget().retain(value)
    return arrays, contents, frames, n_file


def _read_python(reader_class, path, frames, spans_start, spans_end, kept_counts, progress,
                 workers, batch):
    """The kept frames through the reader's ``parse``, in batches, in worker
    processes for a big file: the joined arrays."""
    name = os.path.basename(path)
    total = len(frames)
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
        with xa.worker_pool(workers) as pool:
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
    return joined.arrays


# ── the fast parser (ADR 0100) ──────────────────────────────────────────────

FAST_CONTRACT = 1              # the ffast_xyz.CONTRACT this FFAST expects
GROUPS = 50                    # a group is 1/GROUPS of the kept frames ...
GROUP_BYTES = 256 * 2**20      # ... and at most this much text
# Threads for the fast parser, unless FFAST_READ_WORKERS says: more were
# slower on MeluXina (256 cores; shard_00 first open 5.4 s at 64, 9.4 s at 256).
FAST_THREADS = 32
_fast_usable = None
_fast_missing = True           # when not usable: not installed (True) or too old (False)
_fast_warned = False           # the pop-up was sent (once per server start)


def fast_parser_usable():
    """Whether the compiled package ``ffast_xyz`` imports and follows the
    ``parse_into`` contract this FFAST expects. Checked once per process."""
    global _fast_usable, _fast_missing
    if _fast_usable is None:
        try:
            import ffast_xyz
        except ImportError:
            _fast_usable, _fast_missing = False, True
        else:
            _fast_usable = getattr(ffast_xyz, "CONTRACT", None) == FAST_CONTRACT
            _fast_missing = False
    return _fast_usable


def fast_threads():
    """Threads for the fast parser: ``FFAST_READ_WORKERS`` when set, else
    the cores this process may use, at most FAST_THREADS."""
    if os.environ.get("FFAST_READ_WORKERS"):
        return xa.worker_count()
    return min(xa.worker_count(), FAST_THREADS)


def _warn_no_fast_parser():
    """The pop-up of ADR 0100 decision 5: once per server start, the first
    time a reader with ``parse_into`` reads a file without a usable package."""
    global _fast_warned
    with _lock:
        if _fast_warned:
            return
        _fast_warned = True
    if _fast_missing:
        why, fix = "Fast reader not installed", "install it"
    else:
        why, fix = "Fast reader is too old for this FFAST", "update it"
    logger.warning("%s: .xyz files load with the slower Python parser. Run "
                   "`ffast install-fast-reader` to %s and load them up to 10× faster.", why, fix)


def _groups(sizes, batch=None, max_bytes=None):
    """``[(a, b)]`` row ranges over frames whose texts are ``sizes`` bytes:
    ``batch`` frames each (default 1/GROUPS of them), cut short at
    GROUP_BYTES of text, at least one frame."""
    max_bytes = GROUP_BYTES if max_bytes is None else max_bytes
    total = len(sizes)
    per = int(batch) if batch else -(-total // GROUPS)
    ends = np.cumsum(np.asarray(sizes, dtype=np.int64))
    out, a = [], 0
    while a < total:
        before = int(ends[a - 1]) if a else 0
        fit = int(np.searchsorted(ends, before + max_bytes, side="right"))   # frames up to here fit
        b = max(a + 1, min(a + per, fit, total))
        out.append((a, b))
        a = b
    return out


class _Values:
    """The per-frame values ``parse_into`` returns, joined as ``_Joined``
    joins ``parse``'s batches: ``int64`` while every kept frame has the key
    with a whole number, else ``float64`` with NaN where a frame lacks it."""

    def __init__(self, frames):
        self.frames = frames
        self.parts: dict = {}               # key -> [(rows, values)]

    def add(self, start, got):
        for key, (rows, values) in (got.get("frame") or {}).items():
            self._put(key, rows, np.asarray(values), start)
        for key, (rows, texts) in (got.get("text") or {}).items():
            numbers = [xa._number(t if isinstance(t, str) else bytes(t).decode()) for t in texts]
            keep = [i for i, v in enumerate(numbers) if v is not None]
            if keep:
                self._put(key, np.asarray(rows)[keep], np.array([numbers[i] for i in keep]), start)

    def _put(self, key, rows, values, start):
        rows = np.asarray(rows, dtype=np.int64)
        if values.shape != rows.shape:
            raise ValueError(f"parse_into() gave {values.shape} values of '{key}' for "
                             f"{rows.shape} frames")
        self.parts.setdefault(key, []).append((rows + start, values))

    def result(self):
        out = {}
        for key, parts in self.parts.items():
            rows = np.concatenate([r for r, _ in parts])
            seen = np.zeros(self.frames, dtype=bool)
            seen[rows] = True
            kinds = {v.dtype.kind for _, v in parts}
            whole = seen.all() and not key.startswith("frame:")    # frame fields are float64
            if "O" in kinds:            # a whole number beyond 64 bits: as np.array makes it
                column = np.full(self.frames, np.nan, dtype=object)
                for r, v in parts:
                    column[r] = v
                column = np.array(column.tolist()) if whole else column.astype(np.float64)
            elif whole and kinds <= {"i", "u"}:
                column = np.empty(self.frames, dtype=np.int64)
                for r, v in parts:
                    column[r] = v
            else:
                column = np.full(self.frames, np.nan)
                for r, v in parts:
                    column[r] = v
            out[key] = column
        return out


def _read_fast(reader_class, path, contents, frames, spans_start, spans_end, kept_counts,
               progress, threads, batch):
    from ffast.io.budget import budget
    owner = budget()
    fields = len(contents.get("atom_fields") or [])
    forces = len(contents.get("forces") or [])
    final = int(np.sum(kept_counts)) * (32 + 24 * forces + 8 * fields)
    final += len(frames) * (80 + 8 * (len(contents.get("energies") or [])
                                    + len(contents.get("frame_fields") or [])))
    sizes = _physical_sizes(spans_start, spans_end)
    groups = _groups(sizes, batch, max_bytes=max(1024, min(GROUP_BYTES, owner.limit // 8)))
    peak = max((int(sizes[a:b].sum()) for a, b in groups), default=0)
    with owner.reserve(final + peak * 3) as lease:
        arrays = _read_fast_arrays(reader_class, path, contents, frames, spans_start,
                                   spans_end, kept_counts, progress, threads, batch)
        for value in arrays.values():
            owner.retain(value, lease)
        return arrays


def _physical_sizes(starts, ends):
    sizes = ends - starts
    if len(sizes) > 1:
        gaps = starts[1:] - ends[:-1]
        sizes = sizes.copy()
        sizes[1:] += np.where((gaps >= 0) & (gaps <= 1 << 20), gaps, 0)
    return sizes


def _read_fast_arrays(reader_class, path, contents, frames, spans_start, spans_end, kept_counts,
               progress, threads, batch, *, log_read=True):
    """The kept frames through the reader's ``parse_into``: FFAST's arrays,
    made at full size, filled one group at a time."""
    name = os.path.basename(path)
    total = len(frames)
    atoms = int(kept_counts.sum())
    threads = fast_threads() if threads is None else max(1, int(threads))
    fields = ([f"forces:{k}" for k in contents.get("forces") or []]
              + [f"atom:{k}" for k in contents.get("atom_fields") or []])
    has_cell = bool(contents.get("cell"))
    n_values = len(contents.get("energies") or []) + len(contents.get("frame_fields") or [])

    # Sizing: the final arrays only; the fast parser holds little beyond them.
    per_atom_bytes = 8 + 24 + sum(24 if k.startswith("forces:") else 8 for k in fields)
    per_frame_bytes = 8 + (72 if has_cell else 0) + 8 * n_values
    final = atoms * per_atom_bytes + total * per_frame_bytes
    free = available_memory()
    _, fits, need = xa._plan(final, 0, 1, free)
    if not fits:
        raise NotEnoughMemory(
            f"{name} needs about {_gib(need)} of memory to load ({atoms:,} atoms in {total:,} "
            f"frames), but {_gib(free)} is free. Close other programs, or load a smaller file.")
    from ffast.io.budget import budget
    groups = _groups(_physical_sizes(spans_start, spans_end), batch,
                     max_bytes=max(1024, min(GROUP_BYTES, budget().limit // 8)))
    if log_read:
        logger.info("Reading %s with the fast parser: %s frames, %d groups, %d thread(s), about %s of %s free",
                    name, f"{total:,}", len(groups), threads, _gib(need), _gib(free))

    arrays = {"n_atoms": np.array(kept_counts, dtype=np.int64),
              "numbers": np.empty(atoms, dtype=np.int64),
              "positions": np.empty((atoms, 3), dtype=np.float64)}
    if has_cell:
        arrays["cell"] = np.empty((total, 3, 3), dtype=np.float64)
    for key in fields:
        arrays[key] = np.full((atoms, 3) if key.startswith("forces:") else atoms, np.nan)

    first_atom = np.concatenate([[0], np.cumsum(kept_counts)])
    starts_u, ends_u = spans_start.astype(np.uint64), spans_end.astype(np.uint64)
    counts = np.asarray(kept_counts, dtype=np.int64)
    values = _Values(total)
    filled: set = set()
    for a, b in groups:
        a0, a1 = int(first_atom[a]), int(first_atom[b])
        views = {k: (v[a0:a1] if per_atom(k) else v[a:b]) for k, v in arrays.items() if k != "n_atoms"}
        got = reader_class.parse_into(path, starts_u[a:b], ends_u[a:b], counts[a:b], int(frames[a]),
                                      views, threads) or {}
        values.add(a, got)
        filled.update(got["filled"] if "filled" in got else fields)
        for key, (rows, column) in (got.get("atom_new") or {}).items():
            if not per_atom(key):
                raise ValueError(f"parse_into() gave '{key}' as a new per-atom key")
            column = np.asarray(column, dtype=np.float64)
            if key not in arrays:
                arrays[key] = np.full((atoms,) + column.shape[1:], np.nan)
            arrays[key][a0 + np.asarray(rows, dtype=np.int64)] = column
            filled.add(key)
        left = available_memory()
        if b < total and left < MEMORY_FLOOR:
            raise NotEnoughMemory(
                f"Stopped reading {name} after {b:,} of {total:,} frames: only {_gib(left)} "
                f"of memory is left. Close other programs, or load a smaller file.")
        if progress:
            progress(b, total)

    for key in fields:
        if key not in filled:
            del arrays[key]
    arrays.update(values.result())
    return arrays

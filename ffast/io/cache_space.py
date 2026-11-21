"""Who may keep, and who must give up, disk space in the parsed cache (ADR 0108).

The parsed cache holds two kinds of entries under one root folder
(``frame_cache._folder()``): whole-array entries (``<root>/<key>/``) and
memory-saving batches (``<root>/batches/<identity>.*``). Entries parsed from
the same source file form one *dataset*.

This module finds the entries, tells which are protected and makes room.
A dataset is protected when it is pinned (``pins.json``), when any of its
entries is open in some process (a shared ``flock`` on its ``.open`` file),
or when it is the dataset being saved. That protection covers only the
copies of the file as it is now: a copy of an older version of the file is
protected only while it is open itself. Everything else goes oldest
last-use first. All eviction and every publish of a new entry run under one exclusive
lock, ``<root>/.space.lock``.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

try:
    import fcntl
except ImportError:      # no flock here; FFAST targets macOS and Linux
    fcntl = None

log = logging.getLogger("FFAST")

PINS = "pins.json"
SPACE_LOCK = ".space.lock"
OPEN = ".open"
SOURCE = ".source.json"
_META = "meta.json"
_IDENTITY = re.compile(r"^[0-9a-f]{64}")
# After the identity: a per-atom batch is "-<a>-<b>.npz" with a >= 0. The
# scalar batch "--1--1.npz", the digests and the scan record are small records.
_ATOM_BATCH = re.compile(r"^-\d+-\d+\.npz$")
_NOT_RECORDS = (OPEN, SOURCE)


class InUse(Exception):
    """The dataset has a copy that is open in some process."""


@dataclass
class Entry:
    kind: str                 # "arrays" or "batches"
    key: str                  # folder name, or batch identity
    path: str | None          # source file (symlinks resolved), None if unknown
    files: list = field(default_factory=list)
    bytes: int = 0
    last_used: float = 0.0
    root: Path = None
    state: list | None = None  # the source file's state when the copy was made


    @property
    def folder(self):
        return self.root / self.key if self.kind == "arrays" else self.root / "batches"

    @property
    def open_file(self):
        if self.kind == "arrays":
            return self.root / self.key / OPEN
        return self.root / "batches" / f"{self.key}{OPEN}"

    @property
    def source_file(self):
        return self.root / "batches" / f"{self.key}{SOURCE}"


@dataclass
class Dataset:
    path: str | None
    entries: list
    bytes: int
    last_used: float
    pinned: bool
    open: bool


# ---------------------------------------------------------------- finding

def _read_state(file):
    """The ``state`` list a copy recorded, or None (unknown, older copies)."""
    try:
        value = json.loads(Path(file).read_text()).get("state")
        return list(value) if isinstance(value, list) else None
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def _read_path(file, key=None):
    try:
        data = json.loads(Path(file).read_text())
        value = data["path"] if key is None else data[key]
        return value if isinstance(value, str) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _array_entry(root, d):
    files, size = [], 0
    try:
        for f in d.iterdir():
            if not f.is_file() or (f.name.startswith(".") and f.name != OPEN):
                continue
            files.append(f)
            size += f.stat().st_size
    except OSError:
        pass
    meta = d / _META
    try:
        last = meta.stat().st_mtime
    except OSError:
        try:
            last = d.stat().st_mtime
        except OSError:
            last = 0.0
    return Entry("arrays", d.name, _read_path(meta), files, size, last, root,
                 state=_read_state(meta))


def _batch_entries(root):
    folder = root / "batches"
    groups = {}
    try:
        names = list(folder.iterdir())
    except OSError:
        return []
    for f in names:
        name = f.name
        if name.endswith(".tmp"):
            continue
        m = _IDENTITY.match(name)
        if not m or len(name) <= 64 or name[64] not in ".-":
            # Not made by a store: counted against the budget, evicted as a
            # small record, never locked.
            if not name.startswith(".") and name.endswith((".npz", ".json", ".bin")):
                try:
                    groups.setdefault(name, []).append((f, f.stat()))
                except OSError:
                    pass
            continue
        if name == f"{name[:64]}.lock":       # a scan's lock, held by the scanner
            continue
        try:
            if not f.is_file():
                continue
            stat = f.stat()
        except OSError:
            continue
        groups.setdefault(name[:64], []).append((f, stat))
    out = []
    for identity, items in groups.items():
        entry = Entry("batches", identity, None, [f for f, _ in items],
                      sum(s.st_size for _, s in items), 0.0, root)
        entry.path = _read_path(entry.source_file)
        entry.state = _read_state(entry.source_file)
        stamps = {f.name: s.st_mtime for f, s in items}
        entry.last_used = stamps.get(entry.source_file.name, max(stamps.values()))
        out.append(entry)
    return out


def entries(root):
    """Every whole-array entry and every batch identity under ``root``."""
    root = Path(root)
    out = []
    try:
        children = sorted(root.iterdir())
    except OSError:
        return out
    for d in children:
        if d.name in ("batches", "results") or d.name.startswith("."):
            continue
        try:
            if d.is_dir():
                out.append(_array_entry(root, d))
        except OSError:
            continue
    out += _batch_entries(root)
    return out


def used(root, kind=None):
    """Bytes of every entry (or of one ``kind``): the one number the budget is
    measured against."""
    return sum(e.bytes for e in entries(root) if kind in (None, e.kind))


def allowance(root, free):
    """The most the whole parsed cache under ``root`` may hold: the budget's
    parsed limit, and no more than leaves ``budget().keep_free`` bytes of the
    disk free. ``free`` is the disk's free space now; what the cache holds
    already counts as room, since other datasets give way (ADR 0112)."""
    from ffast.io.budget import budget

    owner = budget()
    return min(owner.parsed_limit, used(root) + max(0, free - owner.keep_free))


def _group(root, ents, pins):
    groups = {}
    for e in ents:
        groups.setdefault(e.path if e.path is not None else (e.kind, e.key), []).append(e)
    out = []
    for key, items in groups.items():
        path = key if isinstance(key, str) else None
        out.append(Dataset(
            path=path, entries=items,
            bytes=sum(e.bytes for e in items),
            last_used=max(e.last_used for e in items),
            pinned=path is not None and path in pins,
            open=any(is_open(e.open_file) for e in items)))
    return out


def _source_state(path):
    """``(dev, inode, size, mtime_ns)`` of the source now, None when it is gone."""
    try:
        s = os.stat(path)
    except (OSError, TypeError):
        return None
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns]


def _is_current(entry, now):
    """True when ``entry`` was made from the source as it is now ``now``. A
    copy that recorded no state, or a source that is gone, counts as current:
    with no way to tell, the copy keeps its protection."""
    if entry.state is None or now is None:
        return True
    if entry.kind == "arrays":                 # recorded as [size, mtime_ns]
        return entry.state == now[2:]
    return entry.state == now


def _split(ds, saving):
    """``(protected, victims, kept_bytes)`` for one dataset (ADR 0108 as changed
    2026-11-21). A pin, an open copy or a save protects the copies of the file
    as it is now. A copy of an older version is protected only while it is
    open itself, and is otherwise evicted on its own, oldest first."""
    now = _source_state(ds.path) if ds.path is not None else None
    current = [e for e in ds.entries if _is_current(e, now)]
    older = [e for e in ds.entries if not _is_current(e, now)]
    saved = saving is not None and ds.path == saving
    victims, kept = [], 0
    if current:
        if saved:
            pass
        elif ds.pinned or any(is_open(e.open_file) for e in current):
            kept += sum(e.bytes for e in current)
        else:
            victims.append(_part_of(ds, current))
    for e in older:
        if is_open(e.open_file):
            kept += e.bytes
        else:
            victims.append(_part_of(ds, [e]))
    return victims, kept


def _part_of(ds, ents):
    return Dataset(path=ds.path, entries=ents, bytes=sum(e.bytes for e in ents),
                   last_used=max(e.last_used for e in ents), pinned=False, open=False)


def datasets(root):
    """The entries grouped by source path, with size, last use, pin and open."""
    root = Path(root)
    return _group(root, entries(root), set(read_pins(root)))


# ------------------------------------------------------------------- pins

def read_pins(root):
    """The pinned paths. An unreadable pin file pins nothing."""
    try:
        data = json.loads((Path(root) / PINS).read_text())
        return [p for p in data["paths"] if isinstance(p, str)]
    except FileNotFoundError:
        return []
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("The cache pin file %s cannot be read; no path is pinned.",
                    Path(root) / PINS, extra={"popup": False})
        return []


def write_pins(root, paths):
    """Write the pin file: temporary file, ``fsync``, then rename."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    unique = sorted(set(paths))
    fd, temp = tempfile.mkstemp(dir=root, prefix=".pins-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"version": 1, "paths": unique}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, root / PINS)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


# ------------------------------------------------------------------ locks

class _Nothing:
    def close(self):
        pass


def hold_open(open_file, needs=None):
    """Take a shared lock on ``open_file`` and return its handle, or None when
    the entry is gone: the file was deleted after we opened it, or ``needs``
    (the entry's ``meta.json`` or ``.source.json``) no longer exists. The lock
    lasts until the handle is closed or the process ends."""
    if fcntl is None:
        return _Nothing()
    path = Path(open_file)
    try:
        handle = open(path, "a+b")
    except OSError:
        try:
            handle = open(path, "rb")        # a read-only cache folder
        except OSError:
            return None
    try:
        fcntl.flock(handle, fcntl.LOCK_SH)
        if os.fstat(handle.fileno()).st_ino != os.stat(path).st_ino:
            raise OSError("the entry was deleted")
        if needs is not None and not os.path.exists(needs):
            raise OSError("the entry was deleted")
    except OSError:
        handle.close()
        return None
    return handle


def is_open(open_file):
    """True when some process, this one included, holds ``open_file``."""
    if fcntl is None:
        return False
    try:
        handle = open(open_file, "rb")
    except OSError:
        return False
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        return False
    finally:
        handle.close()


def _lock_exclusive(open_file):
    """An exclusively locked handle, ``None`` if there is no lock file or no
    locking here, or ``False`` when the copy is open."""
    if fcntl is None:
        return None
    try:
        handle = open(open_file, "a+b")
    except OSError:
        return None
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    return handle


@contextlib.contextmanager
def space_lock(root):
    """Exclusive lock for eviction and for publishing a new entry."""
    root = Path(root)
    handle = None
    if fcntl is not None:
        try:
            root.mkdir(parents=True, exist_ok=True)
            handle = open(root / SPACE_LOCK, "a+b")
            fcntl.flock(handle, fcntl.LOCK_EX)
        except OSError:
            if handle is not None:
                handle.close()
            handle = None
    try:
        yield
    finally:
        if handle is not None:
            handle.close()


# --------------------------------------------------------------- eviction

def _unlink(path):
    try:
        size = path.stat().st_size
        path.unlink()
        return size
    except OSError:
        return 0


def _lock_all(ents):
    """Exclusive handles on the ``.open`` files of ``ents``, or None when one
    of them is open."""
    handles = []
    for e in ents:
        if e.kind == "batches" and len(e.key) != 64:
            continue
        h = _lock_exclusive(e.open_file)
        if h is False:
            for g in handles:
                g.close()
            return None
        if h is not None:
            handles.append(h)
    return handles


def _evict_dataset(ds, excess):
    """Delete parts of ``ds`` until ``excess`` bytes are freed (big parts first,
    small records last). Returns the bytes freed."""
    handles = _lock_all(ds.entries)
    if handles is None:
        return 0
    freed = 0
    try:
        batches = [e for e in ds.entries if e.kind == "batches"]
        arrays = [e for e in ds.entries if e.kind == "arrays"]
        # 1. per-atom batch files, largest first
        big = []
        for e in batches:
            for f in e.files:
                if _ATOM_BATCH.match(f.name[64:]) and f.name.endswith(".npz"):
                    big.append((f.stat().st_size if f.exists() else 0, f))
        for _, f in sorted(big, key=lambda x: (-x[0], x[1].name)):
            if freed >= excess:
                return freed
            freed += _unlink(f)
        # 2. whole-array entries, largest first
        for e in sorted(arrays, key=lambda e: -e.bytes):
            if freed >= excess:
                return freed
            freed += e.bytes
            shutil.rmtree(e.folder, ignore_errors=True)
        # 3. small records, then the source and lock files of an entry
        for e in sorted(batches, key=lambda e: -e.bytes):
            records = [f for f in e.files
                       if f.name[64:] not in _NOT_RECORDS and f.exists()]
            for f in records:
                if freed >= excess:
                    return freed
                freed += _unlink(f)
            if freed >= excess and records:
                return freed
            freed += _unlink(e.source_file)
            freed += _unlink(e.open_file)
        return freed
    finally:
        for h in handles:
            h.close()


def remove_markers(folder, identity):
    """Delete the ``.source.json`` and ``.open`` files of a batch identity
    whose data is gone, unless it is open or still has other files."""
    folder = Path(folder)
    for f in folder.glob(f"{identity}*"):
        if f.name[64:] not in _NOT_RECORDS and not f.name.endswith(".lock"):
            return
    handle = _lock_exclusive(folder / f"{identity}{OPEN}")
    if handle is False:
        return
    try:
        _unlink(folder / f"{identity}{SOURCE}")
        _unlink(folder / f"{identity}{OPEN}")
    finally:
        if handle is not None:
            handle.close()


_warned_full = False       # one warning per server run


def _warn_full(total, limit):
    """Tell the user, once per run, that pinned or open copies fill the cache."""
    global _warned_full
    if _warned_full:
        return
    _warned_full = True
    log.warning(
        "The parsed cache is full of pinned or open copies (%.1f GB of %.1f GB), so a new "
        "copy is not kept. Unpin with `ffast-cli cache unpin PATH` or raise "
        "FFAST_PARSED_CACHE_BYTES.", total / 2**30, limit / 2**30)


def make_room(root, need, limit, saving=None, locked=False, trim_own_atom_batches=False):
    """Delete unprotected parts of the cache until ``need`` more bytes fit in
    ``limit``. ``saving`` is the path of the dataset being saved: it is never
    trimmed, except that ``trim_own_atom_batches`` (the manifest write at the
    end of a finished scan, ADR 0108) may delete its per-atom batch files as
    a last resort, never its small records. True when the need fits.
    ``locked`` says the caller already holds ``space_lock`` (the lock is not
    reentrant)."""
    root = Path(root)
    with contextlib.nullcontext() if locked else space_lock(root):
        ents = entries(root)
        total = sum(e.bytes for e in ents)
        if total + need <= limit:
            return True
        pins = set(read_pins(root))
        victims = []
        kept = 0                  # bytes of pinned or open copies other than the saving one
        for ds in _group(root, ents, pins):
            parts, protected = _split(ds, saving)
            victims += parts
            kept += protected
        victims.sort(key=lambda d: (d.last_used, d.path or ""))
        for ds in victims:
            if total + need <= limit:
                break
            total -= _evict_dataset(ds, total + need - limit)
        if total + need > limit and trim_own_atom_batches and saving is not None:
            own = [f for e in ents if e.kind == "batches" and e.path == saving
                   for f in e.files
                   if len(f.name) > 64 and _ATOM_BATCH.match(f.name[64:])]
            sized = []
            for f in own:
                try:
                    sized.append((f.stat().st_size, f))
                except OSError:
                    pass
            for _, f in sorted(sized, key=lambda x: (-x[0], x[1].name)):
                if total + need <= limit:
                    break
                total -= _unlink(f)
        if total + need > limit and kept:
            _warn_full(total, limit)
        return total + need <= limit


def delete_dataset(root, path):
    """Delete every copy of ``path`` and return the bytes freed. Raises
    ``InUse`` when a copy is open. The pin is the caller's to remove."""
    root = Path(root)
    with space_lock(root):
        mine = [e for e in entries(root) if e.path == path]
        handles = _lock_all(mine)
        if handles is None:
            raise InUse(path)
        freed = 0
        try:
            for e in mine:
                if e.kind == "arrays":
                    freed += e.bytes
                    shutil.rmtree(e.folder, ignore_errors=True)
                else:
                    for f in e.files:
                        freed += _unlink(f)
                    _unlink(e.open_file)
        finally:
            for h in handles:
                h.close()
        return freed


def delete_identity(root, identity):
    """Delete the files of one batch identity (its manifest, digests, batches
    and markers), not the other copies of the same source file. Returns the
    paths deleted. Raises ``InUse`` when the copy is open. Pins are untouched."""
    root = Path(root)
    with space_lock(root):
        mine = [e for e in _batch_entries(root) if e.key == identity]
        handles = _lock_all(mine)
        if handles is None:
            raise InUse(identity)
        gone = []
        try:
            for e in mine:
                for f in e.files:
                    if f.exists():
                        _unlink(f)
                        gone.append(f)
                _unlink(e.open_file)
        finally:
            for h in handles:
                h.close()
        return gone

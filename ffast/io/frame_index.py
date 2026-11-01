"""Frame index of an .xyz/.extxyz file: where each frame starts.

ASE's extxyz reader walks the whole file to list frame starts on every
`ase.io.read`, even for `index=0`. We walk it once, keep the starts (in memory
and in `~/.ffast/frame-index`), and read frames by seeking
(docs/implementation/2026-11-01-frame-index.md).
"""
import hashlib
import io
import logging
import os
import threading
from pathlib import Path

import numpy as np

logger = logging.getLogger("FFAST")

INDEXED_SUFFIXES = (".xyz", ".extxyz")
# FFAST_FRAME_INDEX_DIR lets the tests point servers they spawn at a temp folder.
CACHE_DIR = Path(os.environ.get("FFAST_FRAME_INDEX_DIR", "~/.ffast/frame-index"))
PROGRESS_EVERY = 50_000
READ_BATCH = 1_000
MEMORY_SLOTS = 8


class FrameIndexError(ValueError):
    """A broken file; the message names the frame and the line."""


class FrameIndexCancelled(Exception):
    """The scan, or a caller's wait for it, was cancelled."""


def is_indexable(path):
    """Only .xyz and .extxyz; other formats keep their ASE reader."""
    return str(path).lower().endswith(INDEXED_SUFFIXES)


class FrameIndex:
    """`starts` has N+1 entries; the last is where the last frame ends."""

    def __init__(self, path, starts, size, mtime_ns):
        self.path = str(path)
        self.starts = np.asarray(starts, dtype=np.int64)
        self.size = int(size)
        self.mtime_ns = int(mtime_ns)

    def __len__(self):
        return len(self.starts) - 1

    def read(self, start=0, stop=None, step=1):
        import ase.io
        picked = range(*slice(start, stop, step).indices(len(self)))
        out = []
        with open(self.path, "rb") as f:
            for b in range(0, len(picked), READ_BATCH):
                chunk = picked[b:b + READ_BATCH]
                if step == 1:
                    f.seek(self.starts[chunk[0]])
                    data = f.read(int(self.starts[chunk[-1] + 1] - self.starts[chunk[0]]))
                else:
                    parts = []
                    for i in chunk:
                        f.seek(self.starts[i])
                        parts.append(f.read(int(self.starts[i + 1] - self.starts[i])))
                    data = b"".join(parts)
                if not data.endswith(b"\n"):
                    data += b"\n"
                out.extend(ase.io.read(io.StringIO(data.decode()), index=":", format="extxyz"))
        return out

    def read_one(self, i):
        n = len(self)
        if i < 0:
            i += n
        if not 0 <= i < n:
            raise IndexError(f"Frame {i} out of range {n}")
        return self.read(i, i + 1)[0]


def _more_frames_follow(f):
    """Whether anything but blank lines is left (reads until the first such line)."""
    for line in f:
        if line.strip():
            return True
    return False


def scan(path, *, progress=None, should_stop=None):
    """One pass over the file. A blank line between frames is an error (ASE
    would silently drop the rest); trailing blank lines are fine."""
    st = os.stat(path)
    starts = []
    line_no = 0
    with open(path, "rb", buffering=1 << 20) as f:
        pos = 0
        while True:
            line = f.readline()
            if not line:
                break
            line_no += 1
            text = line.strip()
            if not text:
                if _more_frames_follow(f):
                    raise FrameIndexError(
                        f"Frame {len(starts) + 1:,} (line {line_no:,}) is not a valid frame: "
                        f"expected an atom count, found {text.decode(errors='replace')!r}.")
                break
            try:
                natoms = int(text)
            except ValueError:
                raise FrameIndexError(
                    f"Frame {len(starts) + 1:,} (line {line_no:,}) is not a valid frame: "
                    f"expected an atom count, found {text.decode(errors='replace')!r}.") from None
            header = line_no
            starts.append(pos)
            got = -1
            for _ in range(natoms + 1):
                if not f.readline():
                    raise FrameIndexError(
                        f"Frame {len(starts):,} (line {header:,}) ends early: it says "
                        f"{natoms:,} atoms, but the file ends after {max(got, 0):,} of them.")
                got += 1
                line_no += 1
            pos = f.tell()
            if progress and len(starts) % PROGRESS_EVERY == 0:
                if should_stop and should_stop():
                    raise FrameIndexCancelled(path)
                progress(len(starts))
    starts.append(pos)
    return FrameIndex(path, starts, st.st_size, st.st_mtime_ns)


def cache_file(path):
    key = hashlib.sha1(os.path.abspath(path).encode()).hexdigest()
    return Path(CACHE_DIR).expanduser() / f"{key}.npz"


def load_cached(path):
    try:
        st = os.stat(path)
        with np.load(cache_file(path)) as z:
            if int(z["size"]) != st.st_size or int(z["mtime_ns"]) != st.st_mtime_ns:
                return None
            return FrameIndex(path, z["starts"], z["size"], z["mtime_ns"])
    except Exception:
        return None


def save_cached(index):
    """A failed save is a warning, not an error: the index still works."""
    try:
        target = cache_file(index.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp.npz")
        np.savez(tmp, starts=index.starts, size=index.size, mtime_ns=index.mtime_ns)
        os.replace(tmp, target)
    except Exception as exc:
        logger.warning("Could not save the frame index of %s: %s", index.path, exc, extra={"popup": False})


_lock = threading.Lock()
_memory = {}
_builds = {}


def clear_memory_cache():
    with _lock:
        _memory.clear()


class _Build:
    """One running scan shared by every caller asking for the same file."""

    def __init__(self):
        self.done = threading.Event()
        self.waiters = []  # (progress, cancel)
        self.result = None
        self.error = None
        self.dead = False

    def should_stop(self):
        with _lock:
            if not any(c is None or not c.is_set() for _, c in self.waiters):
                self.dead = True   # a later caller must not join a scan that is stopping
            return self.dead

    def progress(self, n):
        with _lock:
            listeners = [p for p, c in self.waiters if p and not (c and c.is_set())]
        for p in listeners:
            p(n)


def _fresh(index, st):
    return index.size == st.st_size and index.mtime_ns == st.st_mtime_ns


def get_frame_index(path, *, progress=None, cancel=None):
    """Memory, then disk, then join or start a scan in its own thread. A caller
    that cancels stops waiting at once; the scan stops only when none is left."""
    path = os.path.abspath(path)
    st = os.stat(path)
    with _lock:
        hit = _memory.get(path)
        if hit is not None and _fresh(hit, st):
            return hit
    disk = load_cached(path)
    if disk is not None:
        with _lock:
            _memory[path] = disk
        return disk
    key = (path, st.st_size, st.st_mtime_ns)
    me = (progress, cancel)
    with _lock:
        build = _builds.get(key)
        start = build is None or build.dead
        if start:
            build = _builds[key] = _Build()
        build.waiters.append(me)
    if start:
        def run():
            try:
                index = scan(path, progress=build.progress, should_stop=build.should_stop)
                save_cached(index)
                with _lock:
                    _memory[path] = index
                    while len(_memory) > MEMORY_SLOTS:
                        _memory.pop(next(iter(_memory)))
                build.result = index
            except BaseException as exc:
                build.error = exc
            finally:
                with _lock:
                    if _builds.get(key) is build:
                        del _builds[key]
                build.done.set()
        threading.Thread(target=run, name="frame-index", daemon=True).start()
    try:
        while not build.done.wait(0.05):
            if cancel is not None and cancel.is_set():
                raise FrameIndexCancelled(path)
        if cancel is not None and cancel.is_set():
            raise FrameIndexCancelled(path)
        if build.error is not None:
            raise build.error
        return build.result
    finally:
        with _lock:
            if me in build.waiters:
                build.waiters.remove(me)


def read_first_frame(path):
    """Frame 0 only, without scanning the file."""
    if not is_indexable(path):
        from ffast.io.xyz import read_ase_or_explain
        return read_ase_or_explain(path, index=0)
    import ase.io
    with open(path, "rb") as f:
        line = f.readline()
        while line and not line.strip():
            line = f.readline()
        try:
            natoms = int(line.strip())
        except ValueError:
            raise FrameIndexError(
                f"Frame 1 (line 1) is not a valid frame: expected an atom count, "
                f"found {line.strip().decode(errors='replace')!r}.") from None
        lines = [line] + [f.readline() for _ in range(natoms + 1)]
    data = b"".join(lines)
    if not data.endswith(b"\n"):
        data += b"\n"
    return ase.io.read(io.StringIO(data.decode()), index=0, format="extxyz")


def read_every(path, step):
    """Every `step`-th frame."""
    if is_indexable(path):
        return get_frame_index(path).read(0, None, step)
    import ase.io
    return ase.io.read(path, index=slice(0, None, step))

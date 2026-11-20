"""The strict check: prove a parsed cache or a bundle against stored digests
(ADR 0111).

``verify(path)`` takes a source ``.xyz``/``.extxyz`` file (its finished parsed
cache is found by path) or a bundle's ``dataset.ffast.json``. It reads the
source and every batch, and compares

- the source: it is parsed again, window by window and without writing
  anything, and the chunk digest of every column is compared with the chunk
  sidecar the first scan kept (this covers the columns the cache did not
  keep); and
- the values of every batch with the chunk digests of the sidecar.

The report says, part by part, what was verified, what differed and what
could not be checked. A parsed cache is repaired by deletion: a differing
batch is deleted (the next load parses those frames again), a differing
source deletes the whole cache of that identity. A bundle is never changed.
"""
from __future__ import annotations

import json
import logging
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ffast.cache.fingerprint import CHUNK_ROWS, ChunkHasher
from ffast.readers import bundle
from ffast.readers.contract import ReaderError

log = logging.getLogger("FFAST")

VERIFIED, DIFFERS, NOT_CHECKED = "verified", "differs", "not checked"
LISTED = 10                    # byte ranges or batches named before "and N more"
_DAMAGED = (OSError, ValueError, EOFError, KeyError, zipfile.BadZipFile)


@dataclass
class Part:
    name: str
    status: str
    detail: str = ""


@dataclass
class Report:
    target: str
    kind: str = "cache"                      # "cache" or "bundle"
    parts: list = field(default_factory=list)
    changed: list = field(default_factory=list)      # files deleted
    note: str = ""                           # shown after the parts (a title detail)

    @property
    def mismatches(self):
        return sum(1 for p in self.parts if p.status == DIFFERS)

    @property
    def exit_code(self):
        if any(p.status == DIFFERS for p in self.parts):
            return 1
        return 0 if any(p.status == VERIFIED for p in self.parts) else 2


def _and_more(items, shown=LISTED):
    items = list(items)
    text = ", ".join(items[:shown])
    return text + (f" and {len(items) - shown} more" if len(items) > shown else "")


# -- the source -----------------------------------------------------------------

STALE = ("the file's size, modification time or inode differ from the cache's, "
         "so it was read again and compared anyway")


def _frame_ranges(chunks_bad, per_atom, offsets, n_frames):
    """The frames that hold the rows of the differing chunks, as merged
    ``(first, last)`` pairs."""
    spans = []
    for i in sorted(chunks_bad):
        lo, hi = i * CHUNK_ROWS, (i + 1) * CHUNK_ROWS
        if per_atom:
            total = int(offsets[-1])
            hi = min(hi, total)
            first = int(np.searchsorted(offsets, lo, side="right")) - 1
            last = int(np.searchsorted(offsets, max(hi - 1, lo), side="right")) - 1
        else:
            first, last = lo, min(hi, n_frames) - 1
        spans.append((max(first, 0), min(max(last, 0), max(n_frames - 1, 0))))
    merged = []
    for first, last in sorted(spans):
        if merged and first <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    return merged


def _check_source(path, info, chunks, n_frames, progress, stale=False):
    """The part for a source file: parse it again, writing nothing, and compare
    the chunk digest of every column with ``chunks``."""
    from ffast.io.extxyz_arrays import NotFast
    from ffast.readers import text
    from ffast.readers.indexed import IndexedStore
    from ffast.readers.text import per_atom
    from ffast.readers.xyz_reader import XyzReader

    note = ("; " + STALE) if stale else ""
    if not text.fast_parser_usable():
        return Part("source", NOT_CHECKED, "the fast XYZ parser is needed to read the source again")
    try:
        store = IndexedStore(XyzReader, str(path), XyzReader(str(path)).contents(), [], [], [], [],
                             workers=None, scan_source=True, verify=True, progress=progress)
    except NotFast:
        return Part("source", NOT_CHECKED, "the file needs the Python scanner, which verify does not use" + note)
    except (ReaderError, ValueError, OSError) as exc:
        return Part("source", DIFFERS, f"it can no longer be read as it was parsed ({exc})" + note)
    found = len(store.counts)
    if found != n_frames:
        return Part("source", DIFFERS, f"it has {found:,} frames; the cache has {n_frames:,}" + note)
    wrong, named = {}, []
    if set(store.info) != set(info):
        gone, new = sorted(set(info) - set(store.info)), sorted(set(store.info) - set(info))
        what = ([f"no longer has {_and_more(gone, 3)}"] if gone else []) + \
               ([f"now has {_and_more(new, 3)}"] if new else [])
        return Part("source", DIFFERS, "its columns differ: it " + " and ".join(what) + note)
    for key, facts in info.items():
        member = f"{np.dtype(facts['dtype']).str}|{key}"
        again = store.chunk_digests.get((key, np.dtype(facts["dtype"]).str))
        expected = chunks.get(member)
        if again is None or expected is None:
            named.append(f"{key} (not comparable)")
            continue
        bad = {i for i in range(max(len(again), len(expected)))
               if i >= len(again) or i >= len(expected) or again[i] != bytes(expected[i])}
        if bad:
            wrong[key] = _frame_ranges(bad, per_atom(key), store.offsets, found)
    if not wrong and not named:
        return Part("source", VERIFIED, f"parsed again: {found:,} frames, {len(info)} columns{note}")
    spans = {}
    for key, ranges in wrong.items():
        for first, last in ranges:
            spans.setdefault((first, last), []).append(_short(key))
    merged = []
    for (first, last), keys in sorted(spans.items()):
        merged.append(f"frames {first:,}-{last:,} ({', '.join(dict.fromkeys(keys))})")
    return Part("source", DIFFERS, "values differ at " + _and_more(merged + named) + note)


# -- the batches ----------------------------------------------------------------

def _read_columns(file):
    """``(columns, omitted)`` of a batch file: every column it holds, read in
    place, with ``numbers`` restored to its parsed type; ``omitted`` names the
    columns the cache left out on purpose. Raises for a damaged file."""
    from ffast.readers.indexed import _member_rows, _restore
    part, omitted = {}, set()
    with zipfile.ZipFile(file) as archive, open(file, "rb") as f:
        names = archive.namelist()
        if not names:
            raise ValueError("it is empty")
        table = None
        for name in names:
            key = name[:-4]
            if key == "__omitted__":
                with archive.open(name) as member:
                    omitted = {str(k) for k in np.lib.format.read_array(member, allow_pickle=False)}
            elif key == "__restore__":
                with archive.open(name) as member:
                    table = np.lib.format.read_array(member, allow_pickle=False)
            else:
                value = _member_rows(archive, f, key, 0, None)
                if value is None:
                    raise ValueError(f"the column {key} cannot be read in place")
                part[key] = value
    if table is not None:
        _restore(part, table)
    return part, omitted


class _Column:
    """One column's chunk hasher and what it has been compared with."""

    def __init__(self, key, info, expected):
        self.key = key
        self.hasher = ChunkHasher(info["tail"], info["dtype"])
        self.dtype = info["dtype"]
        self.tail = info["tail"]
        self.expected = expected          # (n_chunks, 32) uint8, or None
        self.compared = 0
        self.bad = set()                  # indexes of chunks that differ
        self.spans = []                   # (batch index, first row, end row)
        self.stopped = None               # None, "not cached" or "damaged"
        self.rows = 0


def _compare_new(col):
    done = col.hasher.digests()
    while col.compared < len(done):
        i = col.compared
        if col.expected is None or i >= len(col.expected) or bytes(col.expected[i]) != done[i]:
            col.bad.add(i)
        col.compared += 1


def _check_batches(entries, info, chunks, progress, threads=1):
    """Hash every column of every batch in order and compare with ``chunks``.

    ``entries`` is ``[(a, b, file, bytes or None)]`` in frame order. Returns
    ``(parts, bad)``: the report parts and ``{(a, b): [column, ...]}`` for
    each batch to blame (a batch overlapping a differing chunk, or one that
    cannot be read). A chunk is the unit of the digests, so a differing chunk
    names every batch that has rows in it."""
    from ffast.readers.text import per_atom

    cols = {}
    for key, facts in info.items():
        member = f"{np.dtype(facts['dtype']).str}|{key}"
        cols[key] = _Column(key, facts, chunks[member] if member in chunks else None)
    unreadable, missing = {}, []
    from concurrent.futures import ThreadPoolExecutor

    def read(entry):
        _, _, file, nbytes = entry
        if nbytes is not None and Path(file).stat().st_size != nbytes:
            raise ValueError(f"it has {Path(file).stat().st_size:,} bytes, not the {nbytes:,} it was written with")
        return _read_columns(file)

    # The next batch is read while this one is hashed, and the columns of a
    # batch are hashed side by side (hashlib lets go of the GIL): two batches
    # are in memory at a time.
    pool = ThreadPoolExecutor(max_workers=max(2, threads))
    try:
        _run_batches(entries, cols, unreadable, missing, pool, read, progress, per_atom)
    finally:
        pool.shutdown(wait=True)
    return _blame(entries, cols, unreadable, missing)


def _run_batches(entries, cols, unreadable, missing, pool, read, progress, per_atom):
    ahead = pool.submit(read, entries[0]) if entries else None
    for index, (a, b, file, nbytes) in enumerate(entries):
        current, ahead = ahead, (pool.submit(read, entries[index + 1]) if index + 1 < len(entries) else None)
        try:
            part, omitted = current.result()
        except FileNotFoundError:
            missing.append((a, b))
            for col in cols.values():
                col.stopped = col.stopped or "not cached"
            continue
        except _DAMAGED as exc:
            unreadable[(a, b)] = str(exc) or type(exc).__name__
            for col in cols.values():
                col.stopped = col.stopped or "damaged"
            continue
        atoms = int(part["numbers"].shape[0]) if "numbers" in part else None

        def feed(item, index=index, a=a, b=b, part=part, omitted=omitted, atoms=atoms):
            key, col = item
            if col.stopped:
                return
            value = part.get(key)
            if value is None:
                if key in omitted:
                    col.stopped = "not cached"
                    return
                if col.dtype.kind not in "fc":
                    unreadable[(a, b)] = f"the column {key} is missing"
                    col.stopped = "damaged"
                    return
                rows = (atoms if per_atom(key) else b - a)
                if rows is None:
                    col.stopped = "not cached"
                    return
                value = np.full((rows, *col.tail), np.nan, dtype=np.float64)
            col.spans.append((index, col.rows, col.rows + value.shape[0]))
            col.rows += value.shape[0]
            col.hasher.update(value.astype(col.dtype, copy=False))
            _compare_new(col)

        list(pool.map(feed, list(cols.items())))
        if progress:
            progress(index + 1, len(entries))


def _blame(entries, cols, unreadable, missing):
    bad = {}
    not_cached = []
    for key, col in cols.items():
        if col.stopped == "not cached":
            not_cached.append(key)
            continue
        if col.stopped == "damaged":
            continue
        pending = col.hasher.all_digests()[col.compared:]
        for k, digest in enumerate(pending):
            i = col.compared + k
            if col.expected is None or i >= len(col.expected) or bytes(col.expected[i]) != digest:
                col.bad.add(i)
        total = len(col.hasher.all_digests())
        if col.expected is not None and len(col.expected) != total:
            col.bad.add(total)
        for i in sorted(col.bad):
            lo, hi = i * CHUNK_ROWS, (i + 1) * CHUNK_ROWS
            for index, first, end in col.spans:
                if first < hi and end > lo:
                    a, b, _, _ = entries[index]
                    bad.setdefault((a, b), []).append(key)
    for (a, b), reason in unreadable.items():
        bad.setdefault((a, b), []).append(f"cannot be read: {reason}")
    for a, b in missing:
        bad.setdefault((a, b), []).append("__missing__")
    return cols, bad, not_cached


def _batch_part(entries, info, chunks, progress, *, missing_is_difference, threads=1):
    """The batch part (and the 'not cached' part) of a check."""
    cols, bad, not_cached = _check_batches(entries, info, chunks, progress, threads)
    files = {(a, b): file for a, b, file, _ in entries}
    gone = {k for k, v in bad.items() if v == ["__missing__"]}
    if not missing_is_difference:
        for k in gone:
            bad.pop(k)
    total = len(entries)
    parts = []
    named = []
    for (a, b), what in sorted(bad.items()):
        if what == ["__missing__"]:
            named.append(f"{Path(files[(a, b)]).name}: missing")
        elif what[0].startswith("cannot be read"):
            named.append(f"{Path(files[(a, b)]).name}: {what[0]}")
        else:
            named.append(f"{Path(files[(a, b)]).name}: {', '.join(_short(w) for w in dict.fromkeys(what))} differ")
    checked = total - len(gone) if not missing_is_difference else total
    if bad:
        parts.append(Part("batches", DIFFERS, f"{checked - len(bad)} of {checked} verified; "
                          + _and_more(named)))
    elif checked:
        parts.append(Part("batches", VERIFIED, f"{checked} of {checked}"))
    else:
        parts.append(Part("batches", NOT_CHECKED, "no batch is cached"))
    if not_cached or (gone and not missing_is_difference):
        what = []
        if not_cached:
            what.append("not cached: " + _and_more(_short(k) for k in not_cached))
        if gone and not missing_is_difference:
            what.append(f"{len(gone)} batch files are not cached")
        parts.append(Part("not cached", NOT_CHECKED, "; ".join(what)))
    return parts, bad


def _short(key):
    return key.split(":", 1)[0] if key.startswith(("forces:", "energy:", "frame:", "atom:")) else key


# -- the two targets --------------------------------------------------------------

def _sidecar(path):
    with np.load(path, allow_pickle=False) as found:
        return {name: found[name] for name in found.files}


def verify(path, *, root=None, threads=None, progress=None) -> Report:
    """Check the source file or bundle at ``path`` (ADR 0111). ``root`` is the
    parsed-cache root (default: the usual one). ``progress(stage, done,
    total)`` is called while it reads."""
    path = Path(path)
    if not threads:
        from ffast.readers.text import fast_threads
        threads = fast_threads()
    if path.name == bundle.DESCRIPTION or path.is_dir():
        return _verify_bundle(path, threads, progress)
    return _verify_cache(path, root, threads, progress)


def _stage(progress, name):
    return None if progress is None else (lambda done, total: progress(name, done, total))


def _verify_bundle(path, threads, progress):
    folder = bundle.bundle_folder(path)
    report = Report(str(folder), "bundle")
    fast_problem = None
    try:
        description = bundle.read_description(folder)
    except ReaderError as exc:
        fast_problem = str(exc)
        try:
            description = json.loads((folder / bundle.DESCRIPTION).read_text())
            if not isinstance(description, dict):
                raise ValueError("it is not a description")
        except (OSError, ValueError) as err:
            report.parts.append(Part("description", NOT_CHECKED, f"{fast_problem} ({err})"))
            return report
    report.note = f"schema {description.get('schema', '?')}"
    if fast_problem:
        report.parts.append(Part("fast check", DIFFERS, fast_problem))
    try:
        chunks = _sidecar(folder / bundle.CHUNKS)
    except (OSError, ValueError, EOFError, zipfile.BadZipFile) as exc:
        report.parts.append(Part("chunks", DIFFERS if (folder / bundle.CHUNKS).exists() else NOT_CHECKED,
                                 f"{bundle.CHUNKS} cannot be read ({exc})"))
        return report
    try:
        info = {key: {"dtype": np.dtype(f["dtype"]), "tail": tuple(f["tail"])}
                for key, f in description["columns"].items()}
        entries = [(int(r["start"]), int(r["stop"]), folder / r["file"], int(r["bytes"]))
                   for r in description["ranges"]]
        record = description["source"]
    except (KeyError, TypeError, ValueError) as exc:
        report.parts.append(Part("description", NOT_CHECKED, f"{bundle.DESCRIPTION} is damaged ({exc})"))
        return report
    n_frames = int(description.get("n_frames", entries[-1][1] if entries else 0))
    report.parts.extend(_source_part(chunks, record, folder, info, n_frames, progress))
    parts, _ = _batch_part(entries, info, chunks, _stage(progress, "batches"), missing_is_difference=True,
                           threads=threads)
    report.parts.extend(parts)
    return report


def _source_part(chunks, record, folder, info, n_frames, progress):
    source = bundle.find_source(folder, record)
    if source is None:
        places = " and ".join(str(p) for p in bundle.places_looked(folder, record))
        return [Part("source", NOT_CHECKED, f"not found (looked in {places}); not verified")]
    return [_check_source(source, info, chunks, n_frames, _stage(progress, "source"))]


def _find_cache(root, folder, absolute, current):
    """``(identity, stale, why)``: the finished cache of the file. ``stale`` is
    True when it was made for the file in another state (size, modification
    time or inode), which the check reads again and compares anyway. ``why``
    says what is missing when there is none."""
    from ffast.io import cache_space
    if (folder / f"{current}.json").exists():
        return current, False, None
    if (folder / f"{current}.progress.json").exists():
        return None, False, "the scan is not finished; load the file fully first"
    real = os.path.realpath(absolute)
    mine = [e for e in cache_space.entries(root)
            if e.kind == "batches" and e.path == real and (folder / f"{e.key}.json").exists()]
    if mine:
        return max(mine, key=lambda e: e.last_used).key, True, None
    return None, False, "no finished cache for the file"


def _verify_cache(path, root, threads, progress):
    from ffast.io import cache_space, frame_cache
    from ffast.readers.indexed import IndexedStore, file_state
    from ffast.readers.xyz_reader import XyzReader

    report = Report(str(path), "cache")
    if not path.is_file():
        report.parts.append(Part("source", NOT_CHECKED, f"{path} is not a file"))
        return report
    root = Path(root) if root is not None else frame_cache._folder()
    folder = root / "batches"
    absolute = os.path.abspath(path)
    current = IndexedStore.identity_of(XyzReader, absolute, file_state(absolute))
    identity, stale, why = _find_cache(root, folder, absolute, current)
    if identity is None:
        report.parts.append(Part("cache", NOT_CHECKED, why))
        return report
    try:
        manifest = json.loads((folder / f"{identity}.json").read_text())
        info = {key: {"dtype": np.dtype(f["dtype"]), "tail": tuple(f["tail"])}
                for key, f in manifest["columns"].items()}
        ranges = [tuple(map(int, r)) for r in manifest["ranges"]]
        sidecar = manifest["chunks"]
    except (OSError, ValueError, KeyError, TypeError):
        report.parts.append(Part("cache", NOT_CHECKED,
                                 "the cache has no chunk digests to check against (made before ADR 0107)"))
        return report
    try:
        chunks = _sidecar(folder / sidecar)
    except (OSError, ValueError, EOFError, zipfile.BadZipFile) as exc:
        report.parts.append(Part("cache", NOT_CHECKED, f"the chunk digests cannot be read ({exc})"))
        return report
    source_part = _check_source(absolute, info, chunks, ranges[-1][1], _stage(progress, "source"), stale)
    report.parts.append(source_part)
    entries = [(a, b, folder / f"{identity}-{a}-{b}.npz", None) for a, b in ranges]
    parts, bad = _batch_part(entries, info, chunks, _stage(progress, "batches"), missing_is_difference=False,
                           threads=threads)
    if source_part.status == VERIFIED:
        for part in parts:
            if part.name == "not cached":
                part.detail += "; covered by the source part"
    report.parts.extend(parts)
    if source_part.status == DIFFERS or bad:
        _repair(report, root, folder, identity, absolute, source_part.status == DIFFERS, bad)
    return report


def _repair(report, root, folder, identity, source, source_differs, bad):
    """Delete what differs from a parsed cache (never a bundle)."""
    from ffast.io import cache_space
    if cache_space.is_open(folder / f"{identity}{cache_space.OPEN}"):
        report.parts.append(Part("repair", NOT_CHECKED, "in use by another FFAST; nothing deleted"))
        return
    try:
        if source_differs:
            report.changed.extend(str(p) for p in cache_space.delete_identity(root, identity))
            _forget_tables(source, report)
            return
        doomed = [folder / f"{identity}-{a}-{b}.npz" for (a, b) in sorted(bad)]
        with cache_space.space_lock(root):
            for file in doomed:
                if file.exists():
                    file.unlink()
                    report.changed.append(str(file))
    except cache_space.InUse:
        report.parts.append(Part("repair", NOT_CHECKED, "in use by another FFAST; nothing deleted"))


def _forget_tables(source, report):
    """The frame lists saved for a file that turned out to have changed."""
    from ffast.io import frame_index
    from ffast.readers import text
    from ffast.readers.xyz_reader import XyzReader
    for file in (frame_index.cache_file(source), text._table_file(XyzReader, source),
                 text._energies_file(XyzReader, source)):
        try:
            if file.exists():
                file.unlink()
                report.changed.append(str(file))
        except OSError:
            pass

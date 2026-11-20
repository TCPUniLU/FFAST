"""The strict check: prove a parsed cache or a bundle against stored digests
(ADR 0111).

``verify(path)`` takes a source ``.xyz``/``.extxyz`` file (its finished parsed
cache is found by path and file state) or a bundle's ``dataset.ffast.json``.
It reads the source once and every batch once, and compares

- the source bytes with the digests of the scan's windows (``source|`` arrays
  of the chunk sidecar), and
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
from ffast.cache.source_digest import file_range_digests
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

def _check_source(path, ranges, digests, threads, progress):
    """The part for a source file against its recorded windows."""
    ranges = np.asarray(ranges, dtype=np.int64).reshape(-1, 2)
    size = os.path.getsize(path)
    if len(ranges) == 0 or int(ranges[-1, 1]) != size:
        end = int(ranges[-1, 1]) if len(ranges) else 0
        return Part("source", DIFFERS, f"{path} has {size:,} bytes; {end:,} were parsed")
    got = file_range_digests(path, ranges.tolist(), threads)
    wrong = [(int(a), int(b)) for (a, b), g, want in zip(ranges.tolist(), got, digests)
             if g != bytes(want)]
    if not wrong:
        return Part("source", VERIFIED, f"{len(ranges)} byte ranges, {size:,} bytes")
    merged = []
    for a, b in wrong:
        if merged and merged[-1][1] == a:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    named = [f"{a:,}-{b:,}" for a, b in merged]
    return Part("source", DIFFERS, f"bytes differ at {_and_more(named)}")


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


def _check_batches(entries, info, chunks, progress):
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
    for index, (a, b, file, nbytes) in enumerate(entries):
        try:
            if nbytes is not None and Path(file).stat().st_size != nbytes:
                raise ValueError(f"it has {Path(file).stat().st_size:,} bytes, not the {nbytes:,} it was written with")
            part, omitted = _read_columns(file)
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
        for key, col in cols.items():
            if col.stopped:
                continue
            value = part.get(key)
            if value is None:
                if key in omitted:
                    col.stopped = "not cached"
                    continue
                if col.dtype.kind not in "fc":
                    unreadable[(a, b)] = f"the column {key} is missing"
                    col.stopped = "damaged"
                    continue
                rows = (atoms if per_atom(key) else b - a)
                if rows is None:
                    col.stopped = "not cached"
                    continue
                value = np.full((rows, *col.tail), np.nan, dtype=np.float64)
            col.spans.append((index, col.rows, col.rows + value.shape[0]))
            col.rows += value.shape[0]
            col.hasher.update(value.astype(col.dtype, copy=False))
            _compare_new(col)
        if progress:
            progress(index + 1, len(entries))
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


def _batch_part(entries, info, chunks, progress, *, missing_is_difference):
    """The batch part (and the 'not cached' part) of a check."""
    cols, bad, not_cached = _check_batches(entries, info, chunks, progress)
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


def _source_arrays(chunks):
    if "source|ranges" in chunks and "source|digests" in chunks:
        ranges, digests = chunks["source|ranges"], chunks["source|digests"]
        if len(ranges) == len(digests) and len(ranges):
            return ranges, digests
    return None


NO_DIGESTS = "source digests were not recorded (a cache made before ADR 0111, or with an older ffast-xyz)"


def verify(path, *, root=None, threads=None, progress=None) -> Report:
    """Check the source file or bundle at ``path`` (ADR 0111). ``root`` is the
    parsed-cache root (default: the usual one). ``progress(stage, done,
    total)`` is called while it reads."""
    path = Path(path)
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
    report.parts.extend(_source_part(chunks, record, folder, threads, progress))
    parts, _ = _batch_part(entries, info, chunks, _stage(progress, "batches"), missing_is_difference=True)
    report.parts.extend(parts)
    return report


def _source_part(chunks, record, folder, threads, progress):
    recorded = _source_arrays(chunks)
    if recorded is None:
        return [Part("source", NOT_CHECKED, NO_DIGESTS)]
    source = bundle.find_source(folder, record)
    if source is None:
        places = " and ".join(str(p) for p in bundle.places_looked(folder, record))
        return [Part("source", NOT_CHECKED, f"not found (looked in {places}); not verified")]
    return [_check_source(source, *recorded, threads, _stage(progress, "source"))]


def _identity(reader, path, state):
    """The name of the full parsed cache of ``path``: the formula of
    ``IndexedStore.__init__`` for a full, unsampled load (a test pins them)."""
    import hashlib
    from ffast.cache.fingerprint import SCHEME
    h = hashlib.sha256(repr((1, reader.name, getattr(reader, "cache_version", 1), path, state,
                             SCHEME)).encode())
    h.update(b"all")
    return h.hexdigest()


def _verify_cache(path, root, threads, progress):
    from ffast.io import cache_space, frame_cache
    from ffast.readers.indexed import file_state
    from ffast.readers.xyz_reader import XyzReader

    report = Report(str(path), "cache")
    if not path.is_file():
        report.parts.append(Part("source", NOT_CHECKED, f"{path} is not a file"))
        return report
    root = Path(root) if root is not None else frame_cache._folder()
    folder = root / "batches"
    absolute = os.path.abspath(path)
    identity = _identity(XyzReader, absolute, file_state(absolute))
    manifest_file = folder / f"{identity}.json"
    try:
        manifest = json.loads(manifest_file.read_text())
        info = {key: {"dtype": np.dtype(f["dtype"]), "tail": tuple(f["tail"])}
                for key, f in manifest["columns"].items()}
        ranges = [tuple(map(int, r)) for r in manifest["ranges"]]
        sidecar = manifest["chunks"]
    except FileNotFoundError:
        if (folder / f"{identity}.progress.json").exists():
            why = "the scan is not finished; load the file fully first"
        else:
            why = "no finished cache for the file as it is now"
        report.parts.append(Part("cache", NOT_CHECKED, why))
        return report
    except (OSError, ValueError, KeyError, TypeError):
        report.parts.append(Part("cache", NOT_CHECKED,
                                 "the cache has no chunk digests to check against (made before ADR 0107)"))
        return report
    try:
        chunks = _sidecar(folder / sidecar)
    except (OSError, ValueError, EOFError, zipfile.BadZipFile) as exc:
        report.parts.append(Part("cache", NOT_CHECKED, f"the chunk digests cannot be read ({exc})"))
        return report
    recorded = _source_arrays(chunks)
    source_part = (Part("source", NOT_CHECKED, NO_DIGESTS) if recorded is None
                   else _check_source(absolute, *recorded, threads, _stage(progress, "source")))
    report.parts.append(source_part)
    entries = [(a, b, folder / f"{identity}-{a}-{b}.npz", None) for a, b in ranges]
    parts, bad = _batch_part(entries, info, chunks, _stage(progress, "batches"), missing_is_difference=False)
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

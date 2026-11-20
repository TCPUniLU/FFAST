"""The portable bundle of a parsed dataset: its folder format (ADR 0110).

A bundle is a folder of plain files that holds every parsed column of one
``.xyz`` file, so the dataset opens on another machine without the source::

    so3lr.ffast/
      dataset.ffast.json   description; written last
      counts.npy           atoms per frame
      source-offsets.npy   byte start of each frame in the source, plus the end
      chunks.npz           chunk digests of every column (ADR 0107)
      batches/0-4096.npz   one uncompressed .npz per frame range

This module holds the format: the source probe, the description and its
checks. Writing a bundle and opening one are further down.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import numpy as np

from ffast.cache.fingerprint import SCHEME, column_digest_of_chunks
from ffast.readers.contract import ReaderError

SCHEMA = 1
DESCRIPTION = "dataset.ffast.json"
COUNTS = "counts.npy"
OFFSETS = "source-offsets.npy"
CHUNKS = "chunks.npz"
BATCHES = "batches"
SLICE_BYTES = 1 << 20
SLICES = 8                 # first, last and six evenly spaced
WHOLE_FILE_BELOW = 8 * (1 << 20)


# -- the source probe ---------------------------------------------------------

def _slice_offsets(size):
    """Where the probed slices start, and how long each is."""
    if size < WHOLE_FILE_BELOW:
        return [0], size
    last = size - SLICE_BYTES
    return [last * i // (SLICES - 1) for i in range(SLICES)], SLICE_BYTES


def _hash_slices(path, offsets, length):
    out = []
    with open(path, "rb") as f:
        for offset in offsets:
            f.seek(offset)
            out.append(hashlib.sha256(f.read(length)).hexdigest())
    return out


def source_probe(path):
    """The quick identity of a source file: name, original path, size,
    modification time and the SHA-256 of eight 1 MiB slices (first, last and
    six evenly spaced; the whole file as one slice when it is under 8 MiB)."""
    path = os.path.abspath(path)
    stat = os.stat(path)
    offsets, length = _slice_offsets(stat.st_size)
    return {
        "name": os.path.basename(path),
        "original_path": path,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "slice_bytes": length,
        "offsets": offsets,
        "sha256": _hash_slices(path, offsets, length),
    }


def probe_matches(path, record):
    """Whether the file at ``path`` is the one ``record`` describes: the size
    first (no read), then every slice's hash. A quick check, not proof."""
    try:
        if os.path.getsize(path) != record["size"]:
            return False
        return _hash_slices(path, record["offsets"], record["slice_bytes"]) == list(record["sha256"])
    except (OSError, KeyError, TypeError):
        return False


def find_source(bundle_dir, record):
    """The source file of a bundle: the original path, else a file of the same
    name beside the bundle folder; the first that matches the probe. None when
    neither does."""
    for candidate in places_looked(bundle_dir, record):
        if candidate.is_file() and probe_matches(candidate, record):
            return candidate
    return None


def places_looked(bundle_dir, record):
    return [Path(record["original_path"]), Path(bundle_dir).resolve().parent / record["name"]]


# -- the description ----------------------------------------------------------

def write_description(folder, description):
    """Write ``dataset.ffast.json`` into ``folder`` (a temporary name, then a
    rename). ``schema`` is set here."""
    folder = Path(folder)
    data = json.dumps({"schema": SCHEMA, **description}, indent=1)
    fd, temp = tempfile.mkstemp(dir=folder, prefix=DESCRIPTION + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, folder / DESCRIPTION)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def bundle_folder(path):
    """The bundle folder for a path that is the folder or its description."""
    path = Path(path)
    return path.parent if path.name == DESCRIPTION or path.is_file() else path


def read_description(path):
    """The checked description of the bundle at ``path`` (its folder or its
    ``dataset.ffast.json``). Fast policy (ADR 0110): the schema is known, the
    scheme matches, every listed batch exists with its recorded size, and the
    chunk digests give the column digests. Reads no batch data. Any failure is
    a ReaderError that names the bundle and what is wrong."""
    folder = bundle_folder(path)
    name = folder.name

    def bad(why):
        return ReaderError(f"The bundle {name} cannot be opened: {why}.")

    try:
        description = json.loads((folder / DESCRIPTION).read_text())
    except FileNotFoundError:
        raise bad(f"{DESCRIPTION} is missing") from None
    except (OSError, ValueError) as exc:
        raise bad(f"{DESCRIPTION} cannot be read ({exc})") from None
    schema = description.get("schema") if isinstance(description, dict) else None
    if not isinstance(schema, int) or isinstance(schema, bool):
        raise bad("it has no schema number")
    if schema > SCHEMA:
        raise ReaderError(f"The bundle {name} was made by a newer FFAST (bundle format {schema}, "
                          f"this FFAST reads up to {SCHEMA}); update FFAST.")
    if description.get("scheme") != SCHEME.decode():
        raise bad(f"it was fingerprinted with {description.get('scheme')!r}, not {SCHEME.decode()!r}")
    try:
        for entry in description["ranges"]:
            file = folder / entry["file"]
            try:
                size = file.stat().st_size
            except OSError:
                raise bad(f"the batch {entry['file']} is missing") from None
            if size != entry["bytes"]:
                raise bad(f"the batch {entry['file']} has {size:,} bytes, not the {entry['bytes']:,} "
                          f"it was written with")
        for fixed in (COUNTS, OFFSETS, CHUNKS):
            if not (folder / fixed).is_file():
                raise bad(f"{fixed} is missing")
        with np.load(folder / CHUNKS, allow_pickle=False) as chunks:
            for key, facts in description["columns"].items():
                for field, dtype in (("digest", facts["dtype"]), ("float_digest", "<f8")):
                    if field not in facts:
                        continue
                    member = f"{np.dtype(dtype).str}|{key}"
                    if member not in chunks.files:
                        raise bad(f"chunks.npz has no digests for {key}")
                    if column_digest_of_chunks(chunks[member]) != facts[field]:
                        raise bad(f"the chunk digests of {key} do not match its column digest")
    except ReaderError:
        raise
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise bad(f"{DESCRIPTION} or its files are damaged ({type(exc).__name__}: {exc})") from None
    return description


# -- export -------------------------------------------------------------------

def _is_complete(file):
    """Whether a cached batch file holds every column (no ``__omitted__``)."""
    import zipfile
    try:
        with zipfile.ZipFile(file) as archive:
            return bool(archive.namelist()) and "__omitted__.npy" not in archive.namelist()
    except (OSError, ValueError, zipfile.BadZipFile):
        return False


def _kept_sidecar(store):
    """The chunk digests the cache kept for ``store`` when they agree with its
    column digests, else None (rebuilt from the data then)."""
    from ffast.readers.indexed import CHUNKS_SUFFIX
    file = store.folder / f"{store.identity}{CHUNKS_SUFFIX}"
    if not store.use_cache:
        return None
    try:
        with np.load(file, allow_pickle=False) as found:
            arrays = {name: found[name] for name in found.files}
        for key, info in store.info.items():
            for field, dtype in (("digest", info["dtype"]), ("float_digest", np.float64)):
                if column_digest_of_chunks(arrays[f"{np.dtype(dtype).str}|{key}"]) != info[field]:
                    return None
        return arrays
    except (OSError, ValueError, KeyError, EOFError):
        return None


def _estimate(store):
    """``(bytes the bundle will take, the ranges whose cached batch is complete)``."""
    atom_bytes = (32 + 24 * len(store.contents.get("forces") or [])
                  + 8 * len(store.contents.get("atom_fields") or []))
    frame_bytes = 80 + 8 * (len(store.contents.get("energies") or [])
                            + len(store.contents.get("frame_fields") or []))
    total, complete = 0, set()
    for a, b in store.ranges:
        file = store._cache_path(a, b) if store.use_cache else None
        if file is not None and file.is_file() and _is_complete(file):
            total += file.stat().st_size
            complete.add((a, b))
        else:
            total += int(store.counts[a:b].sum()) * atom_bytes + (b - a) * frame_bytes
    return total, complete


def export(path, target, *, force=False, progress=None):
    """Write the parsed dataset of the XYZ file ``path`` as a bundle folder
    ``target`` (ADR 0110). The bundle holds every column the reader found, for
    every frame: cached batches that are complete are copied, the rest are
    parsed from the source. It is written beside ``target`` and renamed, so a
    failure or a cancel leaves no ``target``. Returns the description."""
    import shutil

    from ffast.io import frame_index
    from ffast.metrics import stop
    from ffast.readers import text
    from ffast.readers.indexed import IndexedArrays, _compact, _write_batch
    from ffast.readers.xyz_reader import XyzReader

    path, target = Path(path), Path(target).absolute()
    name = path.name
    if not path.is_file():
        raise ReaderError(f"{name} is not a file")
    if not frame_index.is_indexable(path):
        raise ReaderError(f"{name} is not an XYZ file: only .xyz and .extxyz files can be exported "
                          f"as a bundle")
    if not text.fast_parser_usable():
        raise ReaderError(f"{name} cannot be exported: the fast XYZ parser is not installed")
    if target.exists() and not force:
        raise ReaderError(f"{target} already exists; use --force to replace it")

    arrays, _, _, _ = text.read_level2(XyzReader, str(path), memory_mode="saving", progress=progress)
    store = arrays.store if isinstance(arrays, IndexedArrays) else None
    if store is None or store.reader is not XyzReader:
        raise ReaderError(f"{name} is not read by the fast XYZ path, so it cannot be exported as a bundle")
    store.check_source()

    kept = _kept_sidecar(store)
    estimate, complete = _estimate(store)
    target.parent.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(target.parent).free
    if free < 1.1 * estimate:
        raise ReaderError(f"Not enough disk space for the bundle: it needs about "
                          f"{estimate / 2**30:.2f} GB (plus 10 %) in {target.parent}, "
                          f"{free / 2**30:.2f} GB is free")

    work = target.parent / f"{target.name}.part-{os.getpid()}"
    shutil.rmtree(work, ignore_errors=True)
    (work / BATCHES).mkdir(parents=True)
    try:
        hashers = None if kept is not None else _new_hashers(store)
        ranges = []
        for a, b in store.ranges:
            stop.check()
            relative = f"{BATCHES}/{a}-{b}.npz"
            out = work / relative
            if (a, b) in complete and hashers is None:
                shutil.copyfile(store._cache_path(a, b), out)
            else:
                part = store._part(a, b)
                if hashers is not None:
                    _feed(hashers, part, store, a, b)
                _write_batch(out, _compact(part), prefix=".")
            ranges.append({"start": a, "stop": b, "file": relative, "bytes": out.stat().st_size})
            if progress:
                progress(b, len(store.counts))
        np.save(work / COUNTS, np.asarray(store.counts, dtype=np.int64))
        np.save(work / OFFSETS, np.concatenate((store.starts, store.ends[-1:])).astype(np.int64))
        if kept is None:
            kept = {f"{d}|{key}": np.frombuffer(b"".join(h.all_digests()), dtype=np.uint8).reshape(-1, 32)
                    for (key, d), h in hashers.items()}
            for key, info in store.info.items():
                for field, dtype in (("digest", info["dtype"]), ("float_digest", np.float64)):
                    if column_digest_of_chunks(kept[f"{np.dtype(dtype).str}|{key}"]) != info[field]:
                        raise ReaderError(f"The digests of {key} changed while {name} was exported; "
                                          f"the file may have been modified. Try again.")
        np.savez(work / CHUNKS, **kept)
        description = _describe(store, ranges, probe=source_probe(path))
        store.check_source()
        stop.check()
        write_description(work, description)
        _put_in_place(work, target, force)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return description


def _new_hashers(store):
    from ffast.cache.fingerprint import ChunkHasher
    out = {}
    for key, info in store.info.items():
        for dtype in {info["dtype"].str, np.dtype(np.float64).str}:
            out[(key, dtype)] = ChunkHasher(info["tail"], np.dtype(dtype))
    return out


def _feed(hashers, part, store, a, b):
    """Add one window to the chunk hashers; a column the window lacks is NaN,
    as the store reads it."""
    from ffast.readers.text import per_atom
    for (key, dtype), hasher in hashers.items():
        info = store.info[key]
        value = part.get(key)
        if value is None:
            if np.dtype(dtype).kind not in "fc":      # only a float candidate holds missing rows
                continue
            rows = int(store.offsets[b] - store.offsets[a]) if per_atom(key) else b - a
            value = np.full((rows, *info["tail"]), np.nan, dtype=np.float64)
        hasher.update(value.astype(np.dtype(dtype), copy=False))


def _describe(store, ranges, *, probe):
    return {
        "reader": {"name": store.reader.name, "cache_version": getattr(store.reader, "cache_version", 1)},
        "scheme": SCHEME.decode(),
        "contents": store.contents,
        "n_frames": int(len(store.counts)),
        "n_atoms": int(store.offsets[-1]),
        "ranges": ranges,
        "columns": {key: {"dtype": info["dtype"].str, "tail": list(info["tail"]),
                          "digest": info["digest"], "float_digest": info["float_digest"],
                          "has_nan": bool(info["has_nan"])}
                    for key, info in store.info.items()},
        "facts": {"first_numbers": store.first_numbers.tolist(),
                  "first_numbers_dtype": store.first_numbers.dtype.str,
                  "uniform": bool(store.uniform), "same_formula": bool(store.same_formula),
                  "elements": sorted(int(z) for z in store.element_set)},
        "source": probe,
    }


def _put_in_place(work, target, force):
    """Rename the finished folder to ``target``; with ``force`` the old one goes
    aside first and is deleted after the rename."""
    import shutil
    aside = None
    if target.exists():
        if not force:
            raise ReaderError(f"{target} already exists; use --force to replace it")
        aside = target.parent / f"{target.name}.old-{os.getpid()}"
        os.replace(target, aside)
    try:
        os.replace(work, target)
    except BaseException:
        if aside is not None:
            os.replace(aside, target)
        raise
    if aside is not None:
        shutil.rmtree(aside, ignore_errors=True) if aside.is_dir() else aside.unlink(missing_ok=True)

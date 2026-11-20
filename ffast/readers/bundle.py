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


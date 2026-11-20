"""The store of an opened bundle: IndexedStore's column reads over a bundle's
batches, with no scan, no saving and no source needed (ADR 0110).

It reuses ``IndexedStore.column``: the slice reads in place, the shared RAM
cache and the windows for small reads. What differs: batches come from the
bundle folder and are never touched, written or locked; the source file is
looked for only when a batch is damaged.
"""
from __future__ import annotations

import hashlib
import logging
import os
import zipfile
from pathlib import Path

import numpy as np

from ffast.cache.fingerprint import SCHEME
from ffast.io.budget import budget
from ffast.readers import bundle
from ffast.readers.contract import ReaderError
from ffast.readers.indexed import IndexedStore, _read_batch, _read_batch_slice

log = logging.getLogger("FFAST")
_DAMAGED = (OSError, ValueError, EOFError, zipfile.BadZipFile)


class BundleStore(IndexedStore):
    def __init__(self, reader, path, workers=None):
        description = bundle.read_description(path)
        self.reader = reader
        self.path = os.path.abspath(path)                 # the dataset.ffast.json file
        self.bundle_dir = Path(self.path).parent
        self.description = description
        self.contents = description["contents"]
        self.workers = workers
        self.use_cache = True                             # reads the bundle's files, never writes
        self.state = None
        counts = np.load(self.bundle_dir / bundle.COUNTS, allow_pickle=False)
        offsets = np.load(self.bundle_dir / bundle.OFFSETS, allow_pickle=False)
        n = int(description["n_frames"])
        if len(counts) != n or len(offsets) != n + 1:
            raise ReaderError(f"The bundle {self.bundle_dir.name} cannot be opened: its frame lists "
                              f"do not match its {n:,} frames.")
        self.counts = counts.astype(np.int64, copy=False)
        self.starts = offsets[:-1].astype(np.int64, copy=False)
        self.ends = offsets[1:].astype(np.int64, copy=False)
        self.offsets = np.concatenate(([0], np.cumsum(self.counts)))
        self.frames = np.arange(n, dtype=np.int64)
        self.ranges = [(int(r["start"]), int(r["stop"])) for r in description["ranges"]]
        self._files = {(int(r["start"]), int(r["stop"])): self.bundle_dir / r["file"]
                       for r in description["ranges"]}
        if (not self.ranges or self.ranges[0][0] != 0 or self.ranges[-1][1] != n
                or any(a >= b for a, b in self.ranges)
                or any(x[1] != y[0] for x, y in zip(self.ranges, self.ranges[1:]))):
            raise ReaderError(f"The bundle {self.bundle_dir.name} cannot be opened: its batches "
                              f"do not cover its frames once each.")
        self.info = {
            key: {"dtype": np.dtype(facts["dtype"]), "tail": tuple(facts["tail"]),
                  "has_nan": bool(facts["has_nan"]), "digest": facts["digest"],
                  "float_digest": facts["float_digest"]}
            for key, facts in description["columns"].items()}
        facts = description["facts"]
        self.first_numbers = np.asarray(facts["first_numbers"], dtype=np.dtype(facts["first_numbers_dtype"]))
        self.uniform = bool(facts["uniform"])
        self.same_formula = bool(facts["same_formula"])
        self.element_set = set(facts["elements"])
        # Two copies of one bundle share the RAM cache: named by what is in it.
        h = hashlib.sha256(SCHEME)
        for key in sorted(self.info):
            h.update(key.encode())
            h.update(bytes.fromhex(self.info[key]["digest"]))
        self.identity = "bundle-" + h.hexdigest()
        self._small_columns = {}
        self._warming = False
        self._source = None

    # -- reads ---------------------------------------------------------------

    def check_source(self):
        """No source is needed while every batch reads."""

    def _cache_path(self, a, b):
        return self._files.get((a, b), self.bundle_dir / bundle.BATCHES / f"{a}-{b}.npz")

    def _cached_slice(self, a, b, key, start, stop):
        try:
            return _read_batch_slice(self._cache_path(a, b), key, start, stop, self.info[key],
                                     touch=False)
        except _DAMAGED:
            return None

    def _part(self, a, b):
        from ffast.metrics.stop import check

        check()
        key = self.identity, a, b
        hit = budget().cached(key)
        if hit is not None:
            return hit
        if (a, b) in self._files:
            try:
                return _read_batch(self._files[(a, b)], touch=False)
            except _DAMAGED:
                pass
        part = self._from_source(a, b)
        budget().keep(key, part, sum(v.nbytes for v in part.values()))
        return part

    def _from_source(self, a, b):
        """Frames ``[a, b)`` parsed from the source file: for a batch that is
        damaged or missing. The bundle is not repaired."""
        from ffast.readers import registry

        record = self.description["source"]
        if self._source is None or not bundle.probe_matches(self._source, record):
            self._source = bundle.find_source(self.bundle_dir, record)
        if self._source is None:
            batch = next((self._files[r] for r in self.ranges if r[0] <= a < r[1]), None)
            where = " or ".join(f"{p}" for p in bundle.places_looked(self.bundle_dir, record))
            raise ReaderError(
                f"The batch {batch.name if batch else f'{a}-{b}'} of the bundle {self.bundle_dir.name} "
                f"cannot be read, and its source file {record['name']} was not found "
                f"(looked at {where}). Put {record['name']} in one of those places.")
        reader = registry.get(self.description["reader"]["name"])
        if reader is None:
            raise ReaderError(f"The bundle {self.bundle_dir.name} needs the "
                              f"{self.description['reader']['name']} reader to re-read its source, "
                              f"which is not installed")
        log.info("%s: a batch of the bundle cannot be read; parsing frames %d-%d of %s",
                 self.bundle_dir.name, a, b, self._source)
        return self._parse(a, b, str(self._source), reader)

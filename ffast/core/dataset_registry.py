"""Registry of loaded datasets for the Environment.

Extracted from ``Environment`` (ADR 0020).  Owns the dataset dict, the
per-dataset stride (``slice_numbers``), and the running ``max_size`` used for
plot smoothing bounds.  Depends only on the cache (to invalidate a dataset's
cached outputs on delete) and the event bus — both injected, one-directional.

Dict-style dunders keep existing call sites (``env.datasets[fp]``,
``len(env.datasets)``, ``iter(env.datasets)``, ``env.datasets.items()``) working
while the codebase migrates to the explicit ``env.datasets.get(fp)`` API.
"""

import logging

logger = logging.getLogger("FFAST")


class DatasetRegistry:
    """Loaded datasets keyed by fingerprint. Deps: cache, events (ADR 0020)."""

    def __init__(self, cache, events, prediction_fields=None):
        self._datasets = {}
        self._cache = cache
        self._events = events
        # Prediction fields stored per (model, dataset); released on delete.
        self._predictionFields = prediction_fields
        self.slice_numbers = {}
        # A dataset loaded as an energy sample (ADR 0096): the file frames it
        # kept (int64, increasing) and the settings that chose them.
        self.kept_frames = {}
        self.sampling = {}
        self.max_size = 0  # largest dataset N, for plot-smoothing bounds

    # ── dict-compatible surface ───────────────────────────────────────────
    def __getitem__(self, key):
        return self._datasets[key]

    def __setitem__(self, key, value):
        self._datasets[key] = value

    def __delitem__(self, key):
        del self._datasets[key]

    def __contains__(self, key):
        return key in self._datasets

    def __len__(self):
        return len(self._datasets)

    def __iter__(self):
        return iter(self._datasets)

    def get(self, key, default=None):
        return self._datasets.get(key, default)

    def keys(self):
        return self._datasets.keys()

    def items(self):
        return self._datasets.items()

    def values(self):
        return self._datasets.values()

    # ── lifecycle ─────────────────────────────────────────────────────────
    def add(self, dataset, slice_num=-2, kept_frames=None, sampling=None):
        """Register a dataset and announce it (was ``Environment.setNewDataset``).

        A dataset with the fingerprint of one already loaded replaces it. The
        fingerprint covers elements, positions, energies and forces only
        (ADR 0058), so the new one may hold other Dataset Fields: what was
        computed for the old one is announced stale with ``OBJECT_REPLACED``.
        Datasets cut from the old one read their frames through it, so they
        are moved onto the new one and announced stale too (their own
        fingerprints are made from the parent's, so they stay the same)."""
        old = self._datasets.get(dataset.fingerprint)
        self._datasets[dataset.fingerprint] = dataset
        if old is not None and old is not dataset:
            for ds in self._datasets.values():
                if getattr(ds, "parent", None) is old:
                    ds.parent = dataset
            for fp in [dataset.fingerprint, *self.cutFrom(dataset)]:
                self._events.eventPush("OBJECT_REPLACED", fp)
        dataset.loaded = True
        if slice_num != -2:  # -2 skips slice tracking (e.g. sub/proxy datasets)
            self.update_max_size(False, dataset)
            self.slice_numbers[dataset.fingerprint] = slice_num
        if kept_frames is not None:
            import numpy as np
            self.kept_frames[dataset.fingerprint] = np.asarray(kept_frames, dtype=np.int64)
            self.sampling[dataset.fingerprint] = dict(sampling or {})
        self._events.eventPush("DATASET_LOADED", dataset.fingerprint)

    def cutFrom(self, dataset):
        """Fingerprints of every dataset cut from ``dataset``, at any depth."""
        out = []
        for fp, ds in self._datasets.items():
            parent = getattr(ds, "parent", None)
            while parent is not None:
                if parent is dataset:
                    out.append(fp)
                    break
                parent = getattr(parent, "parent", None)
        return out

    def delete(self, key):
        """Remove a dataset and invalidate every cached artifact derived from it.

        Everything cut from it (subsets, frozen subsets, atom subsets, hidden
        or not) reads its frames through it, so it goes first, each announced
        as deleted on its own (CONTEXT.md, Dataset)."""
        dataset = self.get(key)
        if dataset is None:
            return
        dataset.deleted = True      # a metric reading it stops (ADR 0079)

        for child in [fp for fp, ds in self._datasets.items()
                      if getattr(ds, "parent", None) is dataset]:
            self.delete(child)

        # Prune every cached artifact derived from this dataset. matches_dataset
        # holds regardless of the identity token's "__" count, so params /
        # Transform Metric keys (>3 segments) are pruned too — the old len==3
        # filter leaked them (CONTEXT.md "Cache Key").
        from ffast.cache import CacheKey
        for cache_key in self._cache.invalidate(
            lambda k: (ck := CacheKey.try_parse(k)) is not None and ck.matches_dataset(key)
        ):
            logger.info(f"Deleted cached data: {cache_key}")

        if self.slice_numbers.get(key) is not None:
            del self.slice_numbers[key]
        self.kept_frames.pop(key, None)
        self.sampling.pop(key, None)

        if self._predictionFields is not None:
            self._predictionFields.drop_object(key)

        dataset.onDelete()
        del self._datasets[key]
        self._events.objects.prune(key)
        logger.info(f"Dataset {key} deleted")
        self.update_max_size(on_deletion=True, dataset=None)
        self._events.eventPush("DATASET_DELETED", key)

    def exists(self, key):
        return key in self._datasets

    # ── queries ───────────────────────────────────────────────────────────
    def all(self, subOnly=False, excludeSubs=False):
        ds = [x for x in self._datasets.values() if x.active]
        if subOnly:
            return [x for x in ds if x.isSubDataset]
        elif excludeSubs:
            return [x for x in ds if not x.isSubDataset]
        return ds

    def all_keys(self):
        return [x.fingerprint for x in self.all()]

    # ── max-size bookkeeping ──────────────────────────────────────────────
    def update_max_size(self, on_deletion, dataset):
        if on_deletion:
            maximum = 0
            for ds in self._datasets.values():
                n = ds.getN()
                if n is not None and n > maximum:
                    maximum = n
            self.max_size = maximum
            logger.info(f"Maximum dataset size updated to : {maximum}")
        else:
            n = dataset.getN()
            if n is not None and n > self.max_size:
                self.max_size = n
                logger.info(f"Maximum dataset size updated to : {n}")

    def get_max_size(self):
        return self.max_size

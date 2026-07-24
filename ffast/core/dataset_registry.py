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

    def __init__(self, cache, events):
        self._datasets = {}
        self._cache = cache
        self._events = events
        self.slice_numbers = {}
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
    def add(self, dataset, slice_num=-2):
        """Register a dataset and announce it (was ``Environment.setNewDataset``)."""
        self._datasets[dataset.fingerprint] = dataset
        dataset.loaded = True
        if slice_num != -2:  # -2 skips slice tracking (e.g. sub/proxy datasets)
            self.update_max_size(False, dataset)
            self.slice_numbers[dataset.fingerprint] = slice_num
        self._events.eventPush("DATASET_LOADED", dataset.fingerprint)

    def delete(self, key):
        """Remove a dataset and invalidate every cached artifact derived from it."""
        dataset = self.get(key)
        if dataset is None:
            return

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

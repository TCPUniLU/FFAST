"""Registry of loaded models for the Environment.

Extracted from ``Environment`` (ADR 0020).  Owns the model dict and the
lifecycle around it (add / delete / lookup).  Depends only on the cache (to
invalidate a model's cached outputs on delete) and the event bus (to announce
load/delete) — both injected, both one-directional.

Dict-style dunders keep existing call sites (``env.models[fp]``,
``len(env.models)``, ``env.models.items()``, ``fp in env.models``) working while
the codebase migrates to the explicit ``env.models.get(fp)`` API.
"""

import logging

logger = logging.getLogger("FFAST")


class ModelRegistry:
    """Loaded models keyed by fingerprint. Deps: cache, events (ADR 0020)."""

    def __init__(self, cache, events):
        self._models = {}
        self._cache = cache
        self._events = events

    # ── dict-compatible surface ───────────────────────────────────────────
    def __getitem__(self, key):
        return self._models[key]

    def __setitem__(self, key, value):
        self._models[key] = value

    def __delitem__(self, key):
        del self._models[key]

    def __contains__(self, key):
        return key in self._models

    def __len__(self):
        return len(self._models)

    def __iter__(self):
        return iter(self._models)

    def get(self, key, default=None):
        return self._models.get(key, default)

    def keys(self):
        return self._models.keys()

    def items(self):
        return self._models.items()

    def values(self):
        return self._models.values()

    # ── lifecycle ─────────────────────────────────────────────────────────
    def add(self, model):
        """Register a model and announce it (was ``Environment.setNewModel``)."""
        self._models[model.fingerprint] = model
        model.loaded = True
        self._events.eventPush("MODEL_LOADED", model.fingerprint)

    def delete(self, key):
        """Remove a model and invalidate every cached artifact produced by it."""
        model = self.get(key)
        if model is None:
            return

        # Prune every cached artifact this model produced. matches_model holds
        # regardless of how many "__" the identity token carries, so params /
        # Transform Metric keys (>3 segments) are pruned too — the old len==3
        # filter leaked them, surviving the delete (CONTEXT.md "Cache Key").
        from ffast.cache import CacheKey
        for cache_key in self._cache.invalidate(
            lambda k: (ck := CacheKey.try_parse(k)) is not None and ck.matches_model(key)
        ):
            logger.info(f"Deleted cached data: {cache_key}")

        model.onDelete()
        del self._models[key]
        self._events.objects.prune(key)
        logger.info(f"Model {key} deleted")
        self._events.eventPush("MODEL_DELETED", key)

    def exists(self, key):
        return key in self._models

    # ── queries ───────────────────────────────────────────────────────────
    def all(self, excludeGhosts=False):
        if excludeGhosts:
            return [m for m in self._models.values() if not m.isGhost]
        return list(self._models.values())

    def all_keys(self):
        return list(self._models.keys())

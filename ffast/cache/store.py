"""Fingerprint-keyed result cache for the Environment.

Extracted from ``Environment`` as the first step of the composition refactor
(ADR 0020).  ``DataCache`` is a *leaf*: a pure key→entity store with no
dependencies on models, datasets, datatypes, or the event bus.  Anything that
needs to turn a cache key back into live model/dataset objects goes through the
coordinator (``DataService``), never through the cache.

The dict-style dunder methods keep existing call sites (``env.cache[key]``,
``key in env.cache``, ``env.cache.get``, ``.keys()``, ``.items()`` — including
the server's read access) working unchanged while the surrounding subsystems are
migrated incrementally.
"""

from ffast.cache.keys import CacheKey


class DataCache:
    """Pure key→entity store. No deps but the CacheKey codec — a leaf (ADR 0020).

    The cache is the single **validation boundary** for cache keys (CONTEXT.md
    "Cache Key", S2): every key is canonicalized through ``CacheKey`` on the way
    in and out, so a malformed key cannot enter the store, and callers may index
    with either the flat string or a ``CacheKey`` object. Storage and the
    iteration surface (``keys``/``items``) stay string-keyed, so disk, wire, and
    sweep call sites are unchanged. Canonicalization is identity for well-formed
    strings, so no stored key changes.
    """

    def __init__(self):
        self._store = {}

    # ── key canonicalization (the validation boundary) ────────────────────
    @staticmethod
    def _store_key(key):
        """Canonical string for storage; raises ``ValueError`` on a malformed key."""
        if isinstance(key, CacheKey):
            return key.format()
        return CacheKey.parse(key).format()

    @staticmethod
    def _lookup_key(key):
        """Canonical string for lookup, or ``None`` if the key cannot be a Cache Key.

        A malformed lookup key can never be present, so reads degrade to
        absent (KeyError / default / False) rather than raising.
        """
        if isinstance(key, CacheKey):
            return key.format()
        ck = CacheKey.try_parse(key)
        return ck.format() if ck is not None else None

    # ── dict-compatible read/write surface (accepts str | CacheKey) ───────
    def __getitem__(self, key):
        canon = self._lookup_key(key)
        if canon is None:
            raise KeyError(key)
        return self._store[canon]

    def __setitem__(self, key, value):
        self._store[self._store_key(key)] = value

    def __delitem__(self, key):
        canon = self._lookup_key(key)
        if canon is None:
            raise KeyError(key)
        del self._store[canon]

    def __contains__(self, key):
        canon = self._lookup_key(key)
        return canon is not None and canon in self._store

    def __len__(self):
        return len(self._store)

    def __iter__(self):
        return iter(self._store)

    def get(self, key, default=None):
        canon = self._lookup_key(key)
        if canon is None:
            return default
        return self._store.get(canon, default)

    def keys(self):
        return self._store.keys()

    def items(self):
        return self._store.items()

    def values(self):
        return self._store.values()

    # ── invalidation ──────────────────────────────────────────────────────
    def invalidate(self, predicate):
        """Delete every key for which ``predicate(key)`` is true.

        Returns the list of deleted keys so callers can log them or push
        their own events (the cache stays free of event-bus knowledge).
        """
        dead = [k for k in self._store if predicate(k)]
        for k in dead:
            del self._store[k]
        return dead

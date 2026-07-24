"""Re-export shim — DataCache now lives in ffast.cache.store (ADR 0047 Phase 1).

Kept so Desktop-Client call sites keep working; deleted in Phase 6.
"""
from ffast.cache.store import DataCache  # noqa: F401

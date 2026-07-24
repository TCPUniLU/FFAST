"""Re-export shim — ConnectionManager now lives in ffast.core.connection_manager (ADR 0047 Phase 3).

The cluster-connect/SLURM machinery it reaches is imported lazily (client-only
paths), so the Headless Core never eagerly pulls cluster/; cluster/ stays a
Desktop-Client dir. Deleted in Phase 6.
"""
from ffast.core.connection_manager import ConnectionManager  # noqa: F401

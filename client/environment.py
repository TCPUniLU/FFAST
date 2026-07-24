"""Re-export shim — Environment now lives in ffast.core.environment (ADR 0047 Phase 4).

The Environment graph is the Headless Core keystone; it moved into ffast/core/.
Desktop-Client call sites (`from client.environment import Environment`) keep
working via this shim; deleted in Phase 6.
"""
from ffast.core.environment import (  # noqa: F401
    Environment,
    HeadlessEnvironment,
    startHeadlessEnvironment,
)

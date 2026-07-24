"""Re-export shim — the event spine now lives in ffast.core.events (ADR 0047).

Kept so Desktop-Client call sites (`from events import EventClass`, `import
events`) keep working during the phased migration. Re-exports the SAME objects
— including the module-global ``subs`` event bus — so there is still exactly
one bus per process. Deleted in ADR 0047 Phase 6 once all importers are
repointed to ffast.core.events.
"""
from ffast.core.events import (  # noqa: F401
    EventChildClass,
    EventClass,
    doNothing,
    logger,
    subs,
)

"""Re-export shim — TaskManager now lives in ffast.core.tasks (ADR 0047).

Kept so Desktop-Client call sites (`from tasks import TaskManager`) keep working
during the phased migration. Deleted in ADR 0047 Phase 6 once all importers are
repointed to ffast.core.tasks.
"""
from ffast.core.tasks import TaskManager  # noqa: F401

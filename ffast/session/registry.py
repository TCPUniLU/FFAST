"""Assign a connecting client's role (ADR 0044 Phase 2, ADR 0051).

This was a ``ConnectionRegistry`` holding a ``websocket -> role`` dict. ADR 0044
Phase 2 removed the single-CONTROLLING gate — every admitted connection controls
its own views — which left the dict with nothing to arbitrate: ``role_of``,
``has_controlling`` and ``count`` had no callers outside their own tests, and
connection liveness is owned by ``ConnectionHub``, which the server already
consults for ``count`` / ``is_empty`` / broadcast fan-out. What remained was a
three-line role decision wrapped in redundant bookkeeping, so ADR 0051 reduced it
to the decision itself.
"""

from __future__ import annotations

import logging

from .token import ClientRole

logger = logging.getLogger("FFAST")


def decide_role(token_ok: bool, read_only_requested: bool = False) -> ClientRole:
    """Resolve the role for one connecting client.

    A client is READ_ONLY when it explicitly opts in (``read_only_requested``)
    or fails token validation; a valid token grants CONTROLLING regardless of
    how many other connections already hold it. The explicit opt-in wins over a
    valid token — a client may hold one and still ask to be a passive viewer.
    """
    if read_only_requested:
        role = ClientRole.READ_ONLY
    elif token_ok:
        role = ClientRole.CONTROLLING
    else:
        role = ClientRole.READ_ONLY
    logger.info(
        "Client role assigned: %s (token_ok=%s read_only_requested=%s)",
        role.value, token_ok, read_only_requested,
    )
    return role

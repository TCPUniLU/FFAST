"""ConnectionHub: fan shared server→client events out to every connection.

ADR 0044 (Phase 1). The server splits its transport into two paths:

- **Unicast** — per-view scenes and per-request replies stay on the owning
  connection's outbound queue (``ServerSession._emit`` → ``self.outbound``).
- **Broadcast** — shared Environment events (object metadata, deletes, the
  metric catalog) go through this hub, which packs once (upstream) and drops the
  bytes into every registered connection's queue.

A connection registers its queue on connect and deregisters on disconnect. A
full queue drops that one client's copy with a warning — never blocking or
starving the others (the pre-Phase-1 single shared queue coupled everyone's
fate; this decouples them). The event bus has no ``eventUnsubscribe``
(``events.py``), so the global ``env.eventSubscribe`` wiring stays a single
subscription pointed at the hub, rather than a per-connection subscribe that
would leak.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("FFAST")


class ConnectionHub:
    """Registry of connected clients' outbound queues, with broadcast fan-out."""

    def __init__(self) -> None:
        self._queues: set[asyncio.Queue] = set()

    def register(self, queue: asyncio.Queue) -> None:
        self._queues.add(queue)
        logger.debug("ConnectionHub: registered queue, total=%d", len(self._queues))

    def deregister(self, queue: asyncio.Queue) -> None:
        self._queues.discard(queue)
        logger.debug("ConnectionHub: deregistered queue, total=%d", len(self._queues))

    def broadcast(self, data) -> None:
        """Enqueue one packed message on every registered queue.

        Iterates a snapshot so a concurrent register/deregister can't mutate the
        set mid-loop. A full queue drops that client's copy (best-effort, like
        the pre-split state-replay) instead of blocking the fan-out.
        """
        for queue in list(self._queues):
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                logger.warning(
                    "ConnectionHub: outbound queue full, dropping broadcast for one client"
                )

    @property
    def count(self) -> int:
        return len(self._queues)

    @property
    def is_empty(self) -> bool:
        """True when no client is connected — the recovery-window trigger
        becomes last-client-leaves rather than controlling-client-leaves."""
        return not self._queues

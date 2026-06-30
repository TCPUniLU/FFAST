"""ConnectionRegistry: track connected clients and their roles."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .token import ClientRole

logger = logging.getLogger("FFAST")


@dataclass
class _ClientRecord:
    role: ClientRole


class ConnectionRegistry:
    """Asyncio-safe registry of connected WebSocket clients."""

    def __init__(self) -> None:
        self._clients: dict[object, _ClientRecord] = {}

    def claim(self, websocket: object, token_ok: bool) -> ClientRole:
        """Register client; grant CONTROLLING if token valid and no one holds it yet."""
        if token_ok and not self.has_controlling:
            role = ClientRole.CONTROLLING
        else:
            role = ClientRole.READ_ONLY
        self._clients[websocket] = _ClientRecord(role=role)
        logger.info(
            "Client registered: role=%s total=%d", role.value, len(self._clients)
        )
        return role

    def release(self, websocket: object) -> ClientRole | None:
        """Unregister client. Returns the role it held, or None if unknown."""
        record = self._clients.pop(websocket, None)
        if record is None:
            return None
        logger.info(
            "Client released: role=%s remaining=%d",
            record.role.value,
            len(self._clients),
        )
        return record.role

    def role_of(self, websocket: object) -> ClientRole | None:
        record = self._clients.get(websocket)
        return record.role if record is not None else None

    @property
    def has_controlling(self) -> bool:
        return any(r.role == ClientRole.CONTROLLING for r in self._clients.values())

    @property
    def count(self) -> int:
        return len(self._clients)

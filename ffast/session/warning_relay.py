"""Server warnings shown in every browser window, not only in the log.

A warning the server logs — "atom-color: displacement unavailable — structures
differ in size; falling back to element colors" — explains why the screen does
not show what the user asked for. Written only to ``server.log``, the user never
sees it. ``WarningRelay`` is a logging handler that sends each WARNING (and
worse) to every connected window as ``SERVER_WARNING`` {message, level}; the
web client shows it as a pop-up in the corner.

Rules:

- The same text is sent at most once a minute. Colouring by displacement warns
  on every frame change; one pop-up says it.
- Warnings from ``websockets`` and ``asyncio`` stay in the log: they are about
  connections, which the user cannot act on.
- A record logged with ``extra={"popup": False}`` stays in the log: the
  window it concerns shows it itself (a failed metric in its panel, ADR 0066).
- ``emit`` runs on whatever thread logged; the send is handed to the server's
  event loop, which owns the outbound queues.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

from ffast.protocol import control

# Seconds before the same text is sent again.
REPEAT_AFTER_S = 60.0
_QUIET_LOGGERS = ("websockets", "asyncio")


class WarningRelay(logging.Handler):
    """Send WARNING+ log records to every window through ``send(packed)``."""

    def __init__(self, loop: asyncio.AbstractEventLoop, send) -> None:
        super().__init__(level=logging.WARNING)
        self._loop = loop
        self._send = send
        self._last_sent: dict[str, float] = {}
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.split(".")[0] in _QUIET_LOGGERS:
            return
        if getattr(record, "popup", True) is False:
            return      # the window it concerns shows it itself (ADR 0066)
        try:
            text = record.getMessage()
            now = time.monotonic()
            with self._lock:
                last = self._last_sent.get(text)
                if last is not None and now - last < REPEAT_AFTER_S:
                    return
                self._last_sent[text] = now
            from ffast.protocol.rpc import pack
            data = pack(control.SERVER_WARNING, [],
                        {"message": text, "level": record.levelname.lower()})
            self._loop.call_soon_threadsafe(self._send, data)
        except RuntimeError:
            pass  # the loop has closed: the server is stopping
        except Exception:
            self.handleError(record)

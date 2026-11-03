"""Which of a window's messages wait for the one before (ADR 0077, ADR 0082)."""
from __future__ import annotations

import asyncio
from collections import deque

from ffast.protocol import control

# Moves where only the newest matters: one still waiting is dropped when a
# newer one of the same kind arrives. The view applies both without a version
# check (``VisualizationView.apply_command``).
_LATEST_WINS = {"SET_FRAME", "SET_CAMERA"}


class MessageLanes:
    """Starts a window's messages in the order received.

    Metric requests run beside the queue (ADR 0077). View commands do too,
    one view's in the order sent, each view in its own lane (ADR 0082), so a
    view whose colours take 13 s holds up neither other messages nor other
    views. Everything else is handled one at a time, in order.
    """

    def __init__(self, handle):
        self._handle = handle                     # async handle(event, args, kwargs, msg_id)
        self._waiting: dict[str, deque] = {}      # view id -> commands not yet started
        self.background: set[asyncio.Task] = set()

    async def take(self, item) -> None:
        """Start one received message ``(event, args, kwargs, msg_id)``."""
        event, _args, kwargs, _msg_id = item
        if event == control.REQUEST_METRIC:
            self._start(self._handle(*item))
        elif event == control.VIEW_COMMAND and isinstance(kwargs, dict) and kwargs.get("view_id"):
            self._queue_view(kwargs["view_id"], item)
        else:
            await self._handle(*item)

    def _queue_view(self, view_id: str, item) -> None:
        queue = self._waiting.get(view_id)
        if queue is None:
            queue = self._waiting[view_id] = deque([item])
            self._start(self._run_view(view_id, queue))
            return
        kind = item[2].get("type")
        if kind in _LATEST_WINS:
            for old in [w for w in queue if w[2].get("type") == kind]:
                queue.remove(old)
        queue.append(item)

    async def _run_view(self, view_id: str, queue: deque) -> None:
        try:
            while queue:
                await self._handle(*queue.popleft())
        finally:
            if self._waiting.get(view_id) is queue:
                del self._waiting[view_id]

    def _start(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.background.add(task)
        task.add_done_callback(self.background.discard)

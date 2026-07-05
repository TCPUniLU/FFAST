"""InboundEventRouter: client-side dispatch table for server→client messages.

Mirrors ``ServerSession``'s shape (``ffast/session/server_session.py``) on the
other end of the RPC Channel: a built-once event→handler table instead of an
inline ``if`` chain. ``ServerConnection.start_listener`` unpacks each message
and routes it here; reply-channel traffic (``_REPLY_CHANNELS`` in
``cluster/connection.py``) is resolved before reaching the router and never
arrives — see the module docstring there and ADR 0032.

Only events in ``CLIENT_ENV_SAFE`` reach the table; the router pushes to
``env.eventPush`` after any local bookkeeping. Events absent from the table
still route (via ``_on_forward``) — the table only exists to give special
cases (task namespacing, phantom-task registration, the repaint delay) a
named, unit-testable home.
"""
import asyncio
import logging

from ffast.protocol import control

logger = logging.getLogger("FFAST")


def _namespace_task_id(args):
    # Server and local env task IDs both start from 1, so they collide (the
    # local connect task is typically ID 1, and the first remote task — a
    # dataset load — is also ID 1). Prefixing keeps them in distinct spaces.
    if not args:
        return args
    args = list(args)
    args[0] = f"remote_{args[0]}"
    return args


class InboundEventRouter:
    """Routes one server→client event to a named handler.

    Built once per listener (``ServerConnection.start_listener``); holds no
    state beyond the ``env`` reference, so it is unit-testable with a fake
    env exposing ``eventPush`` and ``tm.registerPhantomTask``.
    """

    def __init__(self, env):
        self.env = env
        self._handlers = {
            "TASK_CREATED": self._on_task_created,
            "TASK_PROGRESS": self._on_task_namespaced,
            "TASK_DONE": self._on_task_done,
            "TASK_FAILED": self._on_task_namespaced,
            control.REMOTE_DATASET_META: self._on_remote_dataset_meta,
        }

    async def route(self, event, args, kwargs) -> None:
        handler = self._handlers.get(event, self._on_forward)
        await handler(event, args, kwargs)

    # ── handlers ─────────────────────────────────────────────────────────

    async def _on_forward(self, event, args, kwargs) -> None:
        self.env.eventPush(event, *args, **kwargs)

    async def _on_task_namespaced(self, event, args, kwargs) -> None:
        args = _namespace_task_id(args)
        self.env.eventPush(event, *args, **kwargs)

    async def _on_task_created(self, event, args, kwargs) -> None:
        # Phantom task: TASK_CREATED from the server carries only a taskID.
        # The local TaskManager has no record of it, so TasksList.onTaskCreated
        # would silently skip it. Register a minimal entry so progress bars
        # appear.
        args = _namespace_task_id(args)
        if args:
            logger.info("Listener: registering phantom task %r", args[0])
            self.env.tm.registerPhantomTask(args[0])
        self.env.eventPush(event, *args, **kwargs)

    async def _on_task_done(self, event, args, kwargs) -> None:
        args = _namespace_task_id(args)
        # Delay so Qt can paint the progress bar before it disappears. Without
        # this, fast remote tasks (TASK_CREATED + TASK_DONE arriving buffered
        # together) vanish before the first paint cycle.
        await asyncio.sleep(2.0)
        self.env.eventPush(event, *args, **kwargs)

    async def _on_remote_dataset_meta(self, event, args, kwargs) -> None:
        if args:
            logger.info(
                "Listener: forwarding REMOTE_DATASET_META fp=%r kwargs=%r",
                args[0], kwargs,
            )
        self.env.eventPush(event, *args, **kwargs)


class ListenerHandle:
    """Explicit lifecycle handle for a running listener task.

    Replaces ``ConnectionManager`` reaching into ``ServerConnection._listener``
    directly (assign / add_done_callback / cancel — ADR 0032). A fresh Future
    (not the task itself) backs ``wait_done`` so CancelledError always
    propagates to the awaiting caller, even if the listener task already
    completed — awaiting an already-done Task instead returns immediately
    without raising, which let a disconnect's scancel be skipped in a race.
    """

    def __init__(self, task: asyncio.Task):
        self._task = task
        self._done = asyncio.get_event_loop().create_future()
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, _task) -> None:
        if not self._done.done():
            self._done.set_result(None)

    async def wait_done(self) -> None:
        """Block until the listener task exits."""
        await self._done

    def cancel(self) -> None:
        self._task.cancel()

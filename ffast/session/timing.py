"""Timing notes: where the time of each message goes (ADR 0072).

A page that asks for the ``timing`` feature in its HELLO numbers each message
it sends (an ``id`` beside ``event`` in the envelope). The server then tells
the page, in small TIMING messages:

- after each message it sends: when it was queued and sent, how many bytes it
  was, and which of the page's messages caused it (the one whose handler was
  running when it was queued, or a task that handler started), if any;
- when a handler ends: when its message was read and when handling ended;
- in answer to TIMING_PING: the server's clock, so the page can line the
  server's times up with its own.

Times are the server's clock, in ms since the epoch. The page writes the log
(``timing.js``); the server keeps nothing.
"""

from __future__ import annotations

import asyncio
import contextvars
import time
from collections import deque

from ffast.protocol import control
from ffast.protocol.rpc import pack

FEATURE = "timing"

# (queue, message id) of the page message being handled, or None. The queue
# says whose message it is: a handler that broadcasts also fills the other
# pages' queues, and those copies were not caused by anything they sent.
_cause = contextvars.ContextVar("ffast_timing_cause", default=None)


def now_ms() -> float:
    return time.time() * 1000.0


class Note(bytes):
    """A packed TIMING message: sent like any other, but not itself timed."""


class TimedQueue(asyncio.Queue):
    """A connection's outbound queue that remembers, for each message, which
    page message caused it and when it was queued. After ``get()``,
    ``last_meta`` is ``(cause id or None, queued ms)`` for the message got."""

    def _init(self, maxsize):
        super()._init(maxsize)
        self._meta = deque()
        self.last_meta = (None, 0.0)

    def _put(self, item):
        cause = _cause.get()
        self._meta.append((cause[1] if cause and cause[0] is self else None, now_ms()))
        super()._put(item)

    def _get(self):
        self.last_meta = self._meta.popleft()
        return super()._get()


def handling(queue, msg_id):
    """Mark ``msg_id`` as the cause of what is queued on ``queue`` from now
    on, in this task and the tasks it starts. Returns the token for ``done``."""
    return _cause.set((queue, msg_id))


def done(token) -> None:
    _cause.reset(token)


def _note(**kwargs) -> Note:
    return Note(pack(control.TIMING, [], kwargs))


def handled(msg_id, read: float) -> Note:
    return _note(kind="handled", id=msg_id, read=read, done=now_ms())


def sent(cause, queued: float, sent_at: float, size: int) -> Note:
    return _note(kind="sent", cause=cause, queued=queued, sent=sent_at, bytes=size)


def pong(kwargs: dict) -> Note:
    return _note(kind="pong", t0=kwargs.get("t0"), server=now_ms())


def put_note(queue, note: Note) -> None:
    """Queue a note, or drop it if the queue is full: timing is best effort."""
    try:
        queue.put_nowait(note)
    except asyncio.QueueFull:
        pass

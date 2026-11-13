"""Replies name the request they answer (ADR 0097).

The page numbers every message it sends (``id`` in the envelope). While the
server handles one, ``answering`` holds that number, in the handler's task and
in every task it starts, so a handler, or the background work it starts, calls
``reply`` without passing the number around.

A reply carries ``reply_to``. Exactly one reply per request is ``final``;
progress replies come before it. A failed request's final reply carries
``error`` (one line of text) and no result. A message from ``rpc.pack`` names
no request: it is an announcement, and goes on as before.

A message from a page that numbers nothing (an older page, a test socket)
gets plain messages back: ``reply`` then packs exactly what ``rpc.pack`` would.
"""

from __future__ import annotations

import contextvars

from ffast.protocol.rpc import pack

_answering = contextvars.ContextVar("ffast_answering", default=None)


def answering(request_id):
    """Mark ``request_id`` as the request being answered, in this task and the
    tasks it starts. Returns the token for ``done``."""
    return _answering.set(request_id)


def done(token) -> None:
    _answering.reset(token)


def current():
    """The number of the request being answered, or None."""
    return _answering.get()


def reply(event: str, args=(), kwargs=None, *, final: bool = True,
          error: str | None = None, request_id=None) -> bytes:
    """Pack ``event`` as an answer to the current request (or ``request_id``).

    ``final=False`` marks progress. ``error`` fails the request; it implies
    ``final``. Without a request to answer, this is ``rpc.pack``.
    """
    rid = request_id if request_id is not None else _answering.get()
    if error is not None:
        final = True
    return pack(event, tuple(args), kwargs or {}, reply_to=rid,
                final=final, error=error)

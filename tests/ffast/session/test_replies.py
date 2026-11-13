"""Replies name the request they answer (ADR 0097): the envelope fields, and the
server session marking the request a handler answers."""

from __future__ import annotations

import asyncio

import msgpack

from ffast.protocol import reply as replies
from ffast.protocol.rpc import pack
from ffast.session.server_session import ServerSession


def _decode(data: bytes) -> dict:
    return msgpack.unpackb(data, raw=False)


def test_a_plain_message_names_no_request():
    msg = _decode(pack("A", (1,), {"x": 2}))
    assert msg == {"event": "A", "args": [1], "kwargs": {"x": 2}}


def test_a_reply_outside_a_request_is_a_plain_message():
    assert replies.reply("A", (1,), {"x": 2}) == pack("A", (1,), {"x": 2})


def test_a_reply_names_its_request_and_is_final_by_default():
    token = replies.answering(7)
    try:
        msg = _decode(replies.reply("A", (), {"x": 1}))
    finally:
        replies.done(token)
    assert msg["reply_to"] == 7
    assert msg["final"] is True
    assert "error" not in msg
    assert replies.current() is None


def test_progress_is_not_final():
    msg = _decode(replies.reply("A", request_id=3, final=False))
    assert (msg["reply_to"], msg["final"]) == (3, False)


def test_an_error_is_always_final():
    msg = _decode(replies.reply("A", request_id=3, final=False, error="disk full"))
    assert (msg["final"], msg["error"]) == (True, "disk full")


def test_tasks_a_handler_starts_answer_the_same_request():
    async def main():
        token = replies.answering(11)
        try:
            task = asyncio.create_task(asyncio.sleep(0, result=None))
            await task
            later = asyncio.create_task(_current_after_a_wait())
        finally:
            replies.done(token)
        return await later

    async def _current_after_a_wait():
        await asyncio.sleep(0)
        return replies.current()

    assert asyncio.run(main()) == 11


def _bare_session(timed: bool) -> ServerSession:
    session = ServerSession.__new__(ServerSession)
    session.timing = timed
    session.outbound = asyncio.Queue()
    return session


def test_dispatch_marks_the_request_while_the_handler_runs():
    seen = []

    async def fake_dispatch(event, args, kwargs):
        seen.append(replies.current())

    async def main():
        session = _bare_session(timed=False)
        session._dispatch = fake_dispatch
        await session.dispatch("E", [], {}, msg_id=5)
        await session.dispatch("E", [], {})
        return replies.current()

    assert asyncio.run(main()) is None
    assert seen == [5, None]


def test_dispatch_without_timing_queues_no_timing_note():
    async def main():
        session = _bare_session(timed=False)

        async def fake_dispatch(event, args, kwargs):
            pass

        session._dispatch = fake_dispatch
        await session.dispatch("E", [], {}, msg_id=5)
        return session.outbound.qsize()

    assert asyncio.run(main()) == 0

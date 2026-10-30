"""Timing notes for the page's timing log (ADR 0072).

A page that numbers its messages learns, for each one, when the server read it
and finished handling it, and, for each message sent back, which of its
messages caused it. Run against a real ServerSession with a timed queue and a
fake handler — no socket.
"""
import asyncio

from ffast.protocol import control
from ffast.protocol.rpc import pack, unpack, unpack_with_id
from ffast.session import timing
from ffast.session.server_session import ServerSession
from ffast.visualization.protocol import ClientCapabilities, negotiate


def _run(coro):
    # Not asyncio.run: that leaves no current event loop, and a later test in
    # this worker (TaskManager) still asks for one.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _drain(queue):
    """Everything queued, as (item, cause id) pairs."""
    out = []
    while not queue.empty():
        item = queue.get_nowait()
        out.append((item, queue.last_meta[0]))
    return out


def _session_with_handler(handler):
    """A session whose DELETE_TAB runs ``handler(session)``."""
    async def run():
        s = ServerSession(env=None, outbound=timing.TimedQueue())

        async def fn(name):
            await handler(s)

        s._handlers[control.DELETE_TAB] = s._handlers[control.DELETE_TAB].__class__(fn, ["name"], None)
        return s
    return run


def test_a_handlers_reply_is_linked_to_the_message_and_a_note_follows():
    async def handler(s):
        await s._emit(pack(control.TAB_LAYOUT, [], {}))

    async def go():
        s = await _session_with_handler(handler)()
        await s.dispatch(control.DELETE_TAB, [], {"name": "A"}, msg_id=7)
        return _drain(s.outbound)

    (reply, cause), (note, note_cause) = _run(go())
    assert unpack(reply)[0] == control.TAB_LAYOUT and cause == 7
    assert isinstance(note, timing.Note)
    event, _, kw = unpack(note)
    assert event == control.TIMING and kw["kind"] == "handled" and kw["id"] == 7
    assert kw["read"] <= kw["done"]


def test_without_an_id_nothing_is_linked_and_no_note_is_sent():
    async def handler(s):
        await s._emit(pack(control.TAB_LAYOUT, [], {}))

    async def go():
        s = await _session_with_handler(handler)()
        await s.dispatch(control.DELETE_TAB, [], {"name": "A"})
        return _drain(s.outbound)

    [(reply, cause)] = _run(go())
    assert cause is None and not isinstance(reply, timing.Note)


def test_a_task_the_handler_starts_is_linked_too():
    # A metric is computed in a task that outlives its handler.
    async def handler(s):
        async def later():
            await s._emit(pack(control.METRIC_RESULT, [], {}))
        s._task = asyncio.create_task(later())

    async def go():
        s = await _session_with_handler(handler)()
        await s.dispatch(control.DELETE_TAB, [], {"name": "A"}, msg_id=3)
        await s._task
        return _drain(s.outbound)

    linked = {unpack(item)[0]: cause for item, cause in _run(go())}
    assert linked[control.METRIC_RESULT] == 3


def test_a_copy_broadcast_to_another_page_is_not_linked():
    # That page sent nothing; message 5 is the first page's number.
    async def go():
        other = timing.TimedQueue()

        async def handler(s):
            other.put_nowait(pack(control.TAB_LAYOUT, [], {}))

        s = await _session_with_handler(handler)()
        await s.dispatch(control.DELETE_TAB, [], {"name": "A"}, msg_id=5)
        return _drain(other)

    [(_, cause)] = _run(go())
    assert cause is None


def test_a_note_is_dropped_not_waited_for_when_the_queue_is_full():
    queue = timing.TimedQueue(maxsize=1)
    queue.put_nowait(b"x")
    timing.put_note(queue, timing.pong({"t0": 1.0}))
    assert queue.qsize() == 1


def test_pong_and_sent_notes_carry_their_numbers():
    _, _, pong = unpack(timing.pong({"t0": 12.5}))
    assert pong["kind"] == "pong" and pong["t0"] == 12.5 and pong["server"] > 0
    _, _, sent = unpack(timing.sent(4, 100.0, 130.0, 2048))
    assert sent == {"kind": "sent", "cause": 4, "queued": 100.0, "sent": 130.0, "bytes": 2048}


def test_the_message_id_is_read_from_the_envelope():
    import msgpack

    numbered = msgpack.packb({"event": "X", "args": [], "kwargs": {}, "id": 9})
    assert unpack_with_id(numbered)[3] == 9
    assert unpack_with_id(pack("X", [], {}))[3] is None


def test_the_timing_feature_is_granted_only_when_asked_for():
    asked = negotiate(ClientCapabilities(protocol_version="1.0", renderer="webgl", features=["timing"]))
    not_asked = negotiate(ClientCapabilities(protocol_version="1.0", renderer="webgl"))
    assert timing.FEATURE in asked.features
    assert timing.FEATURE not in not_asked.features

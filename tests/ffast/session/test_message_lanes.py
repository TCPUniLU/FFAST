"""Which messages wait for the one before (ADR 0082 part b).

MeluXina 2026-11-03: a colour-by view command took 13 s, and Basic Errors'
metric requests, sent 2.6 s later, waited behind it in the one queue.
"""
import asyncio

from ffast.protocol import control
from ffast.session.message_lanes import MessageLanes


class _Handler:
    """Records when each message starts and ends; a message may be held."""

    def __init__(self):
        self.log = []
        self.gates = {}

    def hold(self, name):
        self.gates[name] = asyncio.Event()
        return self.gates[name]

    async def __call__(self, event, args, kwargs, msg_id):
        name = msg_id
        self.log.append(("start", name))
        if name in self.gates:
            await self.gates[name].wait()
        self.log.append(("end", name))


def _view(view_id, kind, name):
    return (control.VIEW_COMMAND, [], {"type": kind, "view_id": view_id}, name)


def _other(name):
    return ("LOAD_DATASET", [], {}, name)


async def _settle(lanes):
    while lanes.background:
        await asyncio.gather(*list(lanes.background))


def test_a_slow_view_command_holds_up_neither_other_messages_nor_other_views():
    async def scenario():
        h = _Handler()
        lanes = MessageLanes(h)
        gate = h.hold("colour")
        await lanes.take(_view("v1", "SET_PARAMETER", "colour"))
        await lanes.take((control.REQUEST_METRIC, [], {}, "metric"))
        await lanes.take(_other("load"))
        await lanes.take(_view("v2", "SET_FRAME", "other view"))
        await asyncio.sleep(0)
        before = list(h.log)
        gate.set()
        await _settle(lanes)
        return before

    before = asyncio.run(scenario())
    assert ("end", "load") in before
    assert ("end", "metric") in before
    assert ("end", "other view") in before
    assert ("end", "colour") not in before


def test_one_views_commands_run_one_at_a_time_in_order():
    async def scenario():
        h = _Handler()
        lanes = MessageLanes(h)
        gate = h.hold("a")
        for name, kind in (("a", "SET_PARAMETER"), ("b", "TOGGLE_FEATURE"), ("c", "SET_SELECTION")):
            await lanes.take(_view("v1", kind, name))
        await asyncio.sleep(0)
        gate.set()
        await _settle(lanes)
        return h.log

    assert asyncio.run(scenario()) == [
        ("start", "a"), ("end", "a"), ("start", "b"), ("end", "b"), ("start", "c"), ("end", "c")]


def test_a_waiting_frame_move_is_dropped_for_a_newer_one_but_other_commands_are_kept():
    async def scenario():
        h = _Handler()
        lanes = MessageLanes(h)
        gate = h.hold("busy")
        await lanes.take(_view("v1", "SET_PARAMETER", "busy"))
        await lanes.take(_view("v1", "SET_FRAME", "frame 1"))
        await lanes.take(_view("v1", "SET_CAMERA", "camera 1"))
        await lanes.take(_view("v1", "SET_FRAME", "frame 2"))
        await lanes.take(_view("v1", "TOGGLE_FEATURE", "toggle"))
        await lanes.take(_view("v1", "SET_FRAME", "frame 3"))
        await lanes.take(_view("v1", "SET_CAMERA", "camera 2"))
        await asyncio.sleep(0)
        gate.set()
        await _settle(lanes)
        return [name for what, name in h.log if what == "start"]

    assert asyncio.run(scenario()) == ["busy", "toggle", "frame 3", "camera 2"]


def test_a_view_lane_ends_when_empty_and_a_new_command_starts_a_new_one():
    async def scenario():
        h = _Handler()
        lanes = MessageLanes(h)
        await lanes.take(_view("v1", "SET_FRAME", "one"))
        await _settle(lanes)
        await lanes.take(_view("v1", "SET_FRAME", "two"))
        await _settle(lanes)
        return [name for what, name in h.log if what == "end"], lanes._waiting

    ended, waiting = asyncio.run(scenario())
    assert ended == ["one", "two"] and waiting == {}

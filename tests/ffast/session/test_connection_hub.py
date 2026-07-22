"""Unit tests for the multi-client transport split (ADR 0044, Phase 1).

The server used to own one outbound queue and one ServerSession, so a second
connection split the message stream. Phase 1 gives each connection its own queue
and session, and adds a ConnectionHub that fans *shared* (Environment-level)
events out to every connection while per-connection replies stay on the owning
connection's queue.

These test the socket-free seam: a ConnectionHub over plain asyncio.Queues, and
two ServerSessions over one shared fake env — no WebSocket, no thread, no loop.
"""
import asyncio

from ffast.session import ConnectionHub, ServerSession


# ── ConnectionHub: broadcast fan-out ─────────────────────────────────────────

def test_broadcast_reaches_every_registered_queue():
    hub = ConnectionHub()
    a, b = asyncio.Queue(), asyncio.Queue()
    hub.register(a)
    hub.register(b)

    hub.broadcast(b"dataset-meta")

    assert a.get_nowait() == b"dataset-meta"
    assert b.get_nowait() == b"dataset-meta"


def test_is_empty_true_with_no_registered_queues():
    hub = ConnectionHub()
    assert hub.is_empty


def test_is_empty_false_once_a_queue_is_registered():
    hub = ConnectionHub()
    hub.register(asyncio.Queue())
    assert not hub.is_empty


def test_is_empty_true_again_after_last_queue_deregisters():
    hub = ConnectionHub()
    a, b = asyncio.Queue(), asyncio.Queue()
    hub.register(a)
    hub.register(b)
    hub.deregister(a)
    assert not hub.is_empty          # b still connected
    hub.deregister(b)
    assert hub.is_empty              # last client gone — recovery window may arm


def test_deregister_stops_delivery():
    hub = ConnectionHub()
    a, b = asyncio.Queue(), asyncio.Queue()
    hub.register(a)
    hub.register(b)

    hub.deregister(a)
    hub.broadcast(b"model-meta")

    assert a.empty()                      # deregistered — no delivery
    assert b.get_nowait() == b"model-meta"
    assert hub.count == 1


def test_full_queue_drops_for_one_client_not_the_others():
    hub = ConnectionHub()
    slow = asyncio.Queue(maxsize=1)
    healthy = asyncio.Queue()
    slow.put_nowait(b"already-there")     # slow client is backed up
    hub.register(slow)
    hub.register(healthy)

    hub.broadcast(b"delete-object")       # must not raise despite the full queue

    assert healthy.get_nowait() == b"delete-object"
    assert slow.get_nowait() == b"already-there"  # its broadcast copy was dropped
    assert slow.empty()


# ── per-connection ServerSession: replies stay unicast ───────────────────────

class _FakeDatasets:
    def __init__(self, items=None):
        self._items = items or {}

    def get(self, fp):
        return self._items.get(fp)

    def all(self, excludeSubs=False):
        return list(self._items.values())


class _FakeEnv:
    """Shared Environment stand-in — one instance behind many sessions."""
    def __init__(self):
        self.datasets = _FakeDatasets()
        self.models = {}


def test_emit_is_unicast_to_the_owning_connections_queue():
    env = _FakeEnv()                      # one shared Environment…
    qa, qb = asyncio.Queue(), asyncio.Queue()
    sa = ServerSession(env, qa)           # …two independent connections
    sb = ServerSession(env, qb)

    asyncio.run(sa._emit(b"scene-for-a"))

    assert qa.get_nowait() == b"scene-for-a"
    assert qb.empty()                     # B never sees A's per-view reply


def test_two_connections_open_independent_views_and_drive_frames_separately():
    """ADR 0044 Phase 4 gate at the socket-free seam: two connections open a
    view on the SAME shared dataset and drive SET_FRAME independently — each
    queue gets only its own COMMAND_RESULT/SCENE_PATCH stream, never the
    other's, exactly like the two-tab Playwright pop-out scenario."""
    env = _FakeEnv()
    qa, qb = asyncio.Queue(), asyncio.Queue()
    sa = ServerSession(env, qa)
    sb = ServerSession(env, qb)

    async def scenario():
        await sa.dispatch("OPEN_VIEW", [], {"view_id": "view-0"})
        await sb.dispatch("OPEN_VIEW", [], {"view_id": "view-0"})  # same id, different session
        await sa.dispatch("VIEW_COMMAND", [], {
            "type": "SET_FRAME", "view_id": "view-0", "view_version": 0, "frame_index": 5,
        })
        await sb.dispatch("VIEW_COMMAND", [], {
            "type": "SET_FRAME", "view_id": "view-0", "view_version": 0, "frame_index": 9,
        })

    asyncio.run(scenario())

    assert sa.views["view-0"].state.structure_index == 5
    assert sb.views["view-0"].state.structure_index == 9   # unaffected by A's frame

    # Each queue has exactly its own SCENE_SNAPSHOT (OPEN_VIEW) + COMMAND_RESULT
    # + SCENE_PATCH (SET_FRAME) — 3 messages, none of them the other's.
    assert qa.qsize() == 3
    assert qb.qsize() == 3


def test_sessions_share_one_environment_but_own_separate_views():
    env = _FakeEnv()
    sa = ServerSession(env, asyncio.Queue())
    sb = ServerSession(env, asyncio.Queue())

    assert sa.env is sb.env               # shared data layer
    assert sa.views is not sb.views       # independent view namespaces
    sa.views["view-0"] = object()
    assert "view-0" not in sb.views       # B's "view-0" would be its own

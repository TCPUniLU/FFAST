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


def test_sessions_share_one_environment_but_own_separate_views():
    env = _FakeEnv()
    sa = ServerSession(env, asyncio.Queue())
    sb = ServerSession(env, asyncio.Queue())

    assert sa.env is sb.env               # shared data layer
    assert sa.views is not sb.views       # independent view namespaces
    sa.views["view-0"] = object()
    assert "view-0" not in sb.views       # B's "view-0" would be its own

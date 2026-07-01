"""Unit tests for ServerSession — the server-scoped event dispatcher.

The client→server event layer used to be a 15-arm if/elif (``_dispatch_client_event``)
plus eight free handlers, all threading ``env``/``outbound``/``views`` as explicit
arguments — untestable without standing up a WebSocket server. It now lives behind
ServerSession's small interface (``dispatch`` + ``replay`` over a built-once handler
table), so the routing logic tests here with a fake env and a plain asyncio.Queue —
no socket, no thread.

The S2b argument-resolution rule (``_resolve``) is a pure staticmethod and so tests
with no env, queue, or event loop at all.
"""
import asyncio

from ffast.protocol.rpc import unpack
from ffast.session.server_session import ServerSession


def _run(coro):
    return asyncio.run(coro)


# ── fakes ───────────────────────────────────────────────────────────────────

class _FakeDatasets:
    def __init__(self, items=None):
        self._items = items or {}

    def get(self, fp):
        return self._items.get(fp)

    def all(self, excludeSubs=False):
        return list(self._items.values())


class _FakeModels:
    def __init__(self, items=None):
        self._items = items or {}

    def get(self, fp):
        return self._items.get(fp)

    def items(self):
        return self._items.items()


class _FakeCache:
    def __init__(self, entries=None):
        self._entries = entries or {}

    def get(self, key):
        return self._entries.get(key)

    def keys(self):
        return list(self._entries.keys())


class _FakeEnv:
    """Records the env-facing calls the handlers make."""

    def __init__(self, datasets=None, models=None, cache=None):
        self.datasets = _FakeDatasets(datasets)
        self.models = _FakeModels(models)
        self.cache = _FakeCache(cache)
        self.deleted = []
        self.load_dataset_calls = []

    def deleteObject(self, fp):
        self.deleted.append(fp)

    def taskLoadDataset(self, path, dataset_type, **kwargs):
        self.load_dataset_calls.append((path, dataset_type, kwargs))


# ── _resolve: the S2b rule (pure — no env, queue, or loop) ────────────────────

def test_resolve_required_positional():
    resolved, missing, consumed = ServerSession._resolve(
        ["path", "dataset_type"], ["/a", "xyz"], {})
    assert resolved == {"path": "/a", "dataset_type": "xyz"}
    assert missing == []
    assert consumed == {"path", "dataset_type"}


def test_resolve_kwarg_fallback_when_no_positional():
    resolved, missing, _ = ServerSession._resolve(["path"], [], {"path": "/a"})
    assert resolved == {"path": "/a"}
    assert missing == []


def test_resolve_missing_required_is_reported():
    _, missing, _ = ServerSession._resolve(["fingerprint"], [], {})
    assert missing == ["fingerprint"]


def test_resolve_optional_absent_is_not_missing():
    resolved, missing, consumed = ServerSession._resolve(
        ["metric_id", "?key"], ["m"], {})
    assert resolved == {"metric_id": "m", "key": None}
    assert missing == []
    assert consumed == {"metric_id", "key"}


def test_resolve_positional_takes_precedence_over_kwarg():
    resolved, _, _ = ServerSession._resolve(["path"], ["/pos"], {"path": "/kw"})
    assert resolved == {"path": "/pos"}


# ── dispatch routing ──────────────────────────────────────────────────────────

def test_dispatch_unknown_event_is_noop():
    env = _FakeEnv()

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("NONSENSE", [], {})
        return s

    s = _run(scenario())
    assert s.outbound.empty()


def test_dispatch_missing_required_skips_handler():
    env = _FakeEnv()

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("DELETE_OBJECT", [], {})  # no fingerprint
        return s

    s = _run(scenario())
    assert env.deleted == []        # handler never ran
    assert s.outbound.empty()


def test_dispatch_delete_object_reaches_env():
    env = _FakeEnv()

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("DELETE_OBJECT", ["fp123"], {})

    _run(scenario())
    assert env.deleted == ["fp123"]


def test_dispatch_load_dataset_restores_prediction_key_tuples():
    """msgpack delivers tuple keys as lists; the handler restores them, and the
    leftover kwargs (slice_num) ride through as **kwargs."""
    env = _FakeEnv()

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch(
            "LOAD_DATASET",
            ["/data.xyz", "ase"],
            {"prediction_keys": [["e", "f"]], "slice_num": 3},
        )

    _run(scenario())
    assert len(env.load_dataset_calls) == 1
    path, typ, kwargs = env.load_dataset_calls[0]
    assert (path, typ) == ("/data.xyz", "ase")
    assert kwargs["prediction_keys"] == [("e", "f")]   # list → tuple restored
    assert kwargs["slice_num"] == 3


# ── handler enqueue, end-to-end through dispatch ──────────────────────────────

def test_dispatch_list_dir_enqueues_listing(tmp_path):
    """LIST_DIR exercises the optional ``?path`` arg, _emit, and the pack round
    trip — with no env state at all, just the filesystem."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("hi")

    async def scenario():
        s = ServerSession(_FakeEnv(), asyncio.Queue())
        await s.dispatch("LIST_DIR", [str(tmp_path)], {})
        return s.outbound.get_nowait()

    event, _args, kwargs = unpack(_run(scenario()))
    assert event == "DIR_LISTING"
    names = {e["name"] for e in kwargs["entries"]}
    assert {"sub", "a.txt"} <= names


# ── replay ─────────────────────────────────────────────────────────────────────

def test_replay_empty_env_enqueues_only_metric_catalog():
    # replay() is synchronous and uses put_nowait, so no loop needed.
    s = ServerSession(_FakeEnv(), asyncio.Queue())
    s.replay()
    event, _args, _kwargs = unpack(s.outbound.get_nowait())
    assert event == "METRIC_CATALOG"
    assert s.outbound.empty()   # no datasets, no models, no open views


def test_request_state_sync_triggers_replay():
    async def scenario():
        s = ServerSession(_FakeEnv(), asyncio.Queue())
        await s.dispatch("REQUEST_STATE_SYNC", [], {})
        return s.outbound.get_nowait()

    event, _args, _kwargs = unpack(_run(scenario()))
    assert event == "METRIC_CATALOG"

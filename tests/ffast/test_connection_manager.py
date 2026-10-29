"""Qt-free unit tests for ConnectionManager.

active_session() is the single home for the "is a session live?" guard
(ADR 0030's connect-window fallback: a session only counts as live once both
serverConnection and the asyncio event loop exist). Previously duplicated
across LoadingCoordinator, Environment, and PredictionSource.

_onRemoteModelMeta's mutation_lock coverage is a regression test: it used to
mutate the models registry unlocked, unlike every other ghost-creation path
(loadModel/loadDataset/loadPrepredictedDataset all hold mutation_lock).
"""
import threading
import types

from ffast.core.connection_manager import ConnectionManager


def _manager():
    return ConnectionManager(env=None)  # __init__ never touches env


def test_active_session_none_when_disconnected():
    mgr = _manager()
    assert mgr.active_session() == (None, None)


def test_active_session_none_without_loop():
    mgr = _manager()
    mgr.serverConnection = object()  # connect-window: session but no loop yet
    assert mgr.active_session() == (None, None)


def test_active_session_returns_pair_when_both_present():
    mgr = _manager()
    session = object()
    loop = object()
    mgr.serverConnection = session
    mgr._event_loop = loop
    assert mgr.active_session() == (session, loop)


# ── _onRemoteModelMeta: mutation_lock regression ────────────────────────────

class _FakeModels:
    def __init__(self):
        self._existing = None

    def get(self, key):
        return self._existing


class _FakeLoading:
    def __init__(self, lock):
        self._lock = lock
        self.calls = []

    def registerGhostModel(self, fingerprint, path, name):
        self.calls.append(("register", self._lock.locked()))

    def instantiateGhost(self, fingerprint):
        self.calls.append(("instantiate", self._lock.locked()))


def _env_for_model_meta():
    lock = threading.Lock()
    env = types.SimpleNamespace(
        mutation_lock=lock,
        models=_FakeModels(),
        loading=_FakeLoading(lock),
    )
    return env


def test_onRemoteModelMeta_creates_ghost_under_mutation_lock():
    env = _env_for_model_meta()
    mgr = ConnectionManager(env)

    mgr._onRemoteModelMeta("fp123", name="model.xyz")

    assert env.loading.calls == [("register", True), ("instantiate", True)]
    assert not env.mutation_lock.locked()  # released once the handler returns


def test_onRemoteModelMeta_already_loaded_skips_without_deadlock():
    env = _env_for_model_meta()
    env.models._existing = object()  # already loaded
    mgr = ConnectionManager(env)

    mgr._onRemoteModelMeta("fp123", name="model.xyz")

    assert env.loading.calls == []
    assert not env.mutation_lock.locked()

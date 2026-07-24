"""Qt-free unit tests for the Loading Coordinator (ADR 0034).

The load orchestration used to live inside ``client/environment.py`` and was
test-dark except end-to-end. Now that routing + the remote-load algorithm live
on ``LoadingCoordinator`` and take a session + dialog callbacks as parameters,
they can be driven with fakes — no Qt, no live server. These cover the wire
contract, the probe→stride→probe→keys→dispatch algorithm, its cancel/error
branches, and the local-vs-server routing decision.
"""
import asyncio
import types

from client.connection_manager import ConnectionManager
from client.loading_coordinator import LoadingCoordinator
from ffast.protocol import control


class _FakeSession:
    def __init__(self, length=None, keys=None):
        self._length = length if length is not None else {"n": 100}
        self._keys = keys if keys is not None else {
            "energy_keys": [], "force_keys": [],
            "has_calculator_energy": True, "has_calculator_forces": True,
        }
        self.pushed = []            # list of (args, kwargs)
        self.length_calls = 0
        self.keys_calls = 0

    async def probe_dataset_length(self, path):
        self.length_calls += 1
        return self._length

    async def probe_dataset_keys(self, path, dataset_type):
        self.keys_calls += 1
        return self._keys

    async def push_event(self, *args, **kwargs):
        self.pushed.append((args, kwargs))


def _fake_env():
    """Minimal env: records eventPush + newTask, carries a real ConnectionManager.

    A real ``ConnectionManager`` (not a hand-rolled fake) so routing tests
    exercise ``active_session()`` through its actual implementation; its
    ``__init__`` never touches ``env``, so passing the env being built is safe.
    """
    calls = {"events": [], "tasks": []}
    env = types.SimpleNamespace()
    env.remote = ConnectionManager(env)
    env.eventPush = lambda *a, **k: calls["events"].append((a, k))
    env.newTask = lambda *a, **k: calls["tasks"].append((a, k))
    env._calls = calls
    return env


# ── wire contract ─────────────────────────────────────────────────────────

def test_dispatchDatasetLoad_uses_control_constant_and_coerces_pred_keys():
    coord = LoadingCoordinator(_fake_env())
    session = _FakeSession()
    asyncio.run(coord.dispatchDatasetLoad(
        session, "/data.xyz", "ase (auto)",
        selected_energy_key="E", selected_force_key="F",
        prediction_keys=[("e1", "f1", "m1"), ("e2", "f2", "m2")],
        slice_num=5,
    ))
    (args, kwargs), = session.pushed
    assert args[0] == control.LOAD_DATASET
    assert args[1:] == ("/data.xyz", "ase (auto)")
    # tuples coerced to lists (msgpack cannot carry tuples)
    assert kwargs["prediction_keys"] == [["e1", "f1", "m1"], ["e2", "f2", "m2"]]
    assert kwargs["slice_num"] == 5
    assert kwargs["selected_energy_key"] == "E"


def test_dispatchDatasetLoad_none_pred_keys_stays_none():
    coord = LoadingCoordinator(_fake_env())
    session = _FakeSession()
    asyncio.run(coord.dispatchDatasetLoad(session, "/d.xyz", "npz", slice_num=0))
    (_, kwargs), = session.pushed
    assert kwargs["prediction_keys"] is None


# ── remote orchestration ────────────────────────────────────────────────────

def _stride_cb(value):
    async def cb(n_total):
        cb.seen_n = n_total
        return value
    cb.seen_n = "unset"
    return cb


def _keys_cb(value):
    async def cb(probe):
        cb.calls += 1
        return value
    cb.calls = 0
    return cb


def test_loadRemoteDataset_ase_happy_path():
    coord = LoadingCoordinator(_fake_env())
    session = _FakeSession(length={"n": 42})
    get_keys = _keys_cb(("E", "F", [("e", "f", "m")]))
    asyncio.run(coord.loadRemoteDataset(
        session, "/d.xyz", "ase (auto)",
        get_stride=_stride_cb(3), get_keys=get_keys,
    ))
    assert session.length_calls == 1
    assert session.keys_calls == 1
    assert get_keys.calls == 1
    (args, kwargs), = session.pushed
    assert args[0] == control.LOAD_DATASET
    assert kwargs["slice_num"] == 3
    assert kwargs["prediction_keys"] == [["e", "f", "m"]]


def test_loadRemoteDataset_non_ase_skips_key_probe_and_dialog():
    coord = LoadingCoordinator(_fake_env())
    session = _FakeSession()
    get_keys = _keys_cb(None)  # must never be called
    asyncio.run(coord.loadRemoteDataset(
        session, "/d.npz", "npz",
        get_stride=_stride_cb(0), get_keys=get_keys,
    ))
    assert session.keys_calls == 0
    assert get_keys.calls == 0
    (args, kwargs), = session.pushed
    assert kwargs["slice_num"] == 0
    assert kwargs["prediction_keys"] is None


def test_loadRemoteDataset_cancel_at_stride_dispatches_nothing():
    coord = LoadingCoordinator(_fake_env())
    session = _FakeSession()
    get_keys = _keys_cb(None)
    asyncio.run(coord.loadRemoteDataset(
        session, "/d.xyz", "ase (auto)",
        get_stride=_stride_cb(None), get_keys=get_keys,
    ))
    assert session.pushed == []
    assert session.keys_calls == 0
    assert get_keys.calls == 0


def test_loadRemoteDataset_cancel_at_keys_dispatches_nothing():
    coord = LoadingCoordinator(_fake_env())
    session = _FakeSession()
    asyncio.run(coord.loadRemoteDataset(
        session, "/d.xyz", "ase (auto)",
        get_stride=_stride_cb(1), get_keys=_keys_cb(None),
    ))
    assert session.pushed == []


def test_loadRemoteDataset_probe_error_falls_back_to_keyless():
    coord = LoadingCoordinator(_fake_env())
    session = _FakeSession(keys={"error": "bad file"})
    get_keys = _keys_cb(("E", "F", []))  # must not be called on probe error
    asyncio.run(coord.loadRemoteDataset(
        session, "/d.xyz", "ase (auto)",
        get_stride=_stride_cb(2), get_keys=get_keys,
    ))
    assert get_keys.calls == 0
    (args, kwargs), = session.pushed
    assert kwargs["slice_num"] == 2
    assert kwargs["prediction_keys"] is None


# ── routing decision ────────────────────────────────────────────────────────

def test_requestDatasetLoad_no_session_falls_back_to_local_task():
    env = _fake_env()  # remote.serverConnection is None
    coord = LoadingCoordinator(env)

    # Stub the real loadDataset (file I/O, ASE loaders, ...) with a fake that
    # records whether it was actually invoked and with what, so this proves
    # the queued local task really executes end-to-end — not just that
    # taskLoadDataset happened to reference the right method.
    recorded = []
    coord.loadDataset = lambda *a, **k: recorded.append((a, k))

    coord.requestDatasetLoad("/d.xyz", "ase (auto)", slice_num=7)

    # fell back to taskLoadDataset → env.newTask(self.loadDataset, ...)
    (task_args, task_kwargs), = env._calls["tasks"]
    assert task_args[0] is coord.loadDataset

    # Actually run the queued task (as the real TaskManager would) and check
    # it produces the expected effect: the local loader invoked with the
    # right path/type/kwargs.
    queued_fn = task_args[0]
    queued_fn(*task_kwargs["args"], **task_kwargs["kwargs"])
    assert recorded == [(
        ("/d.xyz", "ase (auto)"),
        {
            "selected_energy_key": None,
            "selected_force_key": None,
            "prediction_keys": None,
            "slice_num": 7,
        },
    )]


def test_remoteSession_returns_none_without_loop():
    env = _fake_env()
    env.remote.serverConnection = _FakeSession()
    env.remote._event_loop = None  # connect-window: session but no loop yet
    coord = LoadingCoordinator(env)
    assert coord._remoteSession() == (None, None)


def test_remoteSession_returns_pair_when_both_present():
    env = _fake_env()
    sess = _FakeSession()
    loop = object()
    env.remote.serverConnection = sess
    env.remote._event_loop = loop
    coord = LoadingCoordinator(env)
    assert coord._remoteSession() == (sess, loop)


# ── model dispatch ──────────────────────────────────────────────────────────

def test_dispatchModelLoad_uses_control_constant():
    coord = LoadingCoordinator(_fake_env())
    session = _FakeSession()
    asyncio.run(coord.dispatchModelLoad(session, "/m.pt", "mace"))
    (args, kwargs), = session.pushed
    assert args == (control.LOAD_MODEL, "/m.pt", "mace")


def test_requestModelLoad_no_session_falls_back_to_local_task():
    env = _fake_env()
    coord = LoadingCoordinator(env)
    coord.requestModelLoad("/m.pt", "mace")
    (task_args, _), = env._calls["tasks"]
    assert task_args[0] == coord.loadModel


# ── prediction dispatch + orchestration ─────────────────────────────────────

def test_dispatchPredictionLoad_uses_control_constant():
    coord = LoadingCoordinator(_fake_env())
    session = _FakeSession()
    asyncio.run(coord.dispatchPredictionLoad(
        session, "/p.xyz", "ds1", selected_energy_key="E", selected_force_key="F"
    ))
    (args, kwargs), = session.pushed
    assert args == (control.LOAD_PREDICTION, "/p.xyz", "ds1")
    assert kwargs == {"selected_energy_key": "E", "selected_force_key": "F"}


def test_loadRemotePrediction_npz_dispatches_directly():
    coord = LoadingCoordinator(_fake_env())
    session = _FakeSession()
    get_keys = _keys_cb(None)  # must never be called for NPZ
    asyncio.run(coord.loadRemotePrediction(
        session, "/p.npz", "ds1", get_keys=get_keys
    ))
    assert session.keys_calls == 0
    assert get_keys.calls == 0
    (args, kwargs), = session.pushed
    assert args == (control.LOAD_PREDICTION, "/p.npz", "ds1")
    assert kwargs == {"selected_energy_key": None, "selected_force_key": None}


def test_loadRemotePrediction_ase_happy_path():
    coord = LoadingCoordinator(_fake_env())
    session = _FakeSession()
    asyncio.run(coord.loadRemotePrediction(
        session, "/p.xyz", "ds1", get_keys=_keys_cb(("E", "F"))
    ))
    assert session.keys_calls == 1
    (args, kwargs), = session.pushed
    assert args[0] == control.LOAD_PREDICTION
    assert kwargs == {"selected_energy_key": "E", "selected_force_key": "F"}


def test_loadRemotePrediction_cancel_at_keys_dispatches_nothing():
    coord = LoadingCoordinator(_fake_env())
    session = _FakeSession()
    asyncio.run(coord.loadRemotePrediction(
        session, "/p.xyz", "ds1", get_keys=_keys_cb(None)
    ))
    assert session.pushed == []


def test_loadRemotePrediction_probe_error_falls_back_keyless():
    coord = LoadingCoordinator(_fake_env())
    session = _FakeSession(keys={"error": "bad"})
    get_keys = _keys_cb(("E", "F"))
    asyncio.run(coord.loadRemotePrediction(
        session, "/p.xyz", "ds1", get_keys=get_keys
    ))
    assert get_keys.calls == 0
    (args, kwargs), = session.pushed
    assert args == (control.LOAD_PREDICTION, "/p.xyz", "ds1")


def test_requestPredictionLoad_no_session_falls_back_to_local_task():
    env = _fake_env()
    coord = LoadingCoordinator(env)
    coord.requestPredictionLoad("/p.xyz", "ds1", selected_energy_key="E")
    (task_args, task_kwargs), = env._calls["tasks"]
    assert task_args[0] == coord.loadPrepredictedDataset
    assert task_kwargs["kwargs"]["selected_energy_key"] == "E"


# ── ghost creation ───────────────────────────────────────────────────────────

class _Models(dict):
    """Registry double: ``add`` keys by the model's ``modelKey``."""

    def add(self, model):
        self[model.modelKey] = model


def test_instantiateGhost_constructs_initialises_and_registers(monkeypatch):
    import ffast.core.loading_coordinator as lcmod

    initialised = []

    class _FakeGhost:
        def __init__(self, env, modelKey):
            self.modelKey = modelKey

        def initialise(self):
            initialised.append(self.modelKey)

    monkeypatch.setattr(lcmod, "GhostModelLoader", _FakeGhost)

    env = _fake_env()
    env.models = _Models()
    coord = LoadingCoordinator(env)

    model = coord.instantiateGhost("modelA")

    assert initialised == ["modelA"]
    assert env.models["modelA"] is model
    assert isinstance(model, _FakeGhost)

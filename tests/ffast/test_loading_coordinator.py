"""Qt-free unit tests for the Loading Coordinator (ADR 0034).

The load orchestration used to live inside ``client/environment.py`` and was
test-dark except end-to-end. Now that routing + the remote-load algorithm live
on ``LoadingCoordinator`` and take a session + dialog callbacks as parameters,
they can be driven with fakes — no Qt, no live server. These cover the wire
contract, the probe→stride→probe→keys→dispatch algorithm, its cancel/error
branches, and the local-vs-server routing decision.
"""
import asyncio
import threading
import types

import numpy as np

from ffast.core.connection_manager import ConnectionManager
from ffast.core.loading_coordinator import (
    LoadingCoordinator,
    _isUniformAtomsList,
)
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


# ── prediction ingest: the shared body (ADR 0034 addendum 4) ─────────────────
#
# `loadPrepredictedDataset` (a standalone prediction file) and
# `_loadPredictionsFromKeys` (extra energy/force columns inside the dataset
# file) used to carry two copies of: pick loader flavour → validate against the
# dataset → cache energies → cache forces → register the ghost. The copies had
# drifted; these cover the one body they collapsed into.

class _FakeAtoms:
    def __init__(self, formula):
        self._formula = formula

    def get_chemical_formula(self):
        return self._formula


class _LazyAtomsList:
    """Stands in for a lazy ``AtomsList``: counts frames actually materialised."""

    def __init__(self, formulas):
        self._formulas = formulas
        self.reads = 0

    def __len__(self):
        return len(self._formulas)

    def __getitem__(self, i):
        self.reads += 1
        return _FakeAtoms(self._formulas[i])


def test_isUniform_trivial_lists_are_uniform():
    assert _isUniformAtomsList(_LazyAtomsList([])) is True
    assert _isUniformAtomsList(_LazyAtomsList(["CH4"])) is True


def test_isUniform_two_frames_does_not_raise():
    """The replaced check sampled 3 random frames and raised below 3 frames."""
    assert _isUniformAtomsList(_LazyAtomsList(["CH4", "CH4"])) is True
    assert _isUniformAtomsList(_LazyAtomsList(["CH4", "NH3"])) is False


def test_isUniform_same_atom_count_different_elements_is_variable():
    """Atom counts alone would call this uniform and stamp frame 0's ``z`` on all.

    ``CH4`` and ``SiH4`` are both 5 atoms, so the replaced
    ``len(set(atom_counts)) == 1`` check accepted them into the uniform loader.
    """
    assert _isUniformAtomsList(_LazyAtomsList(["CH4", "SiH4"])) is False


def test_isUniform_samples_large_lists_without_full_materialisation():
    frames = _LazyAtomsList(["CH4"] * 5000)
    assert _isUniformAtomsList(frames) is True
    assert frames.reads <= 61, "sampled check should not walk every frame"


def test_isUniform_sample_always_includes_the_last_frame():
    frames = _LazyAtomsList(["CH4"] * 4999 + ["NH3"])
    assert _isUniformAtomsList(frames) is False


# ── shape validation ────────────────────────────────────────────────────────

class _FakeDataset:
    def __init__(self, energies, fingerprint="ds-fp", name="ds.xyz"):
        self._E = energies
        self.fingerprint = fingerprint
        self._name = name

    def getEnergies(self):
        return self._E

    def getName(self):
        return self._name


def test_predictionMatchesDataset_uniform_arrays():
    ds = _FakeDataset(np.zeros((10,)))
    assert LoadingCoordinator._predictionMatchesDataset(np.ones((10,)), ds, "m") is True
    assert LoadingCoordinator._predictionMatchesDataset(np.ones((7,)), ds, "m") is False


def test_predictionMatchesDataset_variable_lists():
    ds = _FakeDataset([0.0, 1.0, 2.0])
    assert LoadingCoordinator._predictionMatchesDataset([1.0, 2.0, 3.0], ds, "m") is True
    assert LoadingCoordinator._predictionMatchesDataset([1.0, 2.0], ds, "m") is False


def test_predictionMatchesDataset_handles_list_energies_defensively():
    """Energies-as-list is covered even though no dataset type returns it today.

    The two collapsed copies disagreed here — one had an ``isinstance(E, list)``
    branch, the other only ``E.shape`` — which looked like an ``AttributeError``
    waiting to happen. It was not: ``getEnergies()`` returns an ndarray on every
    dataset type, so the list branch is unreachable. Kept because the
    ``getForces()`` side *does* return lists, and asserted so the branch does
    not rot.
    """
    ds = _FakeDataset([0.0, 1.0, 2.0])
    assert LoadingCoordinator._predictionMatchesDataset([1.0, 2.0, 3.0], ds, "m") is True
    assert LoadingCoordinator._predictionMatchesDataset([1.0], ds, "m") is False


def test_predictionMatchesDataset_no_energies_is_vacuously_ok():
    assert LoadingCoordinator._predictionMatchesDataset(None, _FakeDataset(None), "m") is True


# ── _ingestPrediction ───────────────────────────────────────────────────────

class _FakeDataEntity:
    def __init__(self, **payload):
        self.payload = payload


class _FakeDataType:
    def __init__(self, key):
        self.key = key

    def newDataEntity(self, **payload):
        return _FakeDataEntity(**payload)


class _FakeDataService:
    def __init__(self):
        self.stored = []          # (dtype, payload, model, dataset)
        self.predictionFields = {}

    def getDataType(self, key):
        return _FakeDataType(key)

    def setData(self, entity, key, model=None, dataset=None):
        self.stored.append((key, entity.payload, model, dataset))


class _FakeCatalog:
    def __init__(self):
        self.registered = {}

    def register(self, fingerprint, info):
        self.registered[fingerprint] = info


def _ingest_env():
    env = _fake_env()
    env.data = _FakeDataService()
    env.objects = _FakeCatalog()
    env.mutation_lock = threading.Lock()
    return env


def test_ingestPrediction_caches_both_arrays_under_the_ghost_fingerprint():
    env = _ingest_env()
    coord = LoadingCoordinator(env)
    ds = _FakeDataset(np.zeros((3,)))
    E, F = np.array([1.0, 2.0, 3.0]), np.ones((3, 4, 3))

    assert coord._ingestPrediction(
        ds, E, F, path="/p.xyz", name="pred", fingerprint="ghost-fp",
    ) is True

    kinds = {kind: (payload, model) for kind, payload, model, _ in env.data.stored}
    assert set(kinds) == {"energy", "forces"}
    assert kinds["energy"][1] == "ghost-fp"
    assert kinds["forces"][1] == "ghost-fp"
    np.testing.assert_allclose(kinds["energy"][0]["energy"], E)


def test_ingestPrediction_flattens_variable_energy_lists():
    """Variable datasets hand back a list of scalars, not an array."""
    env = _ingest_env()
    coord = LoadingCoordinator(env)
    ds = _FakeDataset([0.0, 0.0])

    coord._ingestPrediction(
        ds, [1.5, 2.5], None, path="/p.xyz", name="pred", fingerprint="fp",
    )

    (kind, payload, _, _), = env.data.stored
    assert kind == "energy"
    np.testing.assert_allclose(payload["energy"], np.array([1.5, 2.5]))


def test_ingestPrediction_registers_the_ghost_with_catalog_extras():
    env = _ingest_env()
    coord = LoadingCoordinator(env)

    coord._ingestPrediction(
        _FakeDataset(np.zeros((2,))), np.zeros((2,)), None,
        path="/p.xyz", name="col-A", fingerprint="fp",
        energy_key="E_dft", force_key="F_dft",
    )

    assert env.objects.registered["fp"] == {
        "path": "/p.xyz", "name": "col-A", "type": "ghost_model",
        "energy_key": "E_dft", "force_key": "F_dft",
    }


def test_ingestPrediction_mismatch_writes_nothing_and_reports_false():
    env = _ingest_env()
    coord = LoadingCoordinator(env)

    assert coord._ingestPrediction(
        _FakeDataset(np.zeros((10,))), np.zeros((4,)), np.zeros((4, 3, 3)),
        path="/p.xyz", name="pred", fingerprint="fp",
    ) is False

    assert env.data.stored == []
    assert env.objects.registered == {}


def test_ingestPrediction_without_a_source_skips_field_extraction():
    """npz predictions carry only E/F — there is no ASE object to read fields off."""
    env = _ingest_env()
    coord = LoadingCoordinator(env)

    coord._ingestPrediction(
        _FakeDataset(np.zeros((2,))), np.zeros((2,)), None,
        path="/p.npz", name="pred", fingerprint="fp", source=None,
    )

    assert env.data.predictionFields == {}


# ── the gap this collapse closed ────────────────────────────────────────────

class _StubPredictionLoader:
    """Loader double exposing just what ingest + field extraction read."""

    def __init__(self, E, F, fields):
        self._E, self._F, self._fields = E, F, fields

    def getEnergies(self):
        return self._E

    def getForces(self):
        return self._F

    def getFrameField(self, key, indices=None):
        return self._fields.get(("info", key))

    def getAtomField(self, key, indices=None):
        return self._fields.get(("atoms", key))


def test_loadPredictionsFromKeys_now_extracts_adr0023_prediction_fields(monkeypatch):
    """In-file prediction columns used to skip ADR 0023 field extraction entirely.

    ``loadPrepredictedDataset`` extracted declared ``prediction.{info,atoms}.<key>``
    fields; ``_loadPredictionsFromKeys`` carried its own copy of the ingest body
    and did not. A metric referencing such a field silently resolved to None for
    predictions that came from extra columns in the dataset file. Collapsing the
    two bodies closed it.
    """
    monkeypatch.setattr(
        "ffast.metrics.fields.declared_field_keys",
        lambda side, registry=None: {"info": {"dipole"}, "atoms": {"charge"}},
    )

    loader = _StubPredictionLoader(
        E=np.array([1.0, 2.0]),
        F=np.ones((2, 3, 3)),
        fields={("info", "dipole"): np.array([0.1, 0.2]),
                ("atoms", "charge"): np.zeros((2, 3))},
    )
    monkeypatch.setattr(
        LoadingCoordinator, "_aseLoaderFor",
        staticmethod(lambda *a, **k: loader),
    )

    env = _ingest_env()
    coord = LoadingCoordinator(env)
    ds = _FakeDataset(np.zeros((2,)), fingerprint="ds-fp")

    coord._loadPredictionsFromKeys(
        ds, "/data.xyz", [("E_pred", "F_pred", "my-model")],
        atomsList=_LazyAtomsList(["CH4", "CH4"]),
    )

    (ghost_fp, dataset_fp), store = next(iter(env.data.predictionFields.items()))
    assert dataset_fp == "ds-fp"
    assert set(store["info"]) == {"dipole"}
    assert set(store["atoms"]) == {"charge"}
    assert env.objects.registered[ghost_fp]["name"] == "my-model"


def test_loadPredictionsFromKeys_skips_a_mismatched_column_and_keeps_going():
    good = _StubPredictionLoader(np.array([1.0, 2.0]), np.ones((2, 3, 3)), {})
    bad = _StubPredictionLoader(np.array([1.0]), np.ones((1, 3, 3)), {})
    handed = iter([bad, good])

    env = _ingest_env()
    coord = LoadingCoordinator(env)
    coord._aseLoaderFor = lambda *a, **k: next(handed)

    coord._loadPredictionsFromKeys(
        _FakeDataset(np.zeros((2,))), "/data.xyz",
        [("E1", "F1", "too-short"), ("E2", "F2", "fits")],
        atomsList=_LazyAtomsList(["CH4", "CH4"]),
    )

    names = {info["name"] for info in env.objects.registered.values()}
    assert names == {"fits"}


# ── real ASE files: the parse paths unit fakes cannot reach ──────────────────
#
# Defects 2-5 in ADR 0034 addendum 4 are all about which loader class gets
# picked and what it does with real frames, so these drive the actual ASE
# loaders over real extxyz. The cache/catalog stay fakes — the risk being
# covered is parsing and loader selection, not the registry write.

import pytest

ase = pytest.importorskip("ase")
from ase import Atoms
from ffast.loaders.ase import aseDatasetLoader, VariableASEDatasetLoader


def _frame(symbols, *, energy, seed=0.0):
    """One extxyz-ready frame carrying a keyed energy and keyed forces."""
    atoms = Atoms(symbols)
    n = len(atoms)
    atoms.set_positions(np.arange(n * 3).reshape(n, 3) * 0.1 + seed)
    atoms.info["ref_energy"] = energy
    atoms.set_array("ref_forces", np.full((n, 3), seed + 0.5))
    return atoms


def _write(tmp_path, frames, name="d.extxyz"):
    import ase.io
    path = tmp_path / name
    ase.io.write(str(path), frames, format="extxyz")
    return str(path)


class _Datasets:
    """Registry double exposing just what the prediction path reads."""

    def __init__(self, dataset, key="ds-fp", slice_num=None):
        self._dataset = dataset
        self._key = key
        self.slice_numbers = {} if slice_num is None else {key: slice_num}

    def get(self, key):
        return self._dataset if key == self._key else None

    def exists(self, key):
        return key == self._key


def _file_env(dataset, key="ds-fp"):
    env = _ingest_env()
    env.datasets = _Datasets(dataset, key=key)
    env.models = {}
    env.cache = {}
    return env


# ── loader selection, against real frames ───────────────────────────────────

def test_aseLoaderFor_picks_uniform_for_identical_frames():
    frames = [_frame("CH4", energy=-1.0, seed=i) for i in range(4)]
    loader = LoadingCoordinator._aseLoaderFor(
        "mem.extxyz", frames, energy_key="ref_energy", force_key="ref_forces")
    assert isinstance(loader, aseDatasetLoader)
    assert loader.getEnergies().shape == (4,)


def test_aseLoaderFor_picks_variable_for_differing_atom_counts():
    frames = [_frame("CH4", energy=-1.0), _frame("C2H6", energy=-2.0)]
    loader = LoadingCoordinator._aseLoaderFor(
        "mem.extxyz", frames, energy_key="ref_energy", force_key="ref_forces")
    assert isinstance(loader, VariableASEDatasetLoader)


def test_aseLoaderFor_picks_variable_for_same_count_different_elements():
    """Defect 5, against real loaders: CH4 / SiH4 are both 5 atoms.

    The replaced atom-count check routed these into the uniform loader, which
    reads frame 0's atomic numbers and applies them to every frame — so every
    Si frame would have been reported as C.
    """
    frames = [_frame("CH4", energy=-1.0), _frame("SiH4", energy=-2.0)]
    loader = LoadingCoordinator._aseLoaderFor(
        "mem.extxyz", frames, energy_key="ref_energy", force_key="ref_forces")
    assert isinstance(loader, VariableASEDatasetLoader)


def test_aseLoaderFor_handles_a_two_frame_file():
    """Defect 3, against real loaders: the replaced check needed >=3 frames."""
    frames = [_frame("CH4", energy=-1.0, seed=i) for i in range(2)]
    loader = LoadingCoordinator._aseLoaderFor(
        "mem.extxyz", frames, energy_key="ref_energy", force_key="ref_forces")
    assert isinstance(loader, aseDatasetLoader)
    assert loader.getEnergies().shape == (2,)


# ── loadPrepredictedDataset over a real file ────────────────────────────────

def test_loadPrepredictedDataset_uniform_file_end_to_end(tmp_path):
    frames = [_frame("CH4", energy=-(i + 1.0), seed=i) for i in range(5)]
    path = _write(tmp_path, frames)

    reference = aseDatasetLoader(
        path, atomsList=frames,
        selected_energy_key="ref_energy", selected_force_key="ref_forces")
    ds = _FakeDataset(reference.getEnergies())
    env = _file_env(ds)

    LoadingCoordinator(env).loadPrepredictedDataset(
        path, "ds-fp",
        selected_energy_key="ref_energy", selected_force_key="ref_forces")

    kinds = {kind for kind, _, _, _ in env.data.stored}
    assert kinds == {"energy", "forces"}
    ghost, = env.objects.registered.values()
    assert ghost["type"] == "ghost_model"
    assert ghost["name"] == "d.extxyz"


def test_loadPrepredictedDataset_variable_file_end_to_end(tmp_path):
    """A variable-sized prediction file loads through the real variable loader.

    Frames of differing composition, so ``_aseLoaderFor`` must route to
    ``VariableASEDatasetLoader`` and its ``getForces()`` (a list of per-frame
    arrays, unlike the uniform loader's stacked ndarray) must survive ingest.
    """
    frames = [_frame("CH4", energy=-1.0), _frame("C2H6", energy=-2.0),
              _frame("CH4", energy=-3.0, seed=2.0)]
    path = _write(tmp_path, frames, name="var.extxyz")

    reference = VariableASEDatasetLoader(
        path, atomsList=frames,
        selected_energy_key="ref_energy", selected_force_key="ref_forces")
    ds = _FakeDataset(reference.getEnergies())
    env = _file_env(ds)

    LoadingCoordinator(env).loadPrepredictedDataset(
        path, "ds-fp",
        selected_energy_key="ref_energy", selected_force_key="ref_forces")

    assert {kind for kind, _, _, _ in env.data.stored} == {"energy", "forces"}
    assert len(env.objects.registered) == 1


def test_loadPrepredictedDataset_wrong_file_length_is_rejected(tmp_path):
    """A mis-picked prediction file must abort the load, not cache half of it."""
    frames = [_frame("CH4", energy=-(i + 1.0), seed=i) for i in range(3)]
    path = _write(tmp_path, frames)

    ds = _FakeDataset(np.zeros((10,)))          # dataset has 10 frames, file has 3
    env = _file_env(ds)

    LoadingCoordinator(env).loadPrepredictedDataset(
        path, "ds-fp",
        selected_energy_key="ref_energy", selected_force_key="ref_forces")

    assert env.data.stored == []
    assert env.objects.registered == {}


# ── in-file prediction columns over a real file ─────────────────────────────

def test_loadPredictionsFromKeys_over_a_real_file(tmp_path):
    """The prediction-keys path end to end: real frames, two keyed columns."""
    frames = []
    for i in range(4):
        atoms = _frame("CH4", energy=-(i + 1.0), seed=i)
        atoms.info["pred_energy"] = -(i + 1.5)
        atoms.arrays["pred_forces"] = np.full((5, 3), 0.25)
        frames.append(atoms)
    path = _write(tmp_path, frames)

    reference = aseDatasetLoader(
        path, atomsList=frames,
        selected_energy_key="ref_energy", selected_force_key="ref_forces")
    ds = _FakeDataset(reference.getEnergies())
    env = _file_env(ds)

    LoadingCoordinator(env)._loadPredictionsFromKeys(
        ds, path,
        [("pred_energy", "pred_forces", "my-pred")],
        atomsList=frames,
    )

    ghost, = env.objects.registered.values()
    assert ghost["name"] == "my-pred"
    assert ghost["energy_key"] == "pred_energy"
    energy, = [p for k, p, _, _ in env.data.stored if k == "energy"]
    np.testing.assert_allclose(energy["energy"], [-1.5, -2.5, -3.5, -4.5])

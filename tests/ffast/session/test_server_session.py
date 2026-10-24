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
import os
from unittest.mock import patch

import numpy as np
import pytest

from ffast.cache import CacheKey, PredictionArrayKey
from ffast.metrics.models import MetricResult
from ffast.protocol import control
from ffast.protocol.rpc import unpack, unpack_arrays, unpack_metric_result
from ffast.session.server_session import ServerSession


def _run(coro):
    return asyncio.run(coro)


def _make_metric_result(metric_id="ffast.force_difference[atom+structure;length]", values=None):
    """A real MetricResult so pack_metric_result reads its actual fields."""
    return MetricResult(
        metric_id=metric_id,
        shape="()",
        dtype="float64",
        unit="eV",
        compute_parameters={},
        implementation_hash="hash123",
        checksum="chk456",
        values=np.asarray([1.5] if values is None else values),
    )


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

    def __init__(self, datasets=None, models=None, cache=None, data=None):
        self.datasets = _FakeDatasets(datasets)
        self.models = _FakeModels(models)
        self.cache = _FakeCache(cache)
        self.data = data
        self.deleted = []
        self.load_dataset_calls = []
        self.create_subset_calls = []
        self.declare_subset_calls = []

    def deleteObject(self, fp):
        self.deleted.append(fp)

    def createAtomFilteredDataset(self, dataset, idxs):
        self.create_subset_calls.append((dataset, idxs))

    def declareSubDataset(self, parent, model, idx, name):
        self.declare_subset_calls.append((parent, model, idx, name))

    def taskLoadDataset(self, path, dataset_type, **kwargs):
        self.load_dataset_calls.append((path, dataset_type, kwargs))


class _FakeData:
    """Stand-in for ``env.data`` (the DataService).

    Records metric/prediction generation and writes results into the shared
    fake cache, so the handlers observe a populated cache exactly as they would
    against the real in-process spine — no worker subprocess involved.
    """

    def __init__(self, cache):
        self._cache = cache
        self.make_key_calls = []
        self.generate_metric_calls = []
        self.generate_data_calls = []

    def make_metric_cache_key(self, metric_id, params, model, dataset):
        self.make_key_calls.append((metric_id, params, model, dataset))
        # Format-valid CacheKey string (identity__model__dataset).
        return f"{metric_id}__nil__nil"

    def generateMetric(self, metric_id, params, model, dataset, key=None):
        assert key is None, "the server builds the key itself (ADR 0061)"
        key = f"{metric_id}__f1234abcd__nil__nil"   # as a user metric's key is: has the formula hash
        self.generate_metric_calls.append((metric_id, key))
        self._cache._entries[key] = _make_metric_result(
            metric_id=metric_id, values=np.array([3.0])
        )
        return key

    def generateData(self, dt_key, model, dataset):
        self.generate_data_calls.append(dt_key)
        key = CacheKey(dt_key, model.fingerprint, dataset.fingerprint).format()
        payload = np.zeros((1, 3)) if dt_key == "forces" else np.array([1.0])
        self._cache._entries[key] = {dt_key: payload}


class _FakeVarDataset:
    """Variable-size dataset double exposing the transfer-array surface."""

    isVariable = True

    def __init__(self, transfer_arrays, n):
        self._transfer = transfer_arrays
        self._n = n

    def getN(self):
        return self._n

    def to_transfer_arrays(self):
        return dict(self._transfer)


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


class _FakeParentDataset:
    """A parent dataset for CREATE_SUBSET: only ``getElements`` is exercised."""

    def __init__(self, z):
        self._z = np.asarray(z)

    def getElements(self, index=None):
        return self._z


def test_dispatch_create_subset_resolves_tokens_and_materializes():
    """CREATE_SUBSET resolves the mixed index spec (integers + element tokens)
    against the parent's composition, then calls createAtomFilteredDataset."""
    parent = _FakeParentDataset([6, 6, 1, 1])  # C C H H
    env = _FakeEnv(datasets={"ds1": parent})

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        # "C" includes both carbons; "-H" is a no-op here (no H included);
        # integer 3 adds the last hydrogen.
        await s.dispatch("CREATE_SUBSET", ["ds1", ["C", 3]], {})

    _run(scenario())
    assert len(env.create_subset_calls) == 1
    dataset, idxs = env.create_subset_calls[0]
    assert dataset is parent
    assert idxs == [0, 1, 3]


def test_dispatch_create_subset_missing_parent_is_dropped():
    env = _FakeEnv(datasets={})

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("CREATE_SUBSET", ["nope", [0, 1]], {})

    _run(scenario())
    assert env.create_subset_calls == []


# ── DECLARE_SUBSET: frame-index subbing (ADR 0045 Phase 3) ──────────────────
def test_dispatch_declare_subset_materializes_frame_subset():
    """DECLARE_SUBSET turns a covered configuration-index list into a live
    SubDataset via declareSubDataset (parent, model, idx, name)."""
    parent = _FakeFrames()
    model = _FakeModel()
    env = _FakeEnv(datasets={"ds1": parent}, models={"m1": model})

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch(
            "DECLARE_SUBSET", ["ds1", [3, 5, 7]],
            {"model_fp": "m1", "name": "Basic Errors"},
        )

    _run(scenario())
    assert len(env.declare_subset_calls) == 1
    p, mdl, idx, name = env.declare_subset_calls[0]
    assert p is parent and mdl is model and idx == [3, 5, 7] and name == "Basic Errors"


def test_dispatch_declare_subset_without_model_uses_none():
    parent = _FakeFrames()
    env = _FakeEnv(datasets={"ds1": parent})

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("DECLARE_SUBSET", ["ds1", [0, 1]], {})

    _run(scenario())
    assert len(env.declare_subset_calls) == 1
    _, mdl, idx, name = env.declare_subset_calls[0]
    assert mdl is None and idx == [0, 1] and name == "Subset"


def test_dispatch_declare_subset_with_an_unknown_model_is_dropped():
    """A model only the window holds (the zero model) is not swapped for no
    model: that subset would have another fingerprint than the window's."""
    env = _FakeEnv(datasets={"ds1": _FakeFrames()})

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("DECLARE_SUBSET", ["ds1", [0, 1]], {"model_fp": "zero"})

    _run(scenario())
    assert env.declare_subset_calls == []


def test_dispatch_declare_subset_empty_indices_is_dropped():
    parent = _FakeFrames()
    env = _FakeEnv(datasets={"ds1": parent})

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("DECLARE_SUBSET", ["ds1", []], {})

    _run(scenario())
    assert env.declare_subset_calls == []


# ── REQUEST_TAB_LAYOUT: analysis-tab layout announcement (ADR 0045 Phase 3) ──
def test_dispatch_request_tab_layout_emits_cached_layout():
    """With a layout cached on the env, REQUEST_TAB_LAYOUT emits TAB_LAYOUT
    carrying exactly those tabs."""
    env = _FakeEnv()
    env.analysis_tab_layout = [{"name": "Custom", "panels": []}]

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("REQUEST_TAB_LAYOUT", [], {})
        return s

    s = _run(scenario())
    event, args, kwargs = unpack(s.outbound.get_nowait())
    assert event == control.TAB_LAYOUT
    assert kwargs["tabs"] == [{"name": "Custom", "panels": []}]


def test_dispatch_request_tab_layout_falls_back_to_bundled_tabs():
    """No cached layout (bare env) → the bundled built-in tabs are emitted, so
    the built-in analyses are always available."""
    import ffast.metrics.builtin  # noqa: F401
    env = _FakeEnv()  # no analysis_tab_layout attribute

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("REQUEST_TAB_LAYOUT", [], {})
        return s

    s = _run(scenario())
    event, args, kwargs = unpack(s.outbound.get_nowait())
    assert event == control.TAB_LAYOUT
    names = [t["name"] for t in kwargs["tabs"]]
    assert "Basic Errors" in names


def test_dispatch_create_subset_empty_resolution_is_dropped():
    """A spec that resolves to no atoms must not create an empty dataset."""
    parent = _FakeParentDataset([6, 6, 1, 1])
    env = _FakeEnv(datasets={"ds1": parent})

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("CREATE_SUBSET", ["ds1", ["Xe"]], {})  # element absent

    _run(scenario())
    assert env.create_subset_calls == []


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


class _FailingLoadEnv(_FakeEnv):
    """A load that raises, as a non-dataset file does inside the load task."""

    def taskLoadDataset(self, path, dataset_type, on_error=None, **kwargs):
        on_error(IndexError("list index out of range"))

    def taskLoadPrepredictedDataset(self, path, dataset_fp, on_error=None, **kwargs):
        on_error(ValueError("no forces"))


@pytest.mark.parametrize("event, args, kind", [
    ("LOAD_DATASET", ["/x/README.md", "ase"], "dataset"),
    ("LOAD_PREDICTION", ["/x/pred.xyz", "abcdef123456"], "prediction"),
])
def test_a_failed_load_tells_the_window_which_file_and_why(event, args, kind):
    async def scenario():
        s = ServerSession(_FailingLoadEnv(), asyncio.Queue())
        await s.dispatch(event, args, {})
        return await _drain(s)

    messages = _run(scenario())

    name, _, kwargs = messages[0]
    assert name == control.LOAD_FAILED
    assert kwargs["kind"] == kind
    assert kwargs["path"] == args[0]
    assert kwargs["error"] in ("list index out of range", "no forces")


class _ExampleEnv:
    """Runs a queued load at once and remembers which paths it loaded."""

    def __init__(self):
        self.calls = []
        self._loaded = {}
        self.loading = self   # the Loading Coordinator's load* methods

    def newTask(self, func, **kwargs):
        func(taskID=1)

    def getDatasetFromPath(self, path):
        return self._loaded.get(path)

    def getKeyFromPath(self, path):
        return self._loaded.get(path)

    def loadDataset(self, path, dataset_type, on_error=None):
        self.calls.append(("dataset", os.path.basename(path), dataset_type))
        self._loaded[path] = "ds-fp"

    def loadPrepredictedDataset(self, path, dataset_fp, on_error=None, **keys):
        self.calls.append(("prediction", os.path.basename(path), dataset_fp, keys))
        self._loaded[path] = "model-fp"


def test_load_example_loads_the_dataset_then_its_prediction_once():
    env = _ExampleEnv()

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("LOAD_EXAMPLE", [], {})
        await s.dispatch("LOAD_EXAMPLE", [], {})   # a second click loads nothing

    _run(scenario())
    assert env.calls == [
        ("dataset", "dataset.xyz", "ase (auto)"),
        ("prediction", "prediction.xyz", "ds-fp",
         {"selected_energy_key": "MACE_energy", "selected_force_key": "MACE_forces"}),
    ]


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

def test_replay_empty_env_enqueues_only_the_shared_pieces():
    # replay() is synchronous and uses put_nowait, so no loop needed.
    s = ServerSession(_FakeEnv(), asyncio.Queue())
    s.replay()
    sent = [unpack(s.outbound.get_nowait()) for _ in range(s.outbound.qsize())]
    assert [e for e, _, _ in sent] == [
        "METRIC_CATALOG", "TAB_LAYOUT", "USER_METRICS", "METRIC_USERS"]
    assert sent[-1][2] == {"users": {}}
    assert s.outbound.empty()   # no datasets, no models, no open views   # no datasets, no models, no open views


# ── _on_request_metric ────────────────────────────────────────────────────────

def test_request_metric_cache_hit_returns_result_without_computing():
    """A cached MetricResult is served straight back as an ok=True
    METRIC_RESULT — the compute path (env.data) is never touched."""
    key = "ffast.force_difference[atom+structure;length]__nil__nil"  # what the server's own key builder gives
    result = _make_metric_result(values=np.array([1.5, 2.5]))
    env = _FakeEnv(datasets={"ds1": object()}, cache={key: result})
    env.data = _FakeData(env.cache)

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch(
            "REQUEST_METRIC", ["ffast.force_difference[atom+structure;length]", "m1"], {"dataset_fp": "ds1"}
        )
        return s.outbound.get_nowait()

    event, args, kwargs = unpack(_run(scenario()))
    assert event == control.METRIC_RESULT
    assert args == ["m1", "ffast.force_difference[atom+structure;length]"]  # (request_id, metric_id) positional
    recovered = unpack_metric_result(kwargs)
    assert recovered is not None
    assert recovered.metric_id == "ffast.force_difference[atom+structure;length]"
    np.testing.assert_array_equal(recovered.values, np.array([1.5, 2.5]))


def test_request_metric_missing_model_signals_client_fallback():
    """A model-dependent metric whose model isn't on the server replies ok=False
    (server_can_compute=False), so the client falls back to in-process compute."""
    env = _FakeEnv(datasets={"ds1": object()}, cache={})   # empty cache, no models
    env.data = _FakeData(env.cache)

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch(
            "REQUEST_METRIC", ["ffast.energy_difference[dataset;mae]", "m2"],
            {"model_fp": "realmodel", "dataset_fp": "ds1"},
        )
        return s.outbound.get_nowait()

    event, args, kwargs = unpack(_run(scenario()))
    assert event == control.METRIC_RESULT
    assert args == ["m2", "ffast.energy_difference[dataset;mae]"]
    assert kwargs["ok"] is False
    assert unpack_metric_result(kwargs) is None


def test_request_metric_computes_on_cache_miss_and_replies_with_request_id():
    """Cache miss: the handler looks the key up through make_metric_cache_key,
    computes through env.data.generateMetric (which builds and returns the key it
    stored under), and replies with the request id, not the key (ADR 0061)."""
    env = _FakeEnv(datasets={"ds1": object()}, cache={})
    env.data = _FakeData(env.cache)

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("REQUEST_METRIC", ["ffast.force_difference[structure;length;rmse]", "m3"], {"dataset_fp": "ds1"})
        return s.outbound.get_nowait()

    event, args, kwargs = unpack(_run(scenario()))
    stored_key = "ffast.force_difference[structure;length;rmse]__f1234abcd__nil__nil"  # carries the formula hash
    assert event == control.METRIC_RESULT
    assert args == ["m3", "ffast.force_difference[structure;length;rmse]"]
    assert env.data.make_key_calls, "make_metric_cache_key should be used"
    assert env.data.generate_metric_calls == [("ffast.force_difference[structure;length;rmse]", stored_key)]
    assert list(env.cache._entries) == [stored_key]   # nothing stored under the request id
    recovered = unpack_metric_result(kwargs)
    assert recovered is not None
    assert recovered.metric_id == "ffast.force_difference[structure;length;rmse]"
    np.testing.assert_array_equal(recovered.values, np.array([3.0]))


# ── _on_request_subdataset_arrays ─────────────────────────────────────────────

def test_request_subdataset_arrays_concatenates_variable_prediction_forces():
    """Variable-dataset predictions arrive as a list of (natoms_i, 3) arrays and
    are flattened to (total_atoms, 3); base transfer arrays + model names ride
    along in the same message."""
    fp = "dsvar"
    f1 = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])   # (2, 3)
    f2 = np.array([[7.0, 8.0, 9.0]])                     # (1, 3)
    base = {"R_flat": np.arange(9.0).reshape(3, 3), "z_flat": np.array([1, 6, 8])}
    ds = _FakeVarDataset(base, n=2)
    pred_key = CacheKey("forces", "modelA", fp).format()
    model = type("_M", (), {"name": "SO3LR"})()
    env = _FakeEnv(
        datasets={fp: ds}, models={"modelA": model},
        cache={pred_key: {"forces": [f1, f2]}},
    )

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("REQUEST_SUBDATASET_ARRAYS", [fp], {})
        return s.outbound.get_nowait()

    event, args, kwargs = unpack(_run(scenario()))
    assert event == control.SUBDATASET_ARRAYS
    assert args == [fp]
    arrays = unpack_arrays(kwargs)
    pa_key = PredictionArrayKey("forces", "modelA").format()
    np.testing.assert_array_equal(
        arrays[pa_key], np.concatenate([f1, f2], axis=0)   # (3, 3)
    )
    np.testing.assert_array_equal(arrays["z_flat"], np.array([1, 6, 8]))
    assert kwargs["model_names"] == {"modelA": "SO3LR"}


def test_request_subdataset_arrays_unknown_fingerprint_emits_nothing():
    env = _FakeEnv(datasets={})

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("REQUEST_SUBDATASET_ARRAYS", ["nope"], {})
        return s

    s = _run(scenario())
    assert s.outbound.empty()


# ── _on_request_prediction_arrays ─────────────────────────────────────────────

def test_request_prediction_arrays_assembles_cached_ghost_predictions():
    """Ghost model: cached energy+forces are packed onto the prediction-only
    channel and NO on-demand generation runs."""
    ds_fp, m_fp = "ds1", "ghost1"
    energy = np.array([1.0, 2.0])
    forces = np.array([[0.1, 0.2, 0.3]])
    cache = {
        CacheKey("energy", m_fp, ds_fp).format(): {"energy": energy},
        CacheKey("forces", m_fp, ds_fp).format(): {"forces": forces},
    }
    ghost = type("_G", (), {"isGhost": True})()
    env = _FakeEnv(datasets={ds_fp: object()}, models={m_fp: ghost}, cache=cache)
    env.data = _FakeData(env.cache)

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("REQUEST_PREDICTION_ARRAYS", [ds_fp, m_fp], {})
        return s.outbound.get_nowait()

    event, args, kwargs = unpack(_run(scenario()))
    assert event == control.PREDICTION_ARRAYS
    assert args == [ds_fp, m_fp]
    arrays = unpack_arrays(kwargs)
    np.testing.assert_array_equal(
        arrays[PredictionArrayKey("energy", m_fp).format()], energy
    )
    np.testing.assert_array_equal(
        arrays[PredictionArrayKey("forces", m_fp).format()], forces
    )
    assert env.data.generate_data_calls == []   # ghost → no on-demand compute


def test_request_prediction_arrays_generates_on_demand_for_real_model():
    """Real (non-ghost) model + empty cache: the handler generates energy and
    forces on demand, then assembles them for transfer."""
    ds_fp, m_fp = "ds2", "real2"
    dataset = type("_D", (), {"fingerprint": ds_fp})()
    model = type("_M", (), {"isGhost": False, "fingerprint": m_fp})()
    env = _FakeEnv(datasets={ds_fp: dataset}, models={m_fp: model}, cache={})
    env.data = _FakeData(env.cache)

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("REQUEST_PREDICTION_ARRAYS", [ds_fp, m_fp], {})
        return s.outbound.get_nowait()

    event, args, kwargs = unpack(_run(scenario()))
    assert event == control.PREDICTION_ARRAYS
    assert env.data.generate_data_calls == ["energy", "forces"]
    arrays = unpack_arrays(kwargs)
    assert PredictionArrayKey("energy", m_fp).format() in arrays
    assert PredictionArrayKey("forces", m_fp).format() in arrays


# ── _on_view_command (VIEW_COMMAND: model=None, own ValidationError branch) ────

def test_view_command_applies_and_emits_command_result_then_scene_patch():
    """A well-formed command parses via the pydantic discriminated union,
    applies to the view, and emits COMMAND_RESULT + SCENE_PATCH."""
    env = _FakeEnv()

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("OPEN_VIEW", [], {"view_id": "v1"})   # no dataset needed
        snapshot = s.outbound.get_nowait()                     # SCENE_SNAPSHOT
        await s.dispatch("VIEW_COMMAND", [], {
            "type": "SET_SELECTION", "view_id": "v1", "view_version": 0,
            "name": "picked", "scope": "current_structure", "indices": [0, 1],
        })
        return s, snapshot

    s, snapshot = _run(scenario())
    assert unpack(snapshot)[0] == control.SCENE_SNAPSHOT

    result_event, _, result_kwargs = unpack(s.outbound.get_nowait())
    assert result_event == control.COMMAND_RESULT
    assert result_kwargs["success"] is True
    assert result_kwargs["new_version"] == 1
    # Several views share a connection (ADR 0056 independent 3D panels), so
    # the result names the view it answers.
    assert result_kwargs["view_id"] == "v1"

    patch_event, _, patch_kwargs = unpack(s.outbound.get_nowait())
    assert patch_event == control.SCENE_PATCH
    assert "selections" in patch_kwargs["changed"]
    assert s.outbound.empty()


def test_view_command_malformed_payload_is_dropped():
    """The VIEW_COMMAND route carries model=None and validates inside the
    handler; a payload that fails the pydantic parse is dropped with no reply."""
    env = _FakeEnv()

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        # SET_SELECTION missing required name/scope/indices → ValidationError.
        await s.dispatch("VIEW_COMMAND", [], {
            "type": "SET_SELECTION", "view_id": "v1", "view_version": 0,
        })
        return s

    s = _run(scenario())
    assert s.outbound.empty()


def test_view_command_for_unknown_view_is_dropped():
    """A well-formed command targeting a view that was never opened is dropped
    (the view-is-None branch) with nothing emitted."""
    env = _FakeEnv()

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("VIEW_COMMAND", [], {
            "type": "SET_FRAME", "view_id": "ghost", "view_version": 0,
            "frame_index": 2,
        })
        return s

    s = _run(scenario())
    assert s.outbound.empty()


# ── delete-race graceful degrade (ADR 0044 Phase 3) ─────────────────────────
# Another connection may delete the dataset/model this view is showing between
# a command arriving and the scene rebuild running. build_scene already
# degrades to a bare scene when the dataset lookup returns None; these pin the
# deeper guard — an unexpected exception from further in the pipeline (e.g. a
# colour-by metric erroring on a just-deleted model) must not stop
# COMMAND_RESULT/SCENE_PATCH (or SCENE_SNAPSHOT) from reaching the client.

def test_view_command_scene_rebuild_exception_degrades_gracefully():
    env = _FakeEnv()

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("OPEN_VIEW", [], {"view_id": "v1"})
        s.outbound.get_nowait()  # SCENE_SNAPSHOT
        with patch(
            "ffast.visualization.scene_builder.build_scene",
            side_effect=RuntimeError("boom: referenced object vanished mid-rebuild"),
        ):
            await s.dispatch("VIEW_COMMAND", [], {
                "type": "TOGGLE_FEATURE", "view_id": "v1", "view_version": 0,
                "feature": "kabsch_align", "enabled": True,
            })
        return s

    s = _run(scenario())  # must not raise

    result_event, _, result_kwargs = unpack(s.outbound.get_nowait())
    assert result_event == control.COMMAND_RESULT
    assert result_kwargs["success"] is True

    patch_event, _, patch_kwargs = unpack(s.outbound.get_nowait())
    assert patch_event == control.SCENE_PATCH
    assert "atoms" in patch_kwargs["changed"]
    assert patch_kwargs["atoms"] is None   # degraded to an empty scene, not crashed
    assert s.outbound.empty()


def test_open_view_snapshot_exception_degrades_to_bare_scene():
    env = _FakeEnv(datasets={"ds1": object()})  # present but "poisoned"

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        with patch(
            "ffast.visualization.scene_builder.build_scene",
            side_effect=RuntimeError("boom: dataset in a torn-down state"),
        ):
            await s.dispatch("OPEN_VIEW", [], {"view_id": "v1", "dataset_ref": "ds1"})
        return s

    s = _run(scenario())  # must not raise

    snapshot_event, _, snapshot_kwargs = unpack(s.outbound.get_nowait())
    assert snapshot_event == control.SCENE_SNAPSHOT
    assert snapshot_kwargs["scene"]["atoms"] is None
    assert s.outbound.empty()


# ── _on_export_subset (ADR 0045 Phase 4, issue 20) ──────────────────────────

class _FakeExportDataset:
    """Stands in for whatever dataset object EXPORT_SUBSET resolves — the
    handler only reads ``getN()`` (reported count); the actual write goes through the mocked ASE loader static
    methods below, so this fake never needs real coordinate arrays."""

    def __init__(self, n=3, is_variable=False):
        self.isVariable = is_variable
        self._n = n

    def getN(self):
        return self._n


def test_dispatch_export_subset_missing_dataset_emits_error():
    env = _FakeEnv(datasets={})

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("EXPORT_SUBSET", [], {"fingerprint": "nope", "path": "/tmp/x.extxyz"})
        return s.outbound.get_nowait()

    event, _args, kwargs = unpack(_run(scenario()))
    assert event == control.SUBSET_EXPORTED
    assert kwargs["ok"] is False
    assert "not found" in kwargs["error"]


@pytest.mark.parametrize("is_variable, n", [(False, 5), (True, 2)], ids=["uniform", "variable"])
def test_dispatch_export_subset_uses_the_one_ase_saver(tmp_path, is_variable, n):
    """Uniform and variable datasets (plain, SubDataset or AtomFilteredDataset)
    go to the one ``aseDatasetLoader.saveDataset``, which writes frame by
    frame, and the reply reports getN() structures."""
    ds = _FakeExportDataset(n=n, is_variable=is_variable)
    env = _FakeEnv(datasets={"ds1": ds})
    target = tmp_path / "out.extxyz"

    with patch("ffast.loaders.ase.aseDatasetLoader.saveDataset") as saver:

        async def scenario():
            s = ServerSession(env, asyncio.Queue())
            await s.dispatch("EXPORT_SUBSET", [], {"fingerprint": "ds1", "path": str(target)})
            return s.outbound.get_nowait()

        event, _args, kwargs = unpack(_run(scenario()))

    assert event == control.SUBSET_EXPORTED
    assert kwargs == {"ok": True, "path": str(target), "error": None, "n": n}
    saver.assert_called_once_with(ds, str(target), "extxyz")


def test_dispatch_export_subset_expands_user_and_relative_path(tmp_path, monkeypatch):
    """A browser-typed "~/…" path (no server file dialog to produce an
    absolute one) is expanded server-side before writing and reporting."""
    monkeypatch.setenv("HOME", str(tmp_path))
    ds = _FakeExportDataset()
    env = _FakeEnv(datasets={"ds1": ds})

    with patch("ffast.loaders.ase.aseDatasetLoader.saveDataset") as uniform:
        async def scenario():
            s = ServerSession(env, asyncio.Queue())
            await s.dispatch(
                "EXPORT_SUBSET", [], {"fingerprint": "ds1", "path": "~/sub/out.extxyz"},
            )
            return s.outbound.get_nowait()

        event, _args, kwargs = unpack(_run(scenario()))

    expected = str(tmp_path / "sub" / "out.extxyz")
    assert kwargs["ok"] is True
    assert kwargs["path"] == expected
    uniform.assert_called_once_with(ds, expected, "extxyz")


def test_dispatch_export_subset_reports_error_on_write_failure(tmp_path):
    ds = _FakeExportDataset()
    env = _FakeEnv(datasets={"ds1": ds})
    target = tmp_path / "out.extxyz"

    with patch(
        "ffast.loaders.ase.aseDatasetLoader.saveDataset",
        side_effect=OSError("disk full"),
    ):
        async def scenario():
            s = ServerSession(env, asyncio.Queue())
            await s.dispatch("EXPORT_SUBSET", [], {"fingerprint": "ds1", "path": str(target)})
            return s.outbound.get_nowait()

        event, _args, kwargs = unpack(_run(scenario()))

    assert event == control.SUBSET_EXPORTED
    assert kwargs["ok"] is False
    assert "disk full" in kwargs["error"]


def test_dispatch_export_subset_respects_explicit_format(tmp_path):
    """An explicit ``format`` kwarg overrides the extension-inferred default."""
    ds = _FakeExportDataset()
    env = _FakeEnv(datasets={"ds1": ds})
    target = tmp_path / "out.xyz"

    with patch("ffast.loaders.ase.aseDatasetLoader.saveDataset") as uniform:
        async def scenario():
            s = ServerSession(env, asyncio.Queue())
            await s.dispatch(
                "EXPORT_SUBSET", [],
                {"fingerprint": "ds1", "path": str(target), "format": "xyz"},
            )
            return s.outbound.get_nowait()

        unpack(_run(scenario()))

    uniform.assert_called_once_with(ds, str(target), "xyz")


# ── session save/load acks (ADR 0050) ─────────────────────────────────────────
#
# SAVE_SESSION / LOAD_SESSION run in a task, and TASK_DONE carries only a task
# id that the requesting client never learns. A client therefore could not tell
# its own save's completion from an unrelated dataset load's, and the web client
# resolved whichever finished first as its own — reporting "Saved session" for a
# dataset load. SESSION_SAVED / SESSION_LOADED name the operation and its path.

class _RecordingPersistence:
    """Persistence double: records calls and can be made to fail."""

    def __init__(self, error=None):
        self.saved = []
        self.loaded = []
        self._error = error

    def save(self, path, taskID=None):
        self.saved.append(path)
        if self._error:
            raise RuntimeError(self._error)

    def load(self, path, taskID=None):
        self.loaded.append(path)
        if self._error:
            raise RuntimeError(self._error)


class _InlineTaskEnv(_FakeEnv):
    """Runs queued tasks immediately, so the ack is observable in one step.

    The real TaskManager runs the body on a worker thread; the handler's ack is
    handed back to the loop with ``call_soon_threadsafe``, which works the same
    way when the caller is already on the loop.
    """

    def __init__(self, persistence):
        super().__init__()
        self.persistence = persistence
        self.task_names = []

    def newTask(self, func, args=(), kwargs=None, visual=False, name=None,
                threaded=False, **_):
        self.task_names.append(name)
        try:
            func(*args, **(kwargs or {}), taskID="t1")
        except Exception:
            pass          # the real TaskManager logs and moves on


async def _drain(session):
    """Let queued call_soon_threadsafe callbacks run, then read the queue."""
    await asyncio.sleep(0)
    out = []
    while not session.outbound.empty():
        out.append(unpack(session.outbound.get_nowait()))
    return out


def test_save_session_acks_with_the_resolved_path():
    persistence = _RecordingPersistence()
    env = _InlineTaskEnv(persistence)

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("SAVE_SESSION", [], {"path": "~/sess"})
        return s, await _drain(s)

    s, messages = _run(scenario())

    assert len(persistence.saved) == 1
    saved_path = persistence.saved[0]
    assert os.path.isabs(saved_path), "the ~ must be expanded server-side"

    event, args, kwargs = messages[0]
    assert event == control.SESSION_SAVED
    assert kwargs == {"ok": True, "path": saved_path, "error": None}


def test_load_session_acks_with_the_resolved_path():
    persistence = _RecordingPersistence()
    env = _InlineTaskEnv(persistence)

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("LOAD_SESSION", [], {"path": "~/sess"})
        return s, await _drain(s)

    s, messages = _run(scenario())

    assert len(persistence.loaded) == 1
    event, args, kwargs = messages[0]
    assert event == control.SESSION_LOADED
    assert kwargs["ok"] is True
    assert kwargs["path"] == persistence.loaded[0]


def test_failed_save_acks_not_ok_with_the_reason():
    """A failure has to reach the client: the replaced TASK_DONE guess could
    only ever report ok/not-ok, never why."""
    env = _InlineTaskEnv(_RecordingPersistence(error="disk full"))

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("SAVE_SESSION", [], {"path": "/tmp/sess"})
        return s, await _drain(s)

    _, messages = _run(scenario())

    event, _, kwargs = messages[0]
    assert event == control.SESSION_SAVED
    assert kwargs["ok"] is False
    assert "disk full" in kwargs["error"]


def test_session_tasks_keep_their_desktop_visible_names():
    """The desktop Tasks panel shows these strings; the ack did not replace the
    task, so they must not have changed."""
    env = _InlineTaskEnv(_RecordingPersistence())

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("SAVE_SESSION", [], {"path": "/tmp/a"})
        await s.dispatch("LOAD_SESSION", [], {"path": "/tmp/a"})

    _run(scenario())
    assert env.task_names == ["Saving session", "Loading save"]


# ── DECLARE_SUBSET from a plot's view: SUB follows the zoom ─────────────────
class _FakeFrames:
    """A parent dataset with a fingerprint and a frame count."""

    def __init__(self, fp="ds1", n=10):
        self.fingerprint = fp
        self._n = n

    def getN(self):
        return self._n


class _FakeSub:
    def __init__(self, frames, active=True):
        self.frames = frames
        self.active = active

    def setActive(self, state):
        self.active = state


class _FakeModel:
    fingerprint = "m1"


def _sub_fp(parent, model, name):
    from ffast.loaders.dataset import DatasetView
    return DatasetView.subsetFingerprint(parent, model, name)


def _declare(env, **kwargs):
    async def scenario():
        await ServerSession(env, asyncio.Queue()).dispatch("DECLARE_SUBSET", [], kwargs)
    _run(scenario())


TIMELINE_VIEW = {"kind": "timeline", "metrics": {"y": "ffast.energy_difference"},
                 "x": [0.6, 3.2]}


def test_declare_subset_from_a_view_covers_the_frames_on_screen():
    parent, model = _FakeFrames(), _FakeModel()
    env = _FakeEnv(datasets={"ds1": parent}, models={"m1": model})
    _declare(env, parent_fingerprint="ds1", model_fp="m1", name="Energy MAE timeline",
             view=TIMELINE_VIEW)
    assert env.declare_subset_calls == [(parent, model, [1, 2, 3], "Energy MAE timeline")]


def test_a_view_with_the_same_frames_changes_nothing():
    """A zoom that keeps the same frames does not rebuild the subset (and so
    does not make every window reload it)."""
    parent, model = _FakeFrames(), _FakeModel()
    sub = _FakeSub([1, 2, 3])
    env = _FakeEnv(datasets={"ds1": parent, _sub_fp(parent, model, "T"): sub},
                   models={"m1": model})
    _declare(env, parent_fingerprint="ds1", model_fp="m1", name="T", view=TIMELINE_VIEW)
    assert env.declare_subset_calls == []


def test_a_view_with_no_frames_keeps_the_last_ones():
    env = _FakeEnv(datasets={"ds1": _FakeFrames()})
    _declare(env, parent_fingerprint="ds1", name="T",
             view={**TIMELINE_VIEW, "x": [40, 50]})
    assert env.declare_subset_calls == []


def test_unticking_sub_hides_the_subset():
    parent = _FakeFrames()
    sub = _FakeSub([1, 2, 3])
    env = _FakeEnv(datasets={"ds1": parent, _sub_fp(parent, None, "T"): sub})
    _declare(env, parent_fingerprint="ds1", name="T", active=False)
    assert sub.active is False and env.declare_subset_calls == []


def test_ticking_sub_again_shows_the_hidden_subset():
    parent = _FakeFrames()
    sub = _FakeSub([1, 2, 3], active=False)
    env = _FakeEnv(datasets={"ds1": parent, _sub_fp(parent, None, "T"): sub})
    _declare(env, parent_fingerprint="ds1", name="T", view=TIMELINE_VIEW)
    assert env.declare_subset_calls == [(parent, None, [1, 2, 3], "T")]


def _declare_and_read(env, **kwargs):
    async def scenario():
        session = ServerSession(env, asyncio.Queue())
        await session.dispatch("DECLARE_SUBSET", [], kwargs)
        out = []
        while not session.outbound.empty():
            out.append(unpack(session.outbound.get_nowait()))
        return out
    return _run(scenario())


def test_the_window_that_ticked_sub_is_told_which_subset_it_made():
    """So that window can show the subset in its main view."""
    parent = _FakeFrames()
    env = _FakeEnv(datasets={"ds1": parent})
    [(event, _, kwargs)] = _declare_and_read(
        env, parent_fingerprint="ds1", name="T", view=TIMELINE_VIEW)
    assert event == control.SUBSET_DECLARED
    assert kwargs == {"fingerprint": _sub_fp(parent, None, "T"), "parent_fingerprint": "ds1",
                      "model_fp": None, "name": "T"}


def test_it_is_told_even_when_the_frames_did_not_change():
    parent = _FakeFrames()
    env = _FakeEnv(datasets={"ds1": parent, _sub_fp(parent, None, "T"): _FakeSub([1, 2, 3])})
    [(event, _, _)] = _declare_and_read(env, parent_fingerprint="ds1", name="T", view=TIMELINE_VIEW)
    assert event == control.SUBSET_DECLARED


def test_hiding_a_subset_or_finding_no_frames_tells_nothing():
    parent = _FakeFrames()
    env = _FakeEnv(datasets={"ds1": parent, _sub_fp(parent, None, "T"): _FakeSub([1, 2, 3])})
    assert _declare_and_read(env, parent_fingerprint="ds1", name="T", active=False) == []
    assert _declare_and_read(env, parent_fingerprint="ds1", name="U",
                             view={**TIMELINE_VIEW, "x": [40, 50]}) == []


class _FakeFrameSubset:
    isSubDataset = True
    isAtomFiltered = False

    def __init__(self, parent, frames):
        self.parent = parent
        self.frames = frames


def test_a_frame_subset_draws_its_parents_predicted_forces():
    """A prediction's forces for a subset are its forces for the parent's
    frames, so the 3D view of a subset keeps its force arrows."""
    forces = np.arange(4 * 2 * 3, dtype=float).reshape(4, 2, 3)
    parent = _FakeFrames("ds1", n=4)
    env = _FakeEnv(datasets={"ds1": parent, "sub": _FakeFrameSubset(parent, [3, 1])},
                   cache={"forces__m1__ds1": {"forces": forces}})
    view = ServerSession(env, asyncio.Queue()).get_prediction("sub", "m1")
    assert np.array_equal(view.forces[0], forces[3]) and np.array_equal(view.forces[1], forces[1])
    assert ServerSession(env, asyncio.Queue()).get_prediction("ds1", "nope") is None


def test_a_frame_subset_of_structures_of_different_sizes_draws_its_parents_forces():
    forces = [np.zeros((1, 3)), np.ones((2, 3)), np.full((3, 3), 2.0)]
    parent = _FakeFrames("ds1", n=3)
    env = _FakeEnv(datasets={"ds1": parent, "sub": _FakeFrameSubset(parent, [2, 0])},
                   cache={"forces__m1__ds1": {"forces": forces}})
    view = ServerSession(env, asyncio.Queue()).get_prediction("sub", "m1")
    assert [f.shape for f in view.forces] == [(3, 3), (1, 3)]


# ── atom colouring reads a metric over the whole dataset ─────────────────────
class _ColourData(_FakeData):
    """A DataService double whose prediction may be missing."""

    def __init__(self, cache, missing=()):
        super().__init__(cache)
        self.inputResolver = type("R", (), {
            "missing_prediction_keys": lambda _self, metric_id, model, dataset: list(missing)})()


def test_colouring_reads_the_metric_the_plots_stored():
    from types import SimpleNamespace
    stored = _make_metric_result(values=np.array([1.0, 2.0]))
    env = _FakeEnv(datasets={"ds1": SimpleNamespace(fingerprint="ds1")},
                   models={"m1": SimpleNamespace(fingerprint="m1")},
                   cache={"ffast.force_difference[atom+structure;length]__nil__nil": stored})
    env.data = _ColourData(env.cache)
    assert ServerSession(env, asyncio.Queue()).get_metric("ffast.force_difference[atom+structure;length]", {}, "ds1", "m1") is stored
    assert env.data.generate_metric_calls == []


def test_colouring_computes_the_whole_dataset_when_the_prediction_is_there():
    from types import SimpleNamespace
    env = _FakeEnv(datasets={"ds1": SimpleNamespace(fingerprint="ds1")},
                   models={"m1": SimpleNamespace(fingerprint="m1")})
    env.data = _ColourData(env.cache)
    result = ServerSession(env, asyncio.Queue()).get_metric("ffast.force_difference[atom+structure;length]", {}, "ds1", "m1")
    assert list(result.values) == [3.0]
    assert [c[0] for c in env.data.generate_metric_calls] == ["ffast.force_difference[atom+structure;length]"]


def test_colouring_never_starts_a_prediction_of_every_frame():
    """A live model predicts the frame on screen; colouring must not ask it
    for every frame, so it gets no whole-dataset result (and uses the frame)."""
    from types import SimpleNamespace
    env = _FakeEnv(datasets={"ds1": SimpleNamespace(fingerprint="ds1")},
                   models={"m1": SimpleNamespace(fingerprint="m1")})
    env.data = _ColourData(env.cache, missing=["forces__m1__ds1"])
    session = ServerSession(env, asyncio.Queue())
    assert session.get_metric("ffast.force_difference[atom+structure;length]", {}, "ds1", "m1") is None
    assert env.data.generate_metric_calls == [] and env.data.generate_data_calls == []
    # A model or dataset that is gone gives nothing either.
    assert session.get_metric("ffast.force_difference[atom+structure;length]", {}, "ds1", "gone") is None
    assert session.get_metric("ffast.force_difference[atom+structure;length]", {}, "gone", "m1") is None


# ── OPEN_VIEW start_from: a subset opens with its parent's look ─────────────
def test_a_new_view_can_start_from_another_views_look():
    """Colouring, display, bonds (the view's stage parameters and features)
    and the camera are copied when the view is made; picks are not."""
    env = _FakeEnv()

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("OPEN_VIEW", [], {"view_id": "parent"})
        parent = s.views["parent"].state
        parent.parameters = {"ffast.bonds": {"style": "sticks"}}
        parent.enabled_features = ["forces"]
        parent.camera = parent.camera.model_copy(update={"distance": 3.0})
        parent.selections = {"picked": None}
        await s.dispatch("OPEN_VIEW", [], {"view_id": "sub", "start_from": "parent"})
        sub = s.views["sub"].state
        assert sub.parameters == {"ffast.bonds": {"style": "sticks"}}
        assert sub.parameters is not parent.parameters
        assert sub.enabled_features == ["forces"] and sub.camera.distance == 3.0
        assert sub.selections == {}

        # Reopening a view keeps its own look.
        sub.parameters = {"ffast.bonds": {"style": "lines"}}
        await s.dispatch("OPEN_VIEW", [], {"view_id": "sub", "start_from": "parent"})
        assert s.views["sub"].state.parameters == {"ffast.bonds": {"style": "lines"}}

    _run(scenario())


def test_request_metric_with_a_key_is_rejected():
    """ADR 0061: the wire has no `key`; a client that still sends one is dropped."""
    env = _FakeEnv(datasets={"ds1": object()}, cache={})
    env.data = _FakeData(env.cache)

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("REQUEST_METRIC", ["ffast.force_difference[structure;length;rmse]", "m4"],
                         {"dataset_fp": "ds1", "key": "ffast.force_difference[structure;length;rmse]__nil__nil"})
        return s.outbound.empty()

    assert _run(scenario())
    assert env.data.generate_metric_calls == []


def test_request_metric_without_a_request_id_is_dropped():
    env = _FakeEnv(datasets={"ds1": object()}, cache={})
    env.data = _FakeData(env.cache)

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("REQUEST_METRIC", ["ffast.force_difference[structure;length;rmse]"], {"dataset_fp": "ds1"})
        return s.outbound.empty()

    assert _run(scenario())

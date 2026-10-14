"""The user-metric messages of the server session (ADR 0057 rules 5-7).

A change answers the window that asked (USER_METRIC_SAVED), drops the cached
results of the changed metrics, refreshes the worker pool, then tells every
window (METRICS_UPDATED, then the list). Real UserMetrics over a temporary
folder, a fake environment and queues — no socket.
"""
import asyncio

import pytest

import ffast.metrics.builtin  # noqa: F401
from ffast.config.user_metrics import UserMetricStore, UserMetrics
from ffast.metrics.registry import default_registry
from ffast.protocol import control
from ffast.protocol.rpc import unpack
from ffast.session.server_session import ServerSession, view_colouring_users

METRIC = {"id": "t57.double", "expr": "2 * f", "vars": {"f": "ffast.force_component_mae"}}


class _Data:
    def __init__(self):
        self.invalidated = []
        self.refreshed = 0
        outer = self

        class _Exec:
            def refresh_registry(self):
                outer.refreshed += 1
        self.metricExecutor = _Exec()

    def invalidate_metrics(self, ids):
        self.invalidated.append(set(ids))
        return []


class _Env:
    pass


@pytest.fixture
def env(tmp_path):
    env = _Env()
    env.user_metrics = UserMetrics(UserMetricStore(tmp_path / "metrics"))
    env.user_metrics.load()
    env.data = _Data()
    yield env
    for mid in [m for m in default_registry.list_metrics() if m.startswith("t57.")]:
        default_registry.unregister(mid)
    default_registry.freeze()


def _dispatch(env, event, kwargs):
    broadcasts = []

    async def scenario():
        s = ServerSession(env, asyncio.Queue(), broadcast=broadcasts.append)
        await s.dispatch(event, [], kwargs)
        out = []
        while not s.outbound.empty():
            out.append(unpack(s.outbound.get_nowait()))
        return out

    own = asyncio.run(scenario())
    return own, [unpack(b) for b in broadcasts]


def test_saving_answers_the_window_then_tells_every_window(env):
    own, everyone = _dispatch(env, control.SAVE_USER_METRIC, {"metric": METRIC})
    assert [(e, k) for e, _, k in own] == [(control.USER_METRIC_SAVED, {
        "ok": True, "action": "save", "id": "t57.double", "error": None, "conflict": False})]
    assert [e for e, _, _ in everyone] == [control.METRICS_UPDATED, control.USER_METRICS]
    [entry] = everyone[1][2]["metrics"]
    assert (entry["id"], entry["status"], entry["shape"]) == ("t57.double", "ok", "scalar")


def test_a_change_drops_cached_results_and_refreshes_the_workers(env):
    _dispatch(env, control.SAVE_USER_METRIC, {"metric": METRIC})
    _dispatch(env, control.SAVE_USER_METRIC, {
        "metric": {**METRIC, "expr": "3 * f"}, "previous_id": "t57.double", "overwrite": True})
    assert env.data.invalidated == [{"t57.double"}, {"t57.double"}]
    assert env.data.refreshed == 2


def test_a_refused_change_says_why_and_tells_nobody(env):
    own, everyone = _dispatch(env, control.SAVE_USER_METRIC,
                              {"metric": {**METRIC, "id": "ffast.force_component_mae"}})
    assert own[0][2]["ok"] is False and "already taken" in own[0][2]["error"]
    assert everyone == [] and env.data.refreshed == 0


def test_a_stale_save_is_flagged_as_a_conflict(env):
    _dispatch(env, control.SAVE_USER_METRIC, {"metric": METRIC})
    own, everyone = _dispatch(env, control.SAVE_USER_METRIC, {
        "metric": {**METRIC, "expr": "4 * f"}, "previous_id": "t57.double",
        "base_revision": "not-the-current-one"})
    assert own[0][2]["conflict"] is True and everyone == []


def test_delete_while_used_names_the_users(env):
    _dispatch(env, control.SAVE_USER_METRIC, {"metric": METRIC})
    env.user_metrics.usage_providers.append(lambda mid: ["tab Mine, panel 1"])
    own, everyone = _dispatch(env, control.DELETE_USER_METRIC, {"id": "t57.double"})
    assert own[0][2]["ok"] is False and "tab Mine, panel 1" in own[0][2]["error"]
    assert everyone == []
    env.user_metrics.usage_providers.clear()
    own, _ = _dispatch(env, control.DELETE_USER_METRIC, {"id": "t57.double"})
    assert own[0][2]["ok"] is True


def test_export_answers_only_the_window_that_asked(env):
    _dispatch(env, control.SAVE_USER_METRIC, {"metric": METRIC})
    own, everyone = _dispatch(env, control.EXPORT_USER_METRIC, {"id": "t57.double"})
    [(event, _, kwargs)] = own
    assert event == control.USER_METRIC_EXPORTED and kwargs["toml"].startswith("[[metrics.expr]]")
    assert everyone == []


def test_the_list_request_answers_the_window(env):
    own, _ = _dispatch(env, control.REQUEST_USER_METRICS, {})
    assert own[0][0] == control.USER_METRICS and own[0][2] == {"metrics": []}


def test_a_server_without_user_metrics_says_so():
    own, _ = _dispatch(_Env(), control.SAVE_USER_METRIC, {"metric": METRIC})
    assert own[0][2]["error"] == "This server keeps no user metrics"


def test_the_catalog_says_where_each_metric_comes_from(env):
    _dispatch(env, control.SAVE_USER_METRIC, {"metric": METRIC})
    from ffast.metrics.catalog import build_metric_catalog
    cat = {m["id"]: m for m in build_metric_catalog(default_registry, env.user_metrics)}
    assert cat["t57.double"]["origin"] == "user"
    assert cat["t57.double"]["expr"] == "2 * f"
    assert cat["ffast.force_component_mae"]["origin"] == "built-in"
    assert cat["ffast.force_component_mae"]["used_by"] == ["metric t57.double"]


def test_user_metric_changes_are_controlling_only():
    assert {control.SAVE_USER_METRIC, control.DELETE_USER_METRIC} <= control.MUTATING_CLIENT_EVENTS
    assert control.EXPORT_USER_METRIC not in control.MUTATING_CLIENT_EVENTS
    assert control.REQUEST_USER_METRICS not in control.MUTATING_CLIENT_EVENTS


def test_a_views_colouring_counts_as_a_user_of_the_metric():
    class _View:
        class state:
            parameters = {"ffast.atom_color": {"source": "metric:t57.double"}}

    env = _Env()
    s = ServerSession(env, asyncio.Queue())
    s.views["v"] = _View()
    env.view_sessions = [s]
    assert view_colouring_users(env, "t57.double") == ["the main view's colouring"]
    assert view_colouring_users(env, "other.metric") == []


# ── check as you type, and Try (rules 11, 12) ───────────────────────────────
import numpy as np  # noqa: E402


class _Resolver:
    """Stands in for the DataService input resolver: 4 frames, 8 atoms."""
    ENERGY = np.array([1.0, 2.0, 3.0, 4.0])

    def resolve(self, ref, model=None, dataset=None):
        if ref == "n_atoms":
            return np.array([2, 3, 1, 2])
        return {"reference.energies": self.ENERGY}.get(ref)


def _try_env(env):
    env.datasets = {"ds1": object()}
    env.models = {}
    env.data.inputResolver = _Resolver()
    return env


def _run_session(env, steps):
    """Run `steps(session)` in one event loop; return everything this window got."""
    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await steps(s)
        out = []
        while not s.outbound.empty():
            out.append(unpack(s.outbound.get_nowait()))
        return out
    return asyncio.run(scenario())


def test_check_replies_with_the_shape_and_echoes_the_check_id(env):
    own, everyone = _dispatch(env, control.CHECK_METRIC, {
        "metric": {"id": "t57.c", "expr": "2 * f", "vars": {"f": "ffast.force_component_mae"}},
        "check_id": "7"})
    assert own == [(control.METRIC_CHECKED, [], {"check_id": "7", "ok": True, "error": None, "shape": "scalar"})]
    assert everyone == []


def test_check_reports_an_error_and_registers_nothing(env):
    own, _ = _dispatch(env, control.CHECK_METRIC, {
        "metric": {"id": "t57.c", "expr": "2 *", "vars": {}}, "check_id": "8"})
    assert own[0][2]["ok"] is False and "valid expression" in own[0][2]["error"]
    assert not default_registry.has("t57.c")


def test_try_replies_with_statistics_and_progress_to_this_window_only(env):
    _try_env(env)
    trial = {"id": "t57.try", "expr": "e * 10", "vars": {"e": "reference.energies"}}

    async def steps(s):
        await s.dispatch(control.TRY_METRIC, [], {"metric": trial, "try_id": "t1", "dataset_fp": "ds1"})
        await s._tries["t1"][1]

    got = _run_session(env, steps)
    events = [e for e, _, _ in got]
    assert events[-1] == control.METRIC_TRIED and "TASK_PROGRESS" in events
    tried = got[-1][2]
    assert (tried["ok"], tried["try_id"], tried["min"], tried["max"], tried["bad_count"]) == (
        True, "t1", 10.0, 40.0, 0)
    assert not default_registry.has("t57.try")           # nothing is registered
    assert env.data.invalidated == [] and env.data.refreshed == 0


def test_try_without_a_dataset_says_so(env):
    _try_env(env)

    async def steps(s):
        await s.dispatch(control.TRY_METRIC, [], {
            "metric": {"id": "t57.try", "expr": "e", "vars": {"e": "reference.energies"}}, "try_id": "t2"})
        await s._tries["t2"][1]

    [(event, _, kwargs)] = _run_session(env, steps)
    assert event == control.METRIC_TRIED and kwargs["ok"] is False
    assert "Select a dataset" in kwargs["error"]


def test_try_finds_the_bad_frames(env):
    _try_env(env)
    trial = {"id": "t57.try", "expr": "1 / (e - 3)", "vars": {"e": "reference.energies"}}

    async def steps(s):
        await s.dispatch(control.TRY_METRIC, [], {"metric": trial, "try_id": "t3", "dataset_fp": "ds1"})
        await s._tries["t3"][1]

    tried = _run_session(env, steps)[-1][2]
    assert tried["ok"] and tried["bad_count"] == 1 and tried["bad_frames"] == [2]


def test_cancel_stops_a_running_try_cleanly(env):
    _try_env(env)
    trial = {"id": "t57.try", "expr": "e", "vars": {"e": "reference.energies"}}

    async def steps(s):
        await s.dispatch(control.TRY_METRIC, [], {"metric": trial, "try_id": "t4", "dataset_fp": "ds1"})
        await s.dispatch(control.CANCEL_TRY_METRIC, [], {"try_id": "t4"})
        await s._tries["t4"][1]

    tried = _run_session(env, steps)[-1][2]
    assert tried["ok"] is True and tried["cancelled"] is True


def test_check_try_and_cancel_are_open_to_viewers():
    for event in (control.CHECK_METRIC, control.TRY_METRIC, control.CANCEL_TRY_METRIC):
        assert event in control.CLIENT_TO_SERVER and event not in control.MUTATING_CLIENT_EVENTS

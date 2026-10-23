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
from ffast.session.server_session import ServerSession
from ffast.session.window_feed import WindowFeed

METRIC = {"id": "t57.double", "expr": "2 * f", "vars": {"f": "ffast.force_component_mae"}}


class _Data:
    def __init__(self):
        self.invalidated = []

    def invalidate_metrics(self, ids):
        self.invalidated.append(set(ids))
        return []


class _Env:
    pass


class _Datasets:
    def get(self, fp):
        return None


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
    assert [e for e, _, _ in everyone] == [
        control.METRICS_UPDATED, control.METRIC_CATALOG, control.TAB_LAYOUT,
        control.USER_METRICS, control.METRIC_USERS]
    [entry] = next(k for e, _, k in everyone if e == control.USER_METRICS)["metrics"]
    assert (entry["id"], entry["status"], entry["shape"]) == ("t57.double", "ok", "scalar")


def test_a_change_drops_cached_results(env):
    _dispatch(env, control.SAVE_USER_METRIC, {"metric": METRIC})
    _dispatch(env, control.SAVE_USER_METRIC, {
        "metric": {**METRIC, "expr": "3 * f"}, "previous_id": "t57.double", "overwrite": True})
    assert env.data.invalidated == [{"t57.double"}, {"t57.double"}]


def test_the_change_and_the_invalidation_run_in_one_thread_call(env, monkeypatch):
    """ADR 0061 decision 4: no other task can run between the change and the drop."""
    import threading
    calls, idents = [], {}
    real_to_thread = asyncio.to_thread

    async def counting(fn, *a, **kw):
        calls.append(fn)
        return await real_to_thread(fn, *a, **kw)

    monkeypatch.setattr(asyncio, "to_thread", counting)
    real_save, real_invalidate = env.user_metrics.save, env.data.invalidate_metrics

    def save(*a, **kw):
        idents["change"] = threading.get_ident()
        return real_save(*a, **kw)

    def invalidate(ids):
        idents["invalidate"] = threading.get_ident()
        return real_invalidate(ids)

    env.user_metrics.save, env.data.invalidate_metrics = save, invalidate
    _dispatch(env, control.SAVE_USER_METRIC, {"metric": METRIC})
    assert len(calls) == 1
    assert idents["change"] == idents["invalidate"]


def test_a_refused_change_says_why_and_tells_nobody(env):
    own, everyone = _dispatch(env, control.SAVE_USER_METRIC,
                              {"metric": {**METRIC, "id": "ffast.force_component_mae"}})
    assert own[0][2]["ok"] is False and "already taken" in own[0][2]["error"]
    assert everyone == []


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
    assert "used_by" not in cat["ffast.force_component_mae"]


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
    assert s.feed.view_colouring_users("t57.double") == ["the main view's colouring"]
    assert s.feed.view_colouring_users("other.metric") == []


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
    from ffast.metrics.executor import InProcessExecutor
    env.datasets = {"ds1": object()}
    env.models = {}
    env.data.inputResolver = _Resolver()
    env.data.metricExecutor = InProcessExecutor(default_registry)
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
    assert own == [(control.METRIC_CHECKED, [], {"check_id": "7", "ok": True, "error": None, "shape": "scalar", "colour_problem": None})]
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
    assert env.data.invalidated == []


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


def test_try_computes_the_formula_on_the_metric_executor(env):
    """ADR 0064 step 1: not in a thread of the server process."""
    _try_env(env)
    calls = []

    class _Recording:
        def call(self, fn, kwargs, label="formula"):
            calls.append(sorted(kwargs))
            return fn(**kwargs)

    env.data.metricExecutor = _Recording()
    trial = {"id": "t57.try", "expr": "e * 10", "vars": {"e": "reference.energies"}}

    async def steps(s):
        await s.dispatch(control.TRY_METRIC, [], {"metric": trial, "try_id": "t6", "dataset_fp": "ds1"})
        await s._tries["t6"][1]

    tried = _run_session(env, steps)[-1][2]
    assert tried["ok"] and tried["max"] == 40.0
    assert calls and all(c == ["e"] for c in calls)


def test_a_try_whose_worker_dies_fails_and_the_server_still_answers(env):
    from ffast.metrics.pool import WorkerCallError
    _try_env(env)

    class _Dying:
        def call(self, fn, kwargs, label="formula"):
            raise WorkerCallError("the worker process stopped (it may have run out of memory)")

    env.data.metricExecutor = _Dying()
    trial = {"id": "t57.try", "expr": "e", "vars": {"e": "reference.energies"}}

    async def steps(s):
        await s.dispatch(control.TRY_METRIC, [], {"metric": trial, "try_id": "t7", "dataset_fp": "ds1"})
        await s._tries["t7"][1]
        await s.dispatch(control.CHECK_METRIC, [], {"metric": trial, "check_id": "9"})

    got = _run_session(env, steps)
    tried = next(k for e, _, k in got if e == control.METRIC_TRIED)
    assert tried["ok"] is False and "worker process stopped" in tried["error"]
    assert got[-1][0] == control.METRIC_CHECKED and got[-1][2]["ok"] is True


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


# ── sessions carry formulas (rules 15, 16) ──────────────────────────────────
from types import SimpleNamespace  # noqa: E402

from ffast.cache.store import DataCache  # noqa: E402
from ffast.config.user_metrics import SESSION_FILE, read_formulas  # noqa: E402
from ffast.session.persistence import SessionPersistence  # noqa: E402


def _persistence(env):
    env.cache = DataCache()
    env.datasets = SimpleNamespace(all=lambda **kw: [])
    env.models = SimpleNamespace(all=lambda **kw: [])
    env.objects = SimpleNamespace(snapshot=lambda: {})
    return SessionPersistence(env)


def test_saving_a_session_writes_the_user_metrics(env, tmp_path):
    _dispatch(env, control.SAVE_USER_METRIC, {"metric": METRIC})
    session = tmp_path / "session"
    _persistence(env).save(str(session))
    assert (session / "info.json").exists()
    assert [(c.id, c.expr) for c in read_formulas(session / SESSION_FILE)] == [("t57.double", "2 * f")]


def test_a_session_saved_with_no_user_metrics_has_no_formulas_file(env, tmp_path):
    session = tmp_path / "session"
    session.mkdir()
    (session / SESSION_FILE).write_text("# left from an earlier save\n")
    _persistence(env).save(str(session))
    assert not (session / SESSION_FILE).exists()


def _write_session(path, *cfgs):
    from ffast.config.user_metrics import formulas_toml
    path.mkdir()
    (path / SESSION_FILE).write_text(formulas_toml(list(cfgs)))


def _offer(env, session):
    """Run the offer the way a load's worker thread does; return what the window gets."""
    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        loop = asyncio.get_running_loop()
        await asyncio.to_thread(s._offer_session_metrics, loop, str(session))
        await asyncio.sleep(0)
        out = []
        while not s.outbound.empty():
            out.append(unpack(s.outbound.get_nowait()))
        return s, out
    return asyncio.run(scenario())


def test_loading_offers_the_metrics_the_server_lacks(env, tmp_path):
    from ffast.config.models import ExprMetricConfig
    _write_session(tmp_path / "s", ExprMetricConfig(**METRIC))
    _, out = _offer(env, tmp_path / "s")
    [(event, _, kwargs)] = out
    assert event == control.SESSION_METRICS
    assert kwargs["metrics"] == [{"id": "t57.double", "label": "", "expr": "2 * f", "clash_with": None}]


def test_loading_offers_nothing_when_the_server_has_them_all(env, tmp_path):
    from ffast.config.models import ExprMetricConfig
    _dispatch(env, control.SAVE_USER_METRIC, {"metric": METRIC})
    _write_session(tmp_path / "s", ExprMetricConfig(**METRIC))
    _, out = _offer(env, tmp_path / "s")
    assert out == []


def test_loading_a_session_without_the_file_offers_nothing(env, tmp_path):
    (tmp_path / "s").mkdir()
    assert _offer(env, tmp_path / "s")[1] == []


def test_yes_adds_them_and_tells_every_window(env, tmp_path):
    from ffast.config.models import ExprMetricConfig
    _write_session(tmp_path / "s", ExprMetricConfig(**METRIC))
    broadcasts, answers = [], []

    async def scenario():
        s = ServerSession(env, asyncio.Queue(), broadcast=broadcasts.append)
        await asyncio.to_thread(s._offer_session_metrics, asyncio.get_running_loop(), str(tmp_path / "s"))
        await asyncio.sleep(0)
        offer = unpack(s.outbound.get_nowait())[2]["offer_id"]
        await s.dispatch(control.ADD_SESSION_METRICS, [], {"offer_id": offer})
        while not s.outbound.empty():
            answers.append(unpack(s.outbound.get_nowait()))
        # the same offer cannot be taken twice
        await s.dispatch(control.ADD_SESSION_METRICS, [], {"offer_id": offer})
        answers.append(unpack(s.outbound.get_nowait()))

    asyncio.run(scenario())
    assert answers[0][2]["ok"] and answers[0][2]["action"] == "add-session"
    assert [unpack(b)[0] for b in broadcasts] == [
        control.METRICS_UPDATED, control.METRIC_CATALOG, control.TAB_LAYOUT,
        control.USER_METRICS, control.METRIC_USERS]
    assert default_registry.has("t57.double")
    assert answers[1][2]["ok"] is False and "no longer open" in answers[1][2]["error"]


def test_a_clash_is_offered_and_added_as_dot_2(env, tmp_path):
    from ffast.config.models import ExprMetricConfig
    _dispatch(env, control.SAVE_USER_METRIC, {"metric": {**METRIC, "expr": "5 * f"}})
    _write_session(tmp_path / "s", ExprMetricConfig(**METRIC))
    s, out = _offer(env, tmp_path / "s")
    [(_, _, kwargs)] = out
    assert [(m["id"], m["clash_with"]) for m in kwargs["metrics"]] == [("t57.double.2", "t57.double")]
    s_plan = next(iter(s._session_offers.values()))
    env.user_metrics.add_session(s_plan)
    assert default_registry.get("t57.double")[1].expr == "5 * f"
    assert default_registry.get("t57.double.2")[1].expr == "2 * f"


def test_adding_session_metrics_is_controlling_only():
    assert control.ADD_SESSION_METRICS in control.MUTATING_CLIENT_EVENTS


# ── "Used by" stays current (rule 7) ────────────────────────────────────────
class _ColourView:
    """A view whose colouring a command changes."""
    def __init__(self):
        self.parameters = {"ffast.atom_color": {"source": "element"}}
        self.state = self

    def apply_command(self, cmd):
        from ffast.visualization.view import CommandResult
        self.parameters = {"ffast.atom_color": {"source": cmd.value}}
        return CommandResult(success=True, new_version=1)


def _dispatch_with_view(env, event, kwargs):
    broadcasts = []

    async def scenario():
        s = ServerSession(env, asyncio.Queue(), broadcast=broadcasts.append)
        s.views["v"] = _ColourView()
        await s.dispatch(event, [], kwargs)

    asyncio.run(scenario())
    return [unpack(b) for b in broadcasts]


def test_changing_a_colouring_sends_who_uses_what_again(env):
    set_colour = {"type": "SET_PARAMETER", "view_id": "v", "view_version": 0,
                  "stage_id": "ffast.atom_color", "parameter": "source", "value": "metric:t57.double"}
    sent = _dispatch_with_view(env, control.VIEW_COMMAND, set_colour)
    assert [e for e, _, _ in sent] == [control.METRIC_USERS]
    same = _dispatch_with_view(env, control.VIEW_COMMAND, {**set_colour, "value": "element"})
    assert same == []   # the source did not change


def test_closing_a_view_sends_who_uses_what_again(env):
    sent = _dispatch_with_view(env, control.CLOSE_VIEW, {"view_id": "v"})
    assert [e for e, _, _ in sent] == [control.METRIC_USERS]


def test_a_tab_change_sends_who_uses_what_again(env):
    class _Sources:
        def layout(self):
            return []

        def set_hidden(self, name, hidden):
            return None

    env.tab_sources = _Sources()
    _, everyone = _dispatch(env, control.HIDE_TAB, {"name": "Basic", "hidden": True})
    assert [e for e, _, _ in everyone] == [
        control.METRIC_CATALOG, control.TAB_LAYOUT, control.METRIC_USERS]


def test_cancel_answers_at_once_while_an_input_is_still_computing(env):
    """ADR 0057 rule 12: an input already computing finishes in the background."""
    import threading
    _try_env(env)
    release, started = threading.Event(), threading.Event()

    class _SlowResolver(_Resolver):
        def resolve(self, ref, model=None, dataset=None):
            started.set()
            release.wait(10)
            return super().resolve(ref, model, dataset)

    env.data.inputResolver = _SlowResolver()
    trial = {"id": "t57.try", "expr": "e", "vars": {"e": "reference.energies"}}
    answered_while_blocked = []

    async def steps(s):
        await s.dispatch(control.TRY_METRIC, [], {"metric": trial, "try_id": "t5", "dataset_fp": "ds1"})
        task = s._tries["t5"][1]
        while not started.is_set():
            await asyncio.sleep(0.01)
        await s.dispatch(control.CANCEL_TRY_METRIC, [], {"try_id": "t5"})
        await asyncio.wait_for(task, 5)
        answered_while_blocked.append(not release.is_set())
        release.set()

    tried = _run_session(env, steps)[-1][2]
    assert answered_while_blocked == [True]
    assert tried["ok"] is True and tried["cancelled"] is True


# ── REQUEST_METRIC for a formula metric (ADR 0061) ──────────────────────────
class _Lookup:
    def get(self, fp):
        return None


class _ResultExecutor:
    """Stands in for the worker pool: every run gives one result."""

    def run(self, id, source, parameters):
        import numpy as np
        from ffast.metrics.models import MetricResult
        return MetricResult(
            metric_id=id, shape="scalar", dtype="float64", unit="energy",
            compute_parameters={}, implementation_hash="h", checksum="c",
            values=np.array(1.0))


class _Events:
    def eventPush(self, *args, **kwargs):
        pass


def test_a_formula_metrics_result_is_stored_under_the_servers_key_not_the_request_id(env):
    from ffast.cache.store import DataCache
    from ffast.core.data_service import DataService
    env.data = DataService(cache=DataCache(), models={}, datasets={}, tm=None, events=_Events(),
                           executor=_ResultExecutor())
    env.cache = env.data.cache
    env.models, env.datasets = _Lookup(), _Lookup()
    formula = {"id": "t57.energy2", "expr": "2 * e", "vars": {"e": "reference.energies"}}
    _dispatch(env, control.SAVE_USER_METRIC, {"metric": formula})

    def request(request_id):
        own, _ = _dispatch(env, control.REQUEST_METRIC,
                           {"metric_id": "t57.energy2", "request_id": request_id})
        [(event, args, kwargs)] = own
        assert event == control.METRIC_RESULT and args == [request_id, "t57.energy2"]
        assert kwargs["ok"] is True
        return [str(k) for k in env.cache.keys()]

    first = request("m1")
    assert len(first) == 1 and "__f" in first[0] and "m1" not in first[0]

    # editing the formula gives a new key; the old result is dropped, not reused
    _dispatch(env, control.SAVE_USER_METRIC, {
        "metric": {**formula, "expr": "3 * e"}, "previous_id": "t57.energy2", "overwrite": True})
    second = request("m2")
    assert len(second) == 1 and "__f" in second[0] and second != first


def test_the_replay_ends_with_who_uses_what(env):
    _dispatch(env, control.SAVE_USER_METRIC, {"metric": METRIC})
    out = asyncio.Queue()
    ServerSession(env, out).replay()
    sent = [unpack(out.get_nowait()) for _ in range(out.qsize())]
    event, _, kwargs = sent[-1]
    assert event == control.METRIC_USERS
    assert kwargs["users"]["ffast.force_component_mae"] == ["metric t57.double"]
    assert "t57.double" not in kwargs["users"]   # nobody uses it


def test_a_metric_save_sends_the_pieces_and_inputs_only_to_a_window_that_asked(env):
    broadcasts = []

    async def scenario():
        feed = WindowFeed(env, broadcasts.append)
        env.window_feed = feed
        asker = ServerSession(env, asyncio.Queue(), broadcast=broadcasts.append)
        other = ServerSession(env, asyncio.Queue(), broadcast=broadcasts.append)
        env.datasets, env.models = _Datasets(), {}
        await asker.dispatch(control.REQUEST_METRIC_INPUTS, [], {"dataset_fp": "ds1"})
        asker.outbound.get_nowait()                    # the answer to the request
        await asker.dispatch(control.SAVE_USER_METRIC, [], {"metric": METRIC})
        got = lambda s: [unpack(s.outbound.get_nowait())[0] for _ in range(s.outbound.qsize())]
        return got(asker), got(other)

    asker, other = asyncio.run(scenario())
    assert asker == [control.USER_METRIC_SAVED, control.METRIC_INPUTS]
    assert other == []
    events = [unpack(b)[0] for b in broadcasts]
    assert events == [control.METRICS_UPDATED, control.METRIC_CATALOG, control.TAB_LAYOUT,
                      control.USER_METRICS, control.METRIC_USERS]


# ── a copied or closed colouring updates "Used by" (ADR 0062) ───────────────
def _coloured_view(source):
    from ffast.visualization.view import VisualizationView
    view = VisualizationView("v")
    view.state.parameters = {"ffast.atom_color": {"source": source}}
    return view


def test_opening_a_view_from_a_metric_coloured_one_tells_every_window(env):
    broadcasts = []

    async def scenario():
        s = ServerSession(env, asyncio.Queue(), broadcast=broadcasts.append)
        s.views["src"] = _coloured_view("metric:t57.double")
        await s.dispatch(control.OPEN_VIEW, [], {"view_id": "copy", "start_from": "src"})

    asyncio.run(scenario())
    assert control.METRIC_USERS in [unpack(b)[0] for b in broadcasts]


def test_opening_a_view_from_an_element_coloured_one_says_nothing(env):
    broadcasts = []

    async def scenario():
        s = ServerSession(env, asyncio.Queue(), broadcast=broadcasts.append)
        s.views["src"] = _coloured_view("element")
        await s.dispatch(control.OPEN_VIEW, [], {"view_id": "copy", "start_from": "src"})

    asyncio.run(scenario())
    assert broadcasts == []


def test_a_closed_window_stops_counting_as_a_user(env):
    from ffast.session.window_feed import WindowFeed
    _dispatch(env, control.SAVE_USER_METRIC, {"metric": METRIC})
    broadcasts = []
    feed = WindowFeed(env, broadcasts.append)
    env.window_feed = feed
    env.user_metrics.usage_providers.append(feed.view_colouring_users)
    gone = ServerSession(env, asyncio.Queue())
    stays = ServerSession(env, asyncio.Queue())
    gone.views["v"] = _coloured_view("metric:t57.double")
    assert env.user_metrics.used_by("t57.double") == ["the main view's colouring"]
    feed.changed(control.METRIC_USERS)          # what the windows now hold
    broadcasts.clear()

    gone.close()

    assert gone not in feed.sessions and stays in feed.sessions
    assert env.user_metrics.used_by("t57.double") == []   # a Delete is allowed
    [(event, _, kwargs)] = [unpack(b) for b in broadcasts]
    assert event == control.METRIC_USERS and "t57.double" not in kwargs["users"]

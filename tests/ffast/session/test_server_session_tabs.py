"""The tab messages of the server session (ADR 0056 rules 9, 10, 16).

The browser saves, deletes, hides and exports tabs over the wire. A change
answers the window that asked (TAB_SAVED: ok, action, name, error) and then
sends the new layout to every connected window, the way a newly loaded
dataset reaches them all. Run against a real TabSources over a temporary
folder, with a fake environment and queues — no socket.
"""
import asyncio

import pytest

import ffast.metrics.builtin  # noqa: F401
from ffast.config.models import AnalysisTabConfig
from ffast.config.user_tabs import TabSources, UserTabStore
from ffast.protocol import control
from ffast.protocol.rpc import unpack
from ffast.session.server_session import ServerSession

VIEW = {"kind": "3d", "row": 0, "col": 0}
TABLE = {"kind": "table", "row": 0, "col": 0, "title": "MAE",
         "metrics": {"value": {"metric": "ffast.force_component_mae"}}}


class _Env:
    pass


@pytest.fixture
def env(tmp_path):
    env = _Env()
    env.tab_sources = TabSources(
        builtin=[AnalysisTabConfig.model_validate(t) for t in (
            {"name": "3D", "panels": [VIEW]}, {"name": "Basic", "panels": [TABLE]})],
        store=UserTabStore(tmp_path / "tabs"))
    return env


def _dispatch(env, event, kwargs):
    """Dispatch one event; return (this window's messages, everyone's)."""
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


def test_saving_a_tab_answers_the_window_then_tells_every_window(env):
    own, everyone = _dispatch(env, control.SAVE_TAB, {"tab": {"name": "Mine", "panels": [TABLE]}})
    assert [(e, k) for e, _, k in own] == [
        (control.TAB_SAVED, {"ok": True, "action": "save", "name": "Mine", "error": None})]
    [(event, _, kwargs)] = everyone
    assert event == control.TAB_LAYOUT
    assert [t["name"] for t in kwargs["tabs"]] == ["3D", "Basic", "Mine"]


def test_editing_a_built_in_tab_replaces_it_for_every_window(env):
    _, everyone = _dispatch(env, control.SAVE_TAB, {
        "tab": {"name": "Basic", "panels": [TABLE, {**TABLE, "row": 1}]}, "previous_name": "Basic"})
    basic = everyone[0][2]["tabs"][1]
    assert (basic["source"], basic["replaces"], len(basic["panels"])) == ("user", "Basic", 2)


def test_a_refused_change_says_why_and_changes_nothing(env):
    own, everyone = _dispatch(env, control.SAVE_TAB, {"tab": {"name": "Basic", "panels": [TABLE]}})
    assert own[0][2] == {"ok": False, "action": "save", "name": "Basic",
                         "error": "A tab named Basic already exists"}
    assert everyone == []


def test_reset_hide_and_show(env):
    _dispatch(env, control.SAVE_TAB, {"tab": {"name": "Basic", "panels": [TABLE]},
                                      "previous_name": "Basic"})
    own, everyone = _dispatch(env, control.DELETE_TAB, {"name": "Basic"})
    assert own[0][2]["ok"] and everyone[0][2]["tabs"][1]["source"] == "built-in"

    own, everyone = _dispatch(env, control.HIDE_TAB, {"name": "Basic", "hidden": True})
    assert own[0][2]["action"] == "hide" and everyone[0][2]["tabs"][1]["hidden"] is True
    own, everyone = _dispatch(env, control.HIDE_TAB, {"name": "Basic", "hidden": False})
    assert own[0][2]["action"] == "show" and everyone[0][2]["tabs"][1]["hidden"] is False

    own, _ = _dispatch(env, control.HIDE_TAB, {"name": "3D", "hidden": True})
    assert own[0][2]["error"] == "At least one tab must show the main view"


def test_export_answers_only_the_window_that_asked(env):
    own, everyone = _dispatch(env, control.EXPORT_TAB, {"name": "Basic"})
    [(event, _, kwargs)] = own
    assert event == control.TAB_EXPORTED and kwargs["name"] == "Basic"
    assert kwargs["toml"].startswith("[[visualization.tabs]]") and kwargs["error"] is None
    assert everyone == []


def test_the_layout_request_reads_the_tab_sources(env):
    own, _ = _dispatch(env, control.REQUEST_TAB_LAYOUT, {})
    assert [t["source"] for t in own[0][2]["tabs"]] == ["built-in", "built-in"]


def test_tab_changes_are_controlling_only():
    """A read-only viewer may export, not change (ADR 0044 Phase 2)."""
    assert {control.SAVE_TAB, control.DELETE_TAB, control.HIDE_TAB} <= control.MUTATING_CLIENT_EVENTS
    assert control.EXPORT_TAB not in control.MUTATING_CLIENT_EVENTS

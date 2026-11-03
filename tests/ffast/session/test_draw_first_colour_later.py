"""Draw first, colour later (ADR 0082 part a).

Colour by force error on 1.77M structures computed the metric over the whole
dataset inside the scene rebuild: 13 s in which the window's other messages
waited. A scene now never computes it: it colours from the frame on screen,
says so, and the final colours follow when the metric is done.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from ffast.protocol import control
from ffast.protocol.rpc import unpack
from ffast.session.server_session import ServerSession
from ffast.visualization.scene import AtomColorBy, AtomScene, RenderScene

from tests.ffast.session.test_server_session import _ColourData, _FakeEnv

METRIC = "ffast.force_difference[atom+structure;length]"


def _env(fail=False):
    env = _FakeEnv(datasets={"ds1": SimpleNamespace(fingerprint="ds1")},
                   models={"m1": SimpleNamespace(fingerprint="m1")})
    env.data = _ColourData(env.cache)
    env.data.metric_failures = {}
    env.data.metricExecutor = None
    env.data.predictionFields = {}
    if fail:
        def failing(metric_id, params, model, dataset, key=None):
            env.data.generate_metric_calls.append((metric_id, None))
            env.data.metric_failures[f"{metric_id}__nil__nil"] = "out of memory"
            return None
        env.data.generateMetric = failing
    return env


def _fake_build_scene(view_state, get_dataset, get_prediction, get_forces, executor,
                      get_metric=None, prediction_fields=None):
    """Colours by METRIC when the whole-dataset result is there, else 'frame'."""
    whole = get_metric(METRIC, {}, "ds1", "m1") if get_metric else None
    values = [9.0] if whole is not None else [1.0]
    return RenderScene(
        view_id=view_state.view_id, version=view_state.version,
        atoms=AtomScene(positions=[[0.0, 0.0, 0.0]], sizes=[1.0], colors=[[1, 1, 1, 1]],
                        color_by=AtomColorBy(values=values, label="Force error")))


def _colour_by(view, source="force error"):
    view.state.parameters["ffast.atom_color"] = {"source": source}


async def _colour_command(s):
    await s.dispatch("VIEW_COMMAND", [], {
        "type": "TOGGLE_FEATURE", "view_id": "v1", "view_version": 0,
        "feature": "kabsch_align", "enabled": True,
    })


def _messages(s):
    out = []
    while not s.outbound.empty():
        out.append(unpack(s.outbound.get_nowait()))
    return out


def _patches(messages):
    return [kw for event, _, kw in messages if event == control.SCENE_PATCH]


def test_the_scene_comes_at_once_with_frame_colours_and_the_final_ones_follow():
    env = _env()

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("OPEN_VIEW", [], {"view_id": "v1"})
        s.outbound.get_nowait()
        _colour_by(s.views["v1"])
        with patch("ffast.visualization.scene_builder.build_scene", _fake_build_scene):
            await _colour_command(s)
            first = _messages(s)
            computed_before = list(env.data.generate_metric_calls)
            await s.colours_settled()
        return first, computed_before, _messages(s)

    first, computed_before, later = _run(scenario())
    (patch_now,) = _patches(first)
    assert patch_now["atoms"]["color_by"]["values"] == [1.0]
    assert "computing" in patch_now["atoms"]["color_by"]["note"].lower()
    # Coming, not missing: Whole dataset stays choosable (ADR 0084).
    assert patch_now["atoms"]["color_by"]["dataset_range"] is True
    assert computed_before == []          # not computed inside the command
    (patch_later,) = _patches(later)
    assert patch_later["atoms"]["color_by"]["values"] == [9.0]
    assert patch_later["atoms"]["color_by"]["note"] == ""
    assert [c[0] for c in env.data.generate_metric_calls] == [METRIC]


def test_a_late_result_does_not_overwrite_a_newer_colouring():
    env = _env()

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("OPEN_VIEW", [], {"view_id": "v1"})
        s.outbound.get_nowait()
        _colour_by(s.views["v1"])
        with patch("ffast.visualization.scene_builder.build_scene", _fake_build_scene):
            await _colour_command(s)
            _messages(s)
            _colour_by(s.views["v1"], source="element")   # the user moved on
            await s.colours_settled()
        return _messages(s)

    assert _patches(_run(scenario())) == []


def test_when_the_metric_fails_the_colour_bar_says_why():
    env = _env(fail=True)

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("OPEN_VIEW", [], {"view_id": "v1"})
        s.outbound.get_nowait()
        _colour_by(s.views["v1"])
        with patch("ffast.visualization.scene_builder.build_scene", _fake_build_scene):
            await _colour_command(s)
            _messages(s)
            await s.colours_settled()
        return _messages(s)

    (patch_later,) = _patches(_run(scenario()))
    assert patch_later["atoms"]["color_by"]["values"] == [1.0]
    note = patch_later["atoms"]["color_by"]["note"]
    assert "could not" in note and "out of memory" in note


def test_a_cached_colour_metric_is_used_at_once_with_no_second_patch():
    env = _env()
    env.data.generateMetric(METRIC, {}, None, None)          # the plots stored it
    env.cache._entries[f"{METRIC}__nil__nil"] = env.cache._entries.pop(
        f"{METRIC}__f1234abcd__nil__nil")
    env.data.generate_metric_calls.clear()

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("OPEN_VIEW", [], {"view_id": "v1"})
        s.outbound.get_nowait()
        _colour_by(s.views["v1"])
        with patch("ffast.visualization.scene_builder.build_scene", _fake_build_scene):
            await _colour_command(s)
            first = _messages(s)
            await s.colours_settled()
        return first, _messages(s)

    first, later = _run(scenario())
    (patch_now,) = _patches(first)
    assert patch_now["atoms"]["color_by"]["values"] == [9.0]
    assert patch_now["atoms"]["color_by"]["note"] == ""
    assert later == [] and env.data.generate_metric_calls == []


def _run(coro):
    return asyncio.run(coro)

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

from ffast.metrics.models import MetricFailure
from tests.ffast.session.test_server_session import _make_metric_result, _store_env

METRIC = "ffast.force_difference[atom+structure;length]"


def _env(fail=False):
    def compute(metric_id):
        if fail:
            return MetricFailure(metric_id=metric_id, traceback="out of memory", kind="temporary")
        return _make_metric_result(metric_id=metric_id, values=[9.0])

    env = _store_env(compute, datasets={"ds1": SimpleNamespace(fingerprint="ds1")},
                     models={"m1": SimpleNamespace(fingerprint="m1")})
    env.data.predictionFields = {}
    return env


def _fake_build_scene(view_state, get_dataset, get_prediction, get_forces, executor,
                      get_metric=None, prediction_fields=None):
    """Colours by METRIC when the whole-dataset result is there, else 'frame'."""
    whole = get_metric(METRIC, {}, "ds1", "m1") if get_metric else None
    values = [9.0] if whole is not None else [1.0]
    chosen = view_state.parameters.get("ffast.atom_color", {}).get("range") or "dataset"
    return RenderScene(
        view_id=view_state.view_id, version=view_state.version,
        atoms=AtomScene(positions=[[0.0, 0.0, 0.0]], sizes=[1.0], colors=[[1, 1, 1, 1]],
                        color_by=AtomColorBy(values=values, label="Force error", range=chosen)))


def _colour_by(view, source="force error", range=None):
    view.state.parameters["ffast.atom_color"] = {"source": source}
    if range:
        view.state.parameters["ffast.atom_color"]["range"] = range


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
            computed_before = list(env.compute_calls)
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
    assert env.compute_calls == [METRIC]


def test_3d_colours_use_the_range_store_without_requesting_full_results():
    env = _env()
    env.data.make_metric_cache_key = lambda mid, params, model, ds: (
        f"{mid}__{model.fingerprint if model else 'nil'}__{ds.fingerprint}")
    env.data.compute_colour_metric = env.data.compute_metric
    def no_full_result(*args, **kwargs):
        raise AssertionError("3D colouring must not request a full metric result")
    env.data.compute_metric = no_full_result

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

    patch_later, = _patches(_run(scenario()))
    assert patch_later["atoms"]["color_by"]["values"] == [9.0]
    assert env.results.peek(METRIC, {}, "ds1", "m1") is None
    assert env.results.colours.peek(METRIC, {}, "ds1", "m1") is not None
    env.results.forget_objects("ds1")
    assert env.results.colours.peek(METRIC, {}, "ds1", "m1") is None


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


def test_this_frame_or_visible_atoms_show_no_whole_dataset_note():
    """The note is about the whole dataset's colours: with This frame or
    Visible atoms chosen it would only confuse, so the bar shows none."""
    for fail in (False, True):
        for chosen in ("frame", "visible"):
            env = _env(fail=fail)

            async def scenario():
                s = ServerSession(env, asyncio.Queue())
                await s.dispatch("OPEN_VIEW", [], {"view_id": "v1"})
                s.outbound.get_nowait()
                _colour_by(s.views["v1"], range=chosen)
                with patch("ffast.visualization.scene_builder.build_scene", _fake_build_scene):
                    await _colour_command(s)
                    first = _messages(s)
                    await s.colours_settled()
                return first, _messages(s)

            first, later = _run(scenario())
            for p in _patches(first) + _patches(later):
                assert p["atoms"]["color_by"]["note"] == "", (fail, chosen)


def test_a_cached_colour_metric_is_used_at_once_with_no_second_patch():
    env = _env()
    asyncio.run(env.results.get(METRIC, {}, "ds1", "m1"))   # the plots stored it
    env.compute_calls.clear()

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
    assert later == [] and env.compute_calls == []


def _run(coro):
    return asyncio.run(coro)


def _framed_build_scene(view_state, get_dataset, get_prediction, get_forces, executor,
                        get_metric=None, prediction_fields=None):
    """``_fake_build_scene`` whose atom sits at x = the frame drawn."""
    scene = _fake_build_scene(view_state, get_dataset, get_prediction, get_forces,
                              executor, get_metric, prediction_fields)
    scene.atoms.positions = [[float(view_state.structure_index), 0.0, 0.0]]
    return scene


async def _set_frame(s, frame):
    await s.dispatch("VIEW_COMMAND", [], {
        "type": "SET_FRAME", "view_id": "v1", "view_version": 0, "frame_index": frame,
    })


def _gated_env():
    """An env whose metric over every structure waits until ``env.gate`` is set."""
    import threading
    gate = threading.Event()

    def compute(metric_id):
        gate.wait(10)
        return _make_metric_result(metric_id=metric_id, values=[9.0])

    env = _store_env(compute, datasets={"ds1": SimpleNamespace(fingerprint="ds1")},
                     models={"m1": SimpleNamespace(fingerprint="m1")})
    env.data.predictionFields = {}
    env.gate = gate
    return env


def test_frame_changes_while_colours_compute_give_one_late_patch_for_the_frame_on_screen():
    # 2026-11-13, 1.77M structures: each frame change while the metric was
    # computed started its own colour task; when it came they all sent their
    # frame, and the view could end on an older one.
    env = _gated_env()

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("OPEN_VIEW", [], {"view_id": "v1"})
        s.outbound.get_nowait()
        _colour_by(s.views["v1"])
        with patch("ffast.visualization.scene_builder.build_scene", _framed_build_scene):
            await _colour_command(s)
            for frame in (1, 2, 3):
                await _set_frame(s, frame)
            tasks = len(s._colouring)
            _messages(s)
            env.gate.set()
            await s.colours_settled()
        return tasks, _messages(s), getattr(s, "_colour_jobs", {})

    tasks, later, jobs = _run(scenario())
    assert tasks == 1
    (patch_later,) = _patches(later)
    assert patch_later["atoms"]["positions"] == [[3.0, 0.0, 0.0]]
    assert patch_later["atoms"]["color_by"]["values"] == [9.0]
    assert jobs == {}


def test_a_late_patch_is_dropped_when_the_frame_moved_while_it_was_drawn():
    env = _env()
    views = {}

    def moved_meanwhile(view_state, *args, get_metric=None, **kwargs):
        scene = _framed_build_scene(view_state, *args, get_metric=get_metric, **kwargs)
        if get_metric is not None and get_metric(METRIC, {}, "ds1", "m1") is not None:
            views["v1"].state.structure_index = 7     # a SET_FRAME during the build
        return scene

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch("OPEN_VIEW", [], {"view_id": "v1"})
        s.outbound.get_nowait()
        views["v1"] = s.views["v1"]
        _colour_by(s.views["v1"])
        with patch("ffast.visualization.scene_builder.build_scene", moved_meanwhile):
            await _colour_command(s)
            _messages(s)
            await s.colours_settled()
        return _messages(s)

    assert _patches(_run(scenario())) == []


def test_a_closed_window_stops_its_colouring_and_its_length_probe():
    # Work started for a window ends with it: nobody reads its queue any more.
    import threading

    async def scenario():
        s = ServerSession(_env(), asyncio.Queue())
        never = asyncio.Event()
        colouring = asyncio.create_task(never.wait())
        s._colouring.add(colouring)
        s._length_probe_cancel = threading.Event()
        s.close()
        await asyncio.sleep(0)          # let a cancelled task finish
        stopped = colouring.done()
        colouring.cancel()              # never leave it running
        return stopped, s._length_probe_cancel.is_set()

    assert asyncio.run(scenario()) == (True, True)

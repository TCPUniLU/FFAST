"""Server side of the live-run view (ADR 0087)."""

from __future__ import annotations

import asyncio

import msgpack
import numpy as np
import pytest

from ffast.live import run_folder as rf
from ffast.live.live_run import LiveRun, LiveRunDataset, open_live_run
from tests.ffast.live.dump_helpers import ASPIRIN_ELEMENTS, dump_text, frame_text, random_frames


class _Registry(dict):
    def add(self, dataset):
        self[dataset.fingerprint] = dataset
        dataset.loaded = True
        self.added = getattr(self, "added", []) + [dataset.fingerprint]


class _Env:
    def __init__(self):
        self.datasets = _Registry()


def _run_folder(tmp_path, frames=2, scores=True):
    d = tmp_path / "20261105-120000-node-1-abcd"
    d.mkdir()
    rf.write_json_atomic(d / rf.RUN_JSON, {"format": rf.RUN_FORMAT, "format_version": 1,
                                           "run_id": d.name, "end": None})
    (d / rf.FRAMES).write_text(dump_text(random_frames(frames)))
    if scores:
        a = d / rf.ANALYSIS_DIR / "0123456789abcdef"
        a.mkdir(parents=True)
        rf.write_json_atomic(a / rf.SETTINGS, {"bundle": "/b.bundle",
                                               "thresholds": {"geometric": {"bead": 5.0, "centroid": 5.0}}})
        rf.ScoresWriter(a / rf.SCORES).append([
            {"segment": 0, "frame": k, "step": 100 * k, "time": 0.05 * k, "identity": "proven",
             "tracks": {"geometric": {"t2": 1.0 + 5 * k, "spe": None,
                                      "flags": {"bead": 1.0 + 5 * k > 5, "centroid": False}}}}
            for k in range(frames)])
    return d


def _unpack(data):
    return msgpack.unpackb(data, raw=False, strict_map_key=False)


def _kwargs(msg):
    m = _unpack(msg)
    if isinstance(m, dict):
        return m.get("kwargs", m)
    return m[-1]


def test_the_dataset_grows_with_a_fixed_fingerprint_and_geometry_only():
    ds = LiveRunDataset("/r", "run1")
    ds.initialise()
    from ffast.live.dump_reader import DumpReader  # noqa: F401 — frames come from it

    class F:
        def __init__(self, p, step):
            self.positions, self.step, self.time, self.elements = p, step, step * 0.0005, ASPIRIN_ELEMENTS

    frames = random_frames(5)
    ds.append([F(frames[0], 0), F(frames[1], 100)])
    assert ds.fingerprint == "live:run1" and ds.getN() == 2
    ds.append([F(p, 100 * k) for k, p in enumerate(frames[2:], start=2)])
    assert ds.getN() == 5 and ds.steps == [0, 100, 200, 300, 400]
    for k in range(5):
        assert np.array_equal(ds.getCoordinates(k), frames[k])
    assert list(ds.getElements()[:3]) == [6, 6, 6] and len(ds.getElements()) == 21
    assert ds.getFingerprint() == "live:run1"           # does not follow the geometry
    assert ds.hasForces() is False
    meta = ds.toMetaDict()
    assert meta["n"] == 5 and meta["variable"] is False


def test_opening_a_run_folder_registers_its_frames_and_sends_the_scores(tmp_path):
    d = _run_folder(tmp_path)
    env, sent = _Env(), []
    run = LiveRun(env, d, sent.append)
    run.apply(run.read_files())
    run.announce()
    assert env.datasets.added == [f"live:{d.name}"]
    kw = _kwargs(sent[-1])
    assert kw["fingerprint"] == f"live:{d.name}" and kw["n_frames"] == 2
    # Step-2 rows (one track, T² only) read as T² ÷ the centroid threshold.
    assert kw["series"] == {"frame": [0, 1], "step": [0, 100], "exceedance": [0.2, 1.2],
                            "test": [["geometric", "T2"], ["geometric", "T2"]],
                            "marked": [False, True], "chem": [False, False]}
    assert kw["role"] == "centroid"
    assert kw["analysis_id"] == "0123456789abcdef" and kw["error"] is None


def test_format_2_rows_give_the_worst_test_and_marks():
    from ffast.live.live_run import _series_of

    rows = [{"frame": 0, "step": 0, "tracks": {"bonds": {"T2": 1.0, "SPE": 0.1}},
             "exceedance": 0.5, "test": ["bonds", "T2"], "marked": False},
            {"frame": 1, "step": 100, "tracks": {"bonds": {"T2": 9.0, "SPE": 3.0}},
             "exceedance": 1.5, "test": ["bonds", "SPE"], "marked": True}]
    assert _series_of(rows, {}) == {"frame": [0, 1], "step": [0, 100], "exceedance": [0.5, 1.5],
                                    "test": [["bonds", "T2"], ["bonds", "SPE"]],
                                    "marked": [False, True], "chem": [False, False]}


def test_new_frames_are_announced_as_changed_frames_and_new_scores_appended(tmp_path, monkeypatch):
    d = _run_folder(tmp_path)
    env = _Env()
    run = LiveRun(env, d, lambda m: None)
    pushed = []
    monkeypatch.setattr(run.dataset, "eventPush", lambda *a: pushed.append(a))
    run.apply(run.read_files())
    assert pushed == []
    with open(d / rf.FRAMES, "a") as fh:
        fh.write(frame_text(200, random_frames(1, seed=4)[0], ASPIRIN_ELEMENTS))
    assert run.apply(run.read_files()) is True
    assert pushed == [("SUBDATASET_INDICES_CHANGED", f"live:{d.name}")]
    assert run.dataset.getN() == 3
    assert run.apply(run.read_files()) is False


def test_a_run_without_frames_yet_is_not_a_dataset_yet(tmp_path):
    d = _run_folder(tmp_path, scores=False)
    (d / rf.FRAMES).unlink()
    env, sent = _Env(), []
    run = LiveRun(env, d, sent.append)
    run.apply(run.read_files())
    run.announce()
    assert not env.datasets
    assert _kwargs(sent[-1])["fingerprint"] is None


def test_a_folder_that_is_not_a_run_is_refused(tmp_path):
    with pytest.raises(ValueError, match="not a live-run folder"):
        LiveRun(_Env(), tmp_path, lambda m: None)


async def test_open_follows_until_the_dataset_is_deleted(tmp_path):
    d = _run_folder(tmp_path)
    env, sent = _Env(), []
    run = open_live_run(env, str(d / rf.RUN_JSON), sent.append)
    assert run.run_dir == d and env.live_runs[d.name] is run
    again = open_live_run(env, str(d), sent.append)
    assert again is run
    del env.datasets[run.fingerprint]
    await asyncio.wait_for(run.task, 5)
    assert d.name not in env.live_runs


# ── the run's tab (ADR 0087) ──────────────────────────────────────────────────

def _live_sources(tmp_path):
    """Tab sources with the built-in tabs and one open run that has frames."""
    from ffast.config.tabs import load_builtin_tabs
    from ffast.config.user_tabs import TabSources, UserTabStore
    from ffast.live.live_run import live_tabs

    env = _Env()
    env.live_runs = {}
    run = LiveRun(env, _run_folder(tmp_path), lambda m: None)
    env.live_runs[run.run_id] = run
    run.apply(run.read_files())
    sources = TabSources(builtin=load_builtin_tabs(), store=UserTabStore(tmp_path / "tabs"),
                         live=lambda layout: live_tabs(env, layout))
    return sources, run


def test_a_run_with_frames_has_a_tab_made_from_the_live_run_layout(tmp_path):
    from ffast.live.live_run import tab_name

    assert tab_name("20261105-103335-mel0314-17254-dae9") == "Live 10:33:35 mel0314"
    assert tab_name("odd") == "Live odd"
    # The Slurm job name leads; the start time and node say which run of it.
    assert tab_name("20261105-103335-mel0314-17254-dae9", "aspirin_nvt") == "Live aspirin_nvt (10:33:35 mel0314)"
    assert tab_name("odd", "aspirin_nvt") == "Live aspirin_nvt (odd)"
    sources, run = _live_sources(tmp_path)
    layout = sources.layout()
    assert "Live run" not in [t["name"] for t in layout]      # the layout is not in the bar itself
    tab = next(t for t in layout if t["source"] == "live")
    assert tab["name"] == "Live 12:00:00 node"
    assert tab["pinned"] == [run.fingerprint] and tab["layout_of"] == "Live run"
    assert [p["kind"] for p in tab["panels"]] == ["live", "3d", "live", "live", "live"]
    assert [p["options"].get("part") for p in tab["panels"]] == [
        "status", None, "attribution", "scores", "events"]
    assert all("live_run" not in p for p in tab["panels"])
    assert tab["controls"] == ["same_step"]
    assert all(t.get("pinned") is None for t in layout if t["source"] != "live")
    assert _kwargs(run.message())["tab"] == "Live 12:00:00 node"


def test_the_message_carries_every_frames_step_and_the_runs_name(tmp_path):
    _, run = _live_sources(tmp_path)
    kw = _kwargs(run.message())
    assert kw["steps"] == [0, 100] and kw["name"] == "12:00:00 node"


def test_a_run_of_a_named_slurm_job_is_named_after_it(tmp_path):
    d = _run_folder(tmp_path)
    meta = rf.read_json(d / rf.RUN_JSON)
    meta["slurm_job_name"] = "aspirin_nvt"
    rf.write_json_atomic(d / rf.RUN_JSON, meta)
    run = LiveRun(_Env(), d, lambda m: None)
    assert run.tab_name == "Live aspirin_nvt (12:00:00 node)"


def test_editing_a_live_tab_edits_the_live_run_layout(tmp_path):
    from ffast.config.user_tabs import TabError

    sources, run = _live_sources(tmp_path)
    tab = next(t for t in sources.layout() if t["source"] == "live")
    edited = {k: tab[k] for k in ("name", "grid", "controls")}
    edited["name"] = "Live run"
    edited["panels"] = [{k: v for k, v in p.items() if k not in ("unresolved", "metric_refs")} for p in tab["panels"]
                        if p.get("options", {}).get("part") != "events"]
    with pytest.raises(TabError, match="keeps its name"):
        sources.save({**edited, "name": "Mine"}, previous_name=tab["name"])
    assert sources.save(edited, previous_name=tab["name"]) == "Live run"
    after = sources.layout()
    assert [t["name"] for t in after if t["source"] == "user"] == []   # not in the bar
    live = next(t for t in after if t["source"] == "live")
    assert "events" not in [p["options"].get("part") for p in live["panels"]]
    # Reset: deleting through the live tab brings the original back.
    sources.delete(live["name"])
    live = next(t for t in sources.layout() if t["source"] == "live")
    assert "events" in [p["options"].get("part") for p in live["panels"]]


def test_a_live_tab_cannot_be_hidden(tmp_path):
    from ffast.config.user_tabs import TabError

    sources, run = _live_sources(tmp_path)
    with pytest.raises(TabError, match="closes with its run"):
        sources.set_hidden(run.tab_name, True)
    with pytest.raises(TabError, match="closes with its run"):
        sources.set_hidden("Live run", True)


def test_a_live_panel_has_a_part_and_no_run_or_metrics():
    from pydantic import ValidationError

    from ffast.config.models import PanelConfig

    for part in ("status", "events", "attribution", "scores"):
        PanelConfig(kind="live", row=0, col=0, options={"part": part})
    with pytest.raises(ValidationError, match="options.part"):
        PanelConfig(kind="live", row=0, col=0, options={"part": "x"})
    with pytest.raises(ValidationError, match="takes no 'metrics'"):
        PanelConfig(kind="live", row=0, col=0, options={"part": "events"},
                    metrics={"y": {"metric": "ffast.gyradius"}})
    with pytest.raises(ValidationError):
        PanelConfig(kind="live", row=0, col=0, live_run="r", options={"part": "events"})


def test_the_built_in_tabs_hold_the_live_run_layout():
    from ffast.config.tabs import load_builtin_tabs

    [layout] = [t for t in load_builtin_tabs() if t.live_layout]
    assert layout.name == "Live run" and layout.controls == ["same_step"]
    assert any(p.kind == "live" and p.options["part"] == "scores" for p in layout.panels)


def test_stop_now_and_stop_on_alarm_write_into_the_run_folder(tmp_path):
    from ffast.live.live_run import find_run, set_stop_on_alarm, stop_run

    d = _run_folder(tmp_path)
    env, sent = _Env(), []
    env.live_runs = {}
    run = LiveRun(env, d, sent.append)
    env.live_runs[run.run_id] = run
    run.apply(run.read_files())
    assert _kwargs(run.message())["stop_on_alarm"] is False
    set_stop_on_alarm(env, run.run_id, True)
    assert rf.read_json(d / rf.CONTROL)["stop_on_alarm"] is True
    assert _kwargs(sent[-1])["stop_on_alarm"] is True
    assert stop_run(env, run.run_id) is True
    req = rf.read_json(d / rf.STOP_REQUEST)
    assert req["by"] == "user" and _kwargs(sent[-1])["stop_requested"] is True
    assert stop_run(env, run.run_id) is False                    # the first request stands
    assert [e["kind"] for e in rf.read_events(d)] == ["stop_requested"]
    with pytest.raises(ValueError, match="not open"):
        find_run(env, "other")
    run.run["end"] = {"state": "finished"}
    with pytest.raises(ValueError, match="already ended"):
        stop_run(env, run.run_id)


def _scored_run_with_bundle(tmp_path, n=3):
    """A run folder whose frames are aspirin (bundle atom order), the last one
    with bond C0–H14 pulled 0.6 Å, and whose settings name a real bundle."""
    from ffast.live.data_file import bundle_sha256
    from tests.ffast.live.bundle_helpers import initial_atoms, make_bundle

    (tmp_path / "b").mkdir()
    bundle_dir = make_bundle(tmp_path / "b")[0]
    base = initial_atoms().get_positions()
    frames = [base.copy() for _ in range(n)]
    u = frames[-1][14] - frames[-1][0]
    frames[-1][14] += 0.6 * u / np.linalg.norm(u)
    d = tmp_path / "20261105-120000-node-1-abcd"
    d.mkdir()
    rf.write_json_atomic(d / rf.RUN_JSON, {"format": rf.RUN_FORMAT, "run_id": d.name, "end": None})
    (d / rf.FRAMES).write_text(dump_text(frames))
    a = d / rf.ANALYSIS_DIR / "0123456789abcdef"
    a.mkdir(parents=True)
    rf.write_json_atomic(a / rf.SETTINGS, {"bundle": str(bundle_dir), "role": "centroid",
                                           "bundle_sha256": bundle_sha256(bundle_dir)})
    return d, bundle_dir


def test_attribution_of_a_live_frame_names_the_stretched_bond(tmp_path):
    d, _ = _scored_run_with_bundle(tmp_path)
    run = LiveRun(_Env(), d, lambda m: None)
    run.apply(run.read_files())
    got = run.attribution(2, ("bonds", "T2"))
    assert got["run_id"] == d.name and got["frame"] == 2 and got["step"] == 200
    assert got["fingerprint"] == f"live:{d.name}" and got["groups"][0]["label"] == "bond C0-H14"
    assert got["role"] == "centroid"
    with pytest.raises(ValueError, match="not read yet"):
        run.attribution(9)


def test_attribution_says_when_the_bundle_is_gone_or_changed(tmp_path):
    import shutil

    d, bundle_dir = _scored_run_with_bundle(tmp_path)
    run = LiveRun(_Env(), d, lambda m: None)
    run.apply(run.read_files())
    with open(bundle_dir / "manifest.json", "a") as fh:
        fh.write(" ")
    with pytest.raises(ValueError, match="changed after this run was scored"):
        run.attribution(0)
    shutil.rmtree(bundle_dir)
    with pytest.raises(ValueError, match="is gone"):
        run.attribution(0)


# ── ADR 0090 step 1: a metric drawn on a growing run grows with it ──────────

def test_a_growing_runs_inputs_are_named_by_their_frame_count(tmp_path):
    from ffast.metrics.input_resolver import _ResolverSource

    d = _run_folder(tmp_path)
    run = LiveRun(_Env(), d, lambda m: None)
    run.apply(run.read_files())
    source = _ResolverSource(None, None, run.dataset)
    before = source.identity("reference.positions")
    with open(d / rf.FRAMES, "a") as fh:
        fh.write(frame_text(200, random_frames(1, seed=4)[0], ASPIRIN_ELEMENTS))
    run.apply(run.read_files())
    after = source.identity("reference.positions")
    assert before and after and before != after


def test_growth_makes_the_result_store_forget_the_run(tmp_path):
    d = _run_folder(tmp_path)
    env = _Env()
    env.results = type("Store", (), {"forgotten": [],
                                      "forget_objects": lambda self, fp: self.forgotten.append(fp)})()
    run = LiveRun(env, d, lambda m: None)
    run.apply(run.read_files())
    env.results.forgotten.clear()
    with open(d / rf.FRAMES, "a") as fh:
        fh.write(frame_text(200, random_frames(1, seed=4)[0], ASPIRIN_ELEMENTS))
    run.apply(run.read_files())
    assert env.results.forgotten == [f"live:{d.name}"]
    run.apply(run.read_files())                      # nothing new: nothing forgotten
    assert env.results.forgotten == [f"live:{d.name}"]


def test_the_result_store_really_forgets_a_live_fingerprint():
    from tests.ffast.test_result_store import PLAIN, _Env as StoreEnv, _run
    from ffast.metrics.result_store import MetricResultStore

    env = StoreEnv()
    env.dataset.fingerprint = "live:run1"
    env.datasets.get = lambda fp: env.dataset if fp == "live:run1" else None
    store = MetricResultStore(env)
    _run(store.get(PLAIN, {}, "live:run1", None))
    assert store.peek(PLAIN, {}, "live:run1", None) is not None
    store.forget_objects("live:run1")
    assert store.peek(PLAIN, {}, "live:run1", None) is None

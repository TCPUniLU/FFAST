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

def test_a_run_with_frames_has_a_tab_its_3d_view_beside_its_scores(tmp_path):
    from ffast.config.user_tabs import TabSources
    from ffast.live.live_run import live_tabs, tab_name

    assert tab_name("20261105-103335-mel0314-17254-dae9") == "Live 10:33:35 mel0314"
    assert tab_name("odd") == "Live odd"
    d = _run_folder(tmp_path)
    env = _Env()
    env.live_runs = {}
    run = LiveRun(env, d, lambda m: None)
    env.live_runs[run.run_id] = run
    assert live_tabs(env) == []                         # no frames read yet
    run.apply(run.read_files())
    sources = TabSources(builtin=[], live=lambda: live_tabs(env))
    [tab] = sources.layout()
    assert tab["name"] == "Live 12:00:00 node" and tab["source"] == "live"
    assert tab["has_data_selector"] is False
    kinds = [(p["kind"], p.get("live_run")) for p in tab["panels"]]
    assert kinds == [("live", run.run_id), ("3d", None), ("live", run.run_id),
                     ("timeline", run.run_id), ("live", run.run_id)]
    assert [p["options"].get("part") for p in tab["panels"]] == [
        "status", None, "attribution", None, "events"]
    assert tab["panels"][3]["metrics"] == {}
    assert _kwargs(run.message())["tab"] == "Live 12:00:00 node"


def test_a_live_run_tab_is_never_saved(tmp_path):
    from ffast.config.user_tabs import TabError, TabSources, UserTabStore

    sources = TabSources(builtin=[], store=UserTabStore(tmp_path / "tabs"))
    tab = {"name": "Mine", "grid": 24, "panels": [
        {"kind": "3d", "row": 0, "col": 0, "rowspan": 4, "colspan": 12},
        {"kind": "timeline", "row": 0, "col": 12, "rowspan": 4, "colspan": 12, "live_run": "r1"}]}
    with pytest.raises(TabError, match="not saved"):
        sources.save(tab)


def test_a_live_run_panel_is_a_timeline_without_metrics():
    from pydantic import ValidationError

    from ffast.config.models import PanelConfig

    PanelConfig(kind="timeline", row=0, col=0, live_run="r1")
    with pytest.raises(ValidationError, match="live-run panel"):
        PanelConfig(kind="scatter", row=0, col=0, live_run="r1")


def test_a_live_panel_names_its_run_and_its_part():
    from pydantic import ValidationError

    from ffast.config.models import PanelConfig

    PanelConfig(kind="live", row=0, col=0, live_run="r", options={"part": "events"})
    with pytest.raises(ValidationError, match="names its run"):
        PanelConfig(kind="live", row=0, col=0, options={"part": "events"})
    with pytest.raises(ValidationError, match="options.part"):
        PanelConfig(kind="live", row=0, col=0, live_run="r", options={"part": "x"})
    with pytest.raises(ValidationError, match="takes no 'metrics'"):
        PanelConfig(kind="scatter", row=0, col=0, live_run="r")

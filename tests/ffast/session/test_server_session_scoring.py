"""Score a dataset from a window: the commands of ADR 0093 step 5.

A fake environment and queues, no socket. The scoring task is run by hand, as the
task manager would run it in a thread.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest
from ase.io import write

from ffast.live import references
from ffast.live import run_folder as rf
from ffast.live import scored_items as si
from ffast.live.scored_item import SCORED_PREFIX, reopen_scored_items
from ffast.loaders.ase import aseDatasetLoader
from ffast.protocol import control
from ffast.protocol.rpc import unpack
from ffast.session.server_session import ServerSession
from tests.ffast.live.bundle_helpers import make_bundle, write_frames


class _Registry(dict):
    def add(self, dataset):
        self[dataset.fingerprint] = dataset
        dataset.loaded = True


class _Env:
    def __init__(self):
        self.datasets = _Registry()
        self.scored_items = {}
        self.tasks = []
        self.cancelled = False

    def newTask(self, func, **kwargs):
        self.tasks.append((func, kwargs))

    def getTask(self, task_id):
        return {"task": SimpleNamespace(cancelling=lambda: int(self.cancelled), cancelled=lambda: False)}


@pytest.fixture(scope="module")
def bundle_dir(tmp_path_factory):
    return make_bundle(tmp_path_factory.mktemp("b"))[0]


@pytest.fixture(autouse=True)
def folders(tmp_path, monkeypatch):
    monkeypatch.setenv(si.ENV, str(tmp_path / "analyses"))
    monkeypatch.setenv(references.ENV, str(tmp_path / "refs"))


def _dataset(tmp_path, n=6, time_step=None):
    path = write_frames(tmp_path / "f.xyz", n, seed=9)
    if time_step is not None:
        from ase.io import read

        frames = read(str(path), index=":")
        for i, a in enumerate(frames):
            a.info["time"] = time_step * i
        write(str(path), frames, format="extxyz")
    ds = aseDatasetLoader(str(path))
    ds.initialise()
    return ds


def _ask(env, event, kwargs, after=None):
    broadcasts = []

    async def scenario():
        s = ServerSession(env, asyncio.Queue(), broadcast=broadcasts.append)
        await s.dispatch(event, [], kwargs)
        await asyncio.gather(*s._score_asks)     # the dialog's answer comes from a task of its own
        if after:
            await asyncio.to_thread(after)
            await asyncio.sleep(0)
        out = []
        while not s.outbound.empty():
            out.append(unpack(s.outbound.get_nowait()))
        return out

    return asyncio.run(scenario()), broadcasts


def _answer(own, event):
    [kw] = [k for e, _, k in own if e == event]
    return kw


def test_the_dialog_gets_the_references_the_time_and_the_items_made(tmp_path, bundle_dir):
    references.record_reference(bundle_dir)
    env = _Env()
    ds = _dataset(tmp_path, time_step=0.5)
    env.datasets.add(ds)
    own, _ = _ask(env, control.SCORE_DIALOG_INFO, {"fingerprint": ds.fingerprint})
    info = _answer(own, control.SCORE_DIALOG_INFO_RESULT)
    assert info["n_frames"] == 6 and info["frame_dt_fs"] == 0.5 and info["refusal"] is None
    assert info["detectana"] is None and info["persist_ps"] == 5.0 and info["existing"] == []
    assert [r["bundle"] for r in info["references"]] == [str(bundle_dir)]


def test_an_unknown_dataset_is_answered_with_an_error():
    own, _ = _ask(_Env(), control.SCORE_DIALOG_INFO, {"fingerprint": "nope"})
    assert "not loaded" in _answer(own, control.SCORE_DIALOG_INFO_RESULT)["error"]
    own, _ = _ask(_Env(), control.SCORE_DATASET, {"fingerprint": "nope", "bundle": "/b"})
    assert "not loaded" in _answer(own, control.SCORE_STARTED)["error"]


def test_scoring_shows_the_item_at_once_then_fills_it_in(tmp_path, bundle_dir):
    env = _Env()
    ds = _dataset(tmp_path)
    env.datasets.add(ds)
    ran = []

    def run_task():
        [(func, kwargs)] = env.tasks
        assert kwargs["visual"] and kwargs["threaded"] and kwargs["name"].startswith("Scoring ")
        func(taskID=1)
        ran.append(True)

    own, _ = _ask(env, control.SCORE_DATASET, {"fingerprint": ds.fingerprint, "bundle": str(bundle_dir),
                                                 "frame_dt_fs": 500.0, "persist_ps": 5.0}, after=run_task)
    out = _answer(own, control.SCORE_STARTED)
    assert out["started"] is True and out["fingerprint"].startswith(SCORED_PREFIX)
    # the item was in the list before a single frame was scored (the task had not run)
    assert out["fingerprint"] in env.datasets and env.scored_items[out["item_id"]].registered
    assert ran
    folder = si.item_dir(out["item_id"])
    assert si.read_item(folder)["state"] == "done"
    assert si.read_item(folder)["settings"]["frame_dt_ps"] == 0.5
    assert len(list((folder / rf.ANALYSIS_DIR).rglob("scores.jsonl"))) == 1


def test_the_same_key_again_scores_nothing_and_unhides(tmp_path, bundle_dir):
    env = _Env()
    ds = _dataset(tmp_path)
    env.datasets.add(ds)
    ask = {"fingerprint": ds.fingerprint, "bundle": str(bundle_dir), "frame_dt_fs": 500.0}
    own, _ = _ask(env, control.SCORE_DATASET, ask, after=lambda: env.tasks[0][0](taskID=1))
    iid = _answer(own, control.SCORE_STARTED)["item_id"]
    si.set_hidden(si.item_dir(iid), True)
    env.tasks.clear()
    env.scored_items.clear()
    own, _ = _ask(env, control.SCORE_DATASET, ask)
    again = _answer(own, control.SCORE_STARTED)
    assert again["item_id"] == iid and again["started"] is False and env.tasks == []
    assert not si.read_item(si.item_dir(iid))["hidden"]


def test_cancelling_leaves_an_incomplete_item_and_continuing_finishes_it(tmp_path, bundle_dir, monkeypatch):
    monkeypatch.setattr(si, "CHUNK", 2)
    env = _Env()
    ds = _dataset(tmp_path, n=6)
    env.datasets.add(ds)
    env.cancelled = True            # Cancel pressed before the first chunk is checked
    own, _ = _ask(env, control.SCORE_DATASET, {"fingerprint": ds.fingerprint, "bundle": str(bundle_dir)},
                  after=lambda: env.tasks[0][0](taskID=1))
    out = _answer(own, control.SCORE_STARTED)
    folder = si.item_dir(out["item_id"])
    assert si.read_item(folder)["state"] == "cancelled"
    rows = rf.read_scores(next((folder / rf.ANALYSIS_DIR).iterdir()) / rf.SCORES)
    assert [r["frame"] for r in rows] == [0, 1]

    env.cancelled = False
    env.tasks.clear()
    own, _ = _ask(env, control.CONTINUE_SCORING, {"fingerprint": out["fingerprint"]},
                  after=lambda: env.tasks[0][0](taskID=2))
    assert _answer(own, control.SCORE_STARTED)["started"] is True
    assert si.read_item(folder)["state"] == "done"
    rows = rf.read_scores(next((folder / rf.ANALYSIS_DIR).iterdir()) / rf.SCORES)
    assert [r["frame"] for r in rows] == list(range(6))


def test_loading_the_same_file_again_brings_its_scored_items_back(tmp_path, bundle_dir):
    ds = _dataset(tmp_path)
    folder = si.score_dataset(ds, bundle_dir, si.make_settings(0.5))
    hidden = si.score_dataset(ds, bundle_dir, si.make_settings(0.25))
    si.set_hidden(hidden, True)

    async def new_session():
        env = _Env()
        again = aseDatasetLoader(str(tmp_path / "f.xyz"))
        again.initialise()
        env.datasets.add(again)
        opened = reopen_scored_items(env, again, lambda m: None)
        return env, opened

    env, opened = asyncio.run(new_session())
    assert [o.run_id for o in opened] == [si.read_item(folder)["item_id"]]
    assert opened[0].fingerprint in env.datasets and len(env.datasets) == 2


def test_new_commands_are_known_and_only_the_mutating_ones_are_blocked_for_viewers():
    for name in ("SCORE_DIALOG_INFO", "SCORE_DATASET", "CONTINUE_SCORING"):
        assert getattr(control, name) in control.CLIENT_TO_SERVER
    assert control.SCORE_DATASET in control.MUTATING_CLIENT_EVENTS
    assert control.CONTINUE_SCORING in control.MUTATING_CLIENT_EVENTS
    assert control.SCORE_DIALOG_INFO not in control.MUTATING_CLIENT_EVENTS


def test_a_quick_ask_only_says_whether_detectana_is_installed(tmp_path):
    env = _Env()
    ds = _dataset(tmp_path)
    env.datasets.add(ds)
    own, _ = _ask(env, control.SCORE_DIALOG_INFO, {"fingerprint": ds.fingerprint, "quick": True})
    info = _answer(own, control.SCORE_DIALOG_INFO_RESULT)
    assert info == {"fingerprint": ds.fingerprint, "quick": True, "detectana": None}

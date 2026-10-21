"""The window feed (ADR 0062): builds every piece the windows share, once."""
import asyncio

import ffast.metrics.builtin  # noqa: F401
from ffast.protocol import control
from ffast.protocol.rpc import unpack
from ffast.session.server_session import ServerSession
from ffast.session.window_feed import WindowFeed


class _Dataset:
    fingerprint = "ds1"

    def toMetaDict(self):
        return {"name": "water", "n": 3}


class _Datasets:
    def all(self):
        return [_Dataset()]


class _Model:
    name = "mace"


class _Env:
    datasets = _Datasets()
    models = {"mfp12345678": _Model()}
    cache = {}


def test_replay_is_datasets_then_models_then_catalog():
    feed = WindowFeed(_Env(), lambda data: None)
    events = [unpack(data)[0] for data, _label in feed.replay()]
    assert events == [control.REMOTE_DATASET_META, control.REMOTE_MODEL_META,
                      control.METRIC_CATALOG]


def test_dataset_meta_is_the_same_bytes_the_replay_holds():
    feed = WindowFeed(_Env(), lambda data: None)
    replayed = dict((label, data) for data, label in feed.replay())
    assert feed.dataset_meta(_Dataset()) == replayed["dataset 'ds1'"]


def test_a_dataset_without_a_fingerprint_has_no_announcement():
    class _Bare:
        pass
    assert WindowFeed(_Env(), lambda data: None).dataset_meta(_Bare()) is None


def test_view_colouring_users_finds_an_attached_sessions_view():
    class _View:
        class state:
            parameters = {"ffast.atom_color": {"source": "metric:t62.double"}}

    feed = WindowFeed(_Env(), lambda data: None)
    s = ServerSession(_Env(), asyncio.Queue())
    s.views["v"] = _View()
    feed.attach(s)
    assert feed.view_colouring_users("t62.double") == ["the main view's colouring"]
    assert feed.view_colouring_users("other.metric") == []

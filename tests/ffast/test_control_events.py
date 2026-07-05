"""Contract + validation tests for the Control message layer (ADR 0033).

Event names used to be scattered string literals with nothing to keep a send
site and the server's handler table in agreement. They now come from
``ffast.protocol.control``; these tests assert the two authoritative tables
(``ServerSession._handlers``, ``cluster.connection._REPLY_CHANNELS``) match
the declared constant sets, and that ``ServerSession.dispatch`` actually
validates a Control message's payload against its typed request model.
"""
import asyncio

import pytest
from pydantic import ValidationError

from ffast.protocol import control
from ffast.protocol.messages import (
    DeleteObjectRequest,
    LoadDatasetRequest,
    OpenViewRequest,
    RequestMetricRequest,
)
from ffast.session.server_session import ServerSession


def _run(coro):
    return asyncio.run(coro)


# ── contract: event-name tables agree with the constants module ─────────────

def test_server_session_handlers_match_client_to_server_constants():
    s = ServerSession(env=None, outbound=asyncio.Queue())
    assert set(s._handlers) == control.CLIENT_TO_SERVER


def test_reply_channels_match_reply_event_constants():
    from cluster.connection import _REPLY_CHANNELS

    assert set(_REPLY_CHANNELS) == control.REPLY_EVENTS


# ── request model shape ──────────────────────────────────────────────────────

def test_load_dataset_request_accepts_the_real_wire_shape():
    payload = {
        "path": "/data.xyz", "dataset_type": "ase",
        "selected_energy_key": "energy", "selected_force_key": "forces",
        "prediction_keys": [["e", "f"]], "slice_num": 3,
    }
    assert LoadDatasetRequest.model_validate(payload).model_dump() == payload


def test_load_dataset_request_rejects_extra_key():
    with pytest.raises(ValidationError):
        LoadDatasetRequest.model_validate({
            "path": "/a", "dataset_type": "ase", "surprise": 1,
        })


def test_load_dataset_request_requires_path_and_type():
    with pytest.raises(ValidationError):
        LoadDatasetRequest.model_validate({"path": "/a"})


def test_open_view_request_all_optional():
    assert OpenViewRequest.model_validate({}).model_dump() == {
        "view_id": None, "dataset_ref": None, "prediction_ref": None,
    }


def test_request_metric_request_shape():
    payload = {
        "metric_id": "ffast.force_rmse", "key": "k1",
        "params": {"order": 1}, "model_fp": "m1", "dataset_fp": "d1",
    }
    assert RequestMetricRequest.model_validate(payload).model_dump() == payload


def test_delete_object_request_requires_fingerprint():
    with pytest.raises(ValidationError):
        DeleteObjectRequest.model_validate({})


# ── dispatch actually gates on the model (not just documents it) ────────────

class _FakeEnv:
    def __init__(self):
        self.deleted = []

    def deleteObject(self, fp):
        self.deleted.append(fp)


def test_dispatch_drops_payload_that_fails_model_validation():
    # DELETE_OBJECT's fingerprint must be a string; a dict can't coerce.
    env = _FakeEnv()

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch(control.DELETE_OBJECT, [{"not": "a string"}], {})

    _run(scenario())
    assert env.deleted == []  # handler never ran — validation gate caught it


def test_dispatch_still_calls_handler_with_raw_resolved_dict_on_success():
    # The gate validates but must not reshape what reaches the handler —
    # OpenViewRequest defaults prediction_ref to None, but OPEN_VIEW's handler
    # relies on the key being ABSENT (vs. explicitly null) to know whether to
    # touch the current overlay at all. dispatch must forward the original
    # kwargs, not the validated model's dump.
    calls = []

    class _ViewEnv:
        class datasets:
            @staticmethod
            def get(fp):
                return None
        models = {}

    async def scenario():
        s = ServerSession(_ViewEnv(), asyncio.Queue())

        async def fake_open_view(**kwargs):
            calls.append(kwargs)

        s._on_open_view = fake_open_view
        s._handlers[control.OPEN_VIEW] = s._handlers[control.OPEN_VIEW].__class__(
            fake_open_view, [], OpenViewRequest
        )
        await s.dispatch(control.OPEN_VIEW, [], {"view_id": "v1"})

    _run(scenario())
    assert calls == [{"view_id": "v1"}]  # prediction_ref key absent, not None

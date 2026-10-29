"""The web launcher's cluster broker (web parity plan 2026-10-29). No real
SSH: every SSH/SLURM step is a fake."""
from unittest import mock

import pytest

import cluster.connection as conn
from cluster.config import ClusterConfig
from cluster.connection import ServerConnection
from cluster.web_broker import ClusterBroker, profile_from_fields
from ffast.protocol.rpc import unpack


def _conn_with_fakes():
    ws = mock.MagicMock()
    ws.send = mock.AsyncMock()
    ws.close = mock.AsyncMock()
    proc = mock.MagicMock()
    sc = ServerConnection(job_id="J1", ssh_proc=proc, websocket=ws, profile=None,
                            local_port=1, remote_port=2)
    return sc, ws, proc


async def test_close_websocket_keeps_the_tunnel():
    sc, ws, proc = _conn_with_fakes()
    await sc.close_websocket()
    assert ws.close.await_count == 1
    assert proc.terminate.call_count == 0
    assert ws.send.await_count == 1
    assert unpack(ws.send.await_args.args[0])[0] == "GRACEFUL_DISCONNECT"


async def test_disconnect_still_closes_both():
    sc, ws, proc = _conn_with_fakes()
    await sc.disconnect()
    assert ws.close.await_count == 1
    assert proc.terminate.call_count == 1


@pytest.fixture
def broker(tmp_path, monkeypatch):
    monkeypatch.setattr(conn, "_SESSIONS_FILE", str(tmp_path / ".ffast" / "sessions.json"))
    yield ClusterBroker(ClusterConfig(str(tmp_path / "clusters.json")))


def test_profile_from_fields_parses_numbers_and_modules():
    p = profile_from_fields({"name": " m ", "cores": "4", "modules": "Python, CUDA  x",
                             "provision": True})
    assert p.name == "m"
    assert p.cores == 4
    assert p.modules == ["Python", "CUDA", "x"]
    assert p.provision is True
    assert p.job_name == "ffast"


def test_profile_from_fields_refuses_bad_input():
    with pytest.raises(ValueError, match="Unknown profile field: bogus"):
        profile_from_fields({"bogus": 1})
    with pytest.raises(ValueError, match="cores must be a whole number"):
        profile_from_fields({"cores": "x"})
    with pytest.raises(ValueError, match="memory_mb cannot be negative"):
        profile_from_fields({"memory_mb": -1})
    with pytest.raises(ValueError, match="Give the profile a name"):
        profile_from_fields({}, need_name=True)


def test_saved_profiles_come_back_in_state(broker, tmp_path):
    status, _ = broker.handle("POST", "/cluster/profile",
                              {"profile": {"name": "meluxina", "host": "login.lxp.lu"}})
    assert status == 200
    assert broker.state()["profiles"][0]["host"] == "login.lxp.lu"
    again = ClusterBroker(ClusterConfig(str(tmp_path / "clusters.json")))
    assert again.state()["profiles"][0]["name"] == "meluxina"
    broker.handle("POST", "/cluster/profile/delete", {"name": "meluxina"})
    assert broker.state()["profiles"] == []


def test_records_are_listed_without_tokens(broker):
    conn.save_session_record("42", "meluxina", "node07", 8765, token="tok")
    records = broker.state()["records"]
    assert [r["job_id"] for r in records] == ["42"]
    assert all("token" not in r for r in records)


def test_unknown_request_is_404_and_bad_profile_is_400(broker):
    assert broker.handle("GET", "/cluster/nope", {})[0] == 404
    status, body = broker.handle("POST", "/cluster/profile", {"profile": {"cores": "x"}})
    assert status == 400 and "whole number" in body["error"]

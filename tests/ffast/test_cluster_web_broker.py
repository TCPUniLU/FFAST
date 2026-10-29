"""The web launcher's cluster broker (web parity plan 2026-10-29). No real
SSH: every SSH/SLURM step is a fake."""
import asyncio
import time
from unittest import mock

import pytest

import cluster.connection as conn
from cluster.backend import ClusterError
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
    b = ClusterBroker(ClusterConfig(str(tmp_path / "clusters.json")))
    yield b
    b.shutdown()


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


class _FakeSession:
    def __init__(self, job_id="J1", local_port=4321, token="tok"):
        self.job_id, self.local_port, self.token_plaintext = job_id, local_port, token
        self.ssh_proc = mock.MagicMock()
        self.ssh_proc.poll.return_value = None
        self.closed_ws = False
        self.disconnected = False

    async def close_websocket(self):
        self.closed_ws = True

    async def disconnect(self):
        self.disconnected = True


def _wait(broker, state, timeout=5.0):
    """Poll state() until status.state is `state` (the broker runs on its own thread)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = broker.state()["status"]
        if s["state"] == state:
            return s
        time.sleep(0.02)
    raise AssertionError(f"still {broker.state()['status']}")


def _wait_for(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError("condition never became true")


@pytest.fixture
def make(tmp_path, monkeypatch):
    """A broker with fake SSH/SLURM steps; `cancelled` lists cancelled jobs."""
    monkeypatch.setattr(conn, "_SESSIONS_FILE", str(tmp_path / ".ffast" / "sessions.json"))
    made, cancelled = [], []

    async def cancel_job(profile, job_id):
        cancelled.append(job_id)

    async def no_log(profile, job_id):
        return ""

    def build(**fakes):
        fakes.setdefault("cancel_job", cancel_job)
        fakes.setdefault("job_log", no_log)
        b = ClusterBroker(ClusterConfig(str(tmp_path / "clusters.json")), **fakes)
        made.append(b)
        return b

    build.cancelled = cancelled
    yield build
    for b in made:
        b.shutdown()


_PROFILE = {"name": "m", "host": "h"}


def test_connect_gives_the_page_the_tunnel(make):
    session = _FakeSession()

    async def connect(profile, progress_cb=None, on_job_submitted=None):
        on_job_submitted("J1")
        progress_cb("Submitting SLURM job…")
        return session

    b = make(connect=connect)
    assert b.handle("POST", "/cluster/connect", {"profile": _PROFILE})[0] == 200
    s = _wait(b, "connected")
    assert s["ws_url"] == "ws://127.0.0.1:4321"
    assert s["token"] == "tok"
    assert s["job_id"] == "J1"
    assert "Submitting SLURM job…" in s["messages"]
    assert session.closed_ws and not session.disconnected


def test_a_second_connect_is_refused(make):
    async def connect(profile, progress_cb=None, on_job_submitted=None):
        await asyncio.Event().wait()

    b = make(connect=connect)
    b.handle("POST", "/cluster/connect", {"profile": _PROFILE})
    assert b.handle("POST", "/cluster/connect", {"profile": _PROFILE})[0] == 409


def test_a_profile_without_host_is_refused(make):
    status, body = make().handle("POST", "/cluster/connect", {"profile": {"name": "m"}})
    assert status == 400 and "No host" in body["error"]


def test_cancel_before_running_cancels_the_submitted_job(make):
    async def connect(profile, progress_cb=None, on_job_submitted=None):
        on_job_submitted("J9")
        await asyncio.sleep(30)

    b = make(connect=connect)
    b.handle("POST", "/cluster/connect", {"profile": _PROFILE})
    _wait_for(lambda: b.state()["status"]["job_id"] == "J9")
    b.handle("POST", "/cluster/cancel", {})
    _wait(b, "idle")
    _wait_for(lambda: make.cancelled == ["J9"])


def test_a_dead_job_on_reconnect_purges_its_record(make):
    conn.save_session_record("J5", "m", "node1", 8765)

    async def reconnect(profile, job_id, progress_cb=None):
        raise ClusterError("Job J5 is no longer running")

    b = make(reconnect=reconnect)
    b.handle("POST", "/cluster/connect", {"profile": _PROFILE, "reconnect_job_id": "J5"})
    s = _wait(b, "failed")
    assert "no longer running" in s["error"]
    assert b.state()["records"] == []
    assert make.cancelled == []


def test_a_tunnel_failure_on_reconnect_keeps_the_record(make):
    conn.save_session_record("J5", "m", "node1", 8765)

    async def reconnect(profile, job_id, progress_cb=None):
        raise OSError("refused")

    b = make(reconnect=reconnect)
    b.handle("POST", "/cluster/connect", {"profile": _PROFILE, "reconnect_job_id": "J5"})
    _wait(b, "failed")
    assert [r["job_id"] for r in b.state()["records"]] == ["J5"]
    assert make.cancelled == []


def test_a_tunnel_failure_on_a_new_job_cancels_it(make):
    async def connect(profile, progress_cb=None, on_job_submitted=None):
        on_job_submitted("J7")
        raise OSError("refused")

    b = make(connect=connect)
    b.handle("POST", "/cluster/connect", {"profile": _PROFILE})
    _wait(b, "failed")
    assert make.cancelled == ["J7"]


def test_install_failure_shows_the_job_log(make):
    async def connect(profile, progress_cb=None, on_job_submitted=None):
        on_job_submitted("J8")
        raise ClusterError("job failed")

    async def job_log(profile, job_id):
        return "pip error"

    b = make(connect=connect, job_log=job_log)
    b.handle("POST", "/cluster/connect", {"profile": dict(_PROFILE, provision=True)})
    assert _wait(b, "failed")["details"] == "pip error"


def _connected(make, session):
    async def connect(profile, progress_cb=None, on_job_submitted=None):
        on_job_submitted(session.job_id)
        return session

    b = make(connect=connect)
    b.handle("POST", "/cluster/connect", {"profile": _PROFILE})
    _wait(b, "connected")
    return b


def test_end_job_closes_the_tunnel_cancels_and_forgets(make):
    session = _FakeSession()
    b = _connected(make, session)
    conn.save_session_record("J1", "m", "node1", 8765)
    assert b.handle("POST", "/cluster/stop", {})[0] == 200
    _wait(b, "idle")
    assert session.disconnected
    assert make.cancelled == ["J1"]
    assert b.state()["records"] == []


def test_shutdown_keeps_the_job_and_its_record(make):
    session = _FakeSession()
    b = _connected(make, session)
    conn.save_session_record("J1", "m", "node1", 8765)
    b.shutdown()
    assert session.disconnected
    assert make.cancelled == []
    assert [r["job_id"] for r in conn.load_session_records()] == ["J1"]


def test_a_dead_tunnel_shows_as_failed(make):
    session = _FakeSession()
    b = _connected(make, session)
    session.ssh_proc.poll.return_value = 255
    s = b.state()["status"]
    assert s["state"] == "failed" and "tunnel" in s["error"]

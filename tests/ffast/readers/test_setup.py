"""Cluster reader setup: shared offers, explicit installs, safe fallbacks."""
import asyncio
import sys
import threading
from types import SimpleNamespace

import pytest
import msgpack

from ffast.protocol import control
from ffast.readers import setup
from ffast.session.server_session import ServerSession


async def test_a_newly_installed_package_is_activated_without_restarting(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(setup, "managed_environment", lambda: True)
    monkeypatch.setattr(setup.text, "_fast_usable", False)
    monkeypatch.setattr(setup.text, "_fast_missing", True)
    previous = sys.modules.pop("ffast_xyz", None)

    def install(source, out):
        (tmp_path / "ffast_xyz.py").write_text(
            f"CONTRACT = {setup.text.FAST_CONTRACT}\n")
        return 0

    monkeypatch.setattr(setup.install_fast, "install", install)
    try:
        result = await setup.FastReaderSetup().install(lambda line: None)
        assert result["ready"]
        assert setup.text.fast_parser_usable()
    finally:
        sys.modules.pop("ffast_xyz", None)
        if previous is not None:
            sys.modules["ffast_xyz"] = previous


@pytest.fixture
def reader(monkeypatch):
    state = {"ready": False}
    monkeypatch.setattr(setup.text, "fast_parser_usable", lambda: state["ready"])
    monkeypatch.setattr(setup.text, "_fast_warned", False)
    monkeypatch.setattr(setup.text, "_fast_missing", True)
    monkeypatch.setattr(setup.text, "_fast_usable", None)
    monkeypatch.setattr(setup, "managed_environment", lambda: True)
    return state


async def test_only_one_window_gets_the_offer_and_no_install_runs(reader, monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("offering or declining must not install anything")
    monkeypatch.setattr(setup.install_fast, "install", forbidden)
    env = SimpleNamespace()
    a, b = (ServerSession(env, asyncio.Queue()) for _ in range(2))
    await a.dispatch(control.FAST_READER, [], {"action": "offer"}, msg_id=17)
    await b.dispatch(control.FAST_READER, [], {"action": "offer"}, msg_id=19)
    first, second = (msgpack.unpackb(s.outbound.get_nowait(), raw=False) for s in (a, b))
    assert first["kwargs"]["offer"] and not second["kwargs"]["offer"]
    assert first["reply_to"] == 17 and second["reply_to"] == 19
    assert setup.text._fast_warned


async def test_manual_environment_never_installs(reader, monkeypatch):
    monkeypatch.setattr(setup, "managed_environment", lambda: False)
    monkeypatch.setattr(setup.install_fast, "install", lambda **kw: pytest.fail("manual env changed"))
    result = await setup.FastReaderSetup().install(lambda line: None)
    assert "manually managed" in result["error"] and not result["ready"]


async def test_two_windows_share_one_install_that_survives_disconnect(reader, monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []

    def install(source, out):
        calls.append(source)
        out("Downloading the matching wheel")
        started.set()
        assert release.wait(5), "test did not release installer"
        reader["ready"] = True
        return 0

    monkeypatch.setattr(setup.install_fast, "install", install)
    service = setup.FastReaderSetup()
    lines = []
    a = asyncio.create_task(service.install(lambda line: None))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        b = asyncio.create_task(service.install(lines.append))
        a.cancel()
        with pytest.raises(asyncio.CancelledError):
            await a
        assert not service.task.cancelled()
        release.set()
        assert (await b)["ready"]
        assert len(calls) == 1
        assert "Downloading the matching wheel" in lines
    finally:
        release.set()
        if service.task:
            await service.task


@pytest.mark.parametrize("failure", ["build failed", "crash", "stale native module"])
async def test_install_failure_keeps_python_available(reader, monkeypatch, failure):
    def install(source, out):
        out("Rust/build details")
        if failure == "crash":
            raise OSError("installer unavailable")
        return 0 if failure == "stale native module" else 1

    monkeypatch.setattr(setup.install_fast, "install", install)
    service = setup.FastReaderSetup()
    result = await service.install(lambda line: None)
    assert not result["ready"] and result["error"]
    assert "Rust/build details" in service.lines
    if failure == "stale native module":
        assert "restart" in result["error"]


def test_install_is_allowed_only_in_a_writable_ffast_provisioned_venv(tmp_path, monkeypatch):
    # Restore the real predicate replaced by the other tests' fixture.
    monkeypatch.setattr(setup.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(setup.sys, "base_prefix", str(tmp_path / "base"))
    assert not setup.managed_environment()
    (tmp_path / ".ffast-install.sha256").write_text("marker")
    assert setup.managed_environment()
    monkeypatch.setattr(setup.os, "access", lambda *args: False)
    assert not setup.managed_environment()


async def test_progress_and_final_reply_keep_each_windows_request_id(reader, monkeypatch):
    def install(source, out):
        out("Installing wheel")
        reader["ready"] = True
        return 0

    monkeypatch.setattr(setup.install_fast, "install", install)
    session = ServerSession(SimpleNamespace(), asyncio.Queue())
    await session.dispatch(control.FAST_READER, [], {"action": "install"}, msg_id=41)
    replies = []
    while not session.outbound.empty():
        replies.append(msgpack.unpackb(session.outbound.get_nowait(), raw=False))
    assert all(r["reply_to"] == 41 for r in replies)
    assert [r["final"] for r in replies].count(True) == 1
    assert replies[-1]["kwargs"]["ready"]
    assert any(r["kwargs"].get("line") == "Installing wheel" and not r["final"] for r in replies)

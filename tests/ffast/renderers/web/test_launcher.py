"""Unit tests for the one-command web launcher (ADR 0045, decision #1).

The launcher's external behaviour is: start the web app + WebSocket server on
loopback and open the user's browser at the app, pointed at the WS port. These
tests exercise that behaviour in-process — the static server really serves the
app and the browser-open is captured — without spawning the heavy ffast-server
(that path is covered end-to-end by the Playwright runtime test).
"""

from __future__ import annotations

import socket
import threading
import urllib.request
from dataclasses import dataclass
from uuid import uuid4
import pytest

from ffast.renderers.web import launcher


def test_app_url_points_web_app_at_ws_port():
    uid = uuid4().hex
    assert (
        f"http://127.0.0.1:9000/?port=8765&launch={uid}" == launcher.app_url(9000, 8765, uid=uid)
    )


def test_app_url_honours_host():
    uid = uuid4().hex
    assert (
        f"http://192.168.0.5:9000/?port=8765&launch={uid}" == launcher.app_url(9000, 8765, host="192.168.0.5", uid=uid)
    )


def test_open_browser_default_uses_opener():
    seen = []
    launcher.open_browser("http://x/", app_mode=False, opener=seen.append)
    assert seen == ["http://x/"]


def test_open_browser_app_mode_launches_chromium(monkeypatch):
    monkeypatch.setattr(launcher, "_find_app_mode_executable", lambda: "/usr/bin/chrome")
    launched = []
    opened = []
    launcher.open_browser(
        "http://x/",
        app_mode=True,
        opener=opened.append,
        launch=launched.append,
    )
    assert launched == [["/usr/bin/chrome", "--app=http://x/"]]
    assert opened == []  # app-mode does not fall through to the default browser


def test_open_browser_app_mode_falls_back_when_no_chromium(monkeypatch):
    monkeypatch.setattr(launcher, "_find_app_mode_executable", lambda: None)
    opened = []
    launcher.open_browser(
        "http://x/",
        app_mode=True,
        opener=opened.append,
        launch=lambda argv: pytest.fail("must not launch when no chromium found"),
    )
    assert opened == ["http://x/"]


def test_wait_until_ready_true_when_port_listens():
    with socket.socket() as lsock:
        lsock.bind(("127.0.0.1", 0))
        lsock.listen(1)
        port = lsock.getsockname()[1]
        assert launcher.wait_until_ready(port, timeout=2.0) is True


def test_wait_until_ready_false_when_nothing_listens():
    # bind then close to obtain a definitely-free port
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert launcher.wait_until_ready(port, timeout=0.3, interval=0.05) is False


def test_spawn_server_binds_the_given_host(monkeypatch):
    captured = {}
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/ffast-server")
    monkeypatch.setattr(
        launcher.subprocess, "Popen", lambda cmd, *a, **k: captured.setdefault("cmd", cmd)
    )
    launcher._spawn_server(8765, host="127.0.0.1")
    cmd = captured["cmd"]
    assert "--host" in cmd and cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert "--port" in cmd and "8765" in cmd


@dataclass
class _FakeProc:
    """Stands in for the ffast-server subprocess in tests."""

    listener: socket.socket
    terminated: bool = False

    def wait(self):  # pragma: no cover - block=False path never calls this
        pass

    def terminate(self):
        self.terminated = True
        self.listener.close()

    def poll(self):
        return None


def test_run_serves_the_app_and_opens_browser():
    ws_port = launcher.pick_free_port()
    web_port = launcher.pick_free_port()

    # Fake the heavy ffast-server with a bare listener so readiness succeeds.
    fake = _FakeProc(listener=socket.socket())
    fake.listener.bind(("127.0.0.1", ws_port))
    fake.listener.listen(1)

    uid = uuid4().hex
    opened = []
    spawn_calls = []
    result = launcher.run(
        ws_port=ws_port,
        web_port=web_port,
        app_mode=False,
        spawn_server=lambda p, h: spawn_calls.append((p, h)) or fake,
        opener=opened.append,
        block=False,
        uid=uid
    )
    try:
        assert spawn_calls == [(ws_port, "127.0.0.1")]  # host threaded to the server
        assert [f"http://127.0.0.1:{web_port}/?port={ws_port}&launch={uid}"][0] == opened[0] # perhaps a better design is required?
        assert result.url == opened[0]
        # The static server really serves the FFAST web app.
        with urllib.request.urlopen(
            f"http://127.0.0.1:{web_port}/", timeout=3
        ) as resp:
            body = resp.read().decode("utf-8")
        assert resp.status == 200
        assert "FFAST" in body
        assert 'src="ffast-viewer.js"' in body
    finally:
        result.httpd.shutdown()
        fake.terminate()


@dataclass
class _DeadProc:
    """A subprocess that has already exited (server failed to start)."""

    returncode: int = 1

    def poll(self):
        return self.returncode

    def wait(self):  # pragma: no cover - abort path never blocks on wait
        return self.returncode

    def terminate(self):
        pass


def test_run_aborts_when_server_dies_during_startup():
    web_port = launcher.pick_free_port()
    ws_port = launcher.pick_free_port()  # nothing ever listens here

    opened = []
    with pytest.raises(launcher.LauncherError):
        launcher.run(
            ws_port=ws_port,
            web_port=web_port,
            spawn_server=lambda p, h: _DeadProc(returncode=2),
            opener=opened.append,
            block=False,
            ready_timeout=2.0,
        )
    assert opened == []  # never open a browser at a server that already died
    # the static server was torn down, so its port is free to bind again
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", web_port))


def _run_with_fake_server(**kwargs):
    """``launcher.run`` against a bare listener standing in for ffast-server."""
    fake = _FakeProc(listener=socket.socket())
    captured = {}

    def spawn(ws_port, host):
        captured["ws_port"] = ws_port
        fake.listener.bind(("127.0.0.1", ws_port))
        fake.listener.listen(1)
        return fake

    result = launcher.run(
        spawn_server=spawn,
        opener=lambda url: None,
        block=False,
        ready_timeout=2.0,
        **kwargs,
    )
    return result, fake, captured


def test_run_picks_a_free_ws_port_when_unspecified(monkeypatch):
    monkeypatch.setattr(launcher, "DEFAULT_WEB_PORT", launcher.pick_free_port())
    result, fake, captured = _run_with_fake_server()
    try:
        assert result.ws_port == captured["ws_port"] > 0
        assert result.ws_port != result.web_port
    finally:
        result.httpd.shutdown()
        fake.terminate()


def test_default_web_port_is_fixed():
    """Browser storage belongs to the page's address, port included, so a
    new port each launch forgot the layout every time (ADR 0056)."""
    assert launcher.DEFAULT_WEB_PORT == 8764


def test_run_serves_the_app_on_the_default_web_port(monkeypatch):
    default = launcher.pick_free_port()
    monkeypatch.setattr(launcher, "DEFAULT_WEB_PORT", default)
    result, fake, _ = _run_with_fake_server()
    try:
        assert result.web_port == default
        assert result.url.startswith(f"http://127.0.0.1:{default}/")
    finally:
        result.httpd.shutdown()
        fake.terminate()


def test_run_takes_a_free_web_port_when_the_default_is_busy(monkeypatch):
    """A second ffast-web still starts; only its layout is not shared."""
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        default = busy.getsockname()[1]
        monkeypatch.setattr(launcher, "DEFAULT_WEB_PORT", default)
        result, fake, _ = _run_with_fake_server()
        try:
            assert result.web_port not in (0, default)
        finally:
            result.httpd.shutdown()
            fake.terminate()


def test_an_explicit_busy_web_port_is_an_error(monkeypatch):
    """--web-port is a request for that port, not a hint."""
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        with pytest.raises(OSError):
            _run_with_fake_server(web_port=busy.getsockname()[1])

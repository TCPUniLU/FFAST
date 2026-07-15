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

import pytest

from ffast.renderers.web import launcher


def test_app_url_points_web_app_at_ws_port():
    assert (
        launcher.app_url(9000, 8765)
        == "http://127.0.0.1:9000/?port=8765"
    )


def test_app_url_honours_host():
    assert (
        launcher.app_url(9000, 8765, host="192.168.0.5")
        == "http://192.168.0.5:9000/?port=8765"
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

    opened = []
    spawn_calls = []
    result = launcher.run(
        ws_port=ws_port,
        web_port=web_port,
        app_mode=False,
        spawn_server=lambda p, h: spawn_calls.append((p, h)) or fake,
        opener=opened.append,
        block=False,
    )
    try:
        assert spawn_calls == [(ws_port, "127.0.0.1")]  # host threaded to the server
        assert opened == [f"http://127.0.0.1:{web_port}/?port={ws_port}"]
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


def test_run_picks_free_ports_when_unspecified():
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
    )
    try:
        assert result.ws_port == captured["ws_port"] > 0
        assert result.web_port > 0
        assert result.ws_port != result.web_port
    finally:
        result.httpd.shutdown()
        fake.terminate()

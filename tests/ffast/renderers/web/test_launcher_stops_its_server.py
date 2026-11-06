"""`ffast-web` must not leave its local ffast-server running after it ends.

Before: only Ctrl-C ran the launcher's cleanup. `kill` (SIGTERM), a closed
terminal (SIGHUP) or `kill -9` left the server running and holding its port,
so the next `ffast-web` on that port could not start (found in the step-5
MeluXina proof, 2026-11-06).
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _wait(cond, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def _launch():
    ws, web = _free_port(), _free_port()
    code = ("import sys; from ffast.renderers.web.launcher import main; "
            f"sys.argv = ['ffast-web', '--no-browser', '--ws-port', '{ws}', '--web-port', '{web}']; main()")
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=str(REPO),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert _wait(lambda: _listening(ws), 60), "the server never started"
    return proc, ws


@pytest.mark.integration
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP, signal.SIGKILL],
                         ids=["kill", "closed-terminal", "kill-9"])
def test_the_server_stops_when_the_launcher_ends(sig):
    proc, ws = _launch()
    try:
        os.kill(proc.pid, sig)
        proc.wait(timeout=30)
        # kill -9 gives the launcher no chance: the server notices its parent
        # is gone (checked every 2 s) and stops itself.
        assert _wait(lambda: not _listening(ws), 20), f"the server still holds port {ws}"
    finally:
        if proc.poll() is None:
            proc.kill()


def test_the_parent_watch_interrupts_once_the_parent_is_gone(monkeypatch):
    import server

    calls = []
    monkeypatch.setattr(server.os, "kill", lambda pid, sig: calls.append((pid, sig)))
    answers = iter([False, False, True])
    t = server._exit_with_parent(12345, interval=0.01, gone=lambda pid: next(answers))
    t.join(timeout=5)
    assert calls == [(os.getpid(), signal.SIGINT)]


def test_the_launcher_passes_its_pid_to_the_server(monkeypatch):
    from ffast.renderers.web import launcher

    seen = {}
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda cmd: seen.setdefault("cmd", cmd))
    launcher._spawn_server(9999)
    cmd = seen["cmd"]
    assert cmd[cmd.index("--exit-with-parent") + 1] == str(os.getpid())

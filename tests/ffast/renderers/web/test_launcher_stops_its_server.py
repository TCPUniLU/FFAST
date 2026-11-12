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


from tests.free_ports import free_port as _free_port  # see tests/free_ports.py


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _holder(port: int) -> str:
    """The command line of whatever listens on ``port``: this test's server,
    or another test's that was given the same port."""
    pids = subprocess.run(["lsof", "-t", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
                          capture_output=True, text=True).stdout.split()
    return "\n".join(subprocess.run(["ps", "-o", "pid=,ppid=,command=", "-p", pid],
                                    capture_output=True, text=True).stdout.strip()
                     for pid in pids) or "(no listener found)"


def _wait(cond, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def _launch(log_path: Path):
    ws, web = _free_port(), _free_port()
    code = ("import sys; from ffast.renderers.web.launcher import main; "
            f"sys.argv = ['ffast-web', '--no-browser', '--ws-port', '{ws}', '--web-port', '{web}']; main()")
    # The launcher's and the server's output go to a file, so a failed start
    # says why (e.g. the server rejecting an argument) instead of only
    # "never started". The server inherits the launcher's stdout/stderr.
    with open(log_path, "wb") as log:
        proc = subprocess.Popen([sys.executable, "-c", code], cwd=str(REPO),
                                stdout=log, stderr=subprocess.STDOUT)
    if not _wait(lambda: _listening(ws) or proc.poll() is not None, 60) or not _listening(ws):
        if proc.poll() is None:
            proc.kill()
        out = log_path.read_text(errors="replace")[-4000:]
        pytest.fail(f"the server never started (launcher exit code {proc.poll()}); output:\n{out}")
    return proc, ws


@pytest.mark.integration
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP, signal.SIGKILL],
                         ids=["kill", "closed-terminal", "kill-9"])
def test_the_server_stops_when_the_launcher_ends(sig, tmp_path):
    proc, ws = _launch(tmp_path / "launcher.log")
    try:
        os.kill(proc.pid, sig)
        proc.wait(timeout=30)
        # kill -9 gives the launcher no chance: the server notices its parent
        # is gone (checked every 2 s) and stops itself.
        assert _wait(lambda: not _listening(ws), 20), (
            f"the server still holds port {ws}; held by:\n{_holder(ws)}")
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

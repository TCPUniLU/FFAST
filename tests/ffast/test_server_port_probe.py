"""A port probe must not read as a server error.

The web launcher waits for ffast-server by opening a TCP connection to the
WebSocket port and closing it without sending anything (``wait_until_ready``).
The websockets library logs that empty connection as ``ERROR opening handshake
failed`` with a full traceback, so every launch log looked like a crash. A
connection that does send bytes and then fails the handshake is a real problem
and must still be logged.

Runs a real ``ffast-server`` subprocess so the check covers the actual
websockets version and the server's own logging setup.
"""
from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pytest
from websockets.sync.client import connect

from ffast.renderers.web.launcher import LOOPBACK, pick_free_port, wait_until_ready

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.integration


def _send_bad_request(port: int) -> None:
    """Send a non-HTTP line, then read until the server hangs up."""
    with socket.create_connection((LOOPBACK, port), timeout=10) as sock:
        sock.sendall(b"not an http request\r\n\r\n")
        while sock.recv(4096):
            pass


def _round_trip(port: int) -> None:
    """One real ping/pong, so the server has logged everything before it.

    The server half-closes a bad request before it logs the failure, so
    stopping it right after ``_send_bad_request`` can lose that line.
    """
    with connect(f"ws://{LOOPBACK}:{port}", open_timeout=10) as ws:
        ws.send("ping")
        assert ws.recv(timeout=10) == "pong"


def test_launcher_port_probe_leaves_no_handshake_error():
    port = pick_free_port()
    proc = subprocess.Popen(
        [
            sys.executable, "server.py",
            "--port", str(port),
            "--host", LOOPBACK,
            "--snapshot-interval", "0",
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        assert wait_until_ready(port, host=LOOPBACK, timeout=60), "server never listened"
        _send_bad_request(port)
        _round_trip(port)
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
    log = out.decode(errors="replace")

    # One failure, from the bad request; the probe added none.
    assert log.count("opening handshake failed") == 1, log[-4000:]

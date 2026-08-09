"""Cluster reconnect / recovery re-verification under multi-client (ADR 0044
Phase 2, issue 25 — the risk slice gated before merge).

Runs a real ``ffast-server`` subprocess (the same seam as
``test_scene_protocol_flow.py``) rather than a SLURM job, but exercises the
exact behaviour that changed: the recovery window now arms on the LAST
connection dropping (not the first/CONTROLLING one), and a returning token
re-admits the client as a controller with shared-state replay rather than
reclaiming a sole role. A regression here is what would orphan or kill a real
cluster job.
"""
from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import websockets

from ffast.protocol.rpc import pack, unpack
from ffast.session.token import SessionToken

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = REPO_ROOT / "examples" / "data" / "dataset.xyz"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATASET_PATH.exists(),
        reason=f"example dataset not found at {DATASET_PATH}",
    ),
]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_for_event(ws, wanted: str, timeout: float = 30.0) -> dict:
    seen: list[str] = []
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=2)
        except asyncio.TimeoutError:
            continue
        if not isinstance(msg, bytes):
            continue
        event, args, kwargs = unpack(msg)
        seen.append(event)
        if event == wanted:
            return {"event": event, "args": args, "kwargs": kwargs}
    raise AssertionError(f"never received {wanted}; saw {seen}")


async def _hello(ws, token: str | None):
    await ws.send("ping")
    assert await asyncio.wait_for(ws.recv(), timeout=5) == "pong"
    await ws.send(pack("HELLO", (), {
        "protocol_version": "1.0",
        "renderer": "headless",
        "supported_codecs": ["raw"],
        "features": [],
        "session_token": token,
    }))
    return await _wait_for_event(ws, "HELLO_ACK", timeout=5)


async def _connect_with_token(port: int, token: str | None):
    ws = None
    last_exc = None
    for _ in range(50):
        try:
            ws = await websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=2)
            break
        except OSError as exc:
            last_exc = exc
            await asyncio.sleep(0.1)
    if ws is None:
        raise RuntimeError(f"server did not accept WebSocket connection: {last_exc}")
    ack = await _hello(ws, token)
    return ws, ack


@pytest.fixture
def token():
    return SessionToken.generate()


@pytest.fixture
def recovery_server(token):
    """A managed-mode server (token-gated) with a real recovery window."""
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable, "server.py",
            "--port", str(port),
            "--snapshot-interval", "0",
            "--recovery-window", "5",
            "--token-hash", token.hash,
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        out, _ = proc.communicate()
        if out:
            tail = out.decode(errors="replace").splitlines()[-60:]
            print("[server log]\n" + "\n".join(tail))


async def test_reconnect_within_window_is_readmitted_as_controller_with_replay(
    recovery_server, token,
):
    """The riskiest integration (PRD Further Notes): a CONTROLLING client
    blips (ungraceful close, no GRACEFUL_DISCONNECT) and reconnects with its
    token inside the recovery window. It must come back as CONTROLLING (not
    some reclaimed/second-class role) and receive a full replay of the shared
    dataset that was already loaded — proof the server (and, on a cluster, the
    job) stayed alive and the state survived the blip."""
    port = recovery_server

    ws1, ack1 = await _connect_with_token(port, token.plaintext)
    assert ack1["kwargs"]["role"] == "CONTROLLING"

    await ws1.send(pack("LOAD_DATASET", (str(DATASET_PATH), "ase (auto)"), {}))
    meta = await _wait_for_event(ws1, "REMOTE_DATASET_META", timeout=30)
    dataset_fp = meta["args"][0]

    # Ungraceful blip: close the socket directly, no GRACEFUL_DISCONNECT — the
    # recovery window should arm (this was the sole/last connection).
    await ws1.close()

    # Reconnect well inside the 5s window, with the same token.
    await asyncio.sleep(1)
    ws2, ack2 = await _connect_with_token(port, token.plaintext)
    try:
        assert ack2["kwargs"]["role"] == "CONTROLLING"   # re-admitted, not READ_ONLY

        replay = await _wait_for_event(ws2, "REMOTE_DATASET_META", timeout=10)
        assert replay["args"][0] == dataset_fp            # shared state survived

        # The server is demonstrably still alive well past where a 5s window
        # with no reconnect would have shut it down.
        await asyncio.sleep(5)
        await ws2.send(pack("REQUEST_STATE_SYNC", (), {}))
        sync_replay = await _wait_for_event(ws2, "REMOTE_DATASET_META", timeout=10)
        assert sync_replay["args"][0] == dataset_fp
    finally:
        await ws2.send(pack("GRACEFUL_DISCONNECT", (), {}))
        await ws2.close()


async def test_second_client_disconnecting_does_not_arm_recovery_while_first_remains(
    recovery_server, token,
):
    """ADR 0044 Phase 2: the window arms on the LAST connection dropping, not
    on any one connection (previously: the CONTROLLING one) dropping. A second
    client blipping while the first stays connected must not start a shutdown
    countdown — the first client's dataset stays reachable throughout."""
    port = recovery_server

    ws1, ack1 = await _connect_with_token(port, token.plaintext)
    try:
        assert ack1["kwargs"]["role"] == "CONTROLLING"
        await ws1.send(pack("LOAD_DATASET", (str(DATASET_PATH), "ase (auto)"), {}))
        meta = await _wait_for_event(ws1, "REMOTE_DATASET_META", timeout=30)
        dataset_fp = meta["args"][0]

        ws2, ack2 = await _connect_with_token(port, token.plaintext)
        assert ack2["kwargs"]["role"] == "CONTROLLING"
        await ws2.close()   # ungraceful — but ws1 is still connected

        # Long enough that an (incorrect) first-client-triggered window would
        # have expired and shut the server down.
        await asyncio.sleep(6)

        await ws1.send(pack("REQUEST_STATE_SYNC", (), {}))
        replay = await _wait_for_event(ws1, "REMOTE_DATASET_META", timeout=10)
        assert replay["args"][0] == dataset_fp
    finally:
        await ws1.send(pack("GRACEFUL_DISCONNECT", (), {}))
        await ws1.close()

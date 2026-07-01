"""
Integration test for Issue #13 — SubDataset array transfer to local Loupe.

Spawns ``ffast-server`` locally (no SLURM/SSH), connects directly via
WebSocket, loads ``examples/data/dataset.xyz``, then requests arrays and
verifies the round-trip.  No cluster required.

Ported from the original root-level ``test_array_transfer.py`` manual harness.
Marked ``integration`` because it launches a real server subprocess; run the
fast suite with ``pytest -m "not integration"``.
"""

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import websockets

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = REPO_ROOT / "examples" / "data" / "dataset.xyz"
SERVER_PORT = 18765  # unusual port so we don't clash with a real server

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATASET_PATH.exists(),
        reason=f"example dataset not found at {DATASET_PATH}",
    ),
]


async def _wait_for_server(url: str, retries: int = 30, delay: float = 0.5):
    for _ in range(retries):
        try:
            ws = await websockets.connect(url)
            await ws.close()
            return
        except Exception:
            await asyncio.sleep(delay)
    raise RuntimeError(f"Server did not start at {url}")


@pytest.fixture
async def ffast_server():
    """Start a local ffast-server subprocess and tear it down afterwards."""
    proc = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(SERVER_PORT)],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        await _wait_for_server(f"ws://localhost:{SERVER_PORT}")
        yield SERVER_PORT
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        out, _ = proc.communicate()
        if out:
            tail = out.decode(errors="replace").splitlines()[-30:]
            print("[server log]\n" + "\n".join(tail))


async def test_subdataset_array_transfer(ffast_server):
    from ffast.protocol.rpc import pack, unpack, unpack_arrays

    url = f"ws://localhost:{ffast_server}"
    ws = await websockets.connect(url)

    # ── 1. ping/pong + HELLO handshake ──────────────────────────────────────
    await ws.send("ping")
    reply = await asyncio.wait_for(ws.recv(), timeout=5)
    assert reply == "pong", f"Expected pong, got {reply!r}"

    await ws.send(pack("HELLO", [], {"protocol_version": "1.0", "renderer": "headless"}))
    ack_msg = await asyncio.wait_for(ws.recv(), timeout=5)
    assert isinstance(ack_msg, bytes), f"Expected binary HELLO_ACK, got {ack_msg!r}"
    ack_event, _, ack_kwargs = unpack(ack_msg)
    assert ack_event == "HELLO_ACK", f"Expected HELLO_ACK, got {ack_event!r}"
    assert ack_kwargs.get("role") == "CONTROLLING", f"Expected controlling role, got {ack_kwargs.get('role')!r}"

    # ── 2. LOAD_DATASET ──────────────────────────────────────────────────────
    await ws.send(pack("LOAD_DATASET", (str(DATASET_PATH), "ase (auto)"), {}))

    # Collect events until REMOTE_DATASET_META arrives (or timeout)
    fingerprint = None
    n_remote = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=2)
        except asyncio.TimeoutError:
            continue
        if not isinstance(msg, bytes):
            continue
        event, args, kwargs = unpack(msg)
        if event == "REMOTE_DATASET_META":
            fingerprint = args[0]
            n_remote = kwargs["n"]
            break
        if event in ("TASK_FAILED",):
            raise RuntimeError(f"Server task failed: {args}")

    assert fingerprint is not None, "Never received REMOTE_DATASET_META"

    # ── 3. REQUEST_SUBDATASET_ARRAYS ─────────────────────────────────────────
    await ws.send(pack("REQUEST_SUBDATASET_ARRAYS", (fingerprint,), {}))

    arrays = None
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
        except asyncio.TimeoutError:
            continue
        if not isinstance(msg, bytes):
            continue
        event, args, kwargs = unpack(msg)
        if event == "SUBDATASET_ARRAYS":
            assert args[0] == fingerprint
            arrays = unpack_arrays(kwargs)
            break

    assert arrays is not None, "Never received SUBDATASET_ARRAYS"

    is_variable = bool(
        arrays.get("variable") is not None
        and int(np.asarray(arrays["variable"]).flat[0])
    )

    if is_variable:
        R_flat = arrays["R_flat"]
        offsets = arrays["offsets"]
        z_flat = arrays["z_flat"]
        assert R_flat.ndim == 2 and R_flat.shape[1] == 3
        assert len(offsets) == n_remote + 1
        assert z_flat.ndim == 1
    else:
        R = arrays["R"]
        z = arrays["z"]
        assert R.ndim == 3
        assert R.shape[0] == n_remote
        assert R.shape[2] == 3
        assert z.ndim == 1

    # ── 4. CachedRemoteDataset round-trip ────────────────────────────────────
    from cluster.remote_dataset import CachedRemoteDataset

    proxy = CachedRemoteDataset(fingerprint, "test", n_remote)
    assert proxy.is_remote_proxy
    proxy.populate(arrays)
    assert not proxy.is_remote_proxy
    assert proxy.getN() == n_remote
    if is_variable:
        counts = proxy.getNAtoms()
        assert hasattr(counts, "__len__") and len(counts) == n_remote
    else:
        assert proxy.getNAtoms() == arrays["R"].shape[1]
        assert np.allclose(proxy.getCoordinates(), arrays["R"])
        assert np.array_equal(proxy.getElements(), arrays["z"])

    await ws.close()

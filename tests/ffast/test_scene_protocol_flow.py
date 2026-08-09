"""
Integration tests for the server-owned renderer protocol.

These tests act as a tiny headless renderer client. They connect to a real
``ffast-server`` process over WebSocket, perform the HELLO handshake, load a
dataset, open a view, and verify that view commands produce scene messages.
"""

import asyncio
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import websockets

from ffast.protocol.rpc import pack, unpack


REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = REPO_ROOT / "examples" / "data" / "dataset.xyz"
PREDICTION_PATH = REPO_ROOT / "examples" / "data" / "prediction.xyz"


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATASET_PATH.exists(),
        reason=f"example dataset not found at {DATASET_PATH}",
    ),
    pytest.mark.skipif(
        not PREDICTION_PATH.exists(),
        reason=f"example prediction not found at {PREDICTION_PATH}",
    ),
]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_for_event(
    ws,
    wanted: str,
    *,
    timeout: float = 30.0,
) -> dict:
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
        if event == "TASK_FAILED":
            raise AssertionError(f"server task failed: args={args} kwargs={kwargs}")
        if event == wanted:
            return {"event": event, "args": args, "kwargs": kwargs}
    raise AssertionError(f"never received {wanted}; saw {seen}")


@pytest.fixture
async def ffast_server_port():
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "server.py",
            "--port",
            str(port),
            "--snapshot-interval",
            "0",
            "--recovery-window",
            "0",
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
            tail = out.decode(errors="replace").splitlines()[-40:]
            print("[server log]\n" + "\n".join(tail))


async def _connect_headless_renderer(port: int):
    ws = None
    last_exc = None
    for _ in range(50):
        try:
            ws = await websockets.connect(
                f"ws://127.0.0.1:{port}",
                open_timeout=2,
            )
            break
        except OSError as exc:
            last_exc = exc
            await asyncio.sleep(0.1)
    if ws is None:
        raise RuntimeError(f"server did not accept WebSocket connection: {last_exc}")

    await ws.send("ping")
    reply = await asyncio.wait_for(ws.recv(), timeout=5)
    assert reply == "pong"

    await ws.send(
        pack(
            "HELLO",
            (),
            {
                "protocol_version": "1.0",
                "renderer": "headless",
                "supported_codecs": ["raw"],
                "features": [],
                "session_token": None,
            },
        )
    )
    hello = await _wait_for_event(ws, "HELLO_ACK", timeout=5)
    assert hello["kwargs"]["role"] == "CONTROLLING"
    return ws


async def test_server_sends_scene_snapshot_and_patches(ffast_server_port):
    ws = await _connect_headless_renderer(ffast_server_port)
    try:
        await ws.send(pack("LOAD_DATASET", (str(DATASET_PATH), "ase (auto)"), {}))
        meta = await _wait_for_event(ws, "REMOTE_DATASET_META", timeout=30)
        dataset_fp = meta["args"][0]
        assert meta["kwargs"]["n"] > 1

        await ws.send(
            pack(
                "OPEN_VIEW",
                (),
                {
                    "view_id": "protocol-test-view",
                    "dataset_ref": dataset_fp,
                },
            )
        )
        snapshot = await _wait_for_event(ws, "SCENE_SNAPSHOT", timeout=10)
        scene = snapshot["kwargs"]["scene"]
        assert scene["view_id"] == "protocol-test-view"
        assert scene["version"] == 0
        assert scene["atoms"] is not None
        assert len(scene["atoms"]["positions"]) > 0

        first_frame_atom_count = len(scene["atoms"]["positions"])

        await ws.send(
            pack(
                "VIEW_COMMAND",
                (),
                {
                    "type": "SET_FRAME",
                    "view_id": "protocol-test-view",
                    "view_version": 0,
                    "frame_index": 1,
                },
            )
        )
        frame_result = await _wait_for_event(ws, "COMMAND_RESULT", timeout=10)
        assert frame_result["kwargs"]["success"] is True

        frame_patch = await _wait_for_event(ws, "SCENE_PATCH", timeout=10)
        patch = frame_patch["kwargs"]
        assert patch["from_version"] == 0
        assert patch["to_version"] == 0
        assert "atoms" in patch["changed"]
        assert patch["atoms"] is not None
        assert len(patch["atoms"]["positions"]) != first_frame_atom_count

        await ws.send(
            pack(
                "VIEW_COMMAND",
                (),
                {
                    "type": "SET_SELECTION",
                    "view_id": "protocol-test-view",
                    "view_version": 0,
                    "name": "picked",
                    "scope": "current_structure",
                    "indices": [0],
                },
            )
        )
        selection_result = await _wait_for_event(ws, "COMMAND_RESULT", timeout=10)
        result = selection_result["kwargs"]
        assert result["success"] is True
        assert result["new_version"] == 1

        selection_patch = await _wait_for_event(ws, "SCENE_PATCH", timeout=10)
        patch = selection_patch["kwargs"]
        assert patch["from_version"] == 0
        assert patch["to_version"] == 1
        assert "selections" in patch["changed"]
        assert patch["selections"][0]["name"] == "picked"
        assert patch["selections"][0]["atom_indices"] == [0]

        await ws.send(
            pack(
                "VIEW_COMMAND",
                (),
                {
                    "type": "SET_SELECTION",
                    "view_id": "protocol-test-view",
                    "view_version": 0,
                    "name": "picked",
                    "scope": "current_structure",
                    "indices": [1],
                },
            )
        )
        stale_result = await _wait_for_event(ws, "COMMAND_RESULT", timeout=10)
        result = stale_result["kwargs"]
        assert result["success"] is False
        assert result["new_version"] == 1
        assert result["error_code"] == "STALE_VERSION"
    finally:
        await ws.close()


async def test_prediction_ref_requires_force_feature_for_scene_forces(ffast_server_port):
    ws = await _connect_headless_renderer(ffast_server_port)
    try:
        await ws.send(pack("LOAD_DATASET", (str(DATASET_PATH), "ase (auto)"), {}))
        dataset_meta = await _wait_for_event(ws, "REMOTE_DATASET_META", timeout=30)
        dataset_fp = dataset_meta["args"][0]

        await ws.send(
            pack(
                "LOAD_PREDICTION",
                (str(PREDICTION_PATH), dataset_fp),
                {
                    "selected_energy_key": "MACE_energy",
                    "selected_force_key": "MACE_forces",
                },
            )
        )
        model_meta = await _wait_for_event(ws, "REMOTE_MODEL_META", timeout=30)
        model_fp = model_meta["args"][0]
        assert dataset_fp in model_meta["kwargs"]["dataset_fingerprints"]

        await ws.send(
            pack(
                "OPEN_VIEW",
                (),
                {
                    "view_id": "prediction-scene-test",
                    "dataset_ref": dataset_fp,
                    "prediction_ref": model_fp,
                },
            )
        )
        snapshot = await _wait_for_event(ws, "SCENE_SNAPSHOT", timeout=10)
        scene = snapshot["kwargs"]["scene"]
        assert scene["view_id"] == "prediction-scene-test"
        assert scene["atoms"] is not None
        assert scene["forces"] is None

        await ws.send(
            pack(
                "VIEW_COMMAND",
                (),
                {
                    "type": "TOGGLE_FEATURE",
                    "view_id": "prediction-scene-test",
                    "view_version": 0,
                    "feature": "forces",
                    "enabled": True,
                },
            )
        )
        toggle_result = await _wait_for_event(ws, "COMMAND_RESULT", timeout=10)
        assert toggle_result["kwargs"]["success"] is True

        toggle_patch = await _wait_for_event(ws, "SCENE_PATCH", timeout=10)
        patch = toggle_patch["kwargs"]
        assert "forces" in patch["changed"]
        assert patch["forces"] is not None
        assert len(patch["forces"]["starts"]) == len(scene["atoms"]["positions"])
        assert len(patch["forces"]["vectors"]) == len(scene["atoms"]["positions"])
        assert any(
            abs(component) > 0
            for vector in patch["forces"]["vectors"]
            for component in vector
        )

        first_frame_force_count = len(patch["forces"]["vectors"])

        await ws.send(
            pack(
                "VIEW_COMMAND",
                (),
                {
                    "type": "SET_FRAME",
                    "view_id": "prediction-scene-test",
                    "view_version": 1,
                    "frame_index": 1,
                },
            )
        )
        frame_result = await _wait_for_event(ws, "COMMAND_RESULT", timeout=10)
        assert frame_result["kwargs"]["success"] is True

        frame_patch = await _wait_for_event(ws, "SCENE_PATCH", timeout=10)
        patch = frame_patch["kwargs"]
        assert "forces" in patch["changed"]
        assert patch["forces"] is not None
        assert len(patch["forces"]["vectors"]) != first_frame_force_count
    finally:
        await ws.close()

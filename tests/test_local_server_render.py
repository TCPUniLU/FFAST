"""
Integration test: local server → load dataset → open view → SCENE_PATCH has atoms.

Run: pytest tests/test_local_server_render.py -v -s
(needs ffast-server in PATH and examples/data/dataset.xyz)
"""
import asyncio
import os
import sys
import time
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DATASET_PATH = os.path.join(ROOT, "examples", "data", "dataset.xyz")
DATASET_TYPE = "ase (auto)"

# Launches a real ffast-server subprocess; run the fast suite with
# ``pytest -m "not integration"``.  Skips (rather than errors) when the
# example dataset is absent, matching tests/ffast/test_array_transfer.py.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.path.exists(DATASET_PATH),
        reason=f"example dataset not found at {DATASET_PATH}",
    ),
]


@pytest.fixture
def local_server():
    import socket
    from ffast.session.token import SessionToken
    from ffast.session.local import LocalServerManager

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    token = SessionToken.generate()
    manager = LocalServerManager()
    handle = manager.start(port, token)
    yield port, token.plaintext
    manager.stop(handle)


@pytest.mark.asyncio
async def test_scene_patch_has_atoms(local_server):
    port, token_plaintext = local_server
    from cluster.connection import connect_direct
    from ffast.protocol.rpc import unpack

    # Connect with retry
    session = None
    for _ in range(20):
        try:
            session = await connect_direct("127.0.0.1", port, token=token_plaintext)
            break
        except OSError:
            await asyncio.sleep(0.5)
    assert session is not None, "Could not connect to local server"

    # received: list of (event, args, kwargs)
    received = []

    async def listener():
        try:
            async for msg in session.websocket:
                if isinstance(msg, bytes):
                    event, args, kwargs = unpack(msg)
                    received.append((event, args, kwargs))
        except Exception:
            pass

    listen_task = asyncio.create_task(listener())

    # Tell server to load the dataset
    await session.push_event("LOAD_DATASET", DATASET_PATH, DATASET_TYPE)

    # Wait for REMOTE_DATASET_META — fingerprint is args[0]
    dataset_fp = None
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        for event, args, kwargs in received:
            if event == "REMOTE_DATASET_META" and args:
                dataset_fp = args[0]
                break
        if dataset_fp:
            break
    assert dataset_fp, (
        f"No REMOTE_DATASET_META in 10s. Got: {[(e, a) for e, a, _ in received]}"
    )
    print(f"\n  dataset_fp = {dataset_fp!r}")

    # Open a view
    view_id = "test-view-001"
    await session.push_event(
        "OPEN_VIEW",
        view_id=view_id,
        dataset_ref=dataset_fp,
        prediction_ref=None,
    )

    # Wait for SCENE_SNAPSHOT
    deadline = time.monotonic() + 10.0
    snapshot_received = False
    while time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        for event, args, kwargs in received:
            if event == "SCENE_SNAPSHOT":
                snapshot_received = True
                break
        if snapshot_received:
            break
    assert snapshot_received, f"No SCENE_SNAPSHOT. Got: {[e for e, _, __ in received]}"

    # Send SET_FRAME for frame 0
    await session.push_event(
        "VIEW_COMMAND",
        type="SET_FRAME",
        view_id=view_id,
        view_version=0,
        frame_index=0,
    )

    # Wait for SCENE_PATCH with atoms
    patch_with_atoms = None
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        for event, args, kwargs in received:
            if event == "SCENE_PATCH":
                changed = kwargs.get("changed", [])
                if "atoms" in changed:
                    patch_with_atoms = kwargs
                    break
        if patch_with_atoms:
            break

    listen_task.cancel()
    await session.disconnect()

    assert patch_with_atoms is not None, (
        f"No SCENE_PATCH with 'atoms'. Got: {[e for e, _, __ in received]}"
    )
    atoms = patch_with_atoms.get("atoms")
    assert atoms is not None, (
        f"SCENE_PATCH 'atoms' in changed but atoms=None. Full patch: {patch_with_atoms}"
    )
    positions = atoms.get("positions", [])
    assert len(positions) > 0, (
        f"SCENE_PATCH atoms has 0 positions. Dataset fp: {dataset_fp!r}"
    )
    print(f"  ✓ {len(positions)} atoms rendered")

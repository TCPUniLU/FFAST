"""A subset made in the desktop window is made on the server too.

Regression: SUB, Freeze and Extract built their subset only inside the desktop
window. The 3D view asks the server to draw the selected dataset by its
fingerprint, so a subset the server never heard of drew a black screen. The
window now repeats each change on the server with the message the browser
sends, and the server builds a subset with the same fingerprint, which the
window then already holds (an announcement for a dataset it holds is ignored).
"""
import asyncio
import threading
import types

from ase import Atoms
import numpy as np
import pytest

from ffast.core.connection_manager import ConnectionManager
from ffast.core.environment import Environment
from ffast.loaders.ase import aseDatasetLoader
from ffast.loaders.dataset import DatasetView
from ffast.protocol import control
from ffast.visualization.scene_builder import _resolve_filter_indices


def _parent():
    frames = [Atoms("H2O", positions=[[0, 0, 0], [0, 0, 1], [1, 0, 0]]) for _ in range(10)]
    dataset = aseDatasetLoader("test.xyz", atomsList=frames)
    dataset.initialise()
    return dataset


def _env(*datasets):
    """The parts of Environment the subset methods use; records what it tells the server."""
    held = {d.fingerprint: d for d in datasets}
    told = []
    return types.SimpleNamespace(
        datasets=types.SimpleNamespace(
            get=held.get, add=lambda d: held.__setitem__(d.fingerprint, d)),
        remote=types.SimpleNamespace(
            tellServer=lambda event, *a, **k: told.append((event, a, k))),
        held=held, told=told,
    )


def _model(fp="m1"):
    return types.SimpleNamespace(fingerprint=fp, getName=lambda: "prediction")


# ── SUB ─────────────────────────────────────────────────────────────────────

def test_a_new_subset_is_declared_on_the_server():
    parent, model = _parent(), _model()
    env = _env(parent)
    Environment.declareSubDataset(env, parent, model, np.array([3, 5]), "Energy")
    assert env.told == [(control.DECLARE_SUBSET, (), {
        "parent_fingerprint": parent.fingerprint, "indices": [3, 5],
        "model_fp": "m1", "name": "Energy"})]


def test_a_zoom_moves_the_servers_subset_too():
    parent = _parent()
    env = _env(parent)
    Environment.declareSubDataset(env, parent, None, np.array([3, 5]), "Energy")
    Environment.declareSubDataset(env, parent, None, np.array([1]), "Energy")
    event, _, kwargs = env.told[-1]
    assert event == control.DECLARE_SUBSET and kwargs["indices"] == [1]
    assert kwargs["model_fp"] is None


def test_a_zoom_onto_no_frames_tells_the_server_nothing():
    # The server keeps the frames it had when told of none.
    parent = _parent()
    env = _env(parent)
    Environment.declareSubDataset(env, parent, None, None, "Energy")
    assert env.told == []


# ── Freeze ──────────────────────────────────────────────────────────────────

def test_freezing_a_subset_freezes_the_servers():
    parent = _parent()
    env = _env(parent)
    Environment.declareSubDataset(env, parent, None, np.array([3, 5]), "Energy")
    live = DatasetView.subsetFingerprint(parent, None, "Energy")
    Environment.freezeSubDataset(env, live)
    assert env.told[-1] == (control.FREEZE_SUBSET, (), {"fingerprint": live})


def test_what_cannot_be_frozen_is_not_sent():
    parent = _parent()
    env = _env(parent)
    Environment.createAtomFilteredDataset(env, parent, [0, 1])
    env.told.clear()
    Environment.freezeSubDataset(env, DatasetView.atomSubsetFingerprint(parent, [0, 1]))
    assert env.told == []


# ── Extract ─────────────────────────────────────────────────────────────────

def test_an_atom_subset_is_created_on_the_server_with_the_same_fingerprint():
    # The server sorts the atoms it is given, so the window must too.
    parent = _parent()
    env = _env(parent)
    Environment.createAtomFilteredDataset(env, parent, [2, 0])
    [(event, _, kwargs)] = env.told
    assert event == control.CREATE_SUBSET
    assert kwargs == {"parent_fingerprint": parent.fingerprint, "indices": [0, 2]}

    onServer = _resolve_filter_indices(kwargs["indices"], parent.getElements(0))
    assert DatasetView.atomSubsetFingerprint(parent, onServer) in env.held


def test_a_refused_atom_subset_is_not_sent():
    frames = [Atoms("H2O"), Atoms("H2")]
    parent = aseDatasetLoader("var.xyz", atomsList=frames)
    parent.initialise()
    env = _env(parent)
    with pytest.raises(ValueError, match="missing"):
        Environment.createAtomFilteredDataset(env, parent, [2])
    assert env.told == []


# ── the transport ───────────────────────────────────────────────────────────

def test_without_a_server_nothing_is_sent():
    # Always so on the server's own Environment.
    assert ConnectionManager(env=None).tellServer(control.FREEZE_SUBSET, fingerprint="x") is None


def test_on_the_loops_own_thread_messages_leave_in_the_order_they_were_made():
    # A zoom tells the server the new frames, then the 3D view asks for its
    # scene. The scene request must not overtake the frames, or the server
    # draws the old ones.
    sent = []

    class _Session:
        async def push_event(self, event, *args, **kwargs):
            sent.append(event)

    async def zoom():
        mgr = ConnectionManager(env=None)
        mgr.serverConnection, mgr._event_loop = _Session(), asyncio.get_running_loop()
        first = mgr.tellServer(control.DECLARE_SUBSET)
        second = asyncio.create_task(mgr.serverConnection.push_event(control.OPEN_VIEW))
        await asyncio.gather(asyncio.wrap_future(first), second)

    asyncio.run(zoom())
    assert sent == [control.DECLARE_SUBSET, control.OPEN_VIEW]


def test_with_a_server_the_message_is_pushed_on_its_loop():
    sent = []

    class _Session:
        async def push_event(self, event, *args, **kwargs):
            sent.append((event, args, kwargs))

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        mgr = ConnectionManager(env=None)
        mgr.serverConnection, mgr._event_loop = _Session(), loop
        mgr.tellServer(control.FREEZE_SUBSET, fingerprint="x").result(timeout=5)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
    assert sent == [(control.FREEZE_SUBSET, (), {"fingerprint": "x"})]

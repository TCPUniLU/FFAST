"""A portable bundle loads through the server, scores and comes back in a saved
session, with its source file gone (ADR 0110, step 5)."""
import pytest

from ffast.io import frame_cache
from ffast.protocol.rpc import pack
from ffast.readers import bundle, chunks, text
from ffast.readers.dataset import ReaderDataset
from ffast.readers.xyz_reader import XyzReader
from tests.ffast.readers.test_bundle_export import write_xyz

from .test_web_runtime import (
    _connect_headless_client,
    _spawn_server,
    _wait_for_event,
    _wait_for_server_ready,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not text.fast_parser_usable(), reason="Optional fast parser unavailable"),
]


async def _send(ws, event, args=(), **kwargs):
    await ws.send(pack(event, tuple(args), kwargs))


async def test_a_bundle_loads_scores_and_is_found_again_by_a_saved_session(tmp_path, monkeypatch):
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "parsed")
    source = write_xyz(tmp_path / "run.xyz", n=400, predictions=True)
    original = ReaderDataset(
        source, chunks.read_arrays(XyzReader, source, memory_mode="saving", workers=1), "xyz")
    fingerprint = original.getFingerprint()
    target = tmp_path / "run.ffast"
    bundle.export(source, target)
    (tmp_path / "run.xyz").unlink()                       # the source is not needed from here on
    file = str(target / bundle.DESCRIPTION)
    session = tmp_path / "session"

    async with _spawn_server() as (ws_port, _web_port):
        await _wait_for_server_ready(ws_port)
        ws = await _connect_headless_client(ws_port)
        try:
            await _send(ws, "LOAD_DATASET", (file, "ase (auto)"))
            meta = await _wait_for_event(ws, "REMOTE_DATASET_META", timeout=30)
            dataset_fp = meta["args"][0]
            assert dataset_fp == fingerprint              # the fingerprint the original load has

            await _send(ws, "LOAD_PREDICTION", (file, dataset_fp),
                        selected_energy_key="energy", selected_force_key="forces")
            model = await _wait_for_event(ws, "REMOTE_MODEL_META", timeout=30)
            model_fp = model["args"][0]

            for metric in ("ffast.energy_difference", "ffast.force_difference"):
                await _send(ws, "REQUEST_METRIC", (), metric_id=metric, request_id=metric,
                            dataset_fp=dataset_fp, model_fp=model_fp)
                reply = await _wait_for_event(ws, "METRIC_RESULT", timeout=60)
                assert reply["kwargs"]["ok"], reply["kwargs"]

            await _send(ws, "SAVE_SESSION", (), path=str(session))
            saved = await _wait_for_event(ws, "SESSION_SAVED", timeout=30)
            assert saved["kwargs"]["ok"], saved["kwargs"]
        finally:
            await _send(ws, "GRACEFUL_DISCONNECT")
            await ws.close()

    # A fresh server that has never seen the bundle finds the dataset by its path.
    async with _spawn_server() as (ws_port, _web_port):
        await _wait_for_server_ready(ws_port)
        ws = await _connect_headless_client(ws_port)
        try:
            await _send(ws, "LOAD_SESSION", (), path=str(session))
            restored = await _wait_for_event(ws, "REMOTE_DATASET_META", timeout=60)
            assert restored["args"][0] == fingerprint
            loaded = await _wait_for_event(ws, "SESSION_LOADED", timeout=60)
            assert loaded["kwargs"]["ok"], loaded["kwargs"]
        finally:
            await _send(ws, "GRACEFUL_DISCONNECT")
            await ws.close()

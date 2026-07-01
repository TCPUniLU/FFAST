"""Runtime smoke tests for the browser WebGL renderer."""

from __future__ import annotations

import asyncio
import io
import re
import socket
import subprocess
import sys
from pathlib import Path

from PIL import Image
import pytest
import websockets
from playwright.async_api import async_playwright, expect

from ffast.protocol.rpc import pack, unpack


REPO_ROOT = Path(__file__).resolve().parents[4]
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
        if event == "TASK_FAILED":
            raise AssertionError(f"server task failed: args={args} kwargs={kwargs}")
        if event == wanted:
            return {"event": event, "args": args, "kwargs": kwargs}
    raise AssertionError(f"never received {wanted}; saw {seen}")


async def _connect_headless_client(port: int):
    ws = None
    last_exc = None
    for _ in range(300):
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
    assert await asyncio.wait_for(ws.recv(), timeout=5) == "pong"
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


@pytest.fixture
async def ffast_web_server():
    ws_port = _free_port()
    web_port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "server.py",
            "--port",
            str(ws_port),
            "--web-port",
            str(web_port),
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
        yield ws_port, web_port
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


async def _preload_dataset(ws_port: int) -> str:
    ws = await _connect_headless_client(ws_port)
    try:
        await ws.send(pack("LOAD_DATASET", (str(DATASET_PATH), "ase (auto)"), {}))
        meta = await _wait_for_event(ws, "REMOTE_DATASET_META", timeout=30)
        return meta["args"][0]
    finally:
        await ws.send(pack("GRACEFUL_DISCONNECT", (), {}))
        await ws.close()


async def _preload_dataset_and_prediction(ws_port: int) -> tuple[str, str]:
    ws = await _connect_headless_client(ws_port)
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
        return dataset_fp, model_fp
    finally:
        await ws.send(pack("GRACEFUL_DISCONNECT", (), {}))
        await ws.close()


async def test_web_renderer_connects_and_draws_scene(ffast_web_server):
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)

    console_errors: list[str] = []
    page_errors: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        page.on(
            "console",
            lambda msg: (
                console_errors.append(msg.text)
                if msg.type in {"error", "warning"}
                else None
            ),
        )
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        try:
            await page.goto(
                f"http://127.0.0.1:{web_port}/?port={ws_port}",
                wait_until="networkidle",
            )
            await page.locator("#connect-btn").click()
            await expect(page.locator("#status")).to_contain_text("Connected")

            dataset_select = page.locator("#dataset-select")
            await expect(dataset_select.locator(f"option[value='{dataset_fp}']")).to_have_count(1)
            await dataset_select.select_option(dataset_fp)

            await page.locator("#open-view-btn").click()
            await expect(page.locator("#overlay")).to_have_class(
                re.compile(r"\bhidden\b")
            )
            await expect(page.locator("#frame-slider")).to_be_enabled()

            png = await page.locator("#canvas").screenshot()
            image = Image.open(io.BytesIO(png)).convert("RGBA")
            bg = (26, 26, 30, 255)
            rgba = image.tobytes()
            non_background_pixels = sum(
                1
                for i in range(0, len(rgba), 4)
                if any(abs(rgba[i + channel] - bg[channel]) > 3 for channel in range(4))
            )
            assert non_background_pixels > 0

            await page.locator("#frame-slider").evaluate(
                """
                (slider) => {
                  slider.value = '1';
                  slider.dispatchEvent(new Event('input', { bubbles: true }));
                }
                """
            )
            await expect(page.locator("#frame-label")).to_contain_text("1 /")
        finally:
            await browser.close()

    assert not page_errors
    assert not [
        msg for msg in console_errors
        if "favicon" not in msg.lower()
        and "gpu stall due to readpixels" not in msg.lower()
    ]


async def test_web_renderer_draws_prediction_force_arrows(ffast_web_server):
    ws_port, web_port = ffast_web_server
    dataset_fp, model_fp = await _preload_dataset_and_prediction(ws_port)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await page.goto(
                f"http://127.0.0.1:{web_port}/?port={ws_port}",
                wait_until="networkidle",
            )
            await page.locator("#connect-btn").click()
            await expect(page.locator("#status")).to_contain_text("Connected")

            await expect(
                page.locator(f"#dataset-select option[value='{dataset_fp}']")
            ).to_have_count(1)
            await page.locator("#dataset-select").select_option(dataset_fp)

            await expect(
                page.locator(f"#model-select option[value='{model_fp}']")
            ).to_have_count(1)
            await page.locator("#model-select").select_option(model_fp)

            await page.locator("#open-view-btn").click()
            await expect(page.locator("#overlay")).to_have_class(
                re.compile(r"\bhidden\b")
            )
            await page.wait_for_timeout(700)

            png = await page.locator("#canvas").screenshot()
            image = Image.open(io.BytesIO(png)).convert("RGBA")
            rgba = image.tobytes()
            orange_force_pixels = sum(
                1
                for i in range(0, len(rgba), 4)
                if rgba[i] > 170
                and 55 <= rgba[i + 1] <= 150
                and rgba[i + 2] < 80
                and rgba[i + 3] > 200
            )
            assert orange_force_pixels > 20
        finally:
            await browser.close()

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

            # The dataset shows as a row in the object rail; selecting it opens
            # the Loupe view (the 3D tab is active by default).
            dataset_row = page.locator(f"#dataset-list .obj-row[data-fp='{dataset_fp}']")
            await expect(dataset_row).to_have_count(1)
            await dataset_row.click()

            await expect(page.locator("#overlay")).to_have_class(
                re.compile(r"\bhidden\b")
            )
            await expect(page.locator("#frame-slider")).to_be_enabled()

            png = await page.locator("#canvas").screenshot()
            image = Image.open(io.BytesIO(png)).convert("RGBA")
            bg = (0, 0, 0, 255)  # viewport clears to black (Qt loupe default)
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


def _count_orange_force_pixels(png_bytes: bytes) -> int:
    image = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    rgba = image.tobytes()
    return sum(
        1
        for i in range(0, len(rgba), 4)
        if rgba[i] > 170
        and 55 <= rgba[i + 1] <= 150
        and rgba[i + 2] < 80
        and rgba[i + 3] > 200
    )


async def test_web_renderer_draws_prediction_force_arrows(ffast_web_server):
    """ADR 0045 issue 07: the Force Vectors pane's 'Show force vectors' toggle
    must actually gate the server-side TOGGLE_FEATURE("forces", ...) — arrows
    appear when checked, disappear when cleared."""
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

            dataset_row = page.locator(f"#dataset-list .obj-row[data-fp='{dataset_fp}']")
            await expect(dataset_row).to_have_count(1)
            await dataset_row.click()

            # The prediction applies to the selected dataset; selecting its row
            # reopens the view with it as the active prediction overlay.
            model_row = page.locator(f"#model-list .obj-row[data-fp='{model_fp}']")
            await expect(model_row).to_have_count(1)
            await model_row.click()

            await expect(page.locator("#overlay")).to_have_class(
                re.compile(r"\bhidden\b")
            )

            show_forces = page.locator(
                ".pane[data-pane='Force Vectors'] .ctl-row[data-label='Show force vectors'] input"
            )
            source = page.locator(
                ".pane[data-pane='Force Vectors'] .ctl-row[data-label='Source'] select"
            )
            length = page.locator(
                ".pane[data-pane='Force Vectors'] .ctl-row[data-label='Length'] input[type=range]"
            )
            # Baseline (off): a few stray orange-ish pixels can occur from
            # antialiased edges between other elements, so compare relatively
            # rather than against a small absolute count.
            baseline_count = _count_orange_force_pixels(await page.locator("#canvas").screenshot())

            await show_forces.check()
            await expect(source.locator("option", has_text="prediction.xyz")).to_have_count(1)
            await source.select_option(label="prediction.xyz")   # the loaded prediction, not ground truth
            # Arrows are normalised to the single largest per-atom force error
            # (scene_builder._build_force_scene); at the default length that
            # one arrow can be foreshortened to a small dot depending on the
            # fitted camera's viewing angle. Max the length so its cone footprint
            # is large regardless of orientation.
            await length.evaluate(
                "(el) => { el.value = el.max; el.dispatchEvent(new Event('input', {bubbles: true})); }"
            )
            await page.wait_for_timeout(700)
            on_count = _count_orange_force_pixels(await page.locator("#canvas").screenshot())
            assert on_count > baseline_count + 20

            await show_forces.uncheck()
            await page.wait_for_timeout(700)
            off_count = _count_orange_force_pixels(await page.locator("#canvas").screenshot())
            assert off_count < on_count
        finally:
            await browser.close()


async def test_web_color_by_selector_recolors_atoms_and_shows_colorbar(ffast_web_server):
    """ADR 0045 issue 03 / Phase 1 gate: selecting a metric in 'Colour By'
    changes atom instance colours (not baked element colours) and shows a
    colourbar; switching back to Elements hides it again."""
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

            dataset_row = page.locator(f"#dataset-list .obj-row[data-fp='{dataset_fp}']")
            await dataset_row.click()
            model_row = page.locator(f"#model-list .obj-row[data-fp='{model_fp}']")
            await model_row.click()
            await expect(page.locator("#overlay")).to_have_class(re.compile(r"\bhidden\b"))
            await page.wait_for_timeout(300)

            colorbar = page.locator("#colorbar")
            await expect(colorbar).to_have_class(re.compile(r"\bhidden\b"))
            before_png = await page.locator("#canvas").screenshot()

            coloring = page.locator(
                ".pane[data-pane='Colour By'] .ctl-row[data-label='Coloring'] select"
            )
            # Exact match: "Acceleration Error (by element)" also exists and
            # would otherwise satisfy a substring match.
            await expect(
                coloring.locator("option", has_text=re.compile(r"^Acceleration Error$"))
            ).to_have_count(1)
            await coloring.select_option(label="Acceleration Error")
            await page.wait_for_timeout(700)

            await expect(colorbar).not_to_have_class(re.compile(r"\bhidden\b"))
            after_png = await page.locator("#canvas").screenshot()
            assert before_png != after_png

            await coloring.select_option(label="Elements")
            await page.wait_for_timeout(300)
            await expect(colorbar).to_have_class(re.compile(r"\bhidden\b"))
        finally:
            await browser.close()


async def test_web_camera_preset_reorients_view(ffast_web_server):
    """ADR 0045 issue 04 / Phase 1 gate: a camera preset button reorients the
    rendered view (observed as a materially different image, not a no-op)."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)

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

            dataset_row = page.locator(f"#dataset-list .obj-row[data-fp='{dataset_fp}']")
            await dataset_row.click()
            await expect(page.locator("#overlay")).to_have_class(re.compile(r"\bhidden\b"))
            await page.wait_for_timeout(300)

            before_png = await page.locator("#canvas").screenshot()

            # frameAtoms()'s initial fit-to-view already sits at az=0/el=0
            # (looking down -Z, i.e. the "XZ" front view) — use "XY" (top view,
            # el=90) so the preset is a genuine reorientation, not a no-op.
            xy_preset = page.locator(".pane[data-pane='Camera'] .ctl-btn-group button", has_text="XY")
            await expect(xy_preset).to_have_count(1)
            await xy_preset.click()
            await page.wait_for_timeout(300)

            after_png = await page.locator("#canvas").screenshot()
            assert before_png != after_png

            # The manual elevation field reflects the preset (az 0°, el 90°).
            elevation = page.locator(
                ".pane[data-pane='Camera'] .ctl-row[data-label='Elevation (°)'] input"
            )
            await expect(elevation).to_have_value(re.compile(r"^90\.0$"))
        finally:
            await browser.close()


async def test_web_playback_advances_frames_and_stops_on_pause(ffast_web_server):
    """ADR 0045 issue 08: play advances the frame index automatically; pause
    stops it — the frame slider must not keep moving once paused."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)

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

            dataset_row = page.locator(f"#dataset-list .obj-row[data-fp='{dataset_fp}']")
            await dataset_row.click()
            await expect(page.locator("#frame-slider")).to_be_enabled()

            fps_input = page.locator("#fps-input")
            await fps_input.fill("20")   # fast enough to see multiple frames advance quickly

            play_pause = page.locator("#play-pause-btn")
            await play_pause.click()
            await page.wait_for_timeout(600)
            playing_frame = int(await page.locator("#frame-slider").input_value())
            assert playing_frame > 0, "frame index should have advanced while playing"

            await play_pause.click()   # pause
            await page.wait_for_timeout(200)
            paused_frame = int(await page.locator("#frame-slider").input_value())
            await page.wait_for_timeout(400)
            still_paused_frame = int(await page.locator("#frame-slider").input_value())
            assert still_paused_frame == paused_frame, "frame index kept advancing after pause"
        finally:
            await browser.close()

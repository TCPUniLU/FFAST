"""Runtime smoke tests for the browser WebGL renderer."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import ase.io
from PIL import Image
import pytest
import websockets
# Collection must not die on a missing optional dependency: without this
# the whole suite aborts, not just these tests (CI, 2026-08-09).
pytest.importorskip("playwright.async_api")

from playwright.async_api import async_playwright, expect

from ffast.protocol.rpc import pack, unpack


REPO_ROOT = Path(__file__).resolve().parents[4]
DATASET_PATH = REPO_ROOT / "examples" / "data" / "dataset.xyz"
# The canvas of the focused 3D panel on screen (ADR 0056: each 3D panel has
# its own, and the 3D controls act on the focused one).
CANVAS = ".tabpanel.active .panel-3d.focused canvas.view3d"
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


@contextlib.asynccontextmanager
async def _spawn_server(*extra_args: str):
    """Launch one ``server.py`` subprocess on free ports; terminate on exit.

    A bare async context manager (not a fixture) so a test that needs *two*
    independent server processes in sequence — e.g. a save/load session
    round trip, where "load" must restore state a *fresh* process never
    itself loaded — can open a second one without fighting pytest's
    one-fixture-instance-per-test model.
    """
    ws_port = _free_port()
    web_port = _free_port()
    # Never the real ~/.ffast/tabs: a test must not read, or write, your tabs.
    tabs_dir = tempfile.mkdtemp(prefix="ffast-tabs-")
    if "--tabs-dir" not in extra_args:
        extra_args = (*extra_args, "--tabs-dir", tabs_dir)
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
            *extra_args,
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
        shutil.rmtree(tabs_dir, ignore_errors=True)


@pytest.fixture
async def ffast_web_server():
    async with _spawn_server() as ports:
        yield ports


async def _wait_for_server_ready(ws_port: int) -> None:
    """Block until the server accepts a WS connection (and, by extension, is
    also serving the static web app — both start together in server.py).

    Every other test in this file gets this for free because `_preload_dataset`
    always runs before the first `page.goto()`; a test with nothing to preload
    (a fresh server for a session-load round trip) needs it explicitly, or
    `page.goto()` can race the subprocess's startup and hit connection-refused.
    """
    ws = await _connect_headless_client(ws_port)
    await ws.send(pack("GRACEFUL_DISCONNECT", (), {}))
    await ws.close()


async def _wait_for_port(port: int) -> None:
    """Block until something accepts TCP connections on `port` — for a server
    whose WebSocket needs a token the headless client does not have."""
    for _ in range(300):
        try:
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.1)
            continue
        writer.close()
        await writer.wait_closed()
        return
    raise RuntimeError(f"nothing listening on port {port}")


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


async def _file_menu(page, label):
    """Run a File menu action by its label."""
    await page.locator("#file-menu-btn").click()
    await page.locator("#file-menu-list").get_by_role("menuitem", name=label, exact=True).click()


FILE_MENU = [
    "Load Dataset…", "Load Prediction…", "Save Session…", "Load Session…",
    "Export Selected Dataset…", "Connect to Server…",
]


async def test_web_file_menu_holds_the_session_actions(ffast_web_server):
    """ADR 0055 "Session actions": one File menu; an action that cannot run
    yet is greyed out with the reason; the top-bar Save/Load buttons and the
    rail's unlabelled ⬇ are gone, the rail's + shortcuts stay."""
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await expect(page.locator("#status")).to_contain_text("Connected")
            for gone in ("#save-session-btn", "#load-session-btn", "#export-dataset-btn"):
                await expect(page.locator(gone)).to_have_count(0)
            await expect(page.locator("#add-dataset-btn")).to_be_enabled()
            await expect(page.locator("#add-prediction-btn")).to_be_enabled()

            await page.locator("#file-menu-btn").click()
            items = page.locator("#file-menu-list [role=menuitem]")
            await expect(items).to_have_text(FILE_MENU)
            # Nothing loaded yet: these wait for data, and say so.
            export = items.filter(has_text="Export Selected Dataset…")
            await expect(export).to_be_disabled()
            await expect(export).to_have_attribute("title", re.compile("Select a dataset"))
            prediction = items.filter(has_text="Load Prediction…")
            await expect(prediction).to_be_disabled()
            await expect(prediction).to_have_attribute("title", re.compile("Load a dataset"))
            await expect(items.filter(has_text="Save Session…")).to_be_enabled()

            await page.keyboard.press("Escape")
            await expect(page.locator("#file-menu-list")).to_be_hidden()
            await page.locator("#file-menu-btn").click()
            # Clicking outside closes the menu.
            await page.locator("#tabbar").click()
            await expect(page.locator("#file-menu-list")).to_be_hidden()

            await _file_menu(page, "Connect to Server…")
            await expect(page.locator("#conn-dialog")).to_be_visible()
            await page.locator("#conn-close").click()

            await _file_menu(page, "Load Dataset…")
            await expect(page.locator("#fb-modal")).to_be_visible()
        finally:
            await browser.close()


async def test_web_file_menu_greys_out_server_actions_while_disconnected(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/", wait_until="networkidle")
            await page.locator("#conn-close").click()
            await page.locator("#file-menu-btn").click()
            items = page.locator("#file-menu-list [role=menuitem]")
            for label in FILE_MENU[:-1]:
                item = items.filter(has_text=label)
                await expect(item).to_be_disabled()
                await expect(item).to_have_attribute("title", re.compile("Connect to a server"))
            await expect(items.filter(has_text="Connect to Server…")).to_be_enabled()
        finally:
            await browser.close()


# ── connection (ADR 0055 "Connection" row) ─────────────────────────────────

async def test_web_connects_by_itself_when_the_url_names_the_port(ffast_web_server):
    """The launcher's link carries the port (and an extra `launch=` id that the
    page ignores); the page connects without a click, and the top bar shows
    only a status label — the connection fields live in the dialog."""
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await page.goto(
                f"http://127.0.0.1:{web_port}/?port={ws_port}&launch=abc123",
                wait_until="networkidle",
            )
            await expect(page.locator("#status")).to_contain_text("Connected")
            await expect(page.locator("#conn-dialog")).to_be_hidden()
            for field in ("#ws-url", "#token-input", "#readonly-toggle",
                          "#connect-btn", "#disconnect-btn"):
                await expect(page.locator(field)).to_be_hidden()
            assert await page.locator("#ws-url").input_value() == f"ws://127.0.0.1:{ws_port}"
        finally:
            await browser.close()


async def test_web_status_label_opens_the_connection_dialog(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await expect(page.locator("#status")).to_contain_text("Connected")

            await page.locator("#status").click()
            dialog = page.locator("#conn-dialog")
            await expect(dialog).to_be_visible()
            for field in ("#ws-url", "#token-input", "#readonly-toggle", "#disconnect-btn"):
                await expect(dialog.locator(field)).to_be_visible()

            await dialog.locator("#disconnect-btn").click()
            await expect(page.locator("#status")).to_contain_text("Disconnected")
            await dialog.locator("#connect-btn").click()
            await expect(page.locator("#status")).to_contain_text("Connected")
            await expect(dialog).to_be_hidden()
        finally:
            await browser.close()


async def test_web_dialog_opens_by_itself_and_remembers_the_last_five_servers(ffast_web_server):
    """With no port in the URL there is no server to connect to, so the dialog
    opens. Servers that connected are remembered (newest first, five at most)
    in browser storage — the address only, never the token."""
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/", wait_until="networkidle")
            await expect(page.locator("#conn-dialog")).to_be_visible()

            older = [f"ws://old-{i}.example:1" for i in range(6)]
            await page.evaluate(
                "(list) => localStorage.setItem('ffast.recentServers', JSON.stringify(list))",
                older,
            )
            await page.locator("#ws-url").fill(f"ws://127.0.0.1:{ws_port}")
            await page.locator("#token-input").fill("secret-token-value")
            await page.locator("#connect-btn").click()
            await expect(page.locator("#status")).to_contain_text("Connected")

            stored = await page.evaluate(
                "() => JSON.stringify({...localStorage}) + JSON.stringify({...sessionStorage})"
            )
            assert "secret-token-value" not in stored

            await page.goto(f"http://127.0.0.1:{web_port}/", wait_until="networkidle")
            await expect(page.locator("#conn-dialog")).to_be_visible()
            recent = page.locator("#recent-servers button")
            await expect(recent).to_have_count(5)
            await expect(recent.first).to_have_text(f"ws://127.0.0.1:{ws_port}")
            assert await page.locator("#ws-url").input_value() == f"ws://127.0.0.1:{ws_port}"

            await recent.first.click()
            await expect(page.locator("#status")).to_contain_text("Connected")
        finally:
            await browser.close()


async def test_web_dialog_asks_for_a_token_when_the_server_needs_one():
    """A token-protected server makes the first automatic connection a
    read-only one; the dialog opens and says a token is needed."""
    token = "let-me-in"
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    async with _spawn_server("--token-hash", token_hash) as (ws_port, web_port):
        await _wait_for_port(web_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1100, "height": 760})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                dialog = page.locator("#conn-dialog")
                await expect(dialog).to_be_visible()
                await expect(dialog).to_contain_text("token")

                await page.locator("#token-input").fill(token)
                await page.locator("#connect-btn").click()
                await expect(page.locator("#status")).to_contain_text("Connected (CONTROLLING)")
                await expect(dialog).to_be_hidden()
            finally:
                await browser.close()


# ── 3D sidebar (ADR 0055 "3D sidebar" row) ─────────────────────────────────

_SECTIONS = ["Colour By", "Camera", "Display", "Bonds", "Force Vectors",
             "Extract Subset", "Export", "Alignment"]


async def _open_section(page, title):
    """Open a sidebar section (one is open at a time, ADR 0055)."""
    pane = page.locator(f"#loupe-sidebar .pane[data-pane='{title}']")
    if "collapsed" in (await pane.get_attribute("class") or ""):
        await pane.locator(".pane-header").click()
    await expect(pane).not_to_have_class(re.compile(r"\bcollapsed\b"))


async def _open_sections(page):
    return await page.evaluate(
        """() => [...document.querySelectorAll('#loupe-sidebar .pane')]
                 .filter((p) => !p.classList.contains('collapsed'))
                 .map((p) => p.dataset.pane)"""
    )


async def test_web_sidebar_opens_one_section_at_a_time_and_remembers_it(ffast_web_server):
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            names = await page.evaluate(
                "() => [...document.querySelectorAll('#loupe-sidebar .pane')].map((p) => p.dataset.pane)"
            )
            assert names == _SECTIONS
            assert await _open_sections(page) == ["Colour By"]   # first visit

            await page.locator(".pane[data-pane='Camera'] .pane-header").click()
            assert await _open_sections(page) == ["Camera"]
            await page.locator(".pane[data-pane='Camera'] .pane-header").click()
            assert await _open_sections(page) == []

            await page.locator(".pane[data-pane='Display'] .pane-header").click()
            await _open_loupe(page, ws_port, web_port, dataset_fp)   # reload
            assert await _open_sections(page) == ["Display"]
        finally:
            await browser.close()


async def test_web_camera_exact_angles_wait_behind_a_link(ffast_web_server):
    """ADR 0055 "Inside sections": azimuth, elevation and distance sit behind
    an "Exact angles" link; the presets and toggles stay in view."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)
    rows = [f".pane[data-pane='Camera'] .ctl-row[data-label='{label}']"
            for label in ("Azimuth (°)", "Elevation (°)", "Distance")]
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            await _open_section(page, "Camera")
            for row in rows:
                await expect(page.locator(row)).to_be_hidden()
            await expect(page.locator(".pane[data-pane='Camera'] .ctl-btn-group")).to_be_visible()

            link = page.locator("#exact-angles-link")
            await expect(link).to_have_text("Exact angles")
            await link.click()
            for row in rows:
                await expect(page.locator(row)).to_be_visible()
            await link.click()
            for row in rows:
                await expect(page.locator(row)).to_be_hidden()
        finally:
            await browser.close()


async def test_web_sidebar_search_opens_matching_sections(ffast_web_server):
    """ADR 0055 "Sidebar search": typing opens every section with a match and
    highlights the matching rows, including rows behind "Exact angles";
    clearing it restores the section that was open."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            await _open_section(page, "Display")
            search = page.locator("#sidebar-search")

            await search.fill("elevation")
            assert await _open_sections(page) == ["Camera"]
            row = page.locator(".pane[data-pane='Camera'] .ctl-row[data-label='Elevation (°)']")
            await expect(row).to_be_visible()
            await expect(row).to_have_class(re.compile(r"\bsearch-hit\b"))

            # Several sections can match at once.
            await search.fill("colo")
            opened = await _open_sections(page)
            assert {"Colour By", "Bonds"} <= set(opened) and "Camera" not in opened, opened

            await search.fill("zzzz")
            assert await _open_sections(page) == []
            await expect(page.locator("#sidebar-search-empty")).to_be_visible()

            await search.fill("")
            assert await _open_sections(page) == ["Display"]
            await expect(page.locator("#loupe-sidebar .search-hit")).to_have_count(0)
            await expect(row).to_be_hidden()   # back behind "Exact angles"
            await expect(page.locator("#sidebar-search-empty")).to_be_hidden()
        finally:
            await browser.close()


async def test_web_sidebar_search_explains_rows_that_wait_for_something(ffast_web_server):
    """A row hidden until something else happens is found too, greyed out,
    with the reason."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            await page.locator("#sidebar-search").fill("prediction")
            row = page.locator(".pane[data-pane='Colour By'] .ctl-row[data-label='Prediction']")
            await expect(row).to_be_visible()
            await expect(row).to_have_class(re.compile(r"\bneeds\b"))
            assert "Load a prediction first" in await row.get_attribute("data-needs")

            await page.locator("#sidebar-search").fill("length")
            row = page.locator(".pane[data-pane='Force Vectors'] .ctl-row[data-label='Length']")
            await expect(row).to_be_visible()
            assert "Show force vectors" in await row.get_attribute("data-needs")

            await page.locator("#sidebar-search").fill("")
            await expect(row).to_be_hidden()
        finally:
            await browser.close()


def _control(section, label, tag="select"):
    return f".pane[data-pane='{section}'] .ctl-row[data-label='{label}'] {tag}"


async def test_web_force_error_style_waits_for_a_prediction(ffast_web_server):
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            styles = page.locator("#quick-styles button")
            await expect(styles).to_have_text(["Force error", "Publication", "Reset"])
            force = page.locator("#quick-styles button", has_text="Force error")
            await expect(force).to_be_disabled()
            await expect(force).to_have_attribute("title", re.compile("Load a prediction first"))
        finally:
            await browser.close()


async def test_web_force_error_style_sets_the_existing_controls(ffast_web_server):
    """ADR 0055 "Quick styles": Force error only sets existing controls —
    colour by the per-atom force error of the selected prediction, with the
    desktop's force_error colour map. Force arrows are left as they are."""
    ws_port, web_port = ffast_web_server
    dataset_fp, model_fp = await _preload_dataset_and_prediction(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            await page.locator(f"#model-list .obj-row[data-fp='{model_fp}']").click()
            name = await page.evaluate(f"() => window.ffastApp._models.get('{model_fp}').name")

            await page.locator("#quick-styles button", has_text="Force error").click()
            assert await _open_sections(page) == ["Colour By"]
            await expect(page.locator(_control("Colour By", "Coloring"))).to_have_value(
                "Force Error (per atom)")
            await expect(page.locator(_control("Colour By", "Prediction"))).to_have_value(name)
            await expect(page.locator(_control("Colour By", "Colormap"))).to_have_value("force_error")
            await expect(page.locator("#colorbar")).not_to_have_class(re.compile(r"\bhidden\b"))
            await expect(page.locator(_control("Force Vectors", "Show force vectors", "input"))).not_to_be_checked()
            assert await page.evaluate("() => window.ffastApp.renderer._forceGroup") is None
        finally:
            await browser.close()


async def test_web_publication_style_and_reset(ffast_web_server):
    """Publication: white background (view and export), orthographic, no axes.
    Reset puts back what the quick styles change."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)
    state = """() => {
      const R = window.ffastApp.renderer, c = new R._bondColor.constructor();
      R._renderer.getClearColor(c);
      return { clear: '#' + c.getHexString(), ortho: R._camera === R._orthoCamera,
               gizmo: R._gizmoEnabled };
    }"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            await _open_section(page, "Camera")
            await page.locator(_control("Camera", "Axes gizmo", "input")).check()

            await page.locator("#quick-styles button", has_text="Publication").click()
            assert await page.evaluate(state) == {"clear": "#ffffff", "ortho": True, "gizmo": False}
            await expect(page.locator(_control("Export", "Background", "input"))).to_have_value("#ffffff")

            await page.locator("#quick-styles button", has_text="Reset").click()
            assert await page.evaluate(state) == {"clear": "#000000", "ortho": False, "gizmo": False}
            await expect(page.locator(_control("Colour By", "Coloring"))).to_have_value("Elements")
            await expect(page.locator(_control("Colour By", "Colormap"))).to_have_value("viridis")
        finally:
            await browser.close()


async def test_web_every_section_has_a_help_button(ffast_web_server):
    """ADR 0055 "Help": a "?" on each section shows what it is for."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            for section in _SECTIONS:
                pane = page.locator(f"#loupe-sidebar .pane[data-pane='{section}']")
                await pane.locator(".pane-header .help-btn").click()
                note = pane.locator(".help-text")
                await expect(note).to_be_visible()
                assert len((await note.text_content()).strip()) > 20, section
                assert await _open_sections(page) == [section]
            # A second click hides it again and leaves the section open.
            await pane.locator(".pane-header .help-btn").click()
            await expect(pane.locator(".help-text")).to_be_hidden()
            assert await _open_sections(page) == [_SECTIONS[-1]]
        finally:
            await browser.close()


async def test_web_input_boxes_are_dark(ffast_web_server):
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            await _open_section(page, "Display")
            brightness = await page.evaluate(
                """() => ['Hide atoms', 'Atom size'].map((label) => {
                  const el = document.querySelector(
                    `.pane[data-pane='Display'] .ctl-row[data-label='${label}'] input`);
                  const [r, g, b] = getComputedStyle(el).backgroundColor.match(/\\d+/g).map(Number);
                  return (r + g + b) / 3;
                })"""
            )
            assert all(v < 80 for v in brightness), brightness
        finally:
            await browser.close()


async def test_web_hint_bar_offers_next_steps_until_dismissed(ffast_web_server):
    """ADR 0055 "Help": a hint bar with numbered next steps; dismissing it is
    remembered in browser storage."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/", wait_until="networkidle")
            await expect(page.locator("#hint-bar")).to_be_hidden()   # nothing open yet

            await _open_loupe(page, ws_port, web_port, dataset_fp)
            hint = page.locator("#hint-bar")
            await expect(hint).to_be_visible()
            await expect(hint).to_contain_text("Load a prediction")
            await expect(hint.locator(".hint-n")).to_have_text(["1", "2", "3"])
            # A step never breaks across lines; the bar wraps between steps.
            lines = await page.evaluate(
                "() => [...document.querySelectorAll('#hint-bar .hint-step')]"
                ".map((el) => el.getClientRects().length)"
            )
            assert lines == [1, 1, 1], lines

            await page.locator("#hint-dismiss").click()
            await expect(hint).to_be_hidden()
            await _open_loupe(page, ws_port, web_port, dataset_fp)   # reload
            await expect(hint).to_be_hidden()
        finally:
            await browser.close()


async def test_web_hint_bar_changes_once_a_prediction_is_loaded(ffast_web_server):
    ws_port, web_port = ffast_web_server
    dataset_fp, _model_fp = await _preload_dataset_and_prediction(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            hint = page.locator("#hint-bar")
            await expect(hint).to_contain_text("Force error")
            await expect(hint).not_to_contain_text("Load a prediction")
        finally:
            await browser.close()


async def test_web_analysis_tab_picker_has_a_help_button(ffast_web_server):
    ws_port, web_port = ffast_web_server
    dataset_fp, _model_fp = await _preload_dataset_and_prediction(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            await page.locator("#tabbar .tab").nth(1).click()
            picker = page.locator(".tabpanel.active [data-control='series-selector']")
            await picker.locator(".help-btn").click()
            await expect(picker.locator(".help-text")).to_contain_text("3D view")
        finally:
            await browser.close()


async def test_web_switching_datasets_restores_display_settings_without_errors(ffast_web_server):
    """Per-dataset Display settings (1e8caca..5429496) survive a switch and
    back. Guards the merge with ADR 0055 step 2: the restore must not touch
    the removed Pick radius control."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)
    ws = await _connect_headless_client(ws_port)
    try:
        await ws.send(pack("LOAD_DATASET", (str(PREDICTION_PATH), "ase (auto)"), {}))
        other_fp = dataset_fp   # the connect replay announces the first one again
        while other_fp == dataset_fp:
            other_fp = (await _wait_for_event(ws, "REMOTE_DATASET_META", timeout=30))["args"][0]
    finally:
        await ws.send(pack("GRACEFUL_DISCONNECT", (), {}))
        await ws.close()
    assert other_fp != dataset_fp

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            await _open_section(page, "Display")
            size = page.locator(_control("Display", "Atom size", "input"))
            await size.fill("2")
            await size.dispatch_event("change")

            await page.locator(f"#dataset-list .obj-row[data-fp='{other_fp}']").click()
            await expect(size).to_have_value(re.compile(r"^1(\.0)?$"))
            await page.locator(f"#dataset-list .obj-row[data-fp='{dataset_fp}']").click()
            await expect(size).to_have_value("2")
        finally:
            await browser.close()
        assert not errors, errors


async def test_web_arming_a_pick_tool_opens_its_section(ffast_web_server):
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            for tool, section in [("bonds", "Bonds"), ("align", "Alignment"),
                                  ("forces", "Force Vectors"), ("extract", "Extract Subset")]:
                await page.locator(f"#pick-toolbar button[data-tool='{tool}']").click()
                assert await _open_sections(page) == [section]
            # Info has no section; arming it leaves the sidebar as it is.
            await page.locator("#pick-toolbar button[data-tool='info']").click()
            assert await _open_sections(page) == ["Extract Subset"]
        finally:
            await browser.close()


async def test_web_sidebar_can_be_hidden_and_stays_hidden(ffast_web_server):
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            width = (await page.locator(CANVAS).bounding_box())["width"]
            await page.locator("#sidebar-toggle").click()
            await expect(page.locator("#loupe-sidebar")).to_be_hidden()
            await page.wait_for_timeout(200)
            assert (await page.locator(CANVAS).bounding_box())["width"] > width

            await _open_loupe(page, ws_port, web_port, dataset_fp)   # reload
            await expect(page.locator("#loupe-sidebar")).to_be_hidden()
            await page.locator("#sidebar-toggle").click()
            await expect(page.locator("#loupe-sidebar")).to_be_visible()
        finally:
            await browser.close()


async def _drag(page, selector, dx):
    box = await page.locator(selector).bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x + dx, y, steps=8)
    await page.mouse.up()


async def _width(page, selector):
    return (await page.locator(selector).bounding_box())["width"]


async def test_web_sidebar_and_object_list_widths_are_adjustable(ffast_web_server):
    """Drag the edge of the Settings sidebar or the Datasets/Predictions list
    to resize it; the width is remembered in browser storage, double-click
    restores the default, and arrow keys work on a focused edge."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1300, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            sidebar, rail = await _width(page, "#loupe-sidebar"), await _width(page, "#objectbar")
            canvas = await _width(page, CANVAS)

            await _drag(page, "#sidebar-resize", -120)   # sidebar is on the right
            await _drag(page, "#rail-resize", 80)
            assert await _width(page, "#loupe-sidebar") == pytest.approx(sidebar + 120, abs=3)
            assert await _width(page, "#objectbar") == pytest.approx(rail + 80, abs=3)
            assert await _width(page, CANVAS) == pytest.approx(canvas - 200, abs=6)

            await _open_loupe(page, ws_port, web_port, dataset_fp)   # reload
            assert await _width(page, "#loupe-sidebar") == pytest.approx(sidebar + 120, abs=3)
            assert await _width(page, "#objectbar") == pytest.approx(rail + 80, abs=3)

            # Limits: the sidebar cannot be dragged away or over the view.
            await _drag(page, "#sidebar-resize", 600)
            assert await _width(page, "#loupe-sidebar") >= 200
            await _drag(page, "#sidebar-resize", -1200)
            assert await _width(page, CANVAS) >= 300

            await page.locator("#sidebar-resize").dblclick()
            assert await _width(page, "#loupe-sidebar") == pytest.approx(sidebar, abs=1)

            await page.locator("#sidebar-resize").focus()
            await page.keyboard.press("ArrowLeft")
            assert await _width(page, "#loupe-sidebar") == pytest.approx(sidebar + 10, abs=1)
        finally:
            await browser.close()


async def test_web_resizing_the_view_redraws_at_once(ffast_web_server):
    """Resizing a WebGL canvas wipes it. The view used to wait for the next
    animation frame to redraw, so every step of a sidebar drag showed one
    empty frame: the view blinked while dragging."""
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/", wait_until="networkidle")
            await _apply_synthetic(page, 1)   # one atom in the middle
            centre = await page.evaluate(
                """() => {
                  const R = window.ffastApp.renderer, c = document.querySelector('.tabpanel.active .panel-3d.focused canvas.view3d');
                  R.setCameraAngles({center: [0, 0, 0], distance: 6});
                  c.style.width = (c.clientWidth - 40) + 'px';   // what a drag does
                  R._resize();
                  // Read the drawing buffer now, before any animation frame.
                  const gl = R._renderer.getContext(), px = new Uint8Array(4);
                  gl.readPixels(gl.drawingBufferWidth >> 1, gl.drawingBufferHeight >> 1,
                                1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
                  return Array.from(px.slice(0, 3));
                }"""
            )
        finally:
            await browser.close()
    assert sum(centre) > 60, f"view empty right after a resize: {centre}"


async def test_web_layout_works_when_browser_storage_is_blocked(ffast_web_server):
    """Private windows and blocked site data make storage throw; the page must
    still lay itself out, just without remembering anything."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        await page.add_init_script(
            "Object.defineProperty(window, 'localStorage',"
            " { get() { throw new DOMException('blocked', 'SecurityError'); } });"
        )
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            assert await _open_sections(page) == ["Colour By"]
            await page.locator(".pane[data-pane='Bonds'] .pane-header").click()
            assert await _open_sections(page) == ["Bonds"]
            await page.locator("#sidebar-toggle").click()
            await expect(page.locator("#loupe-sidebar")).to_be_hidden()
        finally:
            await browser.close()
        assert not errors, errors


# ── 3D empty state (ADR 0055 "3D empty state" row) ──────────────────────────

_LOUPE_CHROME = ("#loupe-sidebar", "#pick-bar", "#loupe-controls")


async def test_web_3d_tab_shows_only_a_load_button_until_a_dataset_is_open(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            # Disconnected: the button says why it cannot load yet.
            await page.goto(f"http://127.0.0.1:{web_port}/", wait_until="networkidle")
            await page.locator("#conn-close").click()
            load = page.locator("#empty-load-btn")
            await expect(load).to_be_visible()
            await expect(load).to_be_disabled()
            await expect(page.locator("#overlay")).to_contain_text("Connect to a server first")
            for chrome in _LOUPE_CHROME:
                await expect(page.locator(chrome)).to_be_hidden()

            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await expect(page.locator("#status")).to_contain_text("Connected")
            await expect(load).to_be_enabled()
            for chrome in _LOUPE_CHROME:
                await expect(page.locator(chrome)).to_be_hidden()
            await load.click()
            await expect(page.locator("#fb-modal")).to_be_visible()
        finally:
            await browser.close()


async def test_web_3d_controls_appear_once_a_dataset_is_open(ffast_web_server):
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            await expect(page.locator("#empty-load-btn")).to_be_hidden()
            for chrome in _LOUPE_CHROME:
                await expect(page.locator(chrome)).to_be_visible()

            # Disconnecting closes the view: back to the empty state.
            await page.locator("#status").click()
            await page.locator("#disconnect-btn").click()
            await expect(page.locator("#empty-load-btn")).to_be_visible()
            for chrome in _LOUPE_CHROME:
                await expect(page.locator(chrome)).to_be_hidden()
        finally:
            await browser.close()


async def test_web_3d_tab_is_an_ordinary_tab_holding_one_3d_panel(ffast_web_server):
    """ADR 0056 rule 6: the fixed 3D tab is the built-in "3D" tab now, built
    from the layout like any other: one 3D panel filling its grid, and no
    dataset picker, since nothing in it plots."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            first = page.locator("#tabbar .tab").first
            await expect(first).to_have_text("3D")
            assert (await first.get_attribute("data-tab")).startswith("tab-")
            panel = page.locator(".tabpanel.active .analysis-grid > .panel-3d")
            await expect(panel).to_have_count(1)
            await expect(panel.locator("canvas.view3d")).to_have_count(1)
            await expect(page.locator(".tabpanel.active [data-control='series-selector']")).to_have_count(0)
            # The panel fills the grid, which fills the tab under the pick bar.
            grid = await page.locator(".tabpanel.active .analysis-grid").bounding_box()
            box = await panel.bounding_box()
            assert box["height"] == pytest.approx(grid["height"], abs=2)
            assert box["width"] == pytest.approx(grid["width"], abs=2)
        finally:
            await browser.close()


_MIXED_TAB_TOML = """
[[visualization.tabs]]
name = "Compare"

[[visualization.tabs.panels]]
kind = "3d"
row = 0
col = 0
rowspan = 2

[[visualization.tabs.panels]]
kind = "timeline"
row = 0
col = 1
title = "Forces MAE timeline"
  [visualization.tabs.panels.metrics.y]
  metric = "ffast.force_mae_per_structure_smoothed"

[[visualization.tabs.panels]]
kind = "table"
row = 1
col = 1
title = "Forces MAE"
  [visualization.tabs.panels.metrics.value]
  metric = "ffast.force_component_mae"
"""


async def test_web_a_tab_mixes_plots_with_a_3d_panel_showing_the_main_view(tmp_path):
    """ADR 0056 rules 1 and 8: a project tab puts a linked 3D panel beside
    plots. The panel shows the main view, the same frame as the "3D" tab, and
    the tab gets the 3D controls; a tab without a 3D panel shows none."""
    config = tmp_path / "ffast.toml"
    config.write_text(_MIXED_TAB_TOML)
    async with _spawn_server("--config", str(config)) as (ws_port, web_port):
        dataset_fp, _ = await _preload_dataset_and_prediction(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1300, "height": 820})
            try:
                await _open_loupe(page, ws_port, web_port, dataset_fp)
                await page.locator("#next-frame-btn").click()
                await expect(page.locator("#frame-label")).to_have_text("1 / 99")

                await _open_analysis_tab(page, "Compare")
                active = page.locator(".tabpanel.active")
                await expect(active.locator(".panel-3d canvas.view3d")).to_have_count(1)
                await page.wait_for_function(
                    _PANEL_HAS_POINTS, arg="Forces MAE timeline", timeout=25000)
                await expect(active.locator(".analysis-panel[data-title='Forces MAE'] td")).not_to_have_count(0)
                for chrome in _LOUPE_CHROME:
                    await expect(active.locator(chrome)).to_be_visible()
                await expect(page.locator("#frame-label")).to_have_text("1 / 99")
                assert await page.evaluate("() => window.ffastApp.renderer.atomCount") > 0

                # The main view's frame is the same wherever it is shown.
                await page.locator("#next-frame-btn").click()
                await expect(page.locator("#frame-label")).to_have_text("2 / 99")
                await _open_analysis_tab(page, "Basic Errors")
                for chrome in _LOUPE_CHROME:
                    await expect(page.locator(chrome)).to_be_hidden()
                # 3D panels in tabs you are not looking at stop drawing.
                assert await page.evaluate(
                    "() => window.ffastApp._mainView.renderers.map((r) => r.drawing)") == [False, False]
                await page.locator("#tabbar .tab", has_text="3D").first.click()
                await expect(page.locator("#frame-label")).to_have_text("2 / 99")
                assert await page.evaluate(
                    "() => document.querySelectorAll('canvas.view3d').length") == 2
            finally:
                await browser.close()


_TWO_VIEWS_TOML = """
[[visualization.tabs]]
name = "Two views"

[[visualization.tabs.panels]]
kind = "3d"
row = 0
col = 0

[[visualization.tabs.panels]]
kind = "3d"
row = 0
col = 1
"""


async def test_web_clicking_a_3d_panel_focuses_it_for_the_3d_controls(tmp_path):
    """ADR 0056 rule 8: one set of 3D controls per tab, acting on the focused
    panel. Clicking a panel focuses it (outlined when there are several), the
    sidebar title names what it shows, a pick lands in it, and the browser
    remembers the focus."""
    config = tmp_path / "ffast.toml"
    config.write_text(_TWO_VIEWS_TOML)
    async with _spawn_server("--config", str(config)) as (ws_port, web_port):
        dataset_fp, _ = await _preload_dataset_and_prediction(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1300, "height": 820})
            try:
                await _open_loupe(page, ws_port, web_port, dataset_fp)
                await expect(page.locator("#sidebar-title")).to_have_text(
                    "Settings — dataset · prediction.xyz (main view)")
                await _open_analysis_tab(page, "Two views")
                panels = page.locator(".tabpanel.active .analysis-grid.multi3d > .panel-3d")
                await expect(panels).to_have_count(2)
                await expect(panels.nth(0)).to_have_class(re.compile(r"\bfocused\b"))

                await panels.nth(1).locator("canvas").click(position={"x": 20, "y": 20})
                await expect(panels.nth(1)).to_have_class(re.compile(r"\bfocused\b"))
                await expect(panels.nth(0)).not_to_have_class(re.compile(r"\bfocused\b"))
                await expect(panels.nth(1).locator("#overlay")).to_have_count(1)

                await page.locator("#pick-toolbar [data-tool='info']").click()
                atom = await _front_atom(page)
                await page.mouse.click(atom["x"], atom["y"])
                await expect(page.locator("#pick-strip-count")).to_have_text("1 picked")

                await page.reload(wait_until="networkidle")
                await expect(page.locator("#status")).to_contain_text("Connected")
                await _open_analysis_tab(page, "Two views")
                await expect(panels.nth(1)).to_have_class(re.compile(r"\bfocused\b"))
            finally:
                await browser.close()


_PLOT_CLICK_TOML = """
[[visualization.tabs]]
name = "Compare"

[[visualization.tabs.panels]]
kind = "3d"
row = 0
col = 0

[[visualization.tabs.panels]]
kind = "timeline"
row = 0
col = 1
title = "Energy"
  [visualization.tabs.panels.metrics.y]
  metric = "ffast.energy_reference"
"""

# Click point `frame` of the curve named `name` in the plot titled `title` on
# screen, the way Plotly reports a click.
_CLICK_POINT = """([title, name, frame]) => {
  const el = document.querySelector(
    `.tabpanel.active .analysis-panel[data-title="${title}"] .panel-plot`);
  const curve = el.data.findIndex((trace) => trace.name === name);
  el.emit('plotly_click', {points: [{curveNumber: curve, pointIndex: frame}]});
  return curve;
}"""


async def _load_more(ws_port, known, *requests):
    """Send each (event, args, kwargs) on one connection and return the
    REMOTE_DATASET_META that announces its new dataset. `known` holds the
    fingerprints already loaded, whose replays are skipped."""
    ws = await _connect_headless_client(ws_port)
    try:
        announced = []
        for event, args, kwargs in requests:
            await ws.send(pack(event, args, kwargs))
            while True:
                meta = await _wait_for_event(ws, "REMOTE_DATASET_META", timeout=30)
                if meta["args"][0] not in known:
                    known.add(meta["args"][0])
                    announced.append(meta)
                    break
        return announced
    finally:
        await ws.send(pack("GRACEFUL_DISCONNECT", (), {}))
        await ws.close()


async def test_web_a_plot_click_shows_the_clicked_structure(tmp_path):
    """ADR 0056 rules 4 and 5. A click on a plot point moves the main view to
    the same configuration (a subset's frame i is its parent's frame
    indices[i]); when the main view does not hold it, the main view switches
    to the clicked data. From a tab without a 3D panel, the tab with a linked
    3D panel used last takes over."""
    config = tmp_path / "ffast.toml"
    config.write_text(_PLOT_CLICK_TOML)
    second = tmp_path / "second.xyz"
    ase.io.write(second, ase.io.read(DATASET_PATH, index=":30"), format="extxyz")
    async with _spawn_server("--config", str(config)) as (ws_port, web_port):
        dataset_fp = await _preload_dataset(ws_port)
        (other,) = await _load_more(
            ws_port, {dataset_fp}, ("LOAD_DATASET", (str(second), "ase (auto)"), {}))
        other_fp, other_name = other["args"][0], other["kwargs"]["name"]
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1300, "height": 820})
            selected = page.locator("#dataset-list .obj-row.selected")
            label = page.locator("#frame-label")
            try:
                await _open_loupe(page, ws_port, web_port, dataset_fp)

                # Rule 5: Compare was the last tab showing the main view.
                await _open_analysis_tab(page, "Compare")
                await _open_analysis_tab(page, "Gyration")
                await page.wait_for_function(
                    _PANEL_HAS_POINTS, arg="Total gyration radius", timeout=25000)
                await page.evaluate(_CLICK_POINT, ["Total gyration radius", "dataset", 7])
                await expect(page.locator("#tabbar .tab.active")).to_have_text("Compare")
                await expect(label).to_have_text("7 / 99")

                # Rules 3 and 4: the subset's frame 1 is the parent's frame 20.
                # (Declared from this page: a new connection is not told
                # about subsets made before it.)
                await page.evaluate(
                    """(fp) => window.ffastApp._sendDeclareSubset({parentFp: fp, modelFp: null,
                         indices: [10, 20, 30], name: 'picked'})""", dataset_fp)
                await expect(page.locator("#dataset-list .obj-row")).to_have_count(3)
                sub_fp, sub_meta = await page.evaluate(
                    """() => [...window.ffastApp._datasets].find(([fp, m]) => m.parent)""")
                assert sub_meta["parent"] == dataset_fp
                assert sub_meta["parent_frames"] == [10, 20, 30]
                picker = page.locator(".tabpanel.active [data-series='datasets']")
                await picker.locator(f"button[data-fp='{sub_fp}']").click()
                await page.wait_for_function(
                    """(name) => [...document.querySelectorAll(
                         '.tabpanel.active .analysis-panel[data-title="Energy"] .panel-plot')]
                       .some((el) => (el.data || []).some((t) => t.name === name))""",
                    arg=sub_meta["name"], timeout=25000)
                await page.evaluate(_CLICK_POINT, ["Energy", sub_meta["name"], 1])
                await expect(label).to_have_text("20 / 99")
                await expect(selected).to_have_attribute("data-fp", dataset_fp)

                # Unrelated data: the main view switches to it.
                await picker.locator(f"button[data-fp='{other_fp}']").click()
                await page.wait_for_function(
                    """(name) => [...document.querySelectorAll(
                         '.tabpanel.active .analysis-panel[data-title="Energy"] .panel-plot')]
                       .some((el) => (el.data || []).some((t) => t.name === name))""",
                    arg=other_name, timeout=25000)
                await page.evaluate(_CLICK_POINT, ["Energy", other_name, 5])
                await expect(selected).to_have_attribute("data-fp", other_fp)
                await expect(label).to_have_text("5 / 29")
                await expect(page.locator("#tabbar .tab.active")).to_have_text("Compare")
            finally:
                await browser.close()


async def test_web_a_subset_made_with_sub_shows_in_the_main_view(tmp_path):
    """Ticking SUB shows the subset in the main view, which keeps up as the
    zoom moves it, staying on the same structure where it can. The tab ticked
    keeps drawing the full dataset (its choice is pinned). Unticking takes
    the main view back to the full dataset, at the same structure."""
    config = tmp_path / "ffast.toml"
    config.write_text(_PLOT_CLICK_TOML)
    async with _spawn_server("--config", str(config)) as (ws_port, web_port):
        dataset_fp = await _preload_dataset(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1300, "height": 820})
            selected = page.locator("#dataset-list .obj-row.selected")
            label = page.locator("#frame-label")
            sub = f"{_sub_panel('Energy')} .sub-toggle input"
            try:
                await _open_loupe(page, ws_port, web_port, dataset_fp)
                await _open_analysis_tab(page, "Compare")
                await page.wait_for_function(_PANEL_HAS_POINTS, arg="Energy", timeout=25000)

                await page.locator(sub).check()
                await expect(selected).not_to_have_attribute("data-fp", dataset_fp, timeout=15000)
                [made] = await page.evaluate(_SUBSETS)
                await expect(selected).to_have_attribute("data-fp", made["fp"])
                parent_button = page.locator(
                    f".tabpanel.active [data-series='datasets'] button[data-fp='{dataset_fp}']")
                await expect(parent_button).to_have_class(re.compile(r"\bactive\b"))
                await expect(parent_button).not_to_have_class(re.compile(r"\bfollowing\b"))

                await _zoom(page, "Energy", [10, 19.5])
                await expect(label).to_have_text("0 / 9", timeout=15000)
                await page.evaluate("window.ffastApp._setFrame(5)")      # parent frame 15
                await expect(label).to_have_text("5 / 9")
                await _zoom(page, "Energy", [12, 30])
                await expect(label).to_have_text("3 / 18", timeout=15000)   # still frame 15

                await page.locator(sub).uncheck()
                await expect(selected).to_have_attribute("data-fp", dataset_fp, timeout=15000)
                await expect(label).to_have_text("15 / 99", timeout=15000)
            finally:
                await browser.close()


_DRAWN = """(title) => {
  const card = document.querySelector(
    `.tabpanel.active .analysis-panel[data-title="${title}"]`);
  const plot = card.querySelector('.panel-plot');
  return {datasets: card.dataset.datasets, points: plot?.data?.[0]?.y?.length ?? null,
          zoom: plot?._fullLayout?.xaxis?.range?.[0] ?? null};
}"""


async def test_web_sub_replots_the_other_plots_of_its_tab(ffast_web_server):
    """With SUB ticked on one plot, the tab's other plots and tables draw only
    the subset, and redraw as the zoom moves it; the plot ticked keeps the
    full data and its zoom. Ticking SUB on another plot moves it there.
    Unticking puts the full data back."""
    ws_port, web_port = ffast_web_server
    dataset_fp, model_fp = await _preload_dataset_and_prediction(ws_port)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await page.locator(f"#dataset-list .obj-row[data-fp='{dataset_fp}']").click()
            await page.locator(f"#model-list .obj-row[data-fp='{model_fp}']").click()
            await _open_analysis_tab(page, "Basic Errors")
            for title in ("Energy MAE timeline", "Forces MAE timeline"):
                await page.wait_for_function(_PANEL_HAS_POINTS, arg=title, timeout=25000)

            sub = f"{_sub_panel('Energy MAE timeline')} .sub-toggle input"
            await page.locator(sub).check()
            await _zoom(page, "Energy MAE timeline", [10, 19.5])
            await page.wait_for_function(
                f"() => ({_DRAWN})('Forces MAE timeline').points === 10", timeout=15000)
            [made] = await page.evaluate(_SUBSETS)
            assert (await page.evaluate(_DRAWN, "Forces MAE timeline"))["datasets"] == made["fp"]
            ticked = await page.evaluate(_DRAWN, "Energy MAE timeline")
            assert ticked == {"datasets": dataset_fp, "points": 100, "zoom": 10}

            await _zoom(page, "Energy MAE timeline", [12, 30])
            await page.wait_for_function(
                f"() => ({_DRAWN})('Forces MAE timeline').points === 19", timeout=15000)
            assert (await page.evaluate(_DRAWN, "Energy MAE timeline"))["zoom"] == 12

            # One SUB per tab: ticking another plot moves it there.
            other = f"{_sub_panel('Forces MAE timeline')} .sub-toggle input"
            await page.locator(other).check()
            await expect(page.locator(sub)).not_to_be_checked()
            await page.wait_for_function(
                f"() => ({_DRAWN})('Energy MAE timeline').datasets !== '{dataset_fp}'",
                timeout=15000)

            await page.locator(other).uncheck()
            for title in ("Energy MAE timeline", "Forces MAE timeline"):
                await page.wait_for_function(
                    f"() => ({_DRAWN})('{title}').points === 100", timeout=15000)
            assert (await page.evaluate(_DRAWN, "Energy MAE timeline"))["datasets"] == dataset_fp
        finally:
            await browser.close()


async def test_web_a_subset_opens_looking_like_its_parent(ffast_web_server):
    """A frame subset shown for the first time takes its parent's look, so
    a subset made with SUB is coloured the way its parent was."""
    ws_port, web_port = ffast_web_server
    dataset_fp, model_fp = await _preload_dataset_and_prediction(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            await page.locator(f"#model-list .obj-row[data-fp='{model_fp}']").click()
            await page.locator("#quick-styles button", has_text="Force error").click()
            coloring = page.locator(_control("Colour By", "Coloring"))
            await expect(coloring).to_have_value("Force Error (per atom)")

            await _open_analysis_tab(page, "Basic Errors")
            await page.wait_for_function(
                _PANEL_HAS_POINTS, arg="Energy MAE timeline", timeout=25000)
            await page.locator(f"{_sub_panel('Energy MAE timeline')} .sub-toggle input").check()
            await _zoom(page, "Energy MAE timeline", [10, 19.5])
            await page.wait_for_function(
                f"() => ({_SUBSETS})().some(s => s.n === 10)", timeout=15000)

            await _open_analysis_tab(page, "3D")
            await expect(page.locator("#frame-label")).to_have_text("0 / 9", timeout=15000)
            await expect(coloring).to_have_value("Force Error (per atom)")
            await expect(page.locator("#colorbar")).not_to_have_class(re.compile(r"\bhidden\b"))
        finally:
            await browser.close()


_COMPARE_TOML = """
[[visualization.tabs]]
name = "Compare"
row_heights = [1]

[[visualization.tabs.panels]]
kind = "3d"
row = 0
col = 0

[[visualization.tabs.panels]]
kind = "3d"
row = 0
col = 1
view = "independent"
  [visualization.tabs.panels.start]
  colour_by = "ffast.force_mae"
  colormap = "force_error"
"""

_INDEPENDENT = "window.ffastApp._independent.get('Compare#0,1')"
_PANEL = "#panel-section"


def _panel_control(label, tag="select"):
    return f'{_PANEL} .ctl-row[data-label="{label}"] {tag}'


async def _focus_3d(page, index):
    box = await page.locator(".tabpanel.active .panel-3d").nth(index).bounding_box()
    await page.mouse.click(box["x"] + 20, box["y"] + box["height"] - 20)


async def _open_compare(page, ws_port, web_port, dataset_fp):
    await _open_loupe(page, ws_port, web_port, dataset_fp)
    await _open_analysis_tab(page, "Compare")
    await page.wait_for_function(f"() => {_INDEPENDENT}?.isOpen && {_INDEPENDENT}.started", timeout=15000)


async def test_web_an_independent_panel_has_its_own_data_and_look(tmp_path):
    """ADR 0056 rules 2, 7, 8 and 13: an independent panel opens on its tab's
    data with the starting look from the tab file, names what it shows, and
    the sidebar acts on it once focused; the main view keeps its own look."""
    config = tmp_path / "ffast.toml"
    config.write_text(_COMPARE_TOML)
    async with _spawn_server("--config", str(config)) as (ws_port, web_port):
        dataset_fp, model_fp = await _preload_dataset_and_prediction(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1500, "height": 850})
            coloring = page.locator(_control("Colour By", "Coloring"))
            try:
                await _open_compare(page, ws_port, web_port, dataset_fp)
                caption = page.locator(".tabpanel.active .panel-3d .panel-caption:not([hidden])")
                await expect(caption).to_have_count(1)
                await expect(caption).to_contain_text("prediction")

                await _focus_3d(page, 1)
                await expect(page.locator("#sidebar-title")).to_contain_text("(independent)")
                await expect(page.locator(_PANEL)).to_be_visible()
                await expect(page.locator(_panel_control("Dataset"))).to_have_value(dataset_fp)
                await expect(page.locator(_panel_control("Prediction"))).to_have_value(model_fp)
                await expect(coloring).to_have_value("Force Error (per atom)")

                await _focus_3d(page, 0)
                await expect(page.locator("#sidebar-title")).to_contain_text("(main view)")
                await expect(page.locator(_PANEL)).to_be_hidden()
                await expect(coloring).to_have_value("Elements")
            finally:
                await browser.close()


async def test_web_a_missing_start_colour_metric_shows_elements_and_says_why(tmp_path):
    """Rule 13: a starting colour metric the server lacks gives element
    colours, and the panel says why."""
    config = tmp_path / "ffast.toml"
    config.write_text(_COMPARE_TOML.replace("ffast.force_mae", "nope.metric"))
    async with _spawn_server("--config", str(config)) as (ws_port, web_port):
        dataset_fp = await _preload_dataset(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1500, "height": 850})
            why = "Colour metric nope.metric is not on this server; showing element colours"
            try:
                await _open_compare(page, ws_port, web_port, dataset_fp)
                await expect(page.locator(".tabpanel.active .panel-3d .panel-caption-note")).to_have_text(why)
                await _focus_3d(page, 1)
                await expect(page.locator(f"{_PANEL} .ctl-hint")).to_have_text(why)
                await expect(page.locator(_control("Colour By", "Coloring"))).to_have_value("Elements")
            finally:
                await browser.close()


async def test_web_an_independent_panel_follows_the_main_frame_or_says_it_cannot(tmp_path):
    """Rules 2 and 3: a frame-linked panel takes the main view's frame; on
    unrelated data, the same number, and when that frame does not exist it
    greys out and says so. Unlinked, it keeps its frame and the playback
    strip moves it alone."""
    config = tmp_path / "ffast.toml"
    config.write_text(_COMPARE_TOML)
    second = tmp_path / "second.xyz"
    ase.io.write(second, ase.io.read(DATASET_PATH, index=":30"), format="extxyz")
    async with _spawn_server("--config", str(config)) as (ws_port, web_port):
        dataset_fp = await _preload_dataset(ws_port)
        (other,) = await _load_more(
            ws_port, {dataset_fp}, ("LOAD_DATASET", (str(second), "ase (auto)"), {}))
        other_fp, other_name = other["args"][0], other["kwargs"]["name"]
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1500, "height": 850})
            note = page.locator(".tabpanel.active .panel-3d .panel-note:not([hidden])")
            frame_of = f"() => {_INDEPENDENT}.frame"
            try:
                await _open_compare(page, ws_port, web_port, dataset_fp)
                await _focus_3d(page, 1)
                await page.locator(_panel_control("Dataset")).select_option(other_fp)
                await _focus_3d(page, 0)
                await page.evaluate("window.ffastApp._setFrame(50)")
                await expect(note).to_have_text(f"No frame 50 ({other_name} has 30)")
                await expect(page.locator(".tabpanel.active .viewport.no-frame")).to_have_count(1)

                await page.evaluate("window.ffastApp._setFrame(10)")
                await expect(note).to_have_count(0)
                assert await page.evaluate(frame_of) == 10

                await _focus_3d(page, 1)
                await page.locator(_panel_control("Follow main view's frame", "input")).uncheck()
                await page.locator("#next-frame-btn").click()
                await page.wait_for_function(f"() => {_INDEPENDENT}.frame === 11")
                assert await page.evaluate("window.ffastApp._mainFrame") == 10
                await _focus_3d(page, 0)
                await page.evaluate("window.ffastApp._setFrame(20)")
                await page.wait_for_timeout(300)
                assert await page.evaluate(frame_of) == 11
            finally:
                await browser.close()


async def test_web_an_independent_panel_follows_the_main_camera_while_linked(tmp_path):
    config = tmp_path / "ffast.toml"
    config.write_text(_COMPARE_TOML)
    camera_of = f"() => {_INDEPENDENT}.panel.renderer._exportCamera().azimuth"
    async with _spawn_server("--config", str(config)) as (ws_port, web_port):
        dataset_fp = await _preload_dataset(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1500, "height": 850})
            try:
                await _open_compare(page, ws_port, web_port, dataset_fp)
                await _focus_3d(page, 0)
                await page.evaluate("window.ffastApp.renderer.setCameraAngles({azimuth: 40, elevation: 20})")
                await page.wait_for_function(f"() => Math.abs(({camera_of})() - 40) < 0.5")

                await _focus_3d(page, 1)
                await page.locator(_panel_control("Follow main view's camera", "input")).uncheck()
                await _focus_3d(page, 0)
                await page.evaluate("window.ffastApp.renderer.setCameraAngles({azimuth: -70, elevation: 20})")
                await page.wait_for_timeout(300)
                assert abs(await page.evaluate(camera_of) - 40) < 0.5
            finally:
                await browser.close()


_UNLINKED_CLICK_TOML = """
[[visualization.tabs]]
name = "Compare"

[[visualization.tabs.panels]]
kind = "3d"
row = 0
col = 0
view = "independent"
link_frame = false

[[visualization.tabs.panels]]
kind = "timeline"
row = 0
col = 1
title = "Energy"
  [visualization.tabs.panels.metrics.y]
  metric = "ffast.energy_reference"
"""


async def test_web_a_plot_click_moves_the_independent_panel_showing_the_data(tmp_path):
    """Rule 4: the 3D panel in the tab showing the clicked data moves; an
    independent one with its own frame moves alone."""
    config = tmp_path / "ffast.toml"
    config.write_text(_UNLINKED_CLICK_TOML)
    async with _spawn_server("--config", str(config)) as (ws_port, web_port):
        dataset_fp = await _preload_dataset(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1500, "height": 850})
            view = "window.ffastApp._independent.get('Compare#0,0')"
            try:
                await _open_loupe(page, ws_port, web_port, dataset_fp)
                await _open_analysis_tab(page, "Compare")
                await page.wait_for_function(f"() => {view}?.isOpen")
                await page.wait_for_function(_PANEL_HAS_POINTS, arg="Energy", timeout=25000)
                name = await page.evaluate(f"() => window.ffastApp._datasets.get('{dataset_fp}').name")
                await page.evaluate(_CLICK_POINT, ["Energy", name, 7])
                await page.wait_for_function(f"() => {view}.frame === 7")
                await expect(page.locator("#tabbar .tab.active")).to_have_text("Compare")
                assert await page.evaluate("window.ffastApp._mainFrame") == 0
            finally:
                await browser.close()


async def test_web_edit_mode_makes_a_3d_panel_independent_with_the_look_on_screen(tmp_path):
    """Rules 1, 13 and 15: in Edit mode a 3D panel's ⚙ makes it independent,
    sets its links, and "Use current 3D settings as start" takes the look on
    screen; Save writes them to the tab file."""
    config = tmp_path / "ffast.toml"
    config.write_text(_COMPARE_TOML.replace('view = "independent"\n  [visualization.tabs.panels.start]\n'
                                            '  colour_by = "ffast.force_mae"\n  colormap = "force_error"\n', ""))
    tabs_dir = tmp_path / "tabs"
    async with _spawn_server("--config", str(config), "--tabs-dir", str(tabs_dir)) as (ws_port, web_port):
        dataset_fp, model_fp = await _preload_dataset_and_prediction(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1500, "height": 850})
            try:
                await _open_loupe(page, ws_port, web_port, dataset_fp)
                await _open_analysis_tab(page, "Compare")
                await page.locator(f"#model-list .obj-row[data-fp='{model_fp}']").click()
                await page.locator("#quick-styles button", has_text="Force error").click()
                await expect(page.locator(_control("Colour By", "Coloring"))).to_have_value(
                    "Force Error (per atom)")

                await page.locator("#tab-edit-btn").click()
                await page.locator(_CHROME).nth(1).locator("[data-edit=panel3d]").click()
                dialog = page.locator(".edit-modal")
                await dialog.locator("select[data-field=view]").select_option("independent")
                await dialog.locator("input[data-field=link_camera]").uncheck()
                await dialog.locator("[data-edit=use-current]").click()
                await expect(dialog.locator("[data-field=start]")).to_contain_text("colour_by = ffast.force_mae")
                await dialog.get_by_role("button", name="Apply").click()
                await page.locator("[data-edit=save]").click()
                await expect(page.locator(".tabpanel.active .edit-bar")).to_have_count(0)

                saved = tomllib.loads((tabs_dir / "compare.toml").read_text())
                panel = saved["tabs"][0]["panels"][1]
                assert panel["view"] == "independent" and panel["link_camera"] is False
                assert panel["start"]["colour_by"] == "ffast.force_mae"
                assert panel["start"]["colormap"] == "force_error"
            finally:
                await browser.close()


_SIZED_TAB_TOML = """
[[visualization.tabs]]
name = "Sized"
column_widths = [3, 1]
row_heights = [2, 1]

[[visualization.tabs.panels]]
kind = "3d"
row = 0
col = 0
rowspan = 2

[[visualization.tabs.panels]]
kind = "table"
row = 0
col = 1
title = "Top"
  [visualization.tabs.panels.metrics.value]
  metric = "ffast.force_component_mae"

[[visualization.tabs.panels]]
kind = "table"
row = 1
col = 1
title = "Bottom"
  [visualization.tabs.panels.metrics.value]
  metric = "ffast.force_component_rmse"
"""


async def test_web_tab_sizes_share_the_window(tmp_path):
    """ADR 0056 rule 12: column_widths and row_heights are relative; rows
    with set heights share the window's height instead of scrolling. (A
    window wide enough that the narrow column is above its 400 px minimum.)"""
    config = tmp_path / "ffast.toml"
    config.write_text(_SIZED_TAB_TOML)
    async with _spawn_server("--config", str(config)) as (ws_port, web_port):
        dataset_fp = await _preload_dataset(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 2400, "height": 820})
            try:
                await _open_loupe(page, ws_port, web_port, dataset_fp)
                await _open_analysis_tab(page, "Sized")
                grid = page.locator(".tabpanel.active .analysis-grid")
                view = await grid.locator(".panel-3d").bounding_box()
                top = await grid.locator("[data-title='Top']").bounding_box()
                bottom = await grid.locator("[data-title='Bottom']").bounding_box()
                gap = 10
                assert view["width"] / top["width"] == pytest.approx(3, rel=0.03)
                assert top["height"] / bottom["height"] == pytest.approx(2, rel=0.03)
                box = await grid.bounding_box()
                assert view["height"] == pytest.approx(box["height"] - 2 * gap, abs=2)
                assert await grid.evaluate("(el) => el.scrollHeight <= el.clientHeight")
            finally:
                await browser.close()


_WIDE_TAB_TOML = "[[visualization.tabs]]\nname = \"Wide\"\n" + "".join(
    f"""
[[visualization.tabs.panels]]
kind = "table"
row = 0
col = {col}
title = "T{col}"
  [visualization.tabs.panels.metrics.value]
  metric = "ffast.force_component_mae"
""" for col in range(4))


async def test_web_columns_keep_a_minimum_width_and_the_tab_scrolls_sideways(tmp_path):
    """Columns are never narrower than 400 px (the desktop's smallest plot);
    when they do not fit, the tab scrolls sideways, as it scrolls down for
    rows. Columns that fit share the window as before."""
    config = tmp_path / "ffast.toml"
    config.write_text(_WIDE_TAB_TOML)
    async with _spawn_server("--config", str(config)) as (ws_port, web_port):
        dataset_fp = await _preload_dataset(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1300, "height": 820})
            try:
                await _open_loupe(page, ws_port, web_port, dataset_fp)
                await _open_analysis_tab(page, "Wide")
                grid = page.locator(".tabpanel.active .analysis-grid")
                await expect(grid.locator(".analysis-panel")).to_have_count(4)
                widths = [(await grid.locator(f"[data-title='T{c}']").bounding_box())["width"]
                          for c in range(4)]
                assert min(widths) >= 400 - 1
                assert await grid.evaluate("(el) => el.scrollWidth > el.clientWidth")
                assert await page.evaluate(
                    "() => document.documentElement.scrollWidth <= window.innerWidth")

                await _open_analysis_tab(page, "Basic Errors")
                basic = page.locator(".tabpanel.active .analysis-grid")
                assert await basic.evaluate("(el) => el.scrollWidth <= el.clientWidth")
            finally:
                await browser.close()


async def _tab_request(ws_port, event, kwargs):
    """Send a tab message from another client; return its TAB_SAVED answer."""
    ws = await _connect_headless_client(ws_port)
    try:
        await ws.send(pack(event, (), kwargs))
        return (await _wait_for_event(ws, "TAB_SAVED", timeout=15))["kwargs"]
    finally:
        await ws.send(pack("GRACEFUL_DISCONNECT", (), {}))
        await ws.close()


async def _tab_menu(page, label):
    await page.locator("#tab-menu-btn").click()
    await page.locator("#tab-menu-list").get_by_role("menuitem", name=label, exact=True).click()


async def test_web_user_tabs_reach_every_window_and_can_be_reset_hidden_exported(tmp_path):
    """ADR 0056 rules 9, 10 and 16. A tab saved anywhere appears in every
    window; an edited built-in tab says so and Reset brings the original back;
    any tab can be hidden and shown again, except the last one showing the main
    view; Export gives TOML for a project's ffast.toml."""
    tabs_dir = tmp_path / "tabs"
    config = tmp_path / "ffast.toml"   # no project tabs: only built-in and user ones
    config.write_text("")
    async with _spawn_server("--tabs-dir", str(tabs_dir), "--config", str(config)) as (ws_port, web_port):
        dataset_fp = await _preload_dataset(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1300, "height": 820})
            tabbar = page.locator("#tabbar .tab")
            try:
                await _open_loupe(page, ws_port, web_port, dataset_fp)

                # Saved by another client: this window gets the new layout.
                answer = await _tab_request(ws_port, "SAVE_TAB", {"tab": {
                    "name": "Mine", "panels": [{"kind": "3d", "row": 0, "col": 0}]}})
                assert answer["ok"], answer
                await expect(tabbar.last).to_have_text("Mine")
                assert (tabs_dir / "mine.toml").exists()

                # An edited built-in tab, in its place, marked.
                answer = await _tab_request(ws_port, "SAVE_TAB", {"previous_name": "Gyration", "tab": {
                    "name": "Gyration", "panels": [{"kind": "table", "row": 0, "col": 0, "title": "MAE",
                        "metrics": {"value": {"metric": "ffast.force_component_mae"}}}]}})
                assert answer["ok"], answer
                gyration = page.locator("#tabbar .tab", has_text="Gyration")
                await expect(gyration.locator(".tab-mark")).to_have_text("edited")
                await gyration.click()
                await _tab_menu(page, "Reset to original…")
                await page.get_by_role("button", name="Reset").click()
                await expect(gyration.locator(".tab-mark")).to_have_count(0)
                await expect(page.locator(".tabpanel.active .analysis-panel[data-kind='overlay_timeline']")).not_to_have_count(0)

                # Hide, and show again from the menu.
                await _tab_menu(page, "Hide this tab")
                await expect(gyration).to_have_count(0)
                await expect(page.locator("#tabbar .tab.active")).to_have_text("3D")
                await _tab_menu(page, "Show Gyration")
                await expect(gyration).to_have_count(1)

                # The last tab showing the main view stays: "Mine" has one too.
                await _tab_menu(page, "Hide this tab")   # on 3D
                await expect(tabbar.first).to_have_text("Basic Errors")
                await tabbar.last.click()                 # Mine
                await page.locator("#tab-menu-btn").click()
                hide = page.locator("#tab-menu-list").get_by_role("menuitem", name="Hide this tab")
                await expect(hide).to_be_disabled()
                assert await hide.get_attribute("title") == "At least one tab must show the main view"
                await page.keyboard.press("Escape")

                # Export, as a project would write the tab.
                await page.locator("#tabbar .tab", has_text="Basic Errors").click()
                await _tab_menu(page, "Export as TOML…")
                text = await page.locator("#text-dialog textarea").input_value()
                assert text.startswith("[[visualization.tabs]]")
                assert 'name = "Basic Errors"' in text
            finally:
                await browser.close()


@contextlib.asynccontextmanager
async def _tab_server(tmp_path):
    """A server with only built-in tabs (no project config) and an empty tabs
    folder; yields (ws_port, web_port, tabs_dir) with a dataset and prediction
    loaded."""
    tabs_dir = tmp_path / "tabs"
    config = tmp_path / "ffast.toml"
    config.write_text("")
    async with _spawn_server("--tabs-dir", str(tabs_dir), "--config", str(config)) as (ws_port, web_port):
        await _preload_dataset_and_prediction(ws_port)
        yield ws_port, web_port, tabs_dir


async def _open_app(page, web_port, ws_port):
    await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
    await expect(page.locator("#status")).to_have_class(re.compile(r"\bconnected\b"))
    await expect(page.locator("#overlay")).to_have_class(re.compile(r"\bhidden\b"))


_CHROME = ".tabpanel.active .edit-chrome"


async def test_web_edit_mode_changes_a_tab_only_on_save(tmp_path):
    """ADR 0056 rules 11, 12 and 15. ✎ opens Edit mode: remove a panel, add a
    ready-made 3D panel and a custom one from the builder, drag one onto
    another to swap them, drag a column divider. Cancel drops all of it and
    nothing is written; Save writes the tab, which is then marked edited."""
    async with _tab_server(tmp_path) as (ws_port, web_port, tabs_dir):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1800, "height": 820})   # room to drag a divider
            draft = "() => window.ffastApp._editor.draft?.panels.map((p) => [p.kind, p.title, p.row, p.col])"
            try:
                await _open_app(page, web_port, ws_port)
                await _open_analysis_tab(page, "Gyration")
                await page.locator("#tab-edit-btn").click()
                await expect(page.locator(".tabpanel.active .edit-bar")).to_contain_text("Editing Gyration")
                await expect(page.locator(".tabpanel.active .analysis-controls")).to_be_hidden()
                await expect(page.locator(_CHROME)).to_have_count(4)

                await page.locator(_CHROME, has_text="Gyradius distribution").locator("[data-edit=remove]").click()
                await expect(page.locator(_CHROME)).to_have_count(3)
                await page.locator("[data-edit=add-panel]").click()
                await page.get_by_role("button", name="3D panel", exact=True).click()
                await expect(page.locator(_CHROME)).to_have_count(4)
                await expect(page.locator(".tabpanel.active canvas.view3d")).to_have_count(1)

                await page.locator("[data-edit=add-panel]").click()
                await page.get_by_role("button", name="Custom panel…").click()
                await page.locator(".edit-modal select[data-field=kind]").select_option("table")
                assert "ffast.gyradius" not in await page.locator(
                    ".edit-modal select[data-role=value]").inner_text()   # not a scalar
                await page.locator(".edit-modal select[data-role=value]").select_option("ffast.force_component_mae")
                await page.locator(".edit-modal input[data-field=title]").fill("Force MAE")
                await page.get_by_role("button", name="Add", exact=True).click()
                await expect(page.locator(_CHROME)).to_have_count(5)

                # Drag the 3D panel onto the first panel: the two swap.
                handle = page.locator(_CHROME, has_text="3D panel").locator(".edit-handle")
                hb = await handle.bounding_box()
                gb = await page.locator(".tabpanel.active .analysis-grid").bounding_box()
                await page.mouse.move(hb["x"] + 10, hb["y"] + 5)
                await page.mouse.down()
                await page.mouse.move(gb["x"] + 60, gb["y"] + 60, steps=8)
                await page.mouse.up()
                await page.wait_for_function(
                    "() => window.ffastApp._editor.draft.panels.some((p) => p.kind === '3d' && p.row === 0 && p.col === 0)")
                assert ["timeline", "Total gyration radius", 1, 0] in await page.evaluate(draft)

                divider = await page.locator(".tabpanel.active .edit-divider.col").first.bounding_box()
                await page.mouse.move(divider["x"] + 4, divider["y"] + 100)
                await page.mouse.down()
                await page.mouse.move(divider["x"] + 150, divider["y"] + 100, steps=6)
                await page.mouse.up()
                widths = await page.evaluate("() => window.ffastApp._editor.draft.column_widths")
                assert len(widths) == 2 and widths[0] > widths[1]

                # Cancel: the tab as it was, and nothing written.
                await page.locator("[data-edit=cancel]").click()
                await expect(page.locator(".tabpanel.active .edit-bar")).to_have_count(0)
                await expect(page.locator(".tabpanel.active .analysis-panel[data-title='Gyradius distribution']")).to_have_count(1)
                assert not list(tabs_dir.glob("*.toml"))

                # Save: written, marked, out of Edit mode.
                await page.locator("#tab-edit-btn").click()
                await page.locator(_CHROME, has_text="Gyradius distribution").locator("[data-edit=remove]").click()
                await page.locator("[data-edit=save]").click()
                await expect(page.locator(".tabpanel.active .edit-bar")).to_have_count(0)
                await expect(page.locator("#tabbar .tab.active .tab-mark")).to_have_text("edited")
                await expect(page.locator(".tabpanel.active .analysis-panel[data-title='Gyradius distribution']")).to_have_count(0)
                saved = (tabs_dir / "gyration.toml").read_text()
                assert "Gyradius distribution" not in saved and "Total gyration radius" in saved
            finally:
                await browser.close()


async def test_web_new_tabs_and_the_main_view_rule(tmp_path):
    """ADR 0056 rules 6 and 14. The last linked 3D panel cannot be removed;
    "+" ▸ Empty tab asks the name and columns, then opens the new tab in
    Edit mode, and it exists once saved."""
    async with _tab_server(tmp_path) as (ws_port, web_port, tabs_dir):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1300, "height": 820})
            try:
                await _open_app(page, web_port, ws_port)
                await page.locator("#tab-edit-btn").click()   # the 3D tab
                remove = page.locator(_CHROME).locator("[data-edit=remove]")
                await expect(remove).to_be_disabled()
                assert await remove.get_attribute("title") == "At least one tab must show the main view"
                await expect(page.locator("#tab-edit-btn")).to_be_disabled()   # already editing
                await page.locator("[data-edit=cancel]").click()

                await page.locator("#tab-new-btn").click()
                await page.locator("#tab-new-list [data-action=tab-new-empty]").click()
                await page.locator(".edit-modal input[data-field=name]").fill("Basic Errors")
                await expect(page.get_by_role("button", name="Create")).to_be_disabled()
                await expect(page.locator(".edit-modal .ctl-hint")).to_have_text(
                    "A tab named Basic Errors already exists")
                await page.locator(".edit-modal input[data-field=name]").fill("Fresh")
                await page.locator(".edit-modal input[data-field=columns]").fill("3")
                await page.get_by_role("button", name="Create").click()
                await expect(page.locator("#tabbar .tab.active")).to_have_text("Fresh")
                await expect(page.locator(".tabpanel.active .edit-bar")).to_contain_text("Editing Fresh")
                await page.locator("[data-edit=add-panel]").click()
                await page.get_by_role("button", name="3D panel", exact=True).click()
                await page.locator("[data-edit=save]").click()
                await expect(page.locator(".tabpanel.active .edit-bar")).to_have_count(0)
                await expect(page.locator("#tabbar .tab.active")).to_have_text("Fresh")
                data = tomllib.loads((tabs_dir / "fresh.toml").read_text())
                assert data["tabs"][0]["column_widths"] == [1, 1, 1]

                # Now a second tab shows the main view, so the 3D tab's panel can go.
                await page.locator("#tabbar .tab", has_text="3D").first.click()
                await page.locator("#tab-edit-btn").click()
                await expect(page.locator(_CHROME).locator("[data-edit=remove]")).to_be_enabled()
            finally:
                await browser.close()


async def test_web_edit_mode_warns_when_another_window_saved_the_tab(tmp_path):
    """ADR 0056 rule 16: Save asks whether to overwrite or discard when the
    tab was saved in another window meanwhile."""
    async with _tab_server(tmp_path) as (ws_port, web_port, tabs_dir):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1300, "height": 820})
            other = {"previous_name": "Basic Errors", "tab": {"name": "Basic Errors", "panels": [
                {"kind": "table", "row": 0, "col": 0, "title": "Theirs",
                 "metrics": {"value": {"metric": "ffast.force_component_mae"}}}]}}
            try:
                await _open_app(page, web_port, ws_port)
                await _open_analysis_tab(page, "Basic Errors")
                await page.locator("#tab-edit-btn").click()
                await page.locator(_CHROME).first.locator("[data-edit=remove]").click()
                assert (await _tab_request(ws_port, "SAVE_TAB", other))["ok"]
                await expect(page.locator(".edit-bar .edit-note")).to_have_text(
                    "Changed in another window since you began.")
                await page.locator("[data-edit=save]").click()
                await expect(page.locator(".ask-dialog .warning-modal-message")).to_have_text(
                    "Basic Errors was changed in another window — overwrite or discard your edits?")
                await page.get_by_role("button", name="Discard my edits").click()
                await expect(page.locator(".tabpanel.active .edit-bar")).to_have_count(0)
                await expect(page.locator(".tabpanel.active .analysis-panel[data-title='Theirs']")).to_have_count(1)

                await page.locator("#tab-edit-btn").click()
                await page.locator("[data-edit=tab-settings]").click()
                await page.locator(".edit-modal input[data-field=name]").fill("Mine now")
                await page.get_by_role("button", name="Apply").click()
                other["tab"]["panels"][0]["title"] = "Theirs again"
                assert (await _tab_request(ws_port, "SAVE_TAB", other))["ok"]
                await page.locator("[data-edit=save]").click()
                await page.get_by_role("button", name="Overwrite").click()
                await expect(page.locator(".tabpanel.active .edit-bar")).to_have_count(0)
                await expect(page.locator("#tabbar .tab.active")).to_contain_text("Mine now")
                assert 'name = "Mine now"' in (tabs_dir / "basic-errors.toml").read_text()
            finally:
                await browser.close()


async def test_web_first_view_fits_the_atoms_and_later_ones_keep_the_camera(ffast_web_server):
    """The first time a dataset's view opens, every atom is in view. After
    that the camera is the user's: switching tabs and back keeps it (457dcaa)."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)
    in_view = """() => {
      const R = window.ffastApp.renderer;
      const rect = document.querySelector('.tabpanel.active .panel-3d.focused canvas.view3d').getBoundingClientRect();
      for (let i = 0; i < R.atomCount; i++) {
        const s = R.atomScreenPosition(i);
        if (!s || s.x < 0 || s.y < 0 || s.x > rect.width || s.y > rect.height) return false;
      }
      return R.atomCount > 0;
    }"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            assert await page.evaluate(in_view)

            await page.evaluate(
                "() => window.ffastApp.renderer.setCameraAngles({azimuth: 70, elevation: 25})"
            )
            await page.wait_for_timeout(400)   # SET_CAMERA is throttled to 100 ms
            before = await page.evaluate("() => window.ffastApp.renderer._exportCamera()")

            await page.evaluate(
                """() => {
                  const app = window.ffastApp, orig = app._onSceneSnapshot.bind(app);
                  window.snapshots = 0;
                  app._onSceneSnapshot = (kw) => { window.snapshots += 1; orig(kw); };
                }"""
            )
            await page.locator("#tabbar .tab").nth(1).click()
            await page.wait_for_timeout(200)
            await page.locator("#tabbar .tab", has_text="3D").first.click()
            await page.wait_for_function("() => window.snapshots > 0", timeout=10000)
            await page.wait_for_timeout(300)
            after = await page.evaluate("() => window.ffastApp.renderer._exportCamera()")
        finally:
            await browser.close()

    assert after["azimuth"] == pytest.approx(before["azimuth"], abs=0.5)
    assert after["elevation"] == pytest.approx(before["elevation"], abs=0.5)
    assert after["distance"] == pytest.approx(before["distance"], rel=0.01)


_PREDICTION_ROWS = (
    ("Colour By", ".pane[data-pane='Colour By'] .ctl-row[data-label='Prediction']"),
    ("Force Vectors", ".pane[data-pane='Force Vectors'] .ctl-row[data-label='Source']"),
)


async def _show_rows_that_offer_a_prediction(page):
    """Both selectors also wait on their own pane: Colour By shows Prediction
    only for a metric colouring, Force Vectors shows Source only with arrows on."""
    await _open_section(page, "Colour By")
    coloring = page.locator(".pane[data-pane='Colour By'] .ctl-row[data-label='Coloring'] select")
    await coloring.select_option(label="Acceleration Error")
    await _open_section(page, "Force Vectors")
    await page.locator(
        ".pane[data-pane='Force Vectors'] .ctl-row[data-label='Show force vectors'] input"
    ).check()


async def test_web_prediction_selectors_wait_for_a_prediction(ffast_web_server):
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            await _show_rows_that_offer_a_prediction(page)
            for section, row in _PREDICTION_ROWS:
                await _open_section(page, section)
                await expect(page.locator(row)).to_be_hidden()
        finally:
            await browser.close()


async def test_web_prediction_selectors_appear_with_a_prediction(ffast_web_server):
    ws_port, web_port = ffast_web_server
    dataset_fp, _model_fp = await _preload_dataset_and_prediction(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            await _show_rows_that_offer_a_prediction(page)
            for section, row in _PREDICTION_ROWS:
                await _open_section(page, section)
                await expect(page.locator(row)).to_be_visible()
        finally:
            await browser.close()


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

            png = await page.locator(CANVAS).screenshot()
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

            await _open_section(page, "Force Vectors")
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
            baseline_count = _count_orange_force_pixels(await page.locator(CANVAS).screenshot())

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
            on_count = _count_orange_force_pixels(await page.locator(CANVAS).screenshot())
            assert on_count > baseline_count + 20

            await show_forces.uncheck()
            await page.wait_for_timeout(700)
            off_count = _count_orange_force_pixels(await page.locator(CANVAS).screenshot())
            assert off_count < on_count
        finally:
            await browser.close()


async def test_web_renderer_draws_the_colours_the_scene_specifies(ffast_web_server):
    """Scene colours are sRGB, like the CSS colour bar (ADR 0052: a renderer
    draws the RGBA it is given). Three.js reads a bare ``setRGB`` as *linear*
    and brightens it on output, so every atom and arrow came out paler than
    specified — force-error colouring looked washed out and no longer matched
    its colour bar. Read back what the GPU buffers hold, in sRGB."""
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
            await expect(page.locator("#status")).to_contain_text("Connected")
            await page.locator(f"#dataset-list .obj-row[data-fp='{dataset_fp}']").click()
            await page.locator(f"#model-list .obj-row[data-fp='{model_fp}']").click()
            await expect(page.locator("#overlay")).to_have_class(re.compile(r"\bhidden\b"))
            await _open_section(page, "Force Vectors")
            await page.locator(
                ".pane[data-pane='Force Vectors'] .ctl-row[data-label='Show force vectors'] input"
            ).check()
            await page.wait_for_function("() => window.ffastApp.renderer._forceGroup !== null")

            drawn = await page.evaluate(
                """() => {
                  const R = window.ffastApp.renderer;
                  const hex = (rgb) => rgb.slice(0, 3)
                    .map((v) => Math.round(v * 255).toString(16).padStart(2, '0')).join('');
                  const c = new R._bondColor.constructor();
                  const atoms = R._atomColors.map((rgb, i) => {
                    R._atomMesh.getColorAt(i, c);
                    return [hex(rgb), c.getHexString()];
                  });
                  const arrow = R._forceGroup.children[0];
                  return { atoms, arrow: arrow.cone.material.color.getHexString() };
                }"""
            )
        finally:
            await browser.close()

    mismatched = [pair for pair in drawn["atoms"] if pair[0] != pair[1]]
    assert not mismatched, f"atoms drawn in a different colour than specified: {mismatched[:5]}"
    # presentation.FORCE_ARROW_COLOR = (0.9, 0.4, 0.1)
    assert drawn["arrow"] == "e6661a"


async def test_web_pick_highlight_does_not_rebuild_a_shader(ffast_web_server):
    """Each pick sends a selection patch. The overlay used to dispose its
    material and make a new one every time, so the GPU recompiled the shader
    program on every pick — a ~115 ms freeze that delayed the next reply.
    Picking again must reuse the program the first highlight compiled."""
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
            await expect(page.locator("#status")).to_contain_text("Connected")
            await page.locator(f"#dataset-list .obj-row[data-fp='{dataset_fp}']").click()
            await expect(page.locator("#overlay")).to_have_class(re.compile(r"\bhidden\b"))

            programs = await page.evaluate(
                """async () => {
                  const app = window.ffastApp, R = app.renderer;
                  const frame = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
                  const highlight = async (indices) => {
                    app._sendSetSelection('picked', 'current_structure', indices);
                    const deadline = performance.now() + 5000;
                    while (R._selectionMeshes.get('picked')?.count !== indices.length) {
                      if (performance.now() > deadline) throw new Error('no highlight for ' + indices);
                      await new Promise((r) => setTimeout(r, 10));
                    }
                    await frame();
                    return R._renderer.info.programs.map((p) => p.id).sort();
                  };
                  const first = await highlight([0]);
                  await highlight([0, 1]);
                  const later = await highlight([2, 3, 4]);
                  return { first, later };
                }"""
            )
        finally:
            await browser.close()

    assert programs["later"] == programs["first"], (
        f"picking rebuilt shader programs: {programs['first']} -> {programs['later']}"
    )


# A synthetic structure of `n` atoms on a line, applied straight to the
# renderer: the drawing rules depend only on what the scene holds.
_SYNTHETIC_SCENE = """(n) => ({
  atoms: {
    positions: Array.from({length: n}, (_, i) => [i * 1.5 - (n - 1) * 0.75, 0, 0]),
    sizes: Array(n).fill(0.6),
    colors: Array(n).fill([0.75, 0.3, 0.3]),
  },
  bonds: { segments: n > 1 ? [[0, 0, 0], [1.5, 0, 0]] : [] },
})"""

_DRAWN_LOOK = """() => {
  const R = window.ffastApp.renderer;
  return {
    rich: R.richLook,
    sphere: R._atomMesh.geometry.parameters.widthSegments,
    bond: R._bondLines.geometry.parameters.radialSegments,
    metalness: R._atomMesh.material.metalness,
    hemi: R._scene.getObjectsByProperty('isHemisphereLight', true).some((l) => l.visible),
  };
}"""


async def _apply_synthetic(page, n):
    await page.evaluate(
        f"(n) => window.ffastApp.renderer.applyScene(({_SYNTHETIC_SCENE})(n))", n
    )


async def test_web_small_structures_get_the_richer_look(ffast_web_server):
    """ADR 0055 "3D drawing": below the atom-count threshold, smoother spheres
    and bonds, glossier materials and a sky light; above it, today's look."""
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/", wait_until="networkidle")
            limit = await page.evaluate("() => window.ffastApp.renderer.richLookMaxAtoms")

            await _apply_synthetic(page, 20)
            small = await page.evaluate(_DRAWN_LOOK)
            await _apply_synthetic(page, limit + 1)
            large = await page.evaluate(_DRAWN_LOOK)
            await _apply_synthetic(page, 20)
            back = await page.evaluate(_DRAWN_LOOK)
        finally:
            await browser.close()

    assert small["rich"] and small["hemi"]
    assert small["sphere"] > large["sphere"] and small["bond"] > large["bond"]
    assert small["metalness"] > large["metalness"]
    assert not large["rich"] and not large["hemi"]
    assert (large["sphere"], large["bond"]) == (10, 6)   # today's look, unchanged
    assert back == small


# Two atoms side by side: a small and a large covalent radius, red and blue,
# one bond between them. Sizes as the server sends them: radius x Atom size.
_BALL_AND_STICK_SCENE = """(scale) => ({
  atoms: {
    positions: [[0, 0, 0], [1.5, 0, 0], [3.0, 0, 0]],
    sizes: [0.31 * scale, 0.76 * scale, 1.40 * scale],
    colors: [[1, 0, 0, 1], [0, 0, 1, 1], [0, 1, 0, 1]],
  },
  bonds: { segments: [[0, 0, 0], [1.5, 0, 0]] },
  camera: { center: [1.5, 0, 0], distance: 12, azimuth: 0, elevation: 0, fov: 60,
            projection: 'perspective' },
})"""

_DRAWN_BALLS = """() => {
  const R = window.ffastApp.renderer, m = R._atomMesh, b = R._bondLines;
  const M = new m.matrix.constructor(), c = new m.material.color.constructor();
  const radii = [], bondColors = [];
  for (let i = 0; i < m.count; i++) {
    m.getMatrixAt(i, M);
    radii.push(+Math.hypot(M.elements[0], M.elements[1], M.elements[2]).toFixed(4));
  }
  for (let i = 0; i < b.count; i++) {
    if (b.instanceColor) { b.getColorAt(i, c); bondColors.push('#' + c.getHexString()); }
  }
  return { radii, bonds: b.count, bondColors, bondMaterial: '#' + b.material.color.getHexString(),
           fov: R._perspCamera.fov, exportedFov: R._exportCamera().fov };
}"""


async def test_web_rich_look_draws_chemistry_alive_ball_and_stick(ffast_web_server):
    """ADR 0055 (revised): below the threshold the browser draws ball-and-stick
    as the chemistry.alive prototype did — 0.42 x covalent radius kept within
    0.24-0.55 A, times Atom size; bonds in two halves coloured like their
    atoms; a 35-degree lens, while the server keeps its own field of view."""
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/", wait_until="networkidle")
            await page.evaluate(
                f"() => window.ffastApp.renderer.applyScene(({_BALL_AND_STICK_SCENE})(1))")
            full = await page.evaluate(_DRAWN_BALLS)

            # Atom size 0.5: the server halves its sizes; the balls halve too.
            await page.evaluate(
                """() => { document.querySelector(".pane[data-pane='Display'] "
                     + ".ctl-row[data-label='Atom size'] input").value = '0.5'; }""")
            await page.evaluate(
                f"() => window.ffastApp.renderer.applyScene(({_BALL_AND_STICK_SCENE})(0.5))")
            half = await page.evaluate(_DRAWN_BALLS)

            # A chosen Bond colour wins over the two-tone bonds.
            await page.evaluate("() => window.ffastApp.renderer.setBondStyle(100, '#00ff00', true)")
            chosen = await page.evaluate(_DRAWN_BALLS)
        finally:
            await browser.close()

    assert full["radii"] == [0.24, round(0.42 * 0.76, 4), 0.55]
    assert half["radii"] == [0.12, round(0.42 * 0.76 * 0.5, 4), 0.275]
    assert full["bonds"] == 2 and full["bondColors"] == ["#ff0000", "#0000ff"]
    assert chosen["bonds"] == 1 and chosen["bondMaterial"] == "#00ff00"
    assert full["fov"] == 35 and full["exportedFov"] == 60


async def test_web_large_structures_keep_server_sizes_and_lens(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/", wait_until="networkidle")
            limit = await page.evaluate("() => window.ffastApp.renderer.richLookMaxAtoms")
            drawn = await page.evaluate(
                f"""(n) => {{
                  const s = ({_SYNTHETIC_SCENE})(n);
                  s.atoms.sizes = s.atoms.sizes.map((_, i) => i ? 0.6 : 0.76);
                  s.camera = {{ center: [0, 0, 0], distance: 20, azimuth: 0, elevation: 0, fov: 60,
                               projection: 'perspective' }};
                  const R = window.ffastApp.renderer;
                  R.applyScene(s);
                  const M = new R._atomMesh.matrix.constructor();
                  R._atomMesh.getMatrixAt(0, M);
                  return {{ r: Math.hypot(M.elements[0], M.elements[1], M.elements[2]),
                           fov: R._perspCamera.fov, bonds: R._bondLines.count }};
                }}""",
                limit + 1,
            )
        finally:
            await browser.close()
    assert drawn == {"r": pytest.approx(0.76), "fov": 60, "bonds": 1}


async def test_web_rich_look_keeps_an_atoms_shade_while_orbiting(ffast_web_server):
    """ADR 0055: the lights turn with the camera, so an atom looks the same
    from every side. Above the threshold the light stays fixed in the world."""
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    shade = """async (azimuth) => {
      const R = window.ffastApp.renderer;
      R.setCameraAngles({center: [0, 0, 0], distance: 8, elevation: 0, azimuth});
      const img = new Image();
      img.src = R.capturePng({background: '#000000'});
      await img.decode();
      const c = document.createElement('canvas');
      c.width = img.width; c.height = img.height;
      const g = c.getContext('2d');
      g.drawImage(img, 0, 0);
      return Array.from(g.getImageData(img.width >> 1, img.height >> 1, 1, 1).data.slice(0, 3));
    }"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/", wait_until="networkidle")
            await _apply_synthetic(page, 1)
            rich = [await page.evaluate(shade, az) for az in (0, 90, 180)]
            limit = await page.evaluate("() => window.ffastApp.renderer.richLookMaxAtoms")
            # Same single atom in the middle, plus far-away atoms to cross the threshold.
            await page.evaluate(
                f"""(n) => {{
                  const s = ({_SYNTHETIC_SCENE})(n);
                  s.atoms.positions = s.atoms.positions.map((p, i) => i ? [p[0], 500, 0] : [0, 0, 0]);
                  window.ffastApp.renderer.applyScene(s);
                }}""",
                limit + 1,
            )
            plain = [await page.evaluate(shade, az) for az in (0, 90, 180)]
        finally:
            await browser.close()

    assert sum(rich[0]) > 60, rich  # the atom is drawn, not background
    for other in rich[1:]:
        assert max(abs(a - b) for a, b in zip(rich[0], other)) <= 2, rich
    assert any(max(abs(a - b) for a, b in zip(plain[0], o)) > 10 for o in plain[1:]), plain


async def test_web_pixel_ratio_is_capped_at_two(ffast_web_server):
    """ADR 0055: a 3x screen draws at 2x, for every structure size."""
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 800, "height": 600}, device_scale_factor=3
        )
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/", wait_until="networkidle")
            ratio = await page.evaluate("() => window.ffastApp.renderer._renderer.getPixelRatio()")
        finally:
            await browser.close()
    assert ratio == 2


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
            await expect(page.locator("#status")).to_contain_text("Connected")

            dataset_row = page.locator(f"#dataset-list .obj-row[data-fp='{dataset_fp}']")
            await dataset_row.click()
            model_row = page.locator(f"#model-list .obj-row[data-fp='{model_fp}']")
            await model_row.click()
            await expect(page.locator("#overlay")).to_have_class(re.compile(r"\bhidden\b"))
            await page.wait_for_timeout(300)

            colorbar = page.locator("#colorbar")
            await expect(colorbar).to_have_class(re.compile(r"\bhidden\b"))
            before_png = await page.locator(CANVAS).screenshot()

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
            after_png = await page.locator(CANVAS).screenshot()
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
            await expect(page.locator("#status")).to_contain_text("Connected")

            dataset_row = page.locator(f"#dataset-list .obj-row[data-fp='{dataset_fp}']")
            await dataset_row.click()
            await expect(page.locator("#overlay")).to_have_class(re.compile(r"\bhidden\b"))
            await page.wait_for_timeout(300)

            await _open_section(page, "Camera")
            before_png = await page.locator(CANVAS).screenshot()

            # frameAtoms()'s initial fit-to-view already sits at az=0/el=0
            # (looking down -Z, i.e. the "XZ" front view) — use "XY" (top view,
            # el=90) so the preset is a genuine reorientation, not a no-op.
            xy_preset = page.locator(".pane[data-pane='Camera'] .ctl-btn-group button", has_text="XY")
            await expect(xy_preset).to_have_count(1)
            await xy_preset.click()
            await page.wait_for_timeout(300)

            after_png = await page.locator(CANVAS).screenshot()
            assert before_png != after_png

            # The manual elevation field reflects the preset (az 0°, el 90°).
            elevation = page.locator(
                ".pane[data-pane='Camera'] .ctl-row[data-label='Elevation (°)'] input"
            )
            await expect(elevation).to_have_value(re.compile(r"^90\.0$"))
        finally:
            await browser.close()


async def test_web_fps_and_skip_live_in_a_gear_pop_up(ffast_web_server):
    """ADR 0055: FPS and Skip move into a ⚙ pop-up, whose button is drawn
    larger than the other strip icons."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            gear = page.locator("#playback-gear")
            fields = (page.locator("#fps-input"), page.locator("#skip-input"))
            for f in fields:
                await expect(f).to_be_hidden()

            sizes = await page.evaluate(
                """() => ['playback-gear', 'play-pause-btn'].map(
                     (id) => parseFloat(getComputedStyle(document.getElementById(id)).fontSize))"""
            )
            assert sizes[0] > sizes[1], sizes

            await gear.click()
            await expect(gear).to_have_attribute("aria-expanded", "true")
            for f in fields:
                await expect(f).to_be_visible()
            await page.keyboard.press("Escape")
            for f in fields:
                await expect(f).to_be_hidden()

            await gear.click()
            await page.locator("#frame-label").click()   # outside the pop-up
            for f in fields:
                await expect(f).to_be_hidden()
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
            await expect(page.locator("#status")).to_contain_text("Connected")

            dataset_row = page.locator(f"#dataset-list .obj-row[data-fp='{dataset_fp}']")
            await dataset_row.click()
            await expect(page.locator("#frame-slider")).to_be_enabled()

            await page.locator("#playback-gear").click()   # FPS lives in the ⚙ pop-up
            fps_input = page.locator("#fps-input")
            await fps_input.fill("20")
            await page.keyboard.press("Escape")   # fast enough to see multiple frames advance quickly

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


# ── Phase 2: selection & picking ─────────────────────────────────────────────

async def _open_loupe(page, ws_port, web_port, dataset_fp):
    """Connect, select the dataset, wait for the 3D view to be live."""
    await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
    # Connected; the text may already have moved on ("Prediction … ready").
    await expect(page.locator("#status")).to_have_class(re.compile(r"\bconnected\b"))
    dataset_row = page.locator(f"#dataset-list .obj-row[data-fp='{dataset_fp}']")
    await expect(dataset_row).to_have_count(1)
    await dataset_row.click()
    await expect(page.locator("#overlay")).to_have_class(re.compile(r"\bhidden\b"))
    await page.wait_for_timeout(300)


async def _atom_page_xy(page, index):
    """Page-space (client) pixel position of a displayed atom, or None."""
    return await page.evaluate(
        """(i) => {
          const s = window.ffastApp.renderer.atomScreenPosition(i);
          if (!s) return null;
          const r = document.querySelector('.tabpanel.active .panel-3d.focused canvas.view3d').getBoundingClientRect();
          return { x: r.left + s.x, y: r.top + s.y };
        }""",
        index,
    )


async def _selection_mesh_count(page):
    return await page.evaluate("() => window.ffastApp.renderer._selectionMeshes.size")


async def test_web_pick_click_and_box_render_selection_overlay(ffast_web_server):
    """ADR 0045 issue 10 gate: with a pick tool armed, a click selects the
    nearest atom and a box-drag selects a group — each commits to the server
    and renders a selection overlay."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            assert await _selection_mesh_count(page) == 0

            # Arm the Extract tool (keeps the picked overlay; rectangle-capable).
            await page.locator("#pick-toolbar button[data-tool='extract']").click()
            await expect(page.locator("#pick-strip")).not_to_have_class(re.compile(r"\bhidden\b"))

            # Click the nearest atom → overlay renders.
            xy = await _atom_page_xy(page, 0)
            assert xy is not None
            await page.mouse.click(xy["x"], xy["y"])
            await expect(page.locator("#pick-strip-count")).to_contain_text("picked")
            await page.wait_for_function(
                "() => window.ffastApp.renderer._selectionMeshes.size > 0"
            )

            # Clear (overlay drops to zero), then box-drag across the viewport →
            # overlay renders again, proving the box gesture itself selected.
            await page.locator("#pick-clear").click()
            await page.wait_for_function(
                "() => window.ffastApp.renderer._selectionMeshes.size === 0"
            )
            rect = await page.locator(CANVAS).bounding_box()
            cx, cy = rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2
            await page.mouse.move(cx - rect["width"] / 3, cy - rect["height"] / 3)
            await page.mouse.down()
            await page.mouse.move(cx + rect["width"] / 3, cy + rect["height"] / 3, steps=6)
            await page.mouse.up()
            await page.wait_for_function(
                "() => window.ffastApp.renderer._selectionMeshes.size > 0"
            )
        finally:
            await browser.close()


async def test_web_info_tool_reports_distance(ffast_web_server):
    """ADR 0045 issue 11 gate: picking two atoms with the Info tool reports a
    distance read-out in the pick strip."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            await page.locator("#pick-toolbar button[data-tool='info']").click()

            # Two atoms that project far apart on screen, so a click on each
            # lands within the pick radius of distinct atoms.
            far = await page.evaluate(
                """() => {
                  const R = window.ffastApp.renderer;
                  const rect = document.querySelector('.tabpanel.active .panel-3d.focused canvas.view3d').getBoundingClientRect();
                  const a = R.atomScreenPosition(0);
                  let bestI = -1, bestD = -1;
                  for (let i = 1; i < R.atomCount; i++) {
                    const s = R.atomScreenPosition(i);
                    if (!s) continue;
                    const d = (s.x - a.x) ** 2 + (s.y - a.y) ** 2;
                    if (d > bestD) { bestD = d; bestI = i; }
                  }
                  const b = R.atomScreenPosition(bestI);
                  return {
                    a: { x: rect.left + a.x, y: rect.top + a.y },
                    b: { x: rect.left + b.x, y: rect.top + b.y },
                  };
                }"""
            )
            await page.mouse.click(far["a"]["x"], far["a"]["y"])
            await page.mouse.click(far["b"]["x"], far["b"]["y"])
            await expect(page.locator("#pick-strip-count")).to_contain_text("2 picked")
            await expect(page.locator("#pick-readout")).to_contain_text("Distance")
        finally:
            await browser.close()


async def _front_atom(page, zoom=1.0):
    """The front-most atom drawn wholly on screen, with its page-space centre
    and its drawn radius in pixels. Nothing in front can cover its ball, so a
    click anywhere on it must pick it. Fits the atoms first, so the result does
    not depend on where the camera was left, then moves `zoom` times closer."""
    return await page.evaluate(
        """(zoom) => {
          const R = window.ffastApp.renderer;
          R.frameAtoms();
          R.setCameraAngles({distance: R._exportCamera().distance / zoom});
          const rect = document.querySelector('.tabpanel.active .panel-3d.focused canvas.view3d').getBoundingClientRect();
          let best = null;
          for (let i = 0; i < R.atomCount; i++) {
            const s = R.atomScreenPosition(i);
            if (!s || s.x < s.radiusPx || s.y < s.radiusPx
                || s.x > rect.width - s.radiusPx || s.y > rect.height - s.radiusPx) continue;
            if (!best || s.ndcZ < best.s.ndcZ) best = { i, s };
          }
          return {
            index: best.i,
            x: rect.left + best.s.x, y: rect.top + best.s.y,
            radius: R.atomScreenRadius(best.i),
          };
        }""",
        zoom,
    )


async def test_web_pick_tools_show_their_names(ffast_web_server):
    """ADR 0055: pick tools show their names next to their icons."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            for tool, name in [("info", "Info"), ("bonds", "Bonds"), ("align", "Align"),
                               ("forces", "Force"), ("extract", "Extract")]:
                await expect(
                    page.locator(f"#pick-toolbar button[data-tool='{tool}']")
                ).to_contain_text(name)
        finally:
            await browser.close()


async def test_web_click_anywhere_on_an_atoms_ball_picks_it(ffast_web_server):
    """ADR 0055: the pick radius is gone; a click anywhere on an atom's drawn
    ball picks it, even far beyond the old 12 px from its centre."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            # Zoom in so the ball is drawn much larger than the old radius.
            atom = await _front_atom(page, zoom=4)
            assert atom["radius"] > 30, atom

            await page.locator("#pick-toolbar button[data-tool='info']").click()
            await page.mouse.click(atom["x"] + 0.85 * atom["radius"], atom["y"])
            await expect(page.locator("#pick-strip-count")).to_contain_text("1 picked")
            picked = await page.evaluate("() => window.ffastApp._picked[0].displayIndex")
            assert picked == atom["index"]
        finally:
            await browser.close()


async def test_web_tiny_atoms_keep_a_minimum_pick_target(ffast_web_server):
    """ADR 0055: very small atoms keep a minimum target of a few pixels."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            atom = await _front_atom(page)
            hit = await page.evaluate(
                """() => {
                  const R = window.ffastApp.renderer;
                  R.setCameraAngles({distance: 2000});
                  let best = null;
                  for (let i = 0; i < R.atomCount; i++) {
                    const s = R.atomScreenPosition(i);
                    if (s && (!best || s.ndcZ < best.s.ndcZ)) best = { i, s };
                  }
                  return {
                    radius: R.atomScreenRadius(best.i),
                    hit: R.pickAtom(best.s.x + 3, best.s.y)?.displayIndex ?? null,
                    miss: R.pickAtom(best.s.x + 40, best.s.y + 40),
                  };
                }"""
            )
            assert hit["radius"] < 2, hit
            assert hit["hit"] is not None
            assert hit["miss"] is None
        finally:
            await browser.close()


async def test_web_armed_tool_highlights_the_atom_under_the_pointer(ffast_web_server):
    """ADR 0055: while a tool is armed, the atom a click would pick is drawn
    larger and lighter; nothing is highlighted with no tool armed."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)

    drawn = """(i) => {
      const R = window.ffastApp.renderer, m = R._atomMesh;
      const M = new m.matrix.constructor(), c = new m.material.color.constructor();
      m.getMatrixAt(i, M); m.getColorAt(i, c);
      return { scale: Math.hypot(M.elements[0], M.elements[1], M.elements[2]),
               light: c.r + c.g + c.b, hovered: R.hoveredAtom };
    }"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            atom = await _front_atom(page)
            base = await page.evaluate(drawn, atom["index"])

            # No tool armed: hovering does nothing.
            await page.mouse.move(atom["x"], atom["y"])
            assert (await page.evaluate(drawn, atom["index"]))["hovered"] is None

            await page.locator("#pick-toolbar button[data-tool='info']").click()
            await page.mouse.move(atom["x"] + 1, atom["y"])
            await page.wait_for_function(
                f"() => window.ffastApp.renderer.hoveredAtom === {atom['index']}"
            )
            lit = await page.evaluate(drawn, atom["index"])
            assert lit["scale"] > base["scale"] * 1.1
            # A white atom (hydrogen) cannot get lighter, only larger.
            assert lit["light"] > base["light"] or base["light"] > 2.99

            # Off the molecule: the atom returns to how it was drawn.
            rect = await page.locator(CANVAS).bounding_box()
            await page.mouse.move(rect["x"] + 3, rect["y"] + 3)
            await page.wait_for_function("() => window.ffastApp.renderer.hoveredAtom === null")
            after = await page.evaluate(drawn, atom["index"])
            assert after["scale"] == pytest.approx(base["scale"])
            assert after["light"] == pytest.approx(base["light"])
        finally:
            await browser.close()


async def test_web_pick_radius_control_is_gone(ffast_web_server):
    """ADR 0055: picking by the drawn ball replaces the Pick radius setting."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            await page.locator("#pick-toolbar button[data-tool='info']").click()
            await expect(page.locator("#pick-strip")).not_to_have_class(re.compile(r"\bhidden\b"))
            await expect(page.locator("#pick-strip")).not_to_contain_text("radius")
            await expect(page.get_by_text("Pick radius")).to_have_count(0)
        finally:
            await browser.close()


async def test_web_picking_does_not_resize_the_3d_view(ffast_web_server):
    """The pick read-out used to wrap the pick bar onto a second line, shrinking
    the 3D view, so the molecule jumped under the pointer after the first pick
    and the next click missed its atom."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)
    size = """() => [document.querySelector('.tabpanel.active .panel-3d.focused canvas.view3d').clientWidth,
                     document.querySelector('.tabpanel.active .panel-3d.focused canvas.view3d').clientHeight,
                     document.getElementById('pick-bar').offsetHeight]"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            await page.locator("#pick-toolbar button[data-tool='info']").click()
            before = await page.evaluate(size)
            atom = await _front_atom(page)
            await page.mouse.click(atom["x"], atom["y"])
            await expect(page.locator("#pick-strip-count")).to_contain_text("1 picked")
            await expect(page.locator("#pick-readout")).not_to_have_text("")
            readout = page.locator("#pick-readout")
            assert await readout.get_attribute("title") == await readout.text_content()
            assert await page.evaluate(size) == before
        finally:
            await browser.close()


async def test_web_extract_creates_subset_dataset(ffast_web_server):
    """ADR 0045 issue 12 gate: typing indices and extracting creates a new
    subset dataset that appears in the dataset list."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            await expect(page.locator("#dataset-list .obj-row")).to_have_count(1)

            await _open_section(page, "Extract Subset")
            indices = page.locator(
                ".pane[data-pane='Extract Subset'] .ctl-row[data-label='Indices'] input"
            )
            await indices.fill("0 1 2")
            await page.locator(
                ".pane[data-pane='Extract Subset'] button", has_text="Extract as Subset Dataset"
            ).click()

            # The server materialises an AtomFilteredDataset and announces it via
            # REMOTE_DATASET_META → a second row appears in the dataset list.
            await expect(page.locator("#dataset-list .obj-row")).to_have_count(2, timeout=15000)
        finally:
            await browser.close()


async def test_web_alignment_pane_wires_kabsch_and_exclusive_modes(ffast_web_server):
    """ADR 0045 issue 13 gate: the Alignment pane drives the server alignment
    features (a VIEW_COMMAND is issued) and its two modes are mutually
    exclusive as in Qt. A rendered-orientation diff is not asserted here — the
    bundled example trajectory is a variable dataset (per-frame atom counts
    differ), for which Kabsch-to-frame-0 correctly no-ops; the wiring and the
    mode exclusivity are the parts this data can verify."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)

    page_errors: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)

            await _open_section(page, "Alignment")
            kabsch = page.locator(
                ".pane[data-pane='Alignment'] .ctl-row[data-label='Kabsch align'] input"
            )
            heavy_row = page.locator(
                ".pane[data-pane='Alignment'] .ctl-row[data-label='Heavy atoms only']"
            )
            atom_align = page.locator(
                ".pane[data-pane='Alignment'] .ctl-row[data-label='3-atom frame align'] input"
            )

            # Heavy-only is contextual on Kabsch (hidden until enabled).
            await expect(heavy_row).to_be_hidden()
            v0 = await page.evaluate("() => window.ffastApp._viewVersion")
            await kabsch.check()
            await expect(heavy_row).to_be_visible()
            v1 = await page.evaluate("() => window.ffastApp._viewVersion")
            assert v1 > v0, "checking Kabsch should issue a VIEW_COMMAND"

            # Enabling 3-atom mode auto-disables Kabsch (mutually exclusive).
            await atom_align.check()
            await expect(kabsch).not_to_be_checked()
            await expect(heavy_row).to_be_hidden()
            ref_row = page.locator(
                ".pane[data-pane='Alignment'] .ctl-row[data-label='Reference atoms']"
            )
            await expect(ref_row).to_be_visible()
        finally:
            await browser.close()

    assert not page_errors


# ── Phase 3: analysis plots + subbing (daily-driver milestone) ───────────────

# JS predicate: the named analysis panel in the active tab has rendered a Plotly
# plot with at least one plotted data point.
_PANEL_HAS_POINTS = """(title) => {
  const el = document.querySelector(
    `#tabpanels .tabpanel.active .analysis-panel[data-title="${title}"] .panel-plot`);
  return !!el && el.classList.contains('js-plotly-plot')
    && Array.isArray(el.data) && el.data.length
    && Array.isArray(el.data[0].x) && el.data[0].x.length > 0;
}"""


async def _open_analysis_tab(page, name):
    tab = page.locator("#tabbar .tab", has_text=name)
    await expect(tab).to_have_count(1)
    await tab.click()


async def test_web_analysis_scatter_renders(ffast_web_server):
    """ADR 0045 Phase 3 gate: the Basic Errors tab's true-vs-predicted scatter
    renders a Plotly plot fed by the metric channel."""
    ws_port, web_port = ffast_web_server
    dataset_fp, model_fp = await _preload_dataset_and_prediction(ws_port)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await expect(page.locator("#status")).to_contain_text("Connected")
            await page.locator(f"#dataset-list .obj-row[data-fp='{dataset_fp}']").click()
            await page.locator(f"#model-list .obj-row[data-fp='{model_fp}']").click()

            await _open_analysis_tab(page, "Basic Errors")
            await page.wait_for_function(
                _PANEL_HAS_POINTS, arg="Energy Scatter", timeout=25000)
        finally:
            await browser.close()


_SUBSETS = """() => [...window.ffastApp._datasets.entries()]
    .filter(([, m]) => m.is_sub).map(([fp, m]) => ({fp, name: m.name, n: m.n, active: m.active}))"""


def _sub_panel(title):
    return f"#tabpanels .tabpanel.active .analysis-panel[data-title='{title}']"


async def _zoom(page, title, x_range):
    await page.evaluate(
        """([sel, x]) => Plotly.relayout(document.querySelector(sel + ' .panel-plot'),
                                         {'xaxis.range': x})""",
        [_sub_panel(title), x_range])


async def test_web_sub_makes_a_subset_that_follows_the_zoom(ffast_web_server):
    """Ticking SUB on a plot makes a subset of the structures on screen, as on
    the desktop. Zooming moves it; unticking hides it; a density plot subs by
    value."""
    ws_port, web_port = ffast_web_server
    dataset_fp, model_fp = await _preload_dataset_and_prediction(ws_port)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await page.locator(f"#dataset-list .obj-row[data-fp='{dataset_fp}']").click()
            await page.locator(f"#model-list .obj-row[data-fp='{model_fp}']").click()
            await _open_analysis_tab(page, "Basic Errors")
            await page.wait_for_function(
                _PANEL_HAS_POINTS, arg="Energy MAE timeline", timeout=25000)

            await page.locator(f"{_sub_panel('Energy MAE timeline')} .sub-toggle input").check()
            await expect(page.locator("#dataset-list .obj-row")).to_have_count(2, timeout=15000)
            [sub] = await page.evaluate(_SUBSETS)
            assert sub["name"].startswith("Energy MAE timeline")

            await _zoom(page, "Energy MAE timeline", [10, 19.5])
            await page.wait_for_function(
                f"() => ({_SUBSETS})().some(s => s.n === 10)", timeout=15000)
            # The plot keeps its zoom: the subset moving does not redraw it.
            assert await page.evaluate(
                """(sel) => document.querySelector(sel + ' .panel-plot')._fullLayout.xaxis.range[0]""",
                _sub_panel("Energy MAE timeline")) == 10

            await page.locator(f"{_sub_panel('Energy MAE timeline')} .sub-toggle input").uncheck()
            await expect(page.locator("#dataset-list .obj-row")).to_have_count(1, timeout=15000)
            hidden = [s for s in await page.evaluate(_SUBSETS) if s["name"].startswith("Energy")]
            assert hidden[0]["active"] is False

            await page.wait_for_function(
                _PANEL_HAS_POINTS, arg="Forces MAE distribution", timeout=25000)
            await page.locator(f"{_sub_panel('Forces MAE distribution')} .sub-toggle input").check()
            await expect(page.locator("#dataset-list .obj-row")).to_have_count(2, timeout=15000)
            await _zoom(page, "Forces MAE distribution", [0, 0.02])
            await page.wait_for_function(
                f"""() => ({_SUBSETS})().some(s => s.name.startsWith('Forces MAE distribution')
                                                && s.n > 0 && s.n < 100)""", timeout=15000)
        finally:
            await browser.close()


async def test_web_custom_toml_tab_matches_builtin(ffast_web_server):
    """ADR 0045 Phase 3 gate: a project TOML [[visualization.tabs]] (the repo's
    'Dataset Fields (demo)' tab) appears in the tab bar with the same structure
    as a built-in tab and renders identically over the same layout channel."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await expect(page.locator("#status")).to_contain_text("Connected")
            await page.locator(f"#dataset-list .obj-row[data-fp='{dataset_fp}']").click()

            builtin = page.locator("#tabbar .tab", has_text="Basic Errors")
            custom = page.locator("#tabbar .tab", has_text="Dataset Fields")
            await expect(builtin).to_have_count(1)
            await expect(custom).to_have_count(1)
            # Same kind of tab node — a tab built from the layout, not a
            # bespoke one (identical DOM contract to a built-in).
            assert (await custom.get_attribute("data-tab")).startswith("tab-")
            assert (await builtin.get_attribute("data-tab")).startswith("tab-")

            # Its panels render over the same layout+metric channel (the custom
            # tab's field metric is per-frame and needs no prediction).
            await custom.click()
            await page.wait_for_function(
                _PANEL_HAS_POINTS, arg="Total charge per frame", timeout=25000)
        finally:
            await browser.close()


# ── Phase 4: export + session ────────────────────────────────────────────────

async def test_web_export_png_opaque_and_transparent_download(ffast_web_server):
    """ADR 0045 issue 19 gate: both PNG export buttons trigger a real browser
    download of a non-empty PNG produced from the WebGL canvas."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)

            await _open_section(page, "Export")
            opaque_btn = page.locator(
                ".pane[data-pane='Export'] button", has_text="Export PNG (opaque)")
            transparent_btn = page.locator(
                ".pane[data-pane='Export'] button", has_text="Export PNG (transparent)")

            async with page.expect_download() as dl_info:
                await opaque_btn.click()
            download = await dl_info.value
            data = Path(await download.path()).read_bytes()
            assert len(data) > 0
            assert data[:8] == b"\x89PNG\r\n\x1a\n"   # PNG magic bytes

            async with page.expect_download() as dl_info:
                await transparent_btn.click()
            download = await dl_info.value
            data2 = Path(await download.path()).read_bytes()
            assert len(data2) > 0
            assert data2[:8] == b"\x89PNG\r\n\x1a\n"
        finally:
            await browser.close()


async def test_web_export_subset_writes_extxyz_and_reports_path(ffast_web_server, tmp_path):
    """ADR 0045 issue 20 gate: exporting a plot-declared SubDataset writes an
    extxyz on the server containing exactly its structures, and the browser
    reports the written path."""
    ws_port, web_port = ffast_web_server
    dataset_fp, model_fp = await _preload_dataset_and_prediction(ws_port)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await expect(page.locator("#status")).to_contain_text("Connected")
            await page.locator(f"#dataset-list .obj-row[data-fp='{dataset_fp}']").click()
            await page.locator(f"#model-list .obj-row[data-fp='{model_fp}']").click()

            # Make a frame-index SubDataset with SUB and a zoom (the subbing
            # gate's gesture) — gives a known, real structure count to check
            # the exported file against.
            await _open_analysis_tab(page, "Basic Errors")
            await page.wait_for_function(
                _PANEL_HAS_POINTS, arg="Energy MAE timeline", timeout=25000)
            await page.locator(f"{_sub_panel('Energy MAE timeline')} .sub-toggle input").check()
            await _zoom(page, "Energy MAE timeline", [20, 44])
            await page.wait_for_function(
                f"() => ({_SUBSETS})().some(s => s.n === 25)", timeout=15000)
            [sub] = await page.evaluate(_SUBSETS)
            sub_fp, n_expected = sub["fp"], sub["n"]

            await page.locator(f"#dataset-list .obj-row[data-fp='{sub_fp}']").click()

            target = tmp_path / "sub.extxyz"
            await _file_menu(page, "Export Selected Dataset…")
            await page.locator("#path-input").fill(str(target))
            await page.locator("#path-ok").click()

            await expect(page.locator("#status")).to_contain_text(str(target), timeout=15000)
            await expect(page.locator("#status")).not_to_contain_text("failed")

            atoms = ase.io.read(str(target), index=":")
            assert len(atoms) == n_expected
        finally:
            await browser.close()


async def test_web_popout_opens_independent_live_controller(ffast_web_server):
    """ADR 0044 Phase 4 gate: the pop-out button opens its OWN WebSocket
    connection (the server advertises ``multi_client`` in HELLO_ACK) — a real
    second controller with its own view, not a same-tab BroadcastChannel
    mirror (ADR 0043). It replays the shared dataset, auto-selects the one the
    opener had open, and scrubbing its frame slider must not move the
    opener's — independent views over the shared Environment (ADR 0044)."""
    ws_port, web_port = ffast_web_server
    dataset_fp = await _preload_dataset(ws_port)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            await expect(page.locator("#popout-btn")).to_be_enabled()

            async with page.context.expect_page() as popup_info:
                await page.locator("#popout-btn").click()
            popup = await popup_info.value
            await popup.wait_for_load_state("networkidle")

            # Its own live controller connection — status/frame-slider are
            # live even though the object rail/toolbar chrome is hidden
            # (loupe-only), unlike the chrome-mirroring satellite fallback.
            await expect(popup.locator("#status")).to_contain_text("Connected")
            await expect(popup.locator("#overlay")).to_have_class(re.compile(r"\bhidden\b"))
            await expect(popup.locator("#frame-slider")).to_be_enabled()

            # Scrub the popped-out tab to a different frame...
            await popup.locator("#frame-slider").evaluate(
                """(slider) => {
                  slider.value = '1';
                  slider.dispatchEvent(new Event('input', { bubbles: true }));
                }"""
            )
            await expect(popup.locator("#frame-label")).to_contain_text("1 /")

            # ...the opener's own view stays put — independent views, not a
            # mirror of one shared view.
            await expect(page.locator("#frame-label")).to_contain_text("0 /")
        finally:
            await browser.close()


async def test_web_save_and_load_session_restores_dataset(tmp_path):
    """ADR 0045 issue 21 gate: a session saved from one server process is
    restored by a *fresh* second process with nothing pre-loaded — the true
    proof of a working round trip, not just "the dataset was already there"."""
    session_dir = tmp_path / "session"

    async with _spawn_server() as (ws_port_a, web_port_a):
        dataset_fp = await _preload_dataset(ws_port_a)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1100, "height": 760})
            try:
                await _open_loupe(page, ws_port_a, web_port_a, dataset_fp)

                await _file_menu(page, "Save Session…")
                await page.locator("#path-input").fill(str(session_dir))
                await page.locator("#path-ok").click()

                # SESSION_SAVED carries {ok, path} and is what produces this
                # status (ADR 0050). It replaced inferring completion from the
                # next TASK_DONE, which names no operation — so an unrelated
                # task finishing first reported itself as the save.
                await expect(page.locator("#status")).to_contain_text("Saved session", timeout=15000)
                assert (session_dir / "info.json").exists()
                # Layout state stays in the browser, never in the session (ADR 0055).
                saved = "".join(
                    f.read_text(errors="ignore") for f in session_dir.rglob("*") if f.is_file()
                    and f.suffix in (".json", ".toml", ".txt")
                )
                assert "openSection" not in saved and "sidebarHidden" not in saved
            finally:
                await browser.close()

    # A brand-new server process — its Environment has never loaded anything;
    # any dataset it shows can only have come from LOAD_SESSION.
    async with _spawn_server() as (ws_port_b, web_port_b):
        await _wait_for_server_ready(ws_port_b)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1100, "height": 760})
            try:
                await page.goto(f"http://127.0.0.1:{web_port_b}/?port={ws_port_b}", wait_until="networkidle")
                await expect(page.locator("#status")).to_contain_text("Connected")
                await expect(page.locator("#dataset-list .obj-row")).to_have_count(0)

                await _file_menu(page, "Load Session…")
                await page.locator("#path-input").fill(str(session_dir))
                await page.locator("#path-ok").click()

                await expect(page.locator("#dataset-list .obj-row")).to_have_count(1, timeout=15000)
                await expect(page.locator("#status")).to_contain_text("Loaded session")
                restored_meta = await page.evaluate(
                    """() => { const [, m] = [...window.ffastApp._datasets][0]; return m; }"""
                )
                # DatasetLoader.initialise() names an object from its path
                # basename with the extension stripped (utils.removeExtension).
                assert restored_meta["name"] == "dataset"
            finally:
                await browser.close()

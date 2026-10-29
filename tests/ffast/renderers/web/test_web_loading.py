"""The web loader against a real server: loader types, energy/force keys,
every Nth frame, .npz predictions, models (web parity plan 2026-10-29)."""
import re

import ase.io
import numpy as np
import pytest
from playwright.async_api import async_playwright, expect

from .test_web_runtime import (
    DATASET_PATH, PREDICTION_PATH, REPO_ROOT, _RECORD_SENDS, _expect_connected,
    _file_menu, _open_loupe, _preload_dataset, _preload_uniform_dataset,
    _spawn_server, _wait_for_server_ready,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def ffast_web_server():
    async with _spawn_server() as ports:
        yield ports


async def _go_to(page, folder):
    """In the open loader, list `folder`."""
    await page.locator("#fb-path").fill(str(folder))
    await page.locator("#fb-path").press("Enter")


async def _sent(page, event):
    """The kwargs of every `event` the page sent since _RECORD_SENDS ran."""
    return await page.evaluate(
        "(e) => window.__sent.filter(([name]) => name === e).map(([, kw]) => kw)", event)


async def test_web_the_type_list_is_the_servers_and_follows_the_file(ffast_web_server, tmp_path):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    np.savez(tmp_path / "m.npz", x=np.zeros(1))
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await _expect_connected(page)
            await _file_menu(page, "Load Dataset…")
            values = await page.locator("#fb-type option").evaluate_all("(os) => os.map((o) => o.value)")
            assert values == ["ase (auto)", "sGDML"]
            await _go_to(page, tmp_path)
            await page.locator("#fb-list .fb-row", has_text="m.npz").click()
            await expect(page.locator("#fb-type")).to_have_value("sGDML")
            ase.io.write(tmp_path / "a.xyz", ase.io.read(DATASET_PATH, index=0))
            await _go_to(page, tmp_path)
            await page.locator("#fb-list .fb-row", has_text="a.xyz").click()
            await expect(page.locator("#fb-type")).to_have_value("ase (auto)")
            await page.locator("#fb-type").select_option("sGDML")
            await expect(page.locator("#fb-type")).to_have_value("sGDML")
        finally:
            await browser.close()

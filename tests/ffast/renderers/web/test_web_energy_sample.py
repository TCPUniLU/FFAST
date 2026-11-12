"""The load dialog samples a file by energy, with a histogram preview
(ADR 0096, step 4), against a real server."""
import re
import shutil

import numpy as np
import pytest
from playwright.async_api import async_playwright, expect

from .test_web_loading import _go_to, _sent, ffast_web_server
from .test_web_runtime import (
    DATASET_PATH, _RECORD_SENDS, _expect_connected, _file_menu, _shared_servers,
    _wait_for_server_ready, shared_server,
)

pytestmark = pytest.mark.integration

_SVG = "#fb-sample-plot svg"


async def _open(p, web_port, ws_port, folder, name):
    browser = await p.chromium.launch(headless=True)
    page = await browser.new_page(viewport={"width": 1200, "height": 820})
    await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
    await _expect_connected(page)
    await page.evaluate(_RECORD_SENDS)
    await _file_menu(page, "Load Dataset…")
    await _go_to(page, folder)
    await page.locator("#fb-list .fb-row", has_text=name).click()
    return browser, page


async def test_web_sample_by_energy_previews_follows_n_and_loads_n_frames(ffast_web_server, tmp_path):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    shutil.copy(DATASET_PATH, tmp_path / "spice.xyz")          # 100 frames of different sizes
    async with async_playwright() as p:
        browser, page = await _open(p, web_port, ws_port, tmp_path, "spice.xyz")
        try:
            option = page.locator("#fb-frames option[value='energy']")
            await expect(option).to_be_enabled(timeout=15000)
            await expect(page.locator("#fb-sample")).to_be_hidden()
            await page.locator("#fb-frames").select_option("energy")
            await expect(page.locator("#fb-sample")).to_be_visible()
            await expect(page.locator("#fb-stride")).to_be_hidden()
            # a tenth of the file to start with, and the bars of what it keeps
            await expect(page.locator("#fb-sample-n")).to_have_value("10", timeout=15000)
            svg = page.locator(_SVG)
            await expect(svg).to_have_attribute("data-kept", "10", timeout=15000)
            await expect(svg).to_contain_text("Energy per atom")
            assert await svg.locator(".fb-h-all").count() > 0
            await expect(page.locator("#fb-sample-bins")).to_have_attribute(
                "placeholder", re.compile(r"^auto \(\d+\)$"))

            await page.locator("#fb-sample-n").fill("30")
            await expect(svg).to_have_attribute("data-kept", "30")
            await expect(page.locator("#fb-sample-note")).to_contain_text("Keeps 30 of 100 frames")

            await page.locator("#fb-load").click()
            [kwargs] = await _sent(page, "LOAD_DATASET")
            assert kwargs == {"sampling": {"method": "even", "n": 30, "bins": None, "seed": 42,
                                           "energy": "REF_energy"}}
            await page.wait_for_function(
                "() => [...window.ffastApp._datasets.values()].some((m) => m.n === 30 && m.sampling)",
                timeout=30000)
            row = page.locator("#dataset-list .obj-row", has_text="spice")
            await expect(row.locator(".meta")).to_have_attribute(
                "title", "30 of 100 frames, sampled by energy (even, seed 42)")
            await row.locator(".obj-menu-btn").click()
            await page.locator(".rail-menu [data-action='dataset-info']").click()
            await expect(page.locator("#info-dialog tr", has_text="Frames")).to_contain_text(
                "30 of 100 frames, sampled by energy (even, seed 42)")
        finally:
            await browser.close()


async def test_web_every_nth_and_sample_by_energy_cannot_both_be_on(ffast_web_server, tmp_path):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    shutil.copy(DATASET_PATH, tmp_path / "spice.xyz")
    async with async_playwright() as p:
        browser, page = await _open(p, web_port, ws_port, tmp_path, "spice.xyz")
        try:
            await expect(page.locator("#fb-frames option[value='energy']")).to_be_enabled(timeout=15000)
            await page.locator("#fb-frames").select_option("nth")
            await page.locator("#fb-stride").fill("10")
            await page.locator("#fb-frames").select_option("energy")
            await expect(page.locator("#fb-stride")).to_be_hidden()
            await expect(page.locator(_SVG)).to_have_attribute("data-kept", "10", timeout=15000)
            await page.locator("#fb-load").click()
            [kwargs] = await _sent(page, "LOAD_DATASET")
            assert "slice_num" not in kwargs and kwargs["sampling"]["n"] == 10
        finally:
            await browser.close()


@pytest.mark.parametrize("name", ["no_energy.xyz", "m.npz"])
async def test_web_sample_by_energy_is_greyed_without_energies_to_sample_by(shared_server, tmp_path, name):
    ws_port, web_port = shared_server
    await _wait_for_server_ready(ws_port)
    (tmp_path / "no_energy.xyz").write_text(
        "1\nProperties=species:S:1:pos:R:3\nH 0 0 0\n1\nProperties=species:S:1:pos:R:3\nH 0 0 1\n")
    np.savez(tmp_path / "m.npz", x=np.zeros(1))                # sGDML: a Level 1 reader
    async with async_playwright() as p:
        browser, page = await _open(p, web_port, ws_port, tmp_path, name)
        try:
            option = page.locator("#fb-frames option[value='energy']")
            # the key check has answered for this file
            await page.wait_for_function("() => window.ffastApp._browser._probing === null", timeout=15000)
            await expect(option).to_be_disabled()
            await expect(page.locator("#fb-frames")).to_have_attribute(
                "title", "Sample by energy needs a reader that lists each frame's energy, "
                         "and a file with energies")
        finally:
            await browser.close()

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


def _two_key_file(tmp_path):
    """10 frames with REF_ and MACE_ energies and forces."""
    frames = ase.io.read(DATASET_PATH, index=":10")
    for a in frames:
        a.info["MACE_energy"] = a.info["REF_energy"] + 0.1
        a.arrays["MACE_forces"] = a.arrays["REF_forces"] + 0.01
    path = tmp_path / "two_keys.xyz"
    ase.io.write(path, frames, format="extxyz")
    return path


async def test_web_a_dataset_with_two_keys_asks_which_one(ffast_web_server, tmp_path):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    _two_key_file(tmp_path)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await _expect_connected(page)
            await page.evaluate(_RECORD_SENDS)
            await _file_menu(page, "Load Dataset…")
            await _go_to(page, tmp_path)
            await page.locator("#fb-list .fb-row", has_text="two_keys.xyz").click()
            await expect(page.locator("#fb-keys")).to_be_visible()
            await expect(page.locator("#fb-ds-energy-key")).to_have_value("REF_energy")
            options = await page.locator("#fb-ds-energy-key option").evaluate_all(
                "(os) => os.map((o) => o.value)")
            assert "MACE_energy" in options
            await page.locator("#fb-ds-energy-key").select_option("MACE_energy")
            await page.locator("#fb-ds-force-key").select_option("MACE_forces")
            await page.locator("#fb-load").click()
            await expect(page.locator("#dataset-list .obj-row")).to_have_count(1, timeout=30000)
            assert await _sent(page, "LOAD_DATASET") == [
                {"selected_energy_key": "MACE_energy", "selected_force_key": "MACE_forces"}]
            await _file_menu(page, "Load Dataset…")
            await _go_to(page, DATASET_PATH.parent)
            await page.locator("#fb-list .fb-row", has_text="dataset.xyz").click()
            await expect(page.locator("#fb-load")).to_be_enabled()
            await expect(page.locator("#fb-keys")).to_be_hidden()
            await page.locator("#fb-load").click()
            assert (await _sent(page, "LOAD_DATASET"))[-1] == {}
        finally:
            await browser.close()


async def test_web_a_recent_dataset_loads_at_once_with_default_keys(ffast_web_server, tmp_path):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    _two_key_file(tmp_path)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await _expect_connected(page)
            await _file_menu(page, "Load Dataset…")
            await _go_to(page, tmp_path)
            await page.locator("#fb-list .fb-row", has_text="two_keys.xyz").click()
            await expect(page.locator("#fb-keys")).to_be_visible()
            await page.locator("#fb-load").click()
            await expect(page.locator("#dataset-list .obj-row")).to_have_count(1, timeout=30000)
            await page.evaluate(_RECORD_SENDS)
            await _file_menu(page, "Load Dataset…")
            await _go_to(page, DATASET_PATH.parent)
            await page.locator("#fb-list .fb-row", has_text="dataset.xyz").click()
            await page.locator(
                "#fb-recent .fb-recent-row", has_text=str(tmp_path / "two_keys.xyz")).click()
            await expect(page.locator("#fb-modal")).to_be_hidden()
            assert await _sent(page, "LOAD_DATASET") == [{}]
            assert len(await _sent(page, "PROBE_DATASET_KEYS")) == 1
        finally:
            await browser.close()


async def test_web_more_keys_of_a_file_load_as_predictions(ffast_web_server, tmp_path):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    _two_key_file(tmp_path)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 900})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await _expect_connected(page)
            await page.evaluate(_RECORD_SENDS)
            await _file_menu(page, "Load Dataset…")
            await _go_to(page, tmp_path)
            await page.locator("#fb-list .fb-row", has_text="two_keys.xyz").click()
            await expect(page.locator("#fb-keys")).to_be_visible()
            await page.locator("#fb-add-pred").click()
            rows = page.locator("#fb-pred-rows .fb-pred-row")
            await expect(rows.locator(".fb-pred-energy")).to_have_value("MACE_energy")
            await expect(rows.locator(".fb-pred-force")).to_have_value("MACE_forces")
            await expect(rows.locator(".fb-pred-name")).to_have_value("MACE")
            await page.locator("#fb-add-pred").click()
            await expect(rows).to_have_count(2)
            await rows.nth(1).get_by_role("button", name="Remove").click()
            await expect(rows).to_have_count(1)
            await page.locator("#fb-load").click()
            await expect(page.locator("#model-list .obj-row", has_text="MACE")).to_be_visible(timeout=30000)
            assert await _sent(page, "LOAD_DATASET") == [{
                "selected_energy_key": "REF_energy", "selected_force_key": "REF_forces",
                "prediction_keys": [["MACE_energy", "MACE_forces", "MACE"]]}]
        finally:
            await browser.close()


async def test_web_every_nth_frame_and_the_large_file_note(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 900})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await _expect_connected(page)
            await page.evaluate(_RECORD_SENDS)
            await page.evaluate("() => { window.ffastApp._browser._largeBytes = 1; }")
            await _file_menu(page, "Load Dataset…")
            await _go_to(page, DATASET_PATH.parent)
            await page.locator("#fb-list .fb-row", has_text="dataset.xyz").click()
            await expect(page.locator("#fb-large")).to_contain_text("100 frames", timeout=15000)
            await page.locator("#fb-stride").fill("10")
            await expect(page.locator("#fb-large")).to_contain_text("~10")
            await page.locator("#fb-load").click()
            assert (await _sent(page, "LOAD_DATASET"))[-1] == {"slice_num": 10}
            await page.wait_for_function(
                "() => [...window.ffastApp._datasets.values()].some((m) => m.n === 10)", timeout=30000)
        finally:
            await browser.close()


async def test_web_an_npz_prediction_loads(ffast_web_server, tmp_path):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    dataset_fp = await _preload_uniform_dataset(ws_port, tmp_path)
    n_atoms = len(ase.io.read(DATASET_PATH, index=0))
    np.savez(tmp_path / "pred.npz", E=np.zeros(5), F=np.zeros((5, n_atoms, 3)))
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            await _open_loupe(page, ws_port, web_port, dataset_fp)
            await page.evaluate(_RECORD_SENDS)
            await _file_menu(page, "Load Prediction…")
            await _go_to(page, tmp_path)
            await page.locator("#fb-list .fb-row", has_text="pred.npz").click()
            await expect(page.locator("#fb-force-key")).to_have_text("from the .npz file")
            await expect(page.locator("#fb-load")).to_be_enabled()
            await page.locator("#fb-load").click()
            await expect(page.locator("#model-list .obj-row", has_text="pred.npz")).to_be_visible(timeout=30000)
            assert await _sent(page, "PROBE_DATASET_KEYS") == []
        finally:
            await browser.close()


async def test_web_a_model_that_cannot_load_says_why(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await _expect_connected(page)
            await page.evaluate(_RECORD_SENDS)
            await _file_menu(page, "Load Model…")
            await expect(page.locator("#fb-title")).to_have_text("Load Model")
            values = await page.locator("#fb-model-type option").evaluate_all(
                "(os) => os.map((o) => o.value)")
            assert "sGDML" in values
            await _go_to(page, REPO_ROOT)
            await page.locator("#fb-list .fb-row", has_text="README.md").click()
            await page.locator("#fb-model-type").select_option("sGDML")
            await page.locator("#fb-load").click()
            status = page.locator("#status")
            await expect(status).to_contain_text("Could not load README.md", timeout=30000)
            await expect(status).to_have_class(re.compile(r"\berror\b"))
            assert await _sent(page, "LOAD_MODEL") == [{}]
            await page.wait_for_timeout(1500)
            await expect(page.locator("#toasts .toast")).to_have_count(1)
        finally:
            await browser.close()

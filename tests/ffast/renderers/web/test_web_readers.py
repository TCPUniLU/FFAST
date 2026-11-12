"""A reader from a pip package shows in the Type list and loads a dataset (ADR 0095).

The toy reader is installed the way an outside reader is: a ``.dist-info``
folder with an ``ffast.readers`` entry point, on the server process's path.
"""
import os

import pytest
from playwright.async_api import async_playwright, expect

from tests.ffast.readers.toy_reader import make_frames, write_toy

from .test_web_runtime import _expect_connected, _file_menu, _spawn_server, _wait_for_server_ready

pytestmark = pytest.mark.integration


@pytest.fixture
def toy_reader_installed(tmp_path, monkeypatch):
    """Make the toy reader findable by ``entry_points`` in a spawned server."""
    dist = tmp_path / "site" / "ffast_toy-0.1.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: ffast-toy\nVersion: 0.1\n")
    (dist / "entry_points.txt").write_text(
        "[ffast.readers]\ntoy = tests.ffast.readers.toy_reader:ToyReader\n")
    old = os.environ.get("PYTHONPATH")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, [str(tmp_path / "site"), old])))


async def test_web_a_readers_type_is_listed_and_loads_a_dataset(toy_reader_installed, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    write_toy(data / "run.toy", make_frames(6, variable=True))
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1200, "height": 820})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _file_menu(page, "Load Dataset…")
                values = await page.locator("#fb-type option").evaluate_all(
                    "(os) => os.map((o) => o.value)")
                assert values == ["ase (auto)", "sGDML", "toy"]
                await page.locator("#fb-path").fill(str(data))
                await page.locator("#fb-path").press("Enter")
                await page.locator("#fb-list .fb-row", has_text="run.toy").click()
                await expect(page.locator("#fb-type")).to_have_value("toy")
                await expect(page.locator("#fb-keys")).to_be_hidden()
                await page.locator("#fb-load").click()
                row = page.locator("#dataset-list .obj-row")
                await expect(row).to_have_count(1, timeout=30000)
                await expect(row).to_contain_text("run")
            finally:
                await browser.close()

"""The guided tour in the real web client (ADR 0088): the events the app sends
the tours, and the "First steps" tour on the example pair."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("playwright.async_api")

from playwright.async_api import async_playwright, expect  # noqa: E402

from tests.ffast.renderers.web.test_web_runtime import (  # noqa: E402,F401
    _expect_connected, _wait_for_server_ready, ffast_web_server,
)

pytestmark = pytest.mark.integration

# Collects every tour event the app sends into `window.got`.
_COLLECT = """() => {
  window.got = [];
  for (const name of ['example-loaded', 'example-load-failed', 'dataset-deleted',
                      'frame-changed', 'quick-style-applied']) {
    window.ffastApp.tourEvents.addEventListener(name, (e) => window.got.push({ name, detail: e.detail }));
  }
}"""


async def _open(page, ws_port, web_port):
    await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
    await _expect_connected(page)


async def _load_example_watched(page):
    """Click Load Example and return the example-loaded event's detail."""
    await page.evaluate(_COLLECT)
    await page.locator("#empty-example-btn").click()
    await page.wait_for_function(
        "() => window.got.some((g) => g.name === 'example-loaded')", timeout=30000)
    return await page.evaluate("() => window.got.find((g) => g.name === 'example-loaded').detail")


async def test_the_app_tells_tours_the_example_loaded_the_frame_moved_and_a_style_applied(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open(page, ws_port, web_port)
            loaded = await _load_example_watched(page)
            assert await page.evaluate(
                "(d) => window.ffastApp._datasets.has(d.datasetFp) && window.ffastApp._models.has(d.modelFp)",
                loaded)

            slider = page.locator("#frame-slider")
            await expect(slider).to_be_enabled()
            await slider.fill("3")
            await page.wait_for_function("() => window.got.some((g) => g.name === 'frame-changed')")

            force = page.locator('#quick-styles [data-action="style-force"]')
            await expect(force).to_be_enabled()
            await force.click()
            await page.wait_for_function(
                "() => window.got.some((g) => g.name === 'quick-style-applied' && g.detail.id === 'style-force')")
        finally:
            await browser.close()


async def test_remove_loaded_deletes_the_example_pair_and_the_empty_screen_returns(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open(page, ws_port, web_port)
            loaded = await _load_example_watched(page)
            await asyncio.wait_for(page.evaluate(
                "(d) => window.ffastApp._removeLoaded(d.datasetFp, d.modelFp)", loaded), timeout=30)
            assert await page.evaluate(
                "() => window.ffastApp._datasets.size + window.ffastApp._models.size") == 0
            assert await page.evaluate(
                "(d) => window.got.some((g) => g.name === 'dataset-deleted' && g.detail.fp === d.datasetFp)",
                loaded)
            await expect(page.locator("#empty-example-btn")).to_be_visible()
        finally:
            await browser.close()

"""The guided tour in the real web client (ADR 0088): the events the app sends
the tours, and the "First steps" tour on the example pair."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("playwright.async_api")

from playwright.async_api import async_playwright, expect  # noqa: E402

from tests.ffast.renderers.web.test_web_runtime import (  # noqa: E402,F401
    _expect_connected, _preload_dataset, _wait_for_server_ready, ffast_web_server,
)

pytestmark = pytest.mark.integration

# Collects every tour event the app sends into `window.got`.
_COLLECT = """() => {
  window.got = [];
  for (const name of ['example-loaded', 'example-load-failed', 'dataset-deleted',
                      'frame-changed', 'quick-style-applied']) {
    // `drawn`: the 3D view and its controls were on screen when it came.
    window.ffastApp.tourEvents.addEventListener(name, (e) => window.got.push({ name, detail: e.detail,
      drawn: !document.getElementById('content').classList.contains('no-view') }));
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
    loaded = await page.evaluate("() => window.got.find((g) => g.name === 'example-loaded')")
    assert loaded["drawn"], "example-loaded came before the view was drawn"
    return loaded["detail"]


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


# ── First steps (ADR 0088) ─────────────────────────────────────────────────

_TITLES = [
    "Load the example", "One frame of the dataset", "Move through frames", "What is loaded",
    "Show where the model is wrong", "Read the colours", "Next steps",
]


async def _start_first_steps(page, ws_port, web_port):
    await _open(page, ws_port, web_port)
    await page.locator("#empty-tour-btn").click()
    await expect(page.locator(".tour-title")).to_have_text(_TITLES[0])


async def _through_step_1(page):
    await page.locator("#empty-example-btn").click()
    await expect(page.locator(".tour-title")).to_have_text(_TITLES[1], timeout=30000)


async def test_first_steps_clicks_through_from_the_empty_screen(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _start_first_steps(page, ws_port, web_port)
            await expect(page.locator(".tour-count")).to_have_text("Step 1 of 7")
            await _through_step_1(page)
            title, nxt = page.locator(".tour-title"), page.locator(".tour-next")
            await nxt.click()
            await expect(title).to_have_text(_TITLES[2])
            # A "do this" step: the slider has focus, and moving it moves on.
            await expect(page.locator("#frame-slider")).to_be_focused()
            await page.keyboard.press("ArrowRight")
            await expect(title).to_have_text(_TITLES[3])
            await nxt.click()
            await expect(title).to_have_text(_TITLES[4])
            await page.locator('#quick-styles [data-action="style-force"]').click()
            await expect(title).to_have_text(_TITLES[5])
            await nxt.click()
            await expect(title).to_have_text(_TITLES[6])
            await expect(nxt).to_have_text("Done")
            await nxt.click()
            await expect(page.locator(".tour-layer")).to_have_count(0)
            await expect(page.locator("#status")).to_contain_text("Tour finished")
        finally:
            await browser.close()


async def test_first_steps_locks_the_rest_and_back_undoes_the_example(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _start_first_steps(page, ws_port, web_port)
            await _through_step_1(page)
            # Locked: a click on the File menu button hits the shade.
            box = await page.locator("#file-menu-btn").bounding_box()
            await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            await expect(page.locator("#file-menu-list")).to_be_hidden()

            # Back deletes the pair this tour loaded, and step 1 waits again.
            await page.locator(".tour-back").click()
            await page.wait_for_function("() => window.ffastApp._datasets.size === 0", timeout=30000)
            await expect(page.locator(".tour-title")).to_have_text(_TITLES[0])
            await expect(page.locator(".tour-next")).to_be_hidden()
            await _through_step_1(page)
        finally:
            await browser.close()


async def test_every_target_of_every_tour_is_on_screen_with_the_example(ffast_web_server):
    """A renamed control breaks this test, not the tour."""
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open(page, ws_port, web_port)
            await page.locator("#empty-example-btn").click()
            await page.wait_for_function(
                "() => window.ffastApp._datasets.size === 1 && window.ffastApp._models.size === 1",
                timeout=30000)
            await expect(page.locator("#hint-bar")).to_be_visible()
            found = await page.evaluate("""async () => {
              const frame = () => new Promise((r) => requestAnimationFrame(() => r()));
              const out = [];
              for (const tour of window.ffastApp._tours) {
                for (const step of tour.steps) {
                  step.prepare?.();
                  await frame();
                  step.targets.forEach((t, k) => {
                    const el = typeof t === 'function' ? t() : document.querySelector(t);
                    const r = el?.getBoundingClientRect();
                    out.push({ tour: tour.id, step: step.title, k, target: String(t),
                               inDom: !!el?.isConnected, sized: !!r && r.width > 0 && r.height > 0 });
                  });
                }
              }
              return out;
            }""")
            assert len(found) >= 9
            for t in found:
                assert t["inDom"], t
                if t["step"] != "Load the example":   # its button is hidden once a view is open
                    assert t["sized"], t
        finally:
            await browser.close()


async def test_first_steps_is_unavailable_with_a_dataset_and_no_prediction(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    await _preload_dataset(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open(page, ws_port, web_port)
            await page.wait_for_function("() => window.ffastApp._currentDatasetFp")
            await page.locator("#file-menu-btn").click()
            item = page.locator("#file-menu-list").get_by_role("menuitem", name="Tour: First steps")
            await expect(item).to_be_disabled()
            await expect(item).to_have_attribute("title", "Open a dataset with a prediction first, or start with nothing loaded")
        finally:
            await browser.close()


async def test_escape_skips_first_steps(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _start_first_steps(page, ws_port, web_port)
            await _through_step_1(page)
            await page.locator(".tour-next").click()
            await expect(page.locator(".tour-title")).to_have_text(_TITLES[2])
            await page.keyboard.press("Escape")
            await expect(page.locator(".tour-layer")).to_have_count(0)
            await expect(page.locator("#status")).to_contain_text("Tour skipped")
        finally:
            await browser.close()


async def test_step_1_holds_while_the_example_dataset_is_in_and_its_prediction_is_not(ffast_web_server):
    """The dataset opens the 3D view, which hides Load Example, before the
    prediction arrives. Step 1 then lights the view instead of stopping."""
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _start_first_steps(page, ws_port, web_port)
            # As if Load Example were asked for, and only its dataset came in.
            await page.evaluate("() => { window.ffastApp._exampleWatch = { datasetFp: null }; }")
            await _preload_dataset(ws_port)
            await expect(page.locator("#empty-example-btn")).to_be_hidden()
            await page.evaluate(
                "() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
            await expect(page.locator(".tour-title")).to_have_text(_TITLES[0])
            await expect(page.locator(".tour-wait")).to_have_text("Click Load Example")
        finally:
            await browser.close()

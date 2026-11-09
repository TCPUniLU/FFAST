"""The guided tour in the real web client (ADR 0088): the events the app sends
the tours, and the "First steps" tour on the example pair."""

from __future__ import annotations

import asyncio
import re

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
    "Show where the model is wrong", "Read the colours", "Open the error plots",
    "Errors over the whole dataset", "Open a bad frame", "The frame you clicked", "Next steps",
]

# Where the highest point of the first half of the first curve in a plot on
# screen is, in page pixels, and its frame: for a real mouse click through the
# tour's lit area. The second half can sit under Plotly's toolbar.
_HIGHEST_POINT = """(title) => {
  const el = document.querySelector(
    `#tabpanels .tabpanel.active .analysis-panel[data-title="${title}"] .panel-plot`);
  const tr = el.data[0], ys = Array.from(tr.y), xs = Array.from(tr.x);
  const half = ys.slice(0, Math.floor(ys.length / 2));
  const i = half.indexOf(Math.max(...half.filter(Number.isFinite)));
  const fl = el._fullLayout, r = el.getBoundingClientRect();
  return { x: r.left + fl._size.l + fl.xaxis.l2p(xs[i]), y: r.top + fl._size.t + fl.yaxis.l2p(ys[i]),
           frame: xs[i] };
}"""

_PLOT_DRAWN = """(title) => {
  const el = document.querySelector(
    `#tabpanels .tabpanel.active .analysis-panel[data-title="${title}"] .panel-plot`);
  return !!el && el.classList.contains('js-plotly-plot') && Array.isArray(el.data)
    && el.data.length > 0 && el.data[0].x && el.data[0].x.length > 0;
}"""


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
            await expect(page.locator(".tour-count")).to_have_text("Step 1 of 11")
            await _through_step_1(page)
            title, nxt = page.locator(".tour-title"), page.locator(".tour-next")
            await nxt.click()
            await expect(title).to_have_text(_TITLES[2])
            # A "do this" step: the slider has focus. Moving it shows Next, and
            # the frames can still be changed until Next is pressed.
            slider = page.locator("#frame-slider")
            await expect(slider).to_be_focused()
            await page.keyboard.press("ArrowRight")
            await expect(nxt).to_be_visible()
            await page.keyboard.press("ArrowRight")
            await expect(slider).to_have_value("2")
            await expect(title).to_have_text(_TITLES[2])
            await nxt.click()
            await expect(title).to_have_text(_TITLES[3])
            await nxt.click()
            await expect(title).to_have_text(_TITLES[4])
            await page.locator('#quick-styles [data-action="style-force"]').click()
            await expect(title).to_have_text(_TITLES[5])
            await nxt.click()
            # Basic Errors: open the tab, look, click a point, see its frame.
            await expect(title).to_have_text(_TITLES[6])
            await page.locator('#tabbar .tab[data-name="Basic Errors"]').click()
            await expect(title).to_have_text(_TITLES[7])
            await nxt.click()
            await expect(title).to_have_text(_TITLES[8])
            await page.wait_for_function(_PLOT_DRAWN, arg="Forces MAE timeline", timeout=30000)
            point = await page.evaluate(_HIGHEST_POINT, "Forces MAE timeline")
            await page.mouse.move(point["x"], point["y"])
            await page.mouse.click(point["x"], point["y"])
            await expect(title).to_have_text(_TITLES[9])
            await expect(page.locator("#frame-label")).to_have_text(re.compile(rf"^{point['frame']} /"))
            await nxt.click()
            await expect(title).to_have_text(_TITLES[10])
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
              // LAMMPS tour targets need its practice run, covered by
              // test_web_lammps_tour.py rather than this example pair.
              for (const tour of window.ffastApp._tours.filter((t) => !t.lammps)) {
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
            assert {t["tour"] for t in found} == {"first-steps", "load-data"}
            for t in found:
                assert t["inDom"], t
                if t["step"] == "Load the example":   # its button is hidden once a view is open
                    continue
                if "#fb-keys" in t["target"]:   # shown only when a file offers a choice of columns
                    continue
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


# ── Loading signals (the loading tour, plan 2026-11-06) ────────────────────

# Collects the loading tour's events into `window.seen`.
_COLLECT_LOADING = """() => {
  window.seen = [];
  for (const name of ['browser-opened', 'folder-listed', 'file-selected', 'target-changed',
                      'browser-cancelled', 'dataset-loaded', 'prediction-loaded', 'load-failed',
                      'load-duplicate', 'series-changed']) {
    window.ffastApp.tourEvents.addEventListener(name, (e) => window.seen.push({ name, detail: e.detail }));
  }
}"""


async def _seen(page, name, timeout=30000):
    """The detail of the last `name` event seen, once there is one."""
    await page.wait_for_function(
        "(n) => window.seen.some((g) => g.name === n)", arg=name, timeout=timeout)
    return await page.evaluate(
        "(n) => window.seen.filter((g) => g.name === n).pop().detail", name)


async def _row(page, name):
    return page.locator("#fb-list .fb-row", has=page.locator(".name", has_text=re.compile(f"^{re.escape(name)}/?$")))


async def test_the_browser_tells_tours_about_folders_files_and_loads(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open(page, ws_port, web_port)
            await page.wait_for_function("() => window.ffastApp._examples !== null")
            await page.evaluate(_COLLECT_LOADING)
            root = await page.evaluate("() => window.ffastApp._examples.folder")
            folder = root + "/variable-sized molecular"

            await page.evaluate("(f) => window.ffastApp._browser.open('dataset', { folder: f })", root)
            assert (await _seen(page, "browser-opened"))["mode"] == "dataset"
            await expect(page.locator("#fb-path")).to_have_value(root)
            await (await _row(page, "variable-sized molecular")).click()
            listed = await _seen(page, "folder-listed")
            assert listed == {"mode": "dataset", "path": folder}
            assert await page.evaluate("() => window.ffastApp._browser.folder()") == folder
            await (await _row(page, "dataset.xyz")).click()
            assert await _seen(page, "file-selected") == {
                "mode": "dataset", "name": "dataset.xyz", "path": folder + "/dataset.xyz"}
            await expect(page.locator("#fb-load")).to_be_enabled()
            await page.locator("#fb-load").click()
            fp = (await _seen(page, "dataset-loaded"))["fp"]
            assert await page.evaluate("(fp) => window.ffastApp._datasets.has(fp)", fp)
            assert await page.evaluate("() => window.ffastApp._browser.mode()") is None

            # The prediction dialog: its dataset choice, then Cancel.
            await page.evaluate("(f) => window.ffastApp._browser.open('prediction', { folder: f })", folder)
            assert (await _seen(page, "browser-opened"))["mode"] == "prediction"
            await page.evaluate("""() => { const s = document.getElementById('fb-target-ds');
              s.dispatchEvent(new Event('change')); }""")
            assert await _seen(page, "target-changed") == {"datasetFp": fp}
            await page.locator("#fb-cancel").click()
            await _seen(page, "browser-cancelled")
            assert await page.evaluate("() => window.ffastApp._browser.mode()") is None

            # The prediction, loaded.
            await page.evaluate("(f) => window.ffastApp._browser.open('prediction', { folder: f })", folder)
            await (await _row(page, "prediction.xyz")).click()
            await expect(page.locator("#fb-load")).to_be_enabled()
            await page.locator("#fb-load").click()
            model = (await _seen(page, "prediction-loaded"))["fp"]
            assert await page.evaluate("(fp) => window.ffastApp._models.has(fp)", model)

            # The same dataset again: the server has it already.
            await page.evaluate("(f) => window.ffastApp._browser.open('dataset', { folder: f })", folder)
            await (await _row(page, "dataset.xyz")).click()
            await expect(page.locator("#fb-load")).to_be_enabled()
            await page.locator("#fb-load").click()
            assert (await _seen(page, "load-duplicate"))["kind"] == "dataset"
        finally:
            await browser.close()


async def test_the_plots_tell_tours_what_a_tab_shows(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open(page, ws_port, web_port)
            loaded = await _load_example_watched(page)
            await page.evaluate(_COLLECT_LOADING)
            await page.locator('#tabbar .tab[data-name="Basic Errors"]').click()
            button = page.locator(
                f'#tabpanels .tabpanel.active .ac-series [data-series="datasets"] button[data-fp="{loaded["datasetFp"]}"]')
            await button.click()
            changed = await _seen(page, "series-changed")
            assert changed == {"tab": "Basic Errors", "datasets": [loaded["datasetFp"]],
                               "models": [loaded["modelFp"]]}
        finally:
            await browser.close()


async def test_load_example_fingerprints_are_kept_until_deleted(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open(page, ws_port, web_port)
            loaded = await _load_example_watched(page)
            assert await page.evaluate("(d) => window.ffastApp._exampleFps.has(d.datasetFp)", loaded)
            await asyncio.wait_for(page.evaluate(
                "(d) => window.ffastApp._removeLoaded(d.datasetFp, d.modelFp)", loaded), timeout=30)
            assert await page.evaluate("() => window.ffastApp._exampleFps.size") == 0
        finally:
            await browser.close()



# ── The loading tour (plan 2026-11-06, revised 2026-11-07) ─────────────────

_LOAD_TITLE = "Tour: Load a dataset and a prediction"


async def _menu_item(page, name):
    await page.locator("#file-menu-btn").click()
    return page.locator("#file-menu-list").get_by_role("menuitem", name=name)


async def _start_loading_tour(page, ws_port, web_port):
    await _open(page, ws_port, web_port)
    await page.wait_for_function("() => window.ffastApp._examples !== null")
    await (await _menu_item(page, _LOAD_TITLE)).click()
    await expect(page.locator(".tour-title")).to_have_text("Open the file browser")


async def _step(page, title, timeout=30000):
    await expect(page.locator(".tour-title")).to_have_text(title, timeout=timeout)


async def _load_round(page, k, folder, dataset, prediction):
    """Steps of one round, with real clicks through the lit area."""
    first = k == 0
    await page.locator("#add-dataset-btn").click()
    await _step(page, "Open the example’s folder" if first else "Open the crystals folder")
    await (await _row(page, folder)).click()
    await _step(page, "Find the dataset file" if first else "Find the second dataset file")
    await (await _row(page, dataset)).click()
    await _step(page, "Units and frames")
    await page.locator(".tour-next").click()
    await _step(page, "Load the dataset" if first else "Load the second dataset")
    await page.locator("#fb-load").click()
    await _step(page, "Load its prediction" if first else "Load the second prediction", timeout=60000)
    await page.locator("#add-prediction-btn").click()
    await _step(page, "Open the same folder")
    await (await _row(page, folder)).click()
    await _step(page, "Find the prediction file")
    await (await _row(page, prediction)).click()
    await _step(page, "Choose its dataset")
    if await page.locator(".tour-wait").is_visible():
        fp = await page.evaluate("(k) => [...window.ffastApp._datasets.keys()][k]", k)
        await page.locator("#fb-target-ds").select_option(fp)
    else:
        await page.locator(".tour-next").click()
    await _step(page, "Energy and force columns")
    await page.locator(".tour-next").click()
    await _step(page, "Load the prediction" if first else "Load the second prediction")
    await page.locator("#fb-load").click()


async def test_the_loading_tour_is_unavailable_without_its_files(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open(page, ws_port, web_port)
            await page.wait_for_function("() => window.ffastApp._examples !== null")
            await page.evaluate("""() => { const e = window.ffastApp._examples;
              e.files = e.files.filter((f) => f !== 'variable-sized periodic/predictions_mptrj_sampled.xyz'); }""")
            item = await _menu_item(page, _LOAD_TITLE)
            await expect(item).to_be_disabled()
            await expect(item).to_have_attribute(
                "title", "Needs variable-sized periodic/predictions_mptrj_sampled.xyz in the examples folder")
            await page.keyboard.press("Escape")
            await page.evaluate("() => { window.ffastApp._examples = null; }")
            item = await _menu_item(page, _LOAD_TITLE)
            await expect(item).to_have_attribute("title", "This server has no example files")
        finally:
            await browser.close()


async def test_the_loading_tour_is_unavailable_after_load_example(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _open(page, ws_port, web_port)
            await _load_example_watched(page)
            item = await _menu_item(page, _LOAD_TITLE)
            await expect(item).to_be_disabled()
            await expect(item).to_have_attribute("title", "The example is loaded: delete it first")
        finally:
            await browser.close()


async def test_the_loading_tour_clicks_through_to_both_pairs_on_basic_errors(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _start_loading_tour(page, ws_port, web_port)
            await expect(page.locator(".tour-count")).to_have_text("Step 1 of 25")
            await _load_round(page, 0, "variable-sized molecular", "dataset.xyz", "prediction.xyz")
            await _step(page, "Add a second dataset", timeout=60000)
            await _load_round(page, 1, "variable-sized periodic",
                              "mptrj_sampled_over_200.extxyz", "predictions_mptrj_sampled.xyz")
            await _step(page, "Compare them in the plots", timeout=60000)
            await page.locator('#tabbar .tab[data-name="Basic Errors"]').click()
            await _step(page, "Choose what the plots show")
            await expect(page.locator(".tour-wait")).to_contain_text("Turn on")
            series = "#tabpanels .tabpanel.active .ac-series"
            for group, key in (("datasets", "_datasets"), ("models", "_models")):
                for fp in await page.evaluate(f"() => [...window.ffastApp.{key}.keys()]"):
                    button = page.locator(f'{series} [data-series="{group}"] button[data-fp="{fp}"]')
                    if "active" not in (await button.get_attribute("class") or ""):
                        await button.click()
            await _step(page, "One series per pair")
            for group in ("datasets", "models"):
                await expect(page.locator(f'{series} [data-series="{group}"] button.active')).to_have_count(2)
            await page.locator(".tour-next").click()
            await expect(page.locator("#status")).to_contain_text("Tour finished")
        finally:
            await browser.close()


async def test_the_loading_tour_refuses_the_wrong_folder_and_file(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _start_loading_tour(page, ws_port, web_port)
            await page.locator("#add-dataset-btn").click()
            await _step(page, "Open the example’s folder")
            await (await _row(page, "fixed-sized molecular")).click()
            await expect(page.locator(".tour-wait")).to_have_text("Click ↑, then variable-sized molecular")
            await page.locator("#fb-up").click()
            await expect(page.locator(".tour-wait")).to_have_text("Click variable-sized molecular")
            await (await _row(page, "variable-sized molecular")).click()
            await _step(page, "Find the dataset file")
            await (await _row(page, "prediction.xyz")).click()
            await expect(page.locator(".tour-wait")).to_have_text("This tour uses dataset.xyz. Click it.")
            await _step(page, "Find the dataset file")

            # Back from the pick step: the folder is open already, so Next shows.
            await page.locator(".tour-back").click()
            await _step(page, "Open the example’s folder")
            await expect(page.locator(".tour-next")).to_be_visible()
            folder = await page.evaluate("() => window.ffastApp._browser.folder()")
            assert folder.endswith("/variable-sized molecular")
        finally:
            await browser.close()


async def test_cancel_while_loading_ends_the_loading_tour(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1100, "height": 760})
        try:
            await _start_loading_tour(page, ws_port, web_port)
            await page.locator("#add-dataset-btn").click()
            await (await _row(page, "variable-sized molecular")).click()
            await (await _row(page, "dataset.xyz")).click()
            await page.locator(".tour-next").click()
            await _step(page, "Load the dataset")
            await page.locator("#fb-cancel").click()
            await expect(page.locator(".tour-box")).to_have_count(0)
            await expect(page.get_by_text(re.compile("“Load the dataset” was cancelled"))).to_be_visible()
            assert await page.evaluate("() => window.ffastApp._datasets.size") == 0
        finally:
            await browser.close()



async def test_the_step_count_and_every_dot_fit_inside_the_tour_box(ffast_web_server):
    """25 steps: the count stays on one line and no dot crosses the box's border."""
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for width in (1100, 420):
            page = await browser.new_page(viewport={"width": width, "height": 760})
            await _start_loading_tour(page, ws_port, web_port)
            fit = await page.evaluate("""() => {
              const box = document.querySelector('.tour-box').getBoundingClientRect();
              const count = document.querySelector('.tour-count');
              const line = parseFloat(getComputedStyle(count).lineHeight) || 16;
              const dots = [...document.querySelectorAll('.tour-dots i')].map((d) => d.getBoundingClientRect());
              return { n: dots.length, oneLine: count.getBoundingClientRect().height < 1.5 * line,
                       inside: dots.every((d) => d.left >= box.left && d.right <= box.right - 8) };
            }""")
            assert fit == {"n": 25, "oneLine": True, "inside": True}, width
            await page.close()
        await browser.close()

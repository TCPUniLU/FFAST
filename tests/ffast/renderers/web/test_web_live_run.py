"""The live-run view in the browser (ADR 0087)."""

from __future__ import annotations

import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

pytest.importorskip("playwright.async_api")

from playwright.async_api import async_playwright, expect  # noqa: E402

from ffast.live import run_folder as rf  # noqa: E402
from tests.ffast.live.dump_helpers import ASPIRIN_ELEMENTS, frame_text, random_frames  # noqa: E402
from tests.ffast.live.test_live_run import _run_folder  # noqa: E402
from tests.ffast.renderers.web.test_web_runtime import (  # noqa: E402
    _expect_connected, _file_menu, _spawn_server, _wait_for_server_ready,
)

pytestmark = pytest.mark.integration

STATIC_DIR = Path(__file__).resolve().parents[4] / "ffast" / "renderers" / "web" / "static"


@pytest.fixture(scope="module")
def static_origin():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(STATIC_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


async def test_the_live_run_helpers_and_the_timeline_with_steps(static_origin):
    kw = {
        "run_id": "r1", "fingerprint": "live:r1", "n_frames": 3,
        "progress": {"scored_frames": 2, "identity": "unverified", "state": "scoring"},
        "thresholds": {"bead": 4.0, "centroid": 5.0},
        "series": {"frame": [0, 1], "step": [0, 100], "t2": [1.0, 6.0], "flag": [False, True]},
        "end": None, "events": [{"kind": "warning", "text": "slow disk"}], "error": None,
    }
    script = f"""async () => {{
      const lr = await import('{static_origin}/live_run.js');
      const pn = await import('{static_origin}/panels.js');
      const kw = {json.dumps(kw)};
      const {{ spec, series }} = lr.liveRunPlot(kw);
      const built = pn.buildPanel(spec, series, {{ peaks: false, units: {{}} }});
      const plain = pn.buildPanel({{ kind: 'timeline' }},
        [{{ name: 'a', data: {{ y: {{ nd: {{ values: [3, 4], shape: [2] }} }} }} }}], {{}});
      return {{
        status: lr.liveRunStatus(kw),
        ended: lr.liveRunStatus({{ ...kw, end: {{ state: 'finished' }}, progress: {{ state: 'refused' }} }}),
        problem: lr.liveRunProblem(kw),
        refused: lr.liveRunProblem({{ ...kw, progress: {{ reason: 'units lj' }} }}),
        x: built.traces[0].x, y: built.traces[0].y,
        xTitle: built.layout.xaxis.title.text,
        line: built.layout.shapes && built.layout.shapes[0].y0,
        plainX: plain.traces[0].x, plainShapes: plain.layout.shapes || null,
      }};
    }}"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(f"{static_origin}/jsconfig.json")
            await page.add_script_tag(url=f"{static_origin}/vendor/plotly/plotly.min.js")
            got = await page.evaluate(script)
        finally:
            await browser.close()
    assert got["status"] == "3 frames · 2 scored · atom order unverified · running"
    assert got["ended"] == "3 frames · run finished · scorer refused"
    assert got["problem"] == "slow disk" and got["refused"] == "units lj"
    assert got["x"] == [0, 100] and got["y"] == [1.0, 6.0] and got["xTitle"] == "Step"
    assert got["line"] == 5.0
    assert got["plainX"] == [0, 1] and got["plainShapes"] is None


async def test_web_a_live_run_opens_shows_its_frames_and_follows_new_ones(tmp_path):
    run = _run_folder(tmp_path, frames=2)
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1200, "height": 820})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _file_menu(page, "Open Live Run…")
                await page.locator("#text-ask input").fill(str(run))
                await page.locator("#text-ask input").press("Enter")

                panel = page.locator(".live-run")
                await expect(panel).to_be_visible(timeout=15000)
                await expect(panel.locator(".live-run-status")).to_contain_text("2 frames")
                await expect(panel.locator(".live-run-plot .main-svg").first).to_be_attached()
                await expect(page.locator("#dataset-list .obj-row")).to_have_count(1, timeout=15000)

                # LAMMPS writes another frame, the scorer scores it.
                with open(run / rf.FRAMES, "a") as fh:
                    fh.write(frame_text(200, random_frames(1, seed=9)[0], ASPIRIN_ELEMENTS))
                [adir] = list((run / rf.ANALYSIS_DIR).iterdir())
                rf.ScoresWriter(adir / rf.SCORES).append([{
                    "segment": 0, "frame": 2, "step": 200, "time": 0.1, "identity": "proven",
                    "tracks": {"geometric": {"t2": 2.0, "spe": None, "flags": {"bead": False}}}}])
                await expect(panel.locator(".live-run-status")).to_contain_text("3 frames", timeout=15000)

                await panel.locator(".live-run-close").click()
                await expect(panel).to_have_count(0)
            finally:
                await browser.close()


async def test_web_a_folder_that_is_not_a_run_says_so(tmp_path):
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1200, "height": 820})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _file_menu(page, "Open Live Run…")
                await page.locator("#text-ask input").fill(str(tmp_path))
                await page.locator("#text-ask input").press("Enter")
                await expect(page.locator("#status")).to_contain_text("not a live-run folder", timeout=15000)
                await expect(page.locator(".live-run")).to_have_count(0)
            finally:
                await browser.close()

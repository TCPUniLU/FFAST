"""The browser's measure.js gives the same answers as the Python geometry.

The browser keeps its own JavaScript copy of distance / angle / dihedral so the
Info read-out is instant (it already holds the atom positions). This test feeds
it the shared cases in ``tests/ffast/geometry_cases.json`` — the same file the
Python side is tested against — so the two copies cannot drift apart silently.
Same zero-build harness as test_web_pure_helpers.py: a static server, a blank
page, a dynamic import, one ``page.evaluate``.
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[4]
STATIC_DIR = ROOT / "ffast" / "renderers" / "web" / "static"
CASES = json.loads((ROOT / "tests" / "ffast" / "geometry_cases.json").read_text())
MEASURES = [(k, c) for k in ("distance", "angle", "dihedral") for c in CASES[k]]


@pytest.fixture(scope="module")
def browser_results():
    """Run every case through measure.js once; NaN comes back as None."""
    playwright_api = pytest.importorskip("playwright.async_api")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(STATIC_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    script = f"""async (cases) => {{
      const ms = await import('{origin}/measure.js');
      return cases.map(([kind, pts]) => {{
        const v = ms[kind](...pts);
        return Number.isNaN(v) ? null : v;
      }});
    }}"""

    async def _collect():
        async with playwright_api.async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            await page.goto(f"{origin}/events.js")
            await page.set_content("<!doctype html><title>measure</title>")
            try:
                out = await page.evaluate(script, [[k, c["points"]] for k, c in MEASURES])
            finally:
                await browser.close()
            assert not errors, f"page errors: {errors}"
            return out

    try:
        yield asyncio.run(_collect())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "i,kind,case", [(i, k, c) for i, (k, c) in enumerate(MEASURES)],
    ids=[f"{k}: {c['name']}" for k, c in MEASURES],
)
def test_measure_js_gives_the_shared_answer(browser_results, i, kind, case):
    got = browser_results[i]
    if case["expected"] is None:
        assert got is None, f"expected undefined, got {got}"
    else:
        assert got == pytest.approx(case["expected"], abs=1e-6)

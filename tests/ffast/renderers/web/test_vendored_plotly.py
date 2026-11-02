"""Every Plotly trace type the page draws exists in the Plotly it ships.

Plotly draws a trace type its build lacks as a plain SVG scatter, without an
error. The page asked for ``scattergl`` from its first plots (921a513) while
shipping the basic build (scatter, bar, pie), so every scatter was SVG: the
force scatter froze the page for 15 min at 55k structures (2026-10-31), and
a density drawn as ``heatmap`` came out as a line (2026-11-02).
"""
from __future__ import annotations

import asyncio
import functools
import http.server
import re
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

STATIC = Path(__file__).resolve().parents[4] / "ffast" / "renderers" / "web" / "static"
PLOTTING = ("panels.js", "analysis.js")


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _shipped_traces() -> set[str]:
    from playwright.async_api import async_playwright

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(_Quiet, directory=str(STATIC)))
    threading.Thread(target=server.serve_forever, daemon=True).start()

    async def read():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.goto(f"http://127.0.0.1:{server.server_port}/index.html")
            await page.wait_for_function("!!window.Plotly")
            traces = await page.evaluate("Object.keys(Plotly.PlotSchema.get().traces)")
            await browser.close()
            return set(traces)

    try:
        return asyncio.run(read())
    finally:
        server.shutdown()


def test_every_trace_type_the_page_draws_is_in_its_plotly():
    asked = set()
    for name in PLOTTING:
        asked |= set(re.findall(r"type: '(\w+)'", (STATIC / name).read_text()))
    asked -= {"number"}     # an <input> type, not a trace
    assert {"scatter", "scattergl", "bar"} <= asked
    assert asked <= _shipped_traces()

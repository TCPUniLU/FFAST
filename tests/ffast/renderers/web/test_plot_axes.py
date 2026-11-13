"""Plot axes and picture size (gaps 1, 2 and 5 of the Qt-removal audit,
2026-11-13): the pure helpers, run in a blank page as in test_metric_client.py."""

from __future__ import annotations

import asyncio
import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

STATIC_DIR = Path(__file__).resolve().parents[4] / "ffast" / "renderers" / "web" / "static"

SCRIPT = """
async (origin) => {
  const pn = await import(`${origin}/panels.js`);
  const an = await import(`${origin}/analysis.js`);
  const dl = await import(`${origin}/dialogs.js`);
  const ex = await import(`${origin}/panel_export.js`);
  const te = await import(`${origin}/tab_edit.js`);
  const built = (layout = {}) => ({
    data: [{ x: [0, 1, 2], y: [0.5, 10, 1000] }, { x: [9], y: [-1], hoverinfo: 'skip' }],
    layout: { xaxis: {}, yaxis: {}, ...layout },
  });
  const axes = (spec, layout) => pn.applyPanelAxes(spec, built(layout)).layout;
  const out = {};
  out.extent = pn.dataExtent(built().data, 'y');
  out.extentPositive = pn.dataExtent([{ y: [-3, 0, 2, 8] }], 'y', true);
  out.extentNone = pn.dataExtent([], 'x');
  out.none = axes({});
  out.log = axes({ y_scale: 'log' }).yaxis;
  out.logRange = axes({ y_scale: 'log', y_min: 1, y_max: 100 }).yaxis;
  out.oneEnd = axes({ x_max: 5 }).xaxis;
  out.logOneEnd = axes({ y_scale: 'log', y_max: 100 }).yaxis;
  out.flip = axes({ x_flip: true }).xaxis;
  out.flipRange = axes({ x_min: 0, x_max: 4, x_flip: true }).xaxis;
  out.flipDrawn = axes({ y_flip: true }, { yaxis: { range: [0, 7] } }).yaxis;
  out.gridOff = axes({ x_grid: false });
  out.logDropsDrawn = axes({ y_scale: 'log' }, { yaxis: { range: [-5, 7] } }).yaxis;
  out.valuesLog = an.axisValues({ type: 'log', range: [0, 2] });
  out.valuesFlipped = an.axisValues({ range: [9, 3] });
  out.problemOrder = dl.axisProblem('X axis', { log: false, min: 3, max: 1 });
  out.problemLog = dl.axisProblem('Y axis', { log: true, min: 0, max: null });
  out.problemNone = dl.axisProblem('Y axis', { log: true, min: 0.1, max: null });
  out.choiceDefault = ex.pictureChoice(null, { width: 640, height: 400 });
  out.choiceSaved = ex.pictureChoice({ width: 1200, height: 800, sharpness: 3, transparent: false },
                                     { width: 640, height: 400 });
  out.choiceBad = ex.pictureChoice({ width: 7, sharpness: 9 }, { width: 640, height: 400 });
  out.pictureProblem = [ex.pictureProblem(800, 600), ex.pictureProblem(10, 600), ex.pictureProblem(800.5, 600)];
  const tab = { name: 'T', panels: [{ kind: 'timeline', title: 'P' }] };
  out.patched = te.setPanelLabels(tab, 0, { y_scale: 'log', y_min: 0.1, x_flip: true, bogus: 1 }).panels[0];
  out.cleared = te.setPanelLabels({ ...tab, panels: [{ kind: 'timeline', y_scale: 'log' }] }, 0,
                                  { y_scale: null }).panels[0];
  return out;
}
"""


@pytest.fixture(scope="module")
def results():
    playwright_api = pytest.importorskip("playwright.async_api")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(STATIC_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"

    async def _collect():
        async with playwright_api.async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            await page.goto(f"{origin}/events.js")
            await page.set_content("<!doctype html><title>axes</title>")
            try:
                out = await page.evaluate(SCRIPT, origin)
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


def test_the_data_extent_skips_helper_traces_and_non_positive_values(results):
    assert results["extent"] == [0.5, 1000]
    assert results["extentPositive"] == [2, 8]
    assert results["extentNone"] is None


def test_no_axis_edits_leave_the_plot_alone(results):
    assert results["none"] == {"xaxis": {}, "yaxis": {}}


def test_a_log_axis_takes_its_range_as_log10(results):
    assert results["log"] == {"type": "log"}
    assert results["logRange"] == {"type": "log", "range": [0, 2], "autorange": False}


def test_one_typed_end_takes_the_other_from_the_data(results):
    assert results["oneEnd"] == {"range": [0, 5], "autorange": False}
    # On a log axis the data's lowest positive value: 0.5.
    lo, hi = results["logOneEnd"]["range"]
    assert abs(lo - (-0.30103)) < 1e-4 and hi == 2


def test_a_flipped_axis_runs_high_to_low(results):
    assert results["flip"] == {"autorange": "reversed"}
    assert results["flipRange"] == {"range": [4, 0], "autorange": False}
    assert results["flipDrawn"] == {"range": [7, 0]}


def test_a_hidden_grid_is_off_on_its_own_axis_only(results):
    assert results["gridOff"] == {"xaxis": {"showgrid": False}, "yaxis": {}}


def test_a_log_axis_drops_a_range_drawn_for_linear(results):
    assert results["logDropsDrawn"] == {"type": "log", "autorange": True}


def test_sub_reads_a_log_or_flipped_range_as_data_values_low_to_high(results):
    assert results["valuesLog"] == [1, 100]
    assert results["valuesFlipped"] == [3, 9]


def test_the_axes_dialog_refuses_what_cannot_be_drawn(results):
    assert "below" in results["problemOrder"]
    assert "above 0" in results["problemLog"]
    assert results["problemNone"] == ""


def test_a_picture_starts_at_the_screen_size_then_keeps_the_last_choice(results):
    assert results["choiceDefault"] == {"width": 640, "height": 400, "sharpness": 1, "transparent": True}
    assert results["choiceSaved"] == {"width": 1200, "height": 800, "sharpness": 3, "transparent": False}
    assert results["choiceBad"] == {"width": 640, "height": 400, "sharpness": 1, "transparent": True}
    ok, small, fraction = results["pictureProblem"]
    assert ok == "" and small and fraction


def test_axis_fields_are_saved_in_the_tab_like_labels(results):
    assert results["patched"] == {"kind": "timeline", "title": "P",
                                  "y_scale": "log", "y_min": 0.1, "x_flip": True}
    assert results["cleared"] == {"kind": "timeline"}

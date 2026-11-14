"""A plot draws only the points that show (thin.js, 2026-11-13).

1.77M frames in a 700 px plot cost Plotly 250-700 ms per redraw. The page
keeps every value and Plotly gets a few thousand; a zoom thins again, a click
still names its frame and the CSV still has every point.
"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

pytestmark = pytest.mark.integration

STATIC_DIR = Path(__file__).resolve().parents[4] / "ffast" / "renderers" / "web" / "static"


@pytest.fixture(scope="module")
def static_origin():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(STATIC_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


async def _run(origin: str, body: str):
    script = f"""async () => {{
      const th = await import('{origin}/thin.js');
      const pn = await import('{origin}/panels.js');
      const plot = () => {{
        const el = document.createElement('div');
        el.style.cssText = 'width:800px;height:300px';
        document.body.append(el);
        return el;
      }};
      const series = (y, x) => [{{ datasetFp: 'd', modelFp: 'm', name: 's',
        data: {{ y: {{ nd: {{ values: y, shape: [y.length] }} }},
                 ...(x ? {{ x: {{ nd: {{ values: x, shape: [x.length] }} }} }} : {{}}) }} }}];
      {body}
    }}"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(f"{origin}/jsconfig.json")
            await page.add_script_tag(url=f"{origin}/vendor/plotly/plotly.min.js")
            return await page.evaluate(script)
        finally:
            await browser.close()


async def test_a_thinned_line_keeps_every_peak_dip_gap_and_end(static_origin):
    got = await _run(static_origin, """
      const n = 200000;
      const xs = Array.from({length: n}, (_, i) => i);
      const ys = xs.map((i) => Math.sin(i / 5000));
      ys[123457] = 50; ys[7771] = -50; ys[150001] = NaN;
      const idx = Array.from(th.thinLine(xs, ys, null, 700));
      const zoom = Array.from(th.thinLine(xs, ys, [1000, 1100], 700));
      return { count: idx.length, peak: idx.includes(123457), dip: idx.includes(7771),
               gap: idx.includes(150001), ends: idx[0] === 0 && idx[idx.length - 1] === n - 1,
               sorted: idx.every((v, k) => k === 0 || v > idx[k - 1]),
               zoomAll: [...Array(103).keys()].every((k) => zoom.includes(999 + k)),
               small: th.thinLine(xs.slice(0, 5000), ys.slice(0, 5000), null, 700) };
    """)
    assert got["count"] <= 4 * 700
    assert got["peak"] and got["dip"] and got["gap"] and got["ends"] and got["sorted"]
    assert got["zoomAll"]       # zoomed in, every point of the view and one beyond each edge
    assert got["small"] is None  # a short line is drawn whole


async def test_thinned_dots_keep_one_per_cell_and_a_lone_outlier(static_origin):
    got = await _run(static_origin, """
      const n = 300000;
      const xs = Array.from({length: n}, (_, i) => (i % 1000) / 1000);
      const ys = xs.map((x, i) => x + ((i * 7919) % 100) / 10000);
      xs[4242] = 0.5; ys[4242] = 3;          // far above the diagonal
      const idx = th.thinPoints(xs, ys, null, 700, 300);
      return { count: idx.length, outlier: Array.from(idx).includes(4242) };
    """)
    assert got["count"] <= 350 * 150
    assert got["outlier"]


async def test_a_long_timeline_draws_few_points_and_thins_again_on_zoom(static_origin):
    got = await _run(static_origin, """
      const n = 1000000;
      const y = Array.from({length: n}, (_, i) => Math.sin(i / 3000) + (i === 777777 ? 9 : 0));
      const el = plot();
      const t0 = performance.now();
      pn.renderPanel(el, { kind: 'timeline' }, series(y), { peaks: false, units: {} });
      const drawMs = performance.now() - t0;
      const drawn = el.data[0].x.length;
      const peakDrawn = Array.from(el.data[0].x).includes(777777);
      const pointOf = (x) => Array.from(el.data[0].x).indexOf(x);
      const clicked = pn.fullIndex(el, 0, pointOf(777777));
      await Plotly.relayout(el, { 'xaxis.range': [500000, 500300] });
      await pn.rethin(el);
      const xs = Array.from(el.data[0].x);
      const zoomAll = [...Array(301).keys()].every((k) => xs.includes(500000 + k));
      const csv = pn.fullTraces(el)[0].y.length;
      return { drawn, peakDrawn, clicked, zoomAll, csv, drawMs };
    """)
    print("timeline draw ms", round(got["drawMs"]))
    assert got["drawn"] < 4000
    assert got["peakDrawn"] and got["clicked"] == 777777
    assert got["zoomAll"]
    assert got["csv"] == 1000000


async def test_a_large_scatter_draws_few_dots_and_a_click_names_its_frame(static_origin):
    got = await _run(static_origin, """
      const n = 500000;
      const x = Array.from({length: n}, (_, i) => -50000 + (i % 977) * 30);
      const y = x.map((v, i) => v + ((i * 31) % 17));
      y[333333] = 5e5;                       // the worst prediction
      const el = plot();
      const s = series(y, x);
      s[0].data.x = { nd: { values: x, shape: [n] } };
      pn.renderPanel(el, { kind: 'scatter', diagonal: true }, s, { units: {}, perFrame: true });
      const drawn = el.data[0].x.length;
      const k = Array.from(el.data[0].y).indexOf(5e5);
      return { drawn, clicked: pn.fullIndex(el, 0, k), csv: pn.fullTraces(el)[0].x.length };
    """)
    assert got["drawn"] < 60000
    assert got["clicked"] == 333333
    assert got["csv"] == 500000


def _parity_cases():
    """Named arrays (each sent to the page once) and the cases that use them."""
    import numpy as np
    rng = np.random.default_rng(7)
    n = 30_000
    x = np.arange(n, dtype=float)
    y = np.sin(x / 700) + rng.normal(0, 0.1, n)
    y[rng.integers(0, n, 40)] = np.nan
    y[10_000:10_300] = np.nan                       # a whole gap
    y[15_000:15_500] = 1.0                          # a plateau: ties
    xu = np.cumsum(rng.uniform(0.1, 3.0, n))        # uneven x
    px = np.concatenate([rng.normal(0, 1, n), [np.nan] * 50, [25.0]])
    py = np.concatenate([px[:n] + rng.normal(0, 0.2, n), [1.0] * 50, [-30.0]])
    arrays = {"x": x, "y": y, "xu": xu, "px": px, "py": py}
    lines = [{"x": "x", "y": "y", "range": r, "w": 700}
             for r in [None, [1000, 1100], [5000, 20_000], [20_000, 5000], [-5, 15_000], [29_000, 40_000]]]
    lines.append({"x": "x", "y": "y", "range": [2500, 12_500], "w": 700.5})
    lines.append({"x": "xu", "y": "y", "range": [float(xu[4500]), float(xu[20500]) + 0.37], "w": 640})
    points = [{"x": "px", "y": "py", "xr": xr, "yr": yr, "w": 700, "h": 300}
              for xr, yr in [(None, None), ([-1, 1], None), ([-1, 1], [-0.5, 0.5]), ([1, -1], [0.5, -0.5])]]
    return arrays, lines, points


async def test_the_page_and_the_server_thin_to_the_same_points(static_origin):
    """ADR 0099: small results are thinned in the page, large ones on the
    server; both must keep the same points, or a plot changes its look when
    a result crosses the size limit."""
    import math
    from ffast.session.thinning import thin_line, thin_points

    arrays, lines, points = _parity_cases()
    js = lambda a: [None if math.isnan(v) else v for v in a.tolist()]
    payload = {"arrays": {k: js(v) for k, v in arrays.items()}, "lines": lines, "points": points}
    script = f"""async (p) => {{
      const th = await import('{static_origin}/thin.js');
      const a = Object.fromEntries(Object.entries(p.arrays).map(([k, v]) => [k, v.map((u) => (u === null ? NaN : u))]));
      const out = (idx) => (idx ? Array.from(idx) : null);
      return {{
        lines: p.lines.map((c) => out(th.thinLine(a[c.x], a[c.y], c.range, c.w))),
        points: p.points.map((c) => out(th.thinPoints(a[c.x], a[c.y],
          (c.xr || c.yr) ? {{ x: c.xr, y: c.yr }} : null, c.w, c.h))),
      }};
    }}"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(f"{static_origin}/jsconfig.json")
            got = await page.evaluate(script, payload)
        finally:
            await browser.close()
    for k, c in enumerate(lines):
        want = thin_line(arrays[c["x"]], arrays[c["y"]], c["range"], c["w"])
        assert want is not None and got["lines"][k] == want.tolist(), f"line case {k}"
    for k, c in enumerate(points):
        want = thin_points(arrays[c["x"]], arrays[c["y"]], c["xr"], c["yr"], c["w"], c["h"])
        assert want is not None and got["points"][k] == want.tolist(), f"points case {k}"

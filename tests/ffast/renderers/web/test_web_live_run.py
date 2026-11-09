"""The live-run view in the browser (ADR 0087)."""

from __future__ import annotations

import functools
import http.server
import json
import re
import threading
from pathlib import Path

import pytest

pytest.importorskip("playwright.async_api")

from playwright.async_api import async_playwright, expect  # noqa: E402

from ffast.live import run_folder as rf  # noqa: E402
from tests.ffast.live.dump_helpers import ASPIRIN_ELEMENTS, frame_text, random_frames  # noqa: E402
from tests.ffast.live.test_live_run import _run_folder  # noqa: E402
from tests.ffast.renderers.web.test_web_runtime import (  # noqa: E402
    _expect_connected, _lammps_menu, _spawn_server, _wait_for_server_ready,
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
        "series": {"frame": [0, 1], "step": [0, 100], "exceedance": [0.2, 1.2],
                   "test": [["bonds", "T2"], ["bonds", "SPE"]], "marked": [False, True]},
        "end": None, "events": [{"kind": "warning", "text": "slow disk"}], "error": None,
    }
    script = f"""async () => {{
      const lr = await import('{static_origin}/live_run.js');
      const pn = await import('{static_origin}/panels.js');
      const kw = {json.dumps(kw)};
      const series = lr.liveRunSeries(kw);
      const spec = {{ kind: 'timeline', x_label: 'Step', y_line: lr.liveRunThreshold(kw) }};
      const built = pn.buildPanel(spec, series, {{ peaks: false, units: {{}} }});
      const plain = pn.buildPanel({{ kind: 'timeline' }},
        [{{ name: 'a', data: {{ y: {{ nd: {{ values: [3, 4], shape: [2] }} }} }} }}], {{}});
      return {{
        status: lr.liveRunStatus(kw),
        ended: lr.liveRunStatus({{ ...kw, end: {{ state: 'finished' }}, progress: {{ state: 'refused' }} }}),
        problem: lr.liveRunProblem(kw),
        refused: lr.liveRunProblem({{ ...kw, progress: {{ reason: 'units lj' }} }}),
        frames: series[0].frames, fp: series[0].datasetFp,
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
    assert got["ended"] == "3 frames · 2 scored · run finished · scorer refused"
    # A warning is for the Events list; only what stops scores is a problem.
    assert got["problem"] == "" and got["refused"] == "units lj"
    assert got["frames"] == [0, 1] and got["fp"] == "live:r1"
    assert got["x"] == [0, 100] and got["y"] == [0.2, 1.2] and got["xTitle"] == "Step"
    assert got["line"] == 1
    assert got["plainX"] == [0, 1] and got["plainShapes"] is None


async def test_a_long_runs_marks_are_one_path_per_row_and_a_resend_is_recognised(static_origin):
    # 20,000 marked frames: one SVG element each froze the page for seconds per
    # redraw. Each row of marks is one path shape; the legend keeps its entry.
    n = 20000
    kw = {"run_id": "r1", "fingerprint": "live:r1",
          "series": {"frame": list(range(n)), "step": [100 * k for k in range(n)],
                     "exceedance": [1.5] * n, "test": [None] * n, "marked": [True] * n,
                     "chem": [k == 777 for k in range(n)]},
          "progress": {}, "end": None, "events": []}
    script = f"""async (kw) => {{
      const lr = await import('{static_origin}/live_run.js');
      const pn = await import('{static_origin}/panels.js');
      const el = document.createElement('div');
      el.style.cssText = 'width:900px;height:300px';
      document.body.append(el);
      const spec = {{ kind: 'timeline', x_label: 'Step', y_line: 1,
                      overlays: lr.liveRunsOverlays([{{ name: 'r', kw }}]) }};
      pn.renderPanel(el, spec, lr.liveRunSeries(kw), {{ peaks: false, units: {{}} }});
      const paths = (el.layout.shapes || []).filter((s) => s.type === 'path');
      const copy = JSON.parse(JSON.stringify(kw));
      return {{
        elements: el.querySelectorAll('*').length,
        paths: paths.map((s) => s.path.split('M').length - 1),
        legend: el.data.filter((t) => t.showlegend).map((t) => t.name),
        same: lr.sameRunUpdate(kw, copy),
        first: lr.sameRunUpdate(undefined, kw),
        newScore: lr.sameRunUpdate(kw, {{ ...copy, series: {{ ...copy.series,
          exceedance: [...copy.series.exceedance.slice(0, -1), 1.6] }} }}),
        newKey: lr.sameRunUpdate(kw, {{ ...copy, stop_requested: true }}),
      }};
    }}"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(f"{static_origin}/jsconfig.json")
            await page.add_script_tag(url=f"{static_origin}/vendor/plotly/plotly.min.js")
            got = await page.evaluate(script, kw)
        finally:
            await browser.close()
    assert got["paths"] == [n, 1]   # every mark kept, the lone chemistry flag too
    assert got["elements"] < 1000
    assert got["legend"] == ["marked", "chemistry flag"]
    assert got["same"] is True and got["first"] is False
    assert got["newScore"] is False and got["newKey"] is False


async def test_with_several_runs_each_runs_marks_take_its_curves_colour(static_origin):
    # Two runs on one score plot: each run's marked and chemistry rows are
    # drawn, and named in the legend, in that run's curve colour.
    def run(rid, marked, chem):
        return {"run_id": rid, "fingerprint": f"live:{rid}",
                "series": {"frame": [0, 1], "step": [0, 100], "exceedance": [0.5, 1.5],
                           "marked": marked, "chem": chem},
                "progress": {}, "end": None, "events": []}
    runs = [{"name": "a", "kw": run("a", [False, True], [True, False])},
            {"name": "b", "kw": run("b", [True, False], [False, True])}]
    script = f"""async (runs) => {{
      const lr = await import('{static_origin}/live_run.js');
      const pn = await import('{static_origin}/panels.js');
      const el = document.createElement('div');
      el.style.cssText = 'width:900px;height:300px';
      document.body.append(el);
      const series = runs.map((r) => ({{ ...lr.liveRunSeries(r.kw)[0], name: r.name }}));
      const spec = {{ kind: 'timeline', x_label: 'Step', y_line: 1, legend: true,
                      overlays: lr.liveRunsOverlays(runs) }};
      pn.renderPanel(el, spec, series, {{ peaks: false, units: {{}} }});
      const curves = el.data.filter((t) => t.meta !== 'overlay').map((t) => t.line.color);
      return {{
        curves,
        paths: (el.layout.shapes || []).filter((s) => s.type === 'path').map((s) => s.line.color),
        legend: el.data.filter((t) => t.meta === 'overlay' && t.showlegend)
          .map((t) => [t.name, t.marker.color]),
      }};
    }}"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(f"{static_origin}/jsconfig.json")
            await page.add_script_tag(url=f"{static_origin}/vendor/plotly/plotly.min.js")
            got = await page.evaluate(script, runs)
        finally:
            await browser.close()
    a, b = got["curves"]
    assert a != b
    assert got["paths"] == [a, a, b, b]
    assert got["legend"] == [["a: marked", a], ["a: chemistry flag", a],
                             ["b: marked", b], ["b: chemistry flag", b]]


async def test_marks_and_bands_hide_with_their_legend_entry_and_their_runs_curve(static_origin):
    # Marks and alarm bands are shapes, which Plotly's legend does not hide.
    # Hiding "a: marked" hides a's marked row; hiding b's curve hides b's rows
    # and band; both stay hidden when the plot is drawn again with new frames.
    def run(rid):
        return {"run_id": rid, "fingerprint": f"live:{rid}",
                "series": {"frame": [0, 1], "step": [0, 100], "exceedance": [0.5, 1.5],
                           "marked": [False, True], "chem": [True, False]},
                "progress": {"alarms": [{"kind": "ood", "onset": {"step": 0}, "confirmation": {"step": 100}}]},
                "end": None, "events": []}
    runs = [{"name": "a", "kw": run("a")}, {"name": "b", "kw": run("b")}]
    setup = f"""async (runs) => {{
      const lr = await import('{static_origin}/live_run.js');
      const pn = await import('{static_origin}/panels.js');
      const el = document.createElement('div');
      el.id = 'plot';
      el.style.cssText = 'width:900px;height:300px';
      document.body.append(el);
      window.draw = (rs) => pn.renderPanel(el, {{ kind: 'timeline', x_label: 'Step', y_line: 1, legend: true,
          overlays: lr.liveRunsOverlays(rs) }},
        rs.map((r) => ({{ ...lr.liveRunSeries(r.kw)[0], name: r.name }})), {{ peaks: false, units: {{}} }});
      draw(runs);
    }}"""
    # Shapes in order (the threshold line left out): a marked, a chem,
    # b marked, b chem, a band, b band. True: shown.
    shown = """() => document.getElementById('plot').layout.shapes
      .filter((s) => s.type !== 'line').map((s) => s.visible !== false)"""
    # a new frame for each run: the plot is drawn again
    redraw = """(runs) => draw(runs.map((r) => ({ ...r, kw: { ...r.kw, series: {
      frame: [0, 1, 2], step: [0, 100, 200], exceedance: [0.5, 1.5, 1.2],
      marked: [false, true, true], chem: [true, false, false] } } })))"""
    every = [True] * 6
    hidden = [False, True, False, False, True, False]

    async def click_legend(page, name):
        # The legend's own click (Plotly keeps only what the viewer did across
        # drawings, not a restyle from code); it waits out the double-click delay.
        item = page.locator("#plot .legend .traces").filter(
            has=page.locator(".legendtext", has_text=re.compile(f"^{re.escape(name)}$")))
        await item.locator(".legendtoggle").click()

    async def wait_shown(page, want):
        await page.wait_for_function(f"(want) => JSON.stringify(({shown})()) === JSON.stringify(want)", arg=want)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(f"{static_origin}/jsconfig.json")
            await page.add_script_tag(url=f"{static_origin}/vendor/plotly/plotly.min.js")
            await page.evaluate(setup, runs)
            assert await page.evaluate(shown) == every
            await click_legend(page, "a: marked")
            await wait_shown(page, [False] + every[1:])
            await click_legend(page, "b")
            await wait_shown(page, hidden)
            await page.evaluate(redraw, runs)
            assert await page.evaluate(shown) == hidden     # still hidden after new frames
            await click_legend(page, "a: marked")
            await wait_shown(page, [True] + hidden[1:])
            await click_legend(page, "b")
            await wait_shown(page, every)
        finally:
            await browser.close()


ACTIVE = "#tabpanels .tabpanel.active"
# The score curve of the tab on screen has `n` points (args: [ACTIVE, n]).
SCORE_POINTS = """([sel, n]) => document.querySelector(
  sel + ' .analysis-panel[data-part="scores"] .panel-plot')?.data?.[0]?.x?.length === n"""
CHROME = ".tabpanel.active .edit-chrome"


async def test_web_a_live_run_opens_in_its_own_tab_and_follows_new_frames(tmp_path):
    run = _run_folder(tmp_path, frames=2)
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _open_run(page, run)

                # The run's tab is listed and opened: its 3D view beside its scores.
                tab = page.locator("#tabbar .tab", has_text="Live 12:00:00 node")
                await expect(tab).to_have_class(re.compile(r"\bactive\b"), timeout=15000)
                active = page.locator(ACTIVE)
                await expect(active.locator(".panel-3d")).to_have_count(1)
                await page.wait_for_function(SCORE_POINTS, arg=[ACTIVE, 2], timeout=15000)
                # No caption under the scores: the run's state is in the Run card.
                caption = active.locator('.analysis-panel[data-part="scores"] .panel-caption-line')
                await expect(caption).to_be_hidden()
                # Status strip and events list are panels of the tab too.
                await expect(active.locator(".live-status thead")).to_contain_text("LAMMPS step")
                await expect(active.locator(".live-event-list tr")).to_have_count(0)
                await expect(active.locator(".analysis-panel .panel-plot .main-svg").first).to_be_attached()
                await expect(active.locator(".sub-toggle")).to_have_count(0)
                await expect(page.locator("#dataset-list .obj-row")).to_have_count(1, timeout=15000)

                await expect(page.locator("#frame-label")).to_have_text("1 / 1", timeout=15000)

                # LAMMPS writes another frame, the scorer scores it.
                with open(run / rf.FRAMES, "a") as fh:
                    fh.write(frame_text(200, random_frames(1, seed=9)[0], ASPIRIN_ELEMENTS))
                [adir] = list((run / rf.ANALYSIS_DIR).iterdir())
                rf.ScoresWriter(adir / rf.SCORES).append([{
                    "segment": 0, "frame": 2, "step": 200, "time": 0.1, "identity": "proven",
                    "tracks": {"bonds": {"T2": 2.0, "SPE": 0.5}}, "exceedance": 0.4,
                    "test": ["bonds", "T2"], "marked": False}])
                await page.wait_for_function(SCORE_POINTS, arg=[ACTIVE, 3], timeout=15000)
                # The plot's third point comes with the run's update; the dataset's
                # new length comes in its own message. The click needs both.
                await expect(page.locator("#frame-label")).to_have_text("2 / 2", timeout=15000)

                # Picking an older score point holds it for inspection.
                xy = await page.evaluate("""(sel) => {
                  const el = document.querySelector(sel + ' .analysis-panel .panel-plot');
                  const fl = el._fullLayout, tr = el.data[0], r = el.getBoundingClientRect();
                  const i = 1;
                  // _size: the margins as drawn, after the legend pushed them
                  return {x: r.left + fl._size.l + fl.xaxis.l2p(tr.x[i]),
                          y: r.top + fl._size.t + fl.yaxis.l2p(tr.y[i])};
                }""", ACTIVE)
                await page.mouse.move(xy["x"], xy["y"])
                await page.mouse.click(xy["x"], xy["y"])
                await expect(page.locator("#frame-label")).to_have_text("1 / 2", timeout=10000)

                # Inspecting an older frame holds it as new frames arrive.
                with open(run / rf.FRAMES, "a") as fh:
                    fh.write(frame_text(300, random_frames(1, seed=10)[0], ASPIRIN_ELEMENTS))
                await expect(page.locator("#frame-label")).to_have_text("1 / 3", timeout=15000)

                # Returning to the newest frame resumes following.
                await page.locator("#frame-slider").fill("3")
                with open(run / rf.FRAMES, "a") as fh:
                    fh.write(frame_text(400, random_frames(1, seed=11)[0], ASPIRIN_ELEMENTS))
                await expect(page.locator("#frame-label")).to_have_text("4 / 4", timeout=15000)

                # Tab Pause holds both the frame and its range; Resume catches up.
                pause = active.locator(".ac-pause")
                await pause.click()
                await expect(pause).to_have_text("Resume")
                with open(run / rf.FRAMES, "a") as fh:
                    fh.write(frame_text(500, random_frames(1, seed=12)[0], ASPIRIN_ELEMENTS))
                await page.wait_for_function("() => [...window.ffastApp._datasets.values()].some((d) => d.n === 6)", timeout=15000)
                await expect(page.locator("#frame-label")).to_have_text("4 / 4")
                await pause.click()
                await expect(page.locator("#frame-label")).to_have_text("5 / 5", timeout=15000)
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
                await _open_run(page, tmp_path)
                await expect(page.locator("#status")).to_contain_text("not a live-run folder", timeout=15000)
                await expect(page.locator(".live-run")).to_have_count(0)
            finally:
                await browser.close()


async def test_the_status_overlays_and_event_helpers(static_origin):
    now = "2026-11-05T12:00:10.000+00:00"
    kw = {
        "run_id": "r1", "fingerprint": "live:r1", "n_frames": 5,
        "producer": {"last_step": 412300},
        "progress": {
            "state": "scoring", "identity": "proven", "heartbeat_utc": "2026-11-05T12:00:07.000+00:00",
            "scored_through_step": 411900, "first_step": 0, "planned_end_step": 1000000,
            "rule": {"window_frames": 100, "step_frames": 10, "fraction_threshold": 0.2,
                     "persist_ps": 5.0, "frame_ps": 0.05},
            "false_alarm": {"probability": 0.018, "at_least": False, "over": "run"},
            "alarms": [{"kind": "ood", "onset": {"step": 380000}, "confirmation": {"step": 386000}}],
        },
        "series": {"frame": [0, 1, 2, 3], "step": [0, 100, 200, 300], "exceedance": [0.5, 1.2, 0.4, 1.5],
                   "test": [None] * 4, "marked": [False, True, False, True], "chem": [False, False, False, True]},
        "stop": {"observed_step": 386500}, "end": None, "stop_requested": True, "events": [],
    }
    events = [
        {"t": now, "kind": "alarm", "alarm": "ood", "onset": {"step": 380000},
         "confirmation": {"step": 386000}, "test": ["dihedrals", "SPE"], "report": "analysis/a/alarms/1.json"},
        {"t": now, "kind": "stop_observed", "step": 386500, "how": "halt"},
        {"t": now, "kind": "warning", "text": "the input writes no restart file"},
    ]
    script = f"""async () => {{
      const lr = await import('{static_origin}/live_run.js');
      const kw = {json.dumps(kw)};
      const now = Date.parse('{now}');
      const stalled = {{ ...kw, progress: {{ ...kw.progress, heartbeat_utc: '2026-11-05T11:56:10.000+00:00' }} }};
      const early = {{ ...kw, progress: {{ ...kw.progress,
        false_alarm: {{ probability: 0.0004, at_least: true, over: 'run' }} }} }};
      return {{
        row: lr.liveRunRow(kw, now),
        stalled: lr.liveRunRow(stalled, now).scorer,
        ended: lr.liveRunRow({{ ...kw, end: {{ state: 'terminated', stop_observed_step: 386400 }} }}, now).state,
        fine: lr.liveRunRow({{ ...kw, stop_requested: false, progress: {{ ...kw.progress, alarms: [] }} }}, now).state,
        finished: lr.liveRunRow({{ ...kw, progress: {{ ...kw.progress, alarms: [] }},
          end: {{ state: 'stopped', stop_observed_step: 500 }} }}, now).state,
        rule: lr.liveRunRuleText(kw), early: lr.liveRunRuleText(early),
        overlays: lr.liveRunOverlays(kw),
        events: {json.dumps(events)}.map(lr.liveEventLine),
        pct: [0.018, 0.25, 3e-7, 0.004].map(lr.percent),
      }};
    }}"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(f"{static_origin}/jsconfig.json")
            got = await page.evaluate(script)
        finally:
            await browser.close()
    # One row of the Run panel's table: plain while fine, coloured only for trouble.
    row = got["row"]
    assert row["name"] == "r1"
    assert (row["lammps"]["text"], row["lammps"]["tone"]) == ("412,300 / 1,000,000", "")
    assert row["lammps"]["fraction"] == pytest.approx(0.4123)
    assert (row["scored"]["text"], row["scored"]["tone"]) == ("411,900", "")
    assert row["scorer"] == {"text": "running", "tone": "", "title": "Last heartbeat 3 s ago"}
    assert (row["atoms"]["text"], row["atoms"]["tone"]) == ("proven", "")
    assert row["state"] == {"text": "Alarm at step 386,000 · stop requested", "tone": "bad"}
    assert got["stalled"] == {"text": "silent 4 min — stalled?", "tone": "warn"}
    assert got["ended"] == {"text": "Alarm at step 386,000 · terminated; last saved frame at step 386,400",
                            "tone": "bad"}
    assert got["fine"] == {"text": "No alarm", "tone": ""}
    assert got["finished"] == {"text": "Stopped at step 500", "tone": ""}   # a stop asked for is not trouble
    assert got["rule"] == ("Alarm rule: 20 % of frames marked within 5 ps (100 frames), checked every "
                           "0.5 ps. False-alarm chance over the planned 1,000,000 steps: ≤ 1.8 % (estimated).")
    assert "≥ 0.04 % (estimated; frames taken as uncorrelated until 200 are scored)" in got["early"]
    o = got["overlays"]
    assert o["ticks"][0]["xs"] == [100, 300] and o["ticks"][1]["xs"] == [300]
    assert o["bands"] == [{"x0": 380000, "x1": 386000, "color": "danger"}]
    # Each moment has its own line style, so the legend can name it.
    assert [(l["x"], l["label"], l["color"], l["dash"]) for l in o["lines"]] == [
        (380000, "estimated onset", "danger", "dot"), (386000, "confirmed", "danger", "solid"),
        (386500, "stop observed", "ink", "solid")]
    texts = [e["text"] for e in got["events"]]
    assert texts[0] == "Alarm · estimated onset step 380,000 · confirmed at step 386,000 · dihedrals SPE"
    assert got["events"][0]["report"] == "analysis/a/alarms/1.json"
    assert texts[1] == "Stop observed at step 386,500 (fix halt line in log.lammps)"
    assert got["events"][2]["tone"] == "warn" and got["events"][0]["time"] == "12:00:10"
    assert got["pct"] == ["1.8 %", "25 %", "< 0.001 %", "0.4 %"]


def _alarmed_run_folder(tmp_path):
    """A run folder whose scorer raised an alarm and whose run stopped."""
    run = _run_folder(tmp_path, frames=4, scores=False)
    a = run / rf.ANALYSIS_DIR / "0123456789abcdef"
    a.mkdir(parents=True)
    rf.write_json_atomic(a / rf.SETTINGS, {"bundle": "/b.bundle", "scores_format": 2, "role": "centroid"})
    rf.ScoresWriter(a / rf.SCORES).append([
        {"segment": 0, "frame": k, "step": 100 * k, "time": 0.05 * k, "identity": "proven",
         "tracks": {"bonds": {"T2": 1.0, "SPE": 0.1}}, "exceedance": [0.3, 0.4, 1.4, 1.6][k],
         "test": ["bonds", "T2"], "marked": k >= 2, "chem": {"broken": 0, "close": 0}}
        for k in range(4)])
    progress = {"analysis_id": a.name, "state": "done", "identity": "proven", "scored_frames": 4,
                "scored_through_step": 300, "first_step": 0, "planned_end_step": 300,
                "alarms": [{"kind": "ood", "onset": {"frame": 1, "step": 100, "time": 0.05},
                            "confirmation": {"frame": 3, "step": 300, "time": 0.15}}]}
    rf.write_json_atomic(a / rf.PROGRESS, progress)
    run_json = rf.read_json(run / rf.RUN_JSON)
    run_json.update(analyses={a.name: progress}, producer={"last_step": 300},
                    stop={"observed_step": 300, "method": "halt", "by": "alarm"},
                    end={"state": "stopped", "stop_observed_step": 300})
    rf.write_json_atomic(run / rf.RUN_JSON, run_json)
    rf.append_event(run, "alarm", alarm="ood", onset={"step": 100}, confirmation={"step": 300},
                    test=["bonds", "T2"], report="analysis/x/alarms/1-ood-step300.json")
    rf.append_event(run, "stop_observed", step=300, how="halt")
    return run


async def test_web_a_stopped_run_shows_its_alarm_moments_and_events(tmp_path):
    run = _alarmed_run_folder(tmp_path)
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _open_run(page, run)
                active = page.locator(ACTIVE)
                status = active.locator(".live-status")
                state = status.locator("td.live-state")
                await expect(state).to_have_text("Alarm at step 300 · stopped at step 300", timeout=15000)
                await expect(state).to_have_class(re.compile(r"\bbad\b"))
                await expect(state.locator("svg.icon")).to_have_count(1)   # never colour alone
                events = active.locator(".live-events")
                await expect(events).to_contain_text("Alarm · estimated onset step 100 · confirmed at step 300")
                await expect(events).to_contain_text("Stop observed at step 300")
                await expect(active.locator(".analysis-panel .panel-plot .main-svg").first).to_be_attached()
                plot = await page.evaluate("""(sel) => {
                  const el = document.querySelector(sel + ' .analysis-panel[data-part="scores"] .panel-plot');
                  return {notes: (el.layout.annotations || []).map((a) => a.text),
                          rects: (el.layout.shapes || []).filter((s) => s.type === 'rect').length,
                          legend: !!el.layout.showlegend,
                          named: el.data.filter((t) => t.showlegend !== false).map((t) => t.name),
                          hover: el.data.filter((t) => t.name === 'confirmed').map((t) => t.hovertemplate),
                          caption: el._figureInfo.nSeries};
                }""", ACTIVE)
                # The moments are named by the legend, not by text floating near the
                # lines (which landed beside the wrong line when lines were close).
                assert plot["legend"] is True
                assert plot["named"] == ["Score ÷ threshold", "marked", "estimated onset", "confirmed",
                                         "stop observed"]
                assert not {"estimated onset", "confirmed", "stop observed"} & set(plot["notes"])
                assert plot["hover"] == ["confirmed · step %{x:,}<extra></extra>"]
                assert plot["rects"] == 1
                assert plot["caption"] == 1     # the caption still counts one data series
                # The "?" in the plot's title says what each legend entry means.
                scores = active.locator('.analysis-panel[data-part="scores"]')
                legend = scores.locator(".live-scores-explain")
                await expect(legend).to_be_hidden()
                await scores.locator(".panel-title .help-btn").click()
                await expect(legend).to_be_visible()
                for entry in ("Marked", "Chemistry flag", "Estimated onset", "Stop observed"):
                    await expect(legend).to_contain_text(entry)
            finally:
                await browser.close()


async def test_web_stop_now_is_in_the_live_runs_dialog_and_stop_on_alarm_in_the_tab(tmp_path, monkeypatch):
    run = _run_folder(tmp_path, frames=2)
    pointers = tmp_path / "pointers"
    pointers.mkdir()
    rf.write_json_atomic(pointers / f"{run.name}.json",
                         {"run_id": run.name, "run_dir": str(run), "started_utc": "2026-11-05T12:00:00+00:00"})
    monkeypatch.setenv("FFAST_LIVE_RUNS", str(pointers))
    meta = rf.read_json(run / rf.RUN_JSON)
    meta["analyses"] = {"0123456789abcdef": {"state": "scoring", "rule": {
        "window_frames": 100, "step_frames": 10, "fraction_threshold": 0.2, "persist_ps": 5.0, "frame_ps": 0.05}}}
    rf.write_json_atomic(run / rf.RUN_JSON, meta)
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _open_run(page, run)
                active = page.locator(ACTIVE)
                tick = active.locator(".live-stop-on-alarm")
                await expect(tick).not_to_be_checked(timeout=15000)
                # The alarm rule is small print: behind the "?" in the Run panel's
                # title row, and it stays open while the table redraws.
                status = active.locator('.analysis-panel[data-part="status"]')
                rule = status.locator(".live-rule")
                await expect(rule).to_be_hidden()
                await status.locator(".help-btn").click()
                await expect(rule).to_be_visible()
                await expect(rule).to_contain_text("Alarm rule: 20 %")
                await page.wait_for_timeout(1500)   # the card redraws every second
                await expect(rule).to_be_visible()
                await tick.check()
                await expect(tick).to_be_checked(timeout=10000)
                assert rf.read_json(run / rf.CONTROL)["stop_on_alarm"] is True

                # Stop now is not in the tab: "Manage runs…" opens the Live Runs
                # dialog on this run's row, and the stop is asked for there.
                await expect(active.locator(".live-stop-now")).to_have_count(0)
                await active.locator(".live-manage").click()
                dialog = page.locator("#live-runs-dialog")
                row = dialog.locator(f'tr[data-run-id="{run.name}"]')
                await expect(row).to_have_class(re.compile("live-runs-highlight"), timeout=10000)
                await row.locator("button.live-runs-stop").click()
                ask = page.locator(".ask-dialog")
                await expect(ask).to_contain_text("Stop Live 12:00:00 node?")
                await ask.get_by_role("button", name="Cancel").click()
                assert not (run / rf.STOP_REQUEST).exists()
                await row.locator("button.live-runs-stop").click()
                await page.locator(".ask-dialog").get_by_role("button", name="Stop run").click()
                await expect(row.locator("button.live-runs-stop")).to_be_disabled(timeout=10000)
                assert rf.read_json(run / rf.STOP_REQUEST)["by"] == "user"
                await dialog.get_by_role("button", name="Close").click()
                await expect(active.locator(".live-status td.live-state")).to_contain_text("Stop requested", timeout=10000)
            finally:
                await browser.close()


async def test_attribution_colours_on_a_fixed_scale(static_origin):
    script = f"""async () => {{
      const lr = await import('{static_origin}/live_run.js');
      const r3 = (c) => c.map((v) => Math.round(v * 255));
      const c = lr.attributionColours({{ atom_strength: [0, 1.5, 3, 0.2], bond_strength: {{'0-1': 1}} }});
      return {{
        zero: r3(lr.attributionRgb(0)), one: r3(lr.attributionRgb(1)), two: r3(lr.attributionRgb(2)),
        over: r3(lr.attributionRgb(7)), nan: r3(lr.attributionRgb(NaN)),
        atoms: c.atoms.map(r3), own: r3(c.bond(1, 0)), weaker: r3(c.bond(2, 3)),
      }};
    }}"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(f"{static_origin}/jsconfig.json")
            got = await page.evaluate(script)
        finally:
            await browser.close()
    assert got["zero"] == [0x4a, 0x50, 0x59] == got["nan"]
    assert got["one"] == [0xd5, 0x5e, 0x00] and got["two"] == [0xff, 0x3d, 0x00] == got["over"]
    assert got["atoms"][2] == got["two"]
    assert got["own"] == got["one"]                       # the bond's own group
    assert got["weaker"] == got["atoms"][3]               # else the weaker atom


async def test_web_clicking_a_live_frame_explains_and_colours_it(tmp_path):
    from tests.ffast.live.test_live_run import _scored_run_with_bundle

    run, _ = _scored_run_with_bundle(tmp_path)
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                # No scores in this folder: the plot needs some to click on.
                [adir] = list((run / rf.ANALYSIS_DIR).iterdir())
                rf.ScoresWriter(adir / rf.SCORES).append([
                    {"segment": 0, "frame": k, "step": 100 * k, "time": 0.05 * k, "identity": "proven",
                     "tracks": {}, "exceedance": [0.2, 3.0, 1.5][k], "test": ["bonds", "T2"],
                     "marked": k == 2, "chem": {"broken": 0, "close": 0}} for k in range(3)])
                await _open_run(page, run)
                active = page.locator(ACTIVE)
                att = active.locator(".live-attribution")
                await page.wait_for_function(SCORE_POINTS, arg=[ACTIVE, 3], timeout=15000)
                await page.locator("#frame-slider").fill("0")
                await expect(page.locator("#frame-label")).to_have_text("0 / 2", timeout=10000)
                # The shown frame is explained without a click.
                await expect(att).to_contain_text("Step 0", timeout=15000)
                # The tab's 3D panel is the run's own view, and attribution
                # explains that panel, as its "Explains" picker says.
                where = await page.evaluate("""() => {
                  const app = window.ffastApp, tab = app._analysis.tab(app._activeTab);
                  const [view] = app._independent.forTab(tab);
                  return {own: view?.datasetFp, explains: app._attributionSource().view === view?.renderers};
                }""")
                assert where == {"own": f"live:{run.name}", "explains": True}
                await expect(att.locator("xpath=..").locator("select.live-att-explains")).to_contain_text(
                    "(own view)")

                xy = await page.evaluate("""(sel) => {
                  const el = document.querySelector(sel + ' .analysis-panel[data-part="scores"] .panel-plot');
                  const fl = el._fullLayout, tr = el.data[0], r = el.getBoundingClientRect();
                  // _size: the margins as drawn, after the legend pushed them
                  return {x: r.left + fl._size.l + fl.xaxis.l2p(tr.x[2]),
                          y: r.top + fl._size.t + fl.yaxis.l2p(tr.y[2])};
                }""", ACTIVE)
                await page.mouse.move(xy["x"], xy["y"])
                await page.mouse.click(xy["x"], xy["y"])
                await expect(page.locator("#frame-label")).to_have_text("2 / 2", timeout=10000)
                await expect(att).to_contain_text("Step 200", timeout=15000)
                await expect(att.locator("table.live-att-groups tr").nth(1)).to_be_visible()
                # "Asking DetectAna…" has a line of its own, there and empty while
                # nothing is asked, so the card does not move when it is.
                status = att.locator(".live-att-status")
                await expect(status).to_have_text("")
                assert (await status.bounding_box())["height"] > 0
                # What a strength means is small print, behind the "?" by Strength.
                explain = att.locator(".live-att-explain")
                await expect(explain).to_be_hidden()
                await att.locator(".help-btn").click()
                await expect(explain).to_be_visible()
                await expect(explain).to_contain_text("do not add up to the score")
                shown = "() => window.ffastApp.renderer.attributionShown"
                await page.wait_for_function(shown, timeout=10000)

                # The bonds test names the stretched bond first.
                await att.locator("select.live-att-test").select_option("bonds|T2")
                await expect(att.locator("table.live-att-groups tr").nth(1)).to_contain_text("bond C0-H14",
                                                                                            timeout=15000)
                # Element colours take the attribution off.
                await att.locator('button[data-mode="element"]').click()
                await page.wait_for_function(f"() => !({shown[6:]})", timeout=10000)
                await att.locator('button[data-mode="attribution"]').click()
                await page.wait_for_function(shown, timeout=10000)
                # The frame buttons and slider move the attribution with the frame.
                await page.locator("#prev-frame-btn").click()
                await expect(page.locator("#frame-label")).to_have_text("1 / 2", timeout=10000)
                await expect(att.locator(".live-att-head")).to_contain_text("Step 100", timeout=15000)
                await expect(att).not_to_contain_text("Asking DetectAna")
                await page.wait_for_function(shown, timeout=10000)
                await page.locator("#frame-slider").fill("0")
                await expect(page.locator("#frame-label")).to_have_text("0 / 2", timeout=10000)
                await expect(att.locator(".live-att-head")).to_contain_text("Step 0", timeout=15000)
                await expect(att).not_to_contain_text("Asking DetectAna")
                await expect(explain).to_be_visible()   # stays open from frame to frame
            finally:
                await browser.close()


async def test_web_the_attribution_card_explains_the_3d_panel_picked_in_it(tmp_path):
    from tests.ffast.live.test_live_run import _scored_run_with_bundle

    run, _ = _scored_run_with_bundle(tmp_path)
    [adir] = list((run / rf.ANALYSIS_DIR).iterdir())
    rf.ScoresWriter(adir / rf.SCORES).append([
        {"segment": 0, "frame": k, "step": 100 * k, "time": 0.05 * k, "identity": "proven",
         "tracks": {}, "exceedance": 0.5, "test": ["bonds", "T2"], "marked": False,
         "chem": {"broken": 0, "close": 0}} for k in range(3)])
    tabs = tmp_path / "tabs"
    _seed_two_own_view_live_layout(tabs, attribution=True)
    async with _spawn_server("--tabs-dir", str(tabs)) as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1600, "height": 1000})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _open_run(page, run)
                active = page.locator(ACTIVE)
                att = active.locator(".live-attribution")
                pick = active.locator("select.live-att-explains")
                # With Same step off, both own views start at the latest frame.
                await page.wait_for_function("""() => {
                  const app = window.ffastApp, tab = app._analysis.tab(app._activeTab);
                  const views = app._independent.forTab(tab);
                  return views.length === 2 && views.every((v) => v.frame === 2);
                }""", timeout=20000)
                await page.locator("#frame-slider").fill("0")
                await expect(att.locator(".live-att-head")).to_contain_text("Step 0", timeout=20000)
                await page.evaluate("""() => {
                  const app = window.ffastApp, tab = app._analysis.tab(app._activeTab);
                  const [, v2] = app._independent.forTab(tab);
                  app._independent.setFrame(v2, 2);
                }""")
                await expect(pick.locator("option")).to_have_count(2)
                await expect(pick).to_have_value("0")   # the first 3D panel by default
                await expect(att.locator(".live-att-head")).to_contain_text("Step 0")
                # Picking the second panel explains its frame, and is saved in the layout.
                await pick.select_option("1")
                await expect(active.locator("select.live-att-explains")).to_have_value("1", timeout=15000)
                await expect(att.locator(".live-att-head")).to_contain_text("Step 200", timeout=15000)
                assert "explains = 1" in (tabs / "live-run.toml").read_text()
            finally:
                await browser.close()


async def test_attribution_follows_the_shown_frame_one_request_at_a_time(static_origin):
    """Frames passed while an answer is computed are skipped; the last one is asked next."""
    script = """async () => {
      const { LiveUi } = await import('./live_ui.js');
      const sent = [];
      const shown = { datasetFp: 'fpA', frame: 0 };
      let run = { run_id: 'r1', bundle: null };
      const ui = new LiveUi({ send: (e, kw) => sent.push(kw), shown: () => shown,
        runOf: (fp) => (fp === 'fpA' ? run : null) });
      const frames = () => sent.map((kw) => kw.frame);
      const out = {};
      ui.sync();
      out.noBundle = frames();                       // no scores yet: nothing asked
      run = { run_id: 'r1', bundle: '/b' };
      ui.sync();
      out.first = frames();
      for (const f of [1, 2, 3]) { shown.frame = f; ui.sync(); }
      out.whileBusy = frames();                      // still only frame 0 in flight
      ui.onResult({ run_id: 'r1', frame: 0, groups: [] });
      out.afterFirst = frames();                     // 1 and 2 skipped
      ui.onResult({ run_id: 'r1', frame: 3, groups: [] });
      out.settled = frames();
      ui.command({ action: 'attribute', run_id: 'r1', frame: 3, track: 'bonds', score: 'T2' });
      out.test = sent.at(-1);
      shown.datasetFp = 'other'; shown.frame = 7; ui.sync();
      out.otherDataset = sent.length;
      return out;
    }"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(f"{static_origin}/jsconfig.json")
            got = await page.evaluate(script)
        finally:
            await browser.close()
    assert got["noBundle"] == []
    assert got["first"] == [0] and got["whileBusy"] == [0]
    assert got["afterFirst"] == [0, 3] and got["settled"] == [0, 3]
    assert got["test"] == {"run_id": "r1", "frame": 3, "track": "bonds", "score": "T2"}
    assert got["otherDataset"] == 3


async def _row_can_stop(static_origin, row):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(f"{static_origin}/jsconfig.json")
            return await page.evaluate(f"""async () => {{
              const d = await import('{static_origin}/live_runs_dialog.js');
              return d.liveRunRow({json.dumps(row)}).canStop;
            }}""")
        finally:
            await browser.close()


async def test_live_runs_rows_say_it_in_the_run_panels_words(static_origin):
    # Plain text, colour only for trouble; the alarms are part of State.
    rows = [
        {"run_id": "a", "name": "Live 10:33:35 mel0314", "run_dir": "/r/a", "exists": True, "bundle": "aspirin_mace",
         "slurm_job_id": "5428960",
         "last_step": 412300, "planned_end_step": 1000000, "first_step": 0, "scored_through_step": 411900,
         "scorer_state": "scoring", "heartbeat_age_s": 3, "stalled": False,
         "alarms": [{"kind": "ood", "step": 386000}], "end": None, "state": "running"},
        {"run_id": "b", "name": "Live 09:12:02 mel0207", "run_dir": "/r/b", "exists": True, "bundle": "aspirin_mace",
         "last_step": 230000, "planned_end_step": 500000, "first_step": 0, "scored_through_step": 118000,
         "scorer_state": "scoring", "heartbeat_age_s": 240, "stalled": True, "alarms": [], "end": None,
         "state": "running"},
        {"run_id": "c", "name": "Live 15:02:01 mel0110", "run_dir": "/r/c", "exists": True, "bundle": None,
         "last_step": 61500, "planned_end_step": None, "scored_through_step": 61500, "scorer_state": "done",
         "heartbeat_age_s": 9000, "stalled": False, "alarms": [{"kind": "chem", "step": 58000}],
         "end": {"state": "terminated", "stop_observed_step": 61500}, "state": "terminated"},
        {"run_id": "d", "name": "Live 08:00:00 n", "run_dir": "/gone/d", "exists": False},
    ]
    script = f"""async () => {{
      const d = await import('{static_origin}/live_runs_dialog.js');
      return {json.dumps(rows)}.map(d.liveRunRow);
    }}"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(f"{static_origin}/jsconfig.json")
            a, b, c, d = await page.evaluate(script)
        finally:
            await browser.close()
    assert a["lammps"] == "LAMMPS 412,300 / 1,000,000" and a["scored"] == "scored 411,900"
    assert round(a["lammpsShare"], 3) == 0.412
    assert a["scorer"] == {"text": "running", "tone": "", "title": "Last heartbeat 3 s ago"}
    assert a["state"] == {"text": "Alarm at step 386,000", "tone": "bad"} and "alarm" not in a
    # The row names the reference; the folder, run ID and job are "More info".
    assert a["sub"] == "aspirin_mace" and a["canOpen"]
    assert a["more"] == [["Folder", "/r/a"], ["Run ID", "a"], ["Slurm job", "5428960"]]
    assert c["sub"] == "" and c["more"] == [["Folder", "/r/c"], ["Run ID", "c"]]
    assert d["sub"] == "" and d["more"] == [["Folder", "/gone/d"], ["Run ID", "d"]]
    assert b["scorer"] == {"text": "silent 4 min — stalled?", "tone": "warn"}
    assert b["state"] == {"text": "No alarm", "tone": ""}
    assert c["lammpsShare"] is None and c["scorer"] == {"text": "done", "tone": ""}
    assert c["state"] == {"text": "Chemistry alarm at step 58,000 · terminated; last saved frame at step 61,500",
                          "tone": "bad"}
    assert d["state"] == {"text": "Folder not found", "tone": "warn"} and not d["canOpen"]
    # Stop now only for a run open on this server that has not ended.
    assert [x.get("canStop") for x in (a, b, c, d)] == [False, False, False, False]
    assert (await _row_can_stop(static_origin, {**rows[0], "open": True})) is True
    assert (await _row_can_stop(static_origin, {**rows[2], "open": True})) is False


async def test_web_the_live_runs_dialog_lists_opens_and_forgets(tmp_path, monkeypatch):
    run = _run_folder(tmp_path, frames=2)
    pointers = tmp_path / "pointers"
    pointers.mkdir()
    rf.write_json_atomic(pointers / f"{run.name}.json",
                         {"run_id": run.name, "run_dir": str(run), "started_utc": "2026-11-05T12:00:00+00:00"})
    rf.write_json_atomic(pointers / "20261104-080000-n-1-0000.json",
                         {"run_id": "20261104-080000-n-1-0000", "run_dir": str(tmp_path / "gone"),
                          "started_utc": "2026-11-04T08:00:00+00:00"})
    monkeypatch.setenv("FFAST_LIVE_RUNS", str(pointers))
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _lammps_menu(page, "Live Runs…")
                dialog = page.locator("#live-runs-dialog")
                rows = dialog.locator("table.live-runs-table tr[data-run-id]")
                await expect(rows).to_have_count(2, timeout=10000)
                await expect(rows.nth(0)).to_contain_text("Live 12:00:00 node")
                # The folder is detail: under "More info", not on the row.
                folder = rows.nth(0).locator(".live-runs-more dd", has_text=str(run))
                await expect(folder).to_be_hidden()
                await rows.nth(0).locator(".live-runs-more summary").click()
                await expect(folder).to_be_visible()
                await expect(rows.nth(1)).to_contain_text("Folder not found")
                await rows.nth(1).locator("button.live-runs-forget").click()
                await expect(rows).to_have_count(1, timeout=10000)
                assert not (pointers / "20261104-080000-n-1-0000.json").exists()
                await rows.nth(0).locator("button.live-runs-open").click()
                await expect(dialog).to_have_count(0)
                tab = page.locator("#tabbar .tab", has_text="Live 12:00:00 node")
                await expect(tab).to_have_class(re.compile(r"\bactive\b"), timeout=15000)
            finally:
                await browser.close()


async def test_make_reference_problems_and_names(static_origin):
    script = f"""async () => {{
      const m = await import('{static_origin}/make_reference_dialog.js');
      const two = [{{ fp: 'a' }}, {{ fp: 'b' }}];
      return {{
        one: m.referenceProblem('a', '', [{{ fp: 'a' }}]), same: m.referenceProblem('a', 'a', two),
        ok: m.referenceProblem('a', 'b', two),
        names: ['aspirin train.xyz', 'mace.extxyz', '', '***'].map(m.referenceName),
      }};
    }}"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(f"{static_origin}/jsconfig.json")
            got = await page.evaluate(script)
        finally:
            await browser.close()
    assert got["one"].startswith("Needs two datasets") and "different" in got["same"] and got["ok"] == ""
    assert got["names"] == ["aspirin_train", "mace", "reference", "reference"]


async def test_web_make_reference_fits_a_bundle_from_two_loaded_datasets(tmp_path):
    from tests.ffast.live.bundle_helpers import write_frames
    from tests.ffast.renderers.web.test_web_runtime import _connect_headless_client, _wait_for_event
    from ffast.protocol.rpc import pack

    train = write_frames(tmp_path / "train.xyz", 300, seed=1)
    valid = write_frames(tmp_path / "valid.xyz", 150, seed=2)
    out = tmp_path / "refs"
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        ws = await _connect_headless_client(ws_port)
        try:
            for path in (train, valid):
                await ws.send(pack("LOAD_DATASET", (str(path), "ase (auto)"), {}))
                await _wait_for_event(ws, "REMOTE_DATASET_META", timeout=30)
        finally:
            await ws.send(pack("GRACEFUL_DISCONNECT", (), {}))
            await ws.close()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1300, "height": 850})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await expect(page.locator("#dataset-list .obj-row")).to_have_count(2, timeout=15000)
                await _lammps_menu(page, "Make Reference…")
                dialog = page.locator("#make-reference-dialog")
                await expect(dialog.locator("#make-ref-train option")).to_have_count(2)
                # The detection settings are small print, behind a "?".
                settings = dialog.locator(".make-ref-settings")
                await expect(settings).to_be_hidden()
                await dialog.locator(".help-btn").click()
                await expect(settings).to_be_visible()
                await expect(settings).to_contain_text("False-alarm rate per frame 1 %")
                # The same dataset twice is refused before anything is sent.
                train_fp = await dialog.locator("#make-ref-train").input_value()
                await dialog.locator("#make-ref-valid").select_option(train_fp)
                await expect(dialog.locator(".make-ref-problem")).to_contain_text("different datasets")
                await expect(dialog.locator(".make-ref-go")).to_be_disabled()
                other = [o for o in await dialog.locator("#make-ref-valid option").evaluate_all(
                    "(os) => os.map((o) => o.value)") if o != train_fp][0]
                await dialog.locator("#make-ref-valid").select_option(other)
                await dialog.locator("#make-ref-name").fill("aspirin")
                await dialog.locator("#make-ref-folder").fill(str(out))
                await dialog.locator(".make-ref-go").click()
                await expect(dialog.locator(".make-ref-ok")).to_contain_text("Reference written", timeout=60000)
                await expect(dialog.locator(".make-ref-line code").first).to_have_text(
                    f"include {out / 'aspirin.lmp'}")
            finally:
                await browser.close()
    assert (out / "aspirin.bundle" / "manifest.json").is_file() and (out / "aspirin.lmp").is_file()


# ── a live tab draws the runs ticked in its selector (ADR 0089) ──────────────

RUN_A, RUN_B = "20261105-120000-node-1-abcd", "20261105-130000-node-2-efgh"


def _two_runs(tmp_path):
    """Two run folders with different run IDs, each with one event."""
    a = _run_folder(tmp_path, frames=2)
    (tmp_path / "b").mkdir()
    b = _run_folder(tmp_path / "b", frames=3)
    meta = rf.read_json(b / rf.RUN_JSON)
    meta["run_id"] = RUN_B
    rf.write_json_atomic(b / rf.RUN_JSON, meta)
    rf.append_event(a, "warning", text="slow disk in A")
    rf.append_event(b, "warning", text="slow disk in B")
    return a, b


async def _open_run(page, folder):
    """Live Runs… → Open run folder…: go to `folder` in the server's file
    browser and open it."""
    await _lammps_menu(page, "Live Runs…")
    await page.get_by_role("button", name="Open run folder…", exact=True).click()
    await expect(page.locator("#fb-modal")).to_be_visible()
    await page.locator("#fb-path").fill(str(folder))
    await page.locator("#fb-path").press("Enter")
    await expect(page.locator("#fb-path")).to_have_value(str(folder))
    await page.locator("#fb-load").click()
    await expect(page.locator("#fb-modal")).to_be_hidden()


async def test_the_helpers_that_merge_several_runs(static_origin):
    script = f"""async () => {{
      const lr = await import('{static_origin}/live_run.js');
      const mk = (name, steps, flags, events, alarm) => ({{ name, kw: {{
        series: {{ step: steps, marked: flags, chem: steps.map(() => false) }},
        progress: {{ alarms: alarm ? [{{ kind: 'ood', onset: {{ step: 100 }}, confirmation: {{ step: 300 }} }}] : [] }},
        events }} }});
      const a = mk('aspirin_nvt', [0, 100], [false, true], [{{ kind: 'warning', text: 'a1', t: '2026-11-05T12:00:05+00:00' }}], true);
      const b = mk('other', [0, 50], [true, false], [{{ kind: 'warning', text: 'b1', t: '2026-11-05T12:00:01+00:00' }}], true);
      const one = lr.liveRunsOverlays([a]);
      const two = lr.liveRunsOverlays([a, b]);
      return {{
        oneLabels: one.lines.map((l) => l.label),
        twoLabels: two.lines.map((l) => l.label),
        twoBands: two.bands.length,
        oneTicks: one.ticks.map((t) => [t.name, t.row, t.xs, t.series ?? null]),
        twoTicks: two.ticks.map((t) => [t.name, t.row, t.xs, t.series ?? null]),
        events: lr.liveRunsEvents([a, b]).map((e) => [e.run, e.event.text]),
        msg: lr.liveNoScoreMessage('ethanol'),
      }};
    }}"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(f"{static_origin}/jsconfig.json")
            got = await page.evaluate(script)
        finally:
            await browser.close()
    assert got["oneLabels"] == ["estimated onset", "confirmed"]
    assert got["twoLabels"] == ["aspirin_nvt: estimated onset", "aspirin_nvt: confirmed",
                                "other: estimated onset", "other: confirmed"]
    assert got["twoBands"] == 2
    # One run: its two rows, plain names, their own colours. Several: each run
    # its own pair of rows, named for it and in its curve's colour (`series`),
    # so one run's marks are never read as another's.
    assert got["oneTicks"] == [["marked", 0, [100], None], ["chemistry flag", 1, [], None]]
    assert got["twoTicks"] == [["aspirin_nvt: marked", 0, [100], 0], ["aspirin_nvt: chemistry flag", 1, [], 0],
                               ["other: marked", 2, [0], 1], ["other: chemistry flag", 3, [], 1]]
    assert got["events"] == [["other", "b1"], ["aspirin_nvt", "a1"]]    # by time, oldest first
    assert got["msg"] == "No score for ethanol: only live runs have scores for now."


async def test_web_a_live_tab_draws_the_runs_ticked_in_its_selector(tmp_path):
    a, b = _two_runs(tmp_path)
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1600, "height": 1000})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _open_run(page, a)
                await expect(page.locator("#tabbar .tab", has_text="Live 12:00:00 node")).to_be_visible(timeout=15000)
                await _open_run(page, b)
                await expect(page.locator("#tabbar .tab", has_text="Live 13:00:00 node")).to_be_visible(timeout=15000)
                await expect(page.locator("#dataset-list .obj-row")).to_have_count(2, timeout=15000)
                # Run B's tab shows it in its own 3D panel: opening B left the main
                # view on run A (the first dataset, shown there as any first one is).
                shows = """() => {
                  const app = window.ffastApp, tab = app._analysis.tab(app._activeTab);
                  return [app._currentDatasetFp, app._independent.forTab(tab).map((v) => v.datasetFp)];
                }"""
                await page.wait_for_function(f"() => ({shows})()[1][0]", timeout=15000)
                assert await page.evaluate(shows) == [f"live:{RUN_A}", [f"live:{RUN_B}"]]

                # Each run has its own tab, opened with that run ticked and no other.
                await page.locator("#tabbar .tab", has_text="Live 12:00:00 node").click()
                active = page.locator(ACTIVE)
                pick = lambda run: active.locator(f'.ac-series button[data-fp="live:{run}"]')  # noqa: E731
                await expect(pick(RUN_A)).to_have_class(re.compile(r"\bactive\b"), timeout=15000)
                await expect(pick(RUN_B)).not_to_have_class(re.compile(r"\bactive\b"))
                # A live tab has no metric panel, so no prediction row.
                await expect(active.locator('.ac-item[data-series="models"]')).to_have_count(0)

                # The left-hand list does not move this tab's ticks.
                await page.locator("#dataset-list .obj-row").nth(1).click()
                await expect(pick(RUN_A)).to_have_class(re.compile(r"\bactive\b"))
                await expect(pick(RUN_B)).not_to_have_class(re.compile(r"\bactive\b"))

                # One run: one curve, one status row, no Run column.
                scores = active.locator('.analysis-panel[data-part="scores"] .panel-plot')
                await expect(scores.locator(".main-svg").first).to_be_attached(timeout=15000)
                names = lambda: page.evaluate("""(sel) => {  // noqa
                  const el = document.querySelector(sel + ' .analysis-panel[data-part="scores"] .panel-plot');
                  return el.data.filter((t) => t.meta !== 'overlay' && (t.showlegend !== false || t.mode))
                    .map((t) => t.name).filter((n) => /node|Score/.test(n));
                }""", ACTIVE)
                marks = lambda: page.evaluate("""(sel) => {  // noqa
                  const el = document.querySelector(sel + ' .analysis-panel[data-part="scores"] .panel-plot');
                  return el.data.filter((t) => t.meta === 'overlay' && t.showlegend && /marked/.test(t.name))
                    .map((t) => t.name);
                }""", ACTIVE)
                assert await names() == ["Score ÷ threshold"]      # one run: as before
                await expect(active.locator(".live-status tbody tr")).to_have_count(1)
                await expect(active.locator(".live-event-list th")).to_have_count(0)

                # Ticking the other run adds its curve, status row and events.
                await pick(RUN_B).click()
                await expect(active.locator(".live-status tbody tr")).to_have_count(2, timeout=15000)
                assert sorted(await names()) == ["12:00:00 node", "13:00:00 node"]
                # Each run's marks are its own legend entry, not pooled.
                got = await marks()
                assert got and all(n.endswith("node: marked") for n in got), got
                await expect(active.locator(".live-event-list tr:not(:has(th))")).to_have_count(2)
                await expect(active.locator(".live-event-list")).to_contain_text("13:00:00 node")
                await expect(active.locator(".live-event-list")).to_contain_text("slow disk in A")

                # A click on run B's curve asks for B's attribution, and the panel names B.
                await page.evaluate("""() => {
                  const an = window.ffastApp._analysis, old = an._onLive;
                  window.__cmds = [];
                  an._onLive = (c) => { window.__cmds.push(c); return old(c); };
                }""")
                xy = await page.evaluate("""(sel) => {
                  const el = document.querySelector(sel + ' .analysis-panel[data-part="scores"] .panel-plot');
                  const tr = el.data.find((t) => t.name === '13:00:00 node');
                  const fl = el._fullLayout, r = el.getBoundingClientRect();
                  const i = tr.x.length - 1;
                  return {x: r.left + fl._size.l + fl.xaxis.l2p(tr.x[i]),
                          y: r.top + fl._size.t + fl.yaxis.l2p(tr.y[i])};
                }""", ACTIVE)
                await page.mouse.move(xy["x"], xy["y"])
                await page.mouse.click(xy["x"], xy["y"])
                await page.wait_for_function("() => window.__cmds.some((c) => c.action === 'attribute')",
                                             timeout=10000)
                cmd = await page.evaluate("() => window.__cmds.find((c) => c.action === 'attribute')")
                assert cmd["run_id"] == RUN_B
            finally:
                await browser.close()


async def test_web_a_hint_says_how_to_compare_runs(tmp_path):
    a, b = _two_runs(tmp_path)
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1600, "height": 1000})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _open_run(page, a)
                tab_a = page.locator("#tabbar .tab", has_text="Live 12:00:00 node")
                await expect(tab_a).to_be_visible(timeout=15000)
                hint = page.locator(f"{ACTIVE} .live-hint")
                await page.wait_for_timeout(1500)
                await expect(hint).to_be_hidden()          # one run: never shown
                await _open_run(page, b)
                await expect(page.locator("#tabbar .tab", has_text="Live 13:00:00 node")).to_be_visible(timeout=15000)
                await tab_a.click()
                await expect(hint).to_be_visible(timeout=15000)
                await expect(hint).to_contain_text("Compare runs: tick several runs in this tab's selector.")
                await page.locator("#tabbar .tab", has_text="Live 13:00:00 node").click()
                await expect(hint).to_be_visible()          # in both tabs
                await hint.get_by_role("button").click()
                await expect(hint).to_be_hidden()
                await page.reload(wait_until="networkidle")
                await _expect_connected(page)
                await page.locator("#tabbar .tab", has_text="Live 12:00:00 node").click()
                await page.wait_for_timeout(1500)
                await expect(page.locator(f"{ACTIVE} .live-hint")).to_be_hidden()   # dismissed for good
            finally:
                await browser.close()


# ── Same step in all 3D panels (ADR 0089) ────────────────────────────────────

async def test_nearest_step_frame(static_origin):
    script = f"""async () => {{
      const f = await import('{static_origin}/frame_links.js');
      const n = f.nearestStepFrame;
      return [n([0, 100, 200], 100), n([0, 100, 200], 120), n([0, 100, 200], 160), n([0, 100, 200], -1),
              n([0, 100, 200], 201), n([], 5), n([0, 50, 100, 0, 50, 100], 50), n([0, 100], 50)];
    }}"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(f"{static_origin}/jsconfig.json")
            got = await page.evaluate(script)
        finally:
            await browser.close()
    # exact; between two (the nearer); between two (the nearer, the later); before first;
    # after last; no frames; repeated steps after a restart (the last of them); a tie (the later)
    assert got == [1, 1, 2, None, None, None, 4, 1]


def _stepped_run(root, run_id, every, n, level=0.5):
    """A run folder with `n` frames `every` steps apart, scored at each."""
    root.mkdir()
    run = _run_folder(root, frames=n, scores=False)
    meta = rf.read_json(run / rf.RUN_JSON)
    meta["run_id"] = run_id
    rf.write_json_atomic(run / rf.RUN_JSON, meta)
    from tests.ffast.live.dump_helpers import dump_text
    (run / rf.FRAMES).write_text(dump_text(random_frames(n), every=every))
    a = run / rf.ANALYSIS_DIR / "0123456789abcdef"
    a.mkdir(parents=True)
    rf.write_json_atomic(a / rf.SETTINGS, {"bundle": "/b.bundle", "scores_format": 2, "role": "centroid"})
    rf.ScoresWriter(a / rf.SCORES).append([
        {"segment": 0, "frame": k, "step": every * k, "time": 0.05 * k, "identity": "proven", "tracks": {},
         "exceedance": level, "test": ["bonds", "T2"], "marked": False, "chem": {"broken": 0, "close": 0}}
        for k in range(n)])
    return run


def _seed_two_own_view_live_layout(tabs, attribution=False):
    """A user "Live run" layout with two own-view 3D panels and the Same step
    control; with `attribution`, an attribution card below them."""
    import tomli_w

    tabs.mkdir()
    panels = [
        {"kind": "3d", "view": "independent", "row": 0, "col": 0, "rowspan": 8, "colspan": 8},
        {"kind": "3d", "view": "independent", "row": 0, "col": 8, "rowspan": 8, "colspan": 8},
        {"kind": "live", "row": 0, "col": 16, "rowspan": 8, "colspan": 8, "title": "Scores",
         "options": {"part": "scores"}},
    ]
    if attribution:
        panels.append({"kind": "live", "row": 8, "col": 0, "rowspan": 8, "colspan": 24,
                       "title": "Attribution", "options": {"part": "attribution"}})
    tab = {"name": "Live run", "live_layout": True, "grid": 24, "controls": ["same_step"], "panels": panels}
    (tabs / "live-run.toml").write_text(tomli_w.dumps({"tabs": [tab]}))
    (tabs / "state.json").write_text(json.dumps(
        {"replaces": {"live-run.toml": {"tab": "Live run", "original": None}}, "order": ["live-run.toml"]}))


async def test_web_same_step_links_own_view_panels_of_runs_saved_at_different_intervals(tmp_path):
    a = _stepped_run(tmp_path / "a", RUN_A, every=100, n=5)     # steps 0, 100 … 400
    b = _stepped_run(tmp_path / "b", RUN_B, every=25, n=17, level=0.9)     # steps 0, 25 … 400
    tabs = tmp_path / "tabs"
    _seed_two_own_view_live_layout(tabs)
    async with _spawn_server("--tabs-dir", str(tabs)) as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1600, "height": 1000})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _open_run(page, a)
                await expect(page.locator("#tabbar .tab", has_text="Live 12:00:00 node")).to_be_visible(timeout=15000)
                await _open_run(page, b)
                await expect(page.locator("#tabbar .tab", has_text="Live 13:00:00 node")).to_be_visible(timeout=15000)
                await expect(page.locator("#dataset-list .obj-row")).to_have_count(2, timeout=15000)
                await page.locator("#tabbar .tab", has_text="Live 12:00:00 node").click()
                active = page.locator(ACTIVE)
                await active.locator(f'.ac-series button[data-fp="live:{RUN_B}"]').click()
                # Panel 1 shows run A, panel 2 run B; each is its own view.
                await page.evaluate("""(b) => {
                  const app = window.ffastApp, tab = app._analysis.tab(app._activeTab);
                  const [v1, v2] = app._independent.forTab(tab);
                  v2.datasetFp = 'live:' + b; app._independent.open(v2);
                  window.__v = [v1, v2];
                }""", RUN_B)
                await page.wait_for_function("() => window.__v[0].isOpen && window.__v[1].isOpen", timeout=15000)
                await expect(active.locator('.ac-item[data-control="same_step"]')).to_contain_text(
                    "Same step in all 3D panels")
                async def click_curve(name, index):
                    xy = await page.evaluate("""([sel, name, i]) => {
                      const el = document.querySelector(sel + ' .analysis-panel[data-part="scores"] .panel-plot');
                      const tr = el.data.find((t) => t.name === name);
                      const fl = el._fullLayout, r = el.getBoundingClientRect();
                      return {x: r.left + fl._size.l + fl.xaxis.l2p(tr.x[i]),
                              y: r.top + fl._size.t + fl.yaxis.l2p(tr.y[i])};
                    }""", [ACTIVE, name, index])
                    await page.mouse.move(xy["x"], xy["y"])
                    await page.mouse.click(xy["x"], xy["y"])

                frames = "() => window.__v.map((v) => v.frame ?? 0)"
                # Off by default: a click on A's curve (step 100) moves only A's panel.
                await click_curve("12:00:00 node", 1)
                await page.wait_for_function("() => window.__v[0].frame === 1", timeout=10000)
                await page.wait_for_timeout(500)
                assert await page.evaluate(frames) == [1, 0]
                # On: the same click moves B's panel to B's frame at step 100 (frame 4).
                await active.locator('.ac-item[data-control="same_step"] input').check()
                await click_curve("12:00:00 node", 2)       # step 200
                await page.wait_for_function("() => window.__v[1].frame === 8", timeout=10000)
                assert await page.evaluate(frames) == [2, 8]
                # And from B's curve back to A (B's frame 12 is step 300; A's frame 3).
                await click_curve("13:00:00 node", 12)
                await page.wait_for_function("() => window.__v[0].frame === 3", timeout=10000)
                assert await page.evaluate(frames) == [3, 12]
            finally:
                await browser.close()


# ── editing a live tab edits the Live run layout (ADR 0089) ──────────────────

async def test_web_editing_a_live_tab_edits_the_layout_of_every_live_tab(tmp_path):
    from tests.ffast.renderers.web.test_web_runtime import _tab_menu

    a, b = _two_runs(tmp_path)
    tabs = tmp_path / "tabs"
    tabs.mkdir()
    async with _spawn_server("--tabs-dir", str(tabs)) as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1600, "height": 1000})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _open_run(page, a)
                tab_a = page.locator("#tabbar .tab", has_text="Live 12:00:00 node")
                await expect(tab_a).to_be_visible(timeout=15000)
                await _open_run(page, b)
                tab_b = page.locator("#tabbar .tab", has_text="Live 13:00:00 node")
                await expect(tab_b).to_be_visible(timeout=15000)
                await tab_a.click()
                events = page.locator(f"{ACTIVE} .live-events")
                await expect(events).to_have_count(1)
                # A live tab offers no Reset while the built-in layout is in use.
                await page.locator("#tab-menu-btn").click()
                await expect(page.locator("#tab-menu-list").get_by_role("menuitem", name="Export as TOML…")).to_be_visible()
                await expect(page.locator("#tab-menu-list").get_by_role("menuitem", name="Reset to original…")).to_have_count(0)
                await page.keyboard.press("Escape")

                # Edit mode edits the layout: its name is fixed; remove the events panel; save.
                await page.locator("#tab-edit-btn").click()
                await page.locator("[data-edit=tab-settings]").click()
                await expect(page.locator(".edit-modal input[data-field=name]")).to_have_value("Live run")
                await expect(page.locator(".edit-modal input[data-field=name]")).to_be_disabled()
                await page.locator(".edit-modal").get_by_role("button", name="Cancel").click()
                await page.locator(f'{CHROME}[data-panel="4"] [data-edit=remove]').click()   # the events panel
                await page.locator("[data-edit=save]").click()
                await expect(page.locator(".tabpanel.active .edit-bar")).to_have_count(0, timeout=15000)
                assert (tabs / "live-run.toml").exists()
                await expect(page.locator(f"{ACTIVE} .live-status")).to_have_count(1)
                await expect(page.locator(f"{ACTIVE} .live-events")).to_have_count(0)
                await tab_b.click()
                await expect(page.locator(f"{ACTIVE} .live-status")).to_have_count(1)
                await expect(page.locator(f"{ACTIVE} .live-events")).to_have_count(0)
                # The edited layout is not a tab of the bar.
                await expect(page.locator("#tabbar .tab", has_text="Live run")).to_have_count(0)

                # Reset brings the original layout back in both.
                await _tab_menu(page, "Reset to original…")
                await page.locator(".ask-dialog").get_by_role("button", name="Confirm").click()
                await expect(page.locator(f"{ACTIVE} .live-events")).to_have_count(1, timeout=15000)
                await tab_a.click()
                await expect(page.locator(f"{ACTIVE} .live-events")).to_have_count(1)
                assert not (tabs / "live-run.toml").exists()
            finally:
                await browser.close()


import contextlib  # noqa: E402  (to_fix_v3 tests below)


async def _pure(static_origin, script):
    """Run `script` (an async JS function) in a page that can import the static modules."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(f"{static_origin}/jsconfig.json")
            return await page.evaluate(script)
        finally:
            await browser.close()


def _three_scored_frames(tmp_path):
    """A run of 3 frames, frame 2 marked; every score high on the plot, so a
    click by the marks along the bottom is not on the curve."""
    run = _run_folder(tmp_path, frames=3, scores=False)
    adir = run / rf.ANALYSIS_DIR / "0123456789abcdef"
    adir.mkdir(parents=True)
    rf.write_json_atomic(adir / rf.SETTINGS, {"bundle": "/b.bundle",
                                              "thresholds": {"geometric": {"bead": 5.0, "centroid": 5.0}}})
    rf.ScoresWriter(adir / rf.SCORES).append([
        {"segment": 0, "frame": k, "step": 100 * k, "time": 0.05 * k, "identity": "proven",
         "tracks": {}, "exceedance": [2.5, 3.0, 2.8][k], "test": ["bonds", "T2"],
         "marked": k == 2, "chem": {"broken": 0, "close": 0}} for k in range(3)])
    return run


@contextlib.asynccontextmanager
async def _live_page(run):
    """A page showing `run` in its live tab, its score curve drawn."""
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1400, "height": 900}, accept_downloads=True)
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _open_run(page, run)
                await page.wait_for_function(SCORE_POINTS, arg=[ACTIVE, 3], timeout=15000)
                yield page
            finally:
                await browser.close()


async def test_a_picture_has_the_background_asked_for_and_the_csv_only_the_scores(static_origin):
    """to_fix_v3: a PNG with a transparent background or with one; the CSV
    leaves out a live run's marks and lines."""
    got = await _pure(static_origin, f"""async () => {{
      const ex = await import('{static_origin}/panel_export.js');
      return {{
        transparent: ex.pictureLayout({{ a: 1 }}, null), filled: ex.pictureLayout({{ a: 1 }}, 'rgb(1, 2, 3)'),
        csv: ex.plotCsv([{{ name: 's', x: [0, 100], y: [1, 2] }},
          {{ name: 'confirmed', meta: 'overlay', x: [100, 100], y: [0, 1] }}], {{ xTitle: 'Step', yTitle: 'Score' }}),
      }};
    }}""")
    assert got["transparent"] == {"a": 1, "paper_bgcolor": "rgba(0,0,0,0)", "plot_bgcolor": "rgba(0,0,0,0)"}
    assert got["filled"]["paper_bgcolor"] == "rgb(1, 2, 3)"
    assert got["csv"].splitlines() == ["series,Step,Score", "s,0,1", "s,100,2"]


def _png_corner_alpha(path):
    from PIL import Image
    with Image.open(path) as im:
        return im.convert("RGBA").getpixel((1, 1))[3]


async def test_web_a_live_score_plot_downloads_as_png_and_csv(tmp_path):
    """to_fix_v3: the live score plot's ⋯ menu offered a table download that
    never worked; it downloads as any plot does."""
    async with _live_page(_three_scored_frames(tmp_path)) as page:
        scores = page.locator(f'{ACTIVE} .analysis-panel[data-part="scores"]')
        menu = page.locator("#panel-menu-list")
        await scores.locator(".panel-more").click()
        await expect(menu.locator("[data-action='panel-table-csv']")).to_have_count(0)
        async with page.expect_download() as dl:
            await menu.locator("[data-action='panel-png']").click()
        png = await dl.value
        assert png.suggested_filename.endswith(".png")
        assert _png_corner_alpha(await png.path()) == 0
        await scores.locator(".panel-more").click()
        async with page.expect_download() as dl:
            await menu.locator("[data-action='panel-png-bg']").click()
        assert _png_corner_alpha(await (await dl.value).path()) == 255
        await scores.locator(".panel-more").click()
        async with page.expect_download() as dl:
            await menu.locator("[data-action='panel-csv']").click()
        with open(await (await dl.value).path()) as f:
            lines = f.read().splitlines()
        assert len(lines) == 4 and lines[1].endswith(",0,2.5")


async def test_a_click_finds_the_mark_under_it(static_origin):
    """to_fix_v3: a mark is a path, not a point; tickAt finds the one clicked."""
    got = await _pure(static_origin, f"""async () => {{
      const pn = await import('{static_origin}/panels.js');
      const ticks = [{{ xs: [0, 100, 200], row: 0 }}, {{ xs: [150], row: 1 }}];
      // x px = step; plot area 100 px high: row 0 at 97.5 px, row 1 at 93.5 px
      const geom = {{ h: 100, xToPx: (x) => x }};
      const at = (px, py, shown = new Set([0, 1])) => pn.tickAt(ticks, shown, px, py, geom);
      return {{ onMark: at(102, 97), nearest: at(147, 94), between: at(50, 97), above: at(100, 60),
               hidden: at(102, 97, new Set([1])) }};
    }}""")
    assert got["onMark"] == {"tick": 0, "x": 100}
    assert got["nearest"] == {"tick": 1, "x": 150}
    assert got["between"] is None and got["above"] is None
    assert got["hidden"] is None   # a row hidden in the legend is not clicked


async def test_web_a_click_on_a_mark_opens_its_frame(tmp_path):
    """to_fix_v3: a click on a mark opens its frame, as a click on its point does."""
    async with _live_page(_three_scored_frames(tmp_path)) as page:
        await page.locator("#frame-slider").fill("0")
        await expect(page.locator("#frame-label")).to_have_text("0 / 2", timeout=10000)
        # The tab's grid settles after the run opens and moves the plot by
        # hundreds of pixels: aim once it has kept its place for 10 frames.
        await page.wait_for_function("""(sel) => {
          const el = document.querySelector(sel + ' .analysis-panel[data-part="scores"] .panel-plot');
          const r = el.getBoundingClientRect(), at = `${r.left},${r.top},${r.width},${r.height}`;
          window.__plotAt = window.__plotAt === undefined || window.__plotAt.at !== at
            ? { at, n: 0 } : { at, n: window.__plotAt.n + 1 };
          return window.__plotAt.n >= 10;
        }""", arg=ACTIVE, polling="raf", timeout=10000)
        xy = await page.evaluate("""(sel) => {
          const el = document.querySelector(sel + ' .analysis-panel[data-part="scores"] .panel-plot');
          const fl = el._fullLayout, r = el.getBoundingClientRect();
          return {x: r.left + fl._size.l + fl.xaxis.l2p(200),
                  y: r.top + fl._size.t + fl._size.h * (1 - 0.025)};
        }""", ACTIVE)
        await page.mouse.move(xy["x"], xy["y"])
        await page.mouse.click(xy["x"], xy["y"])
        await expect(page.locator("#frame-label")).to_have_text("2 / 2", timeout=10000)


async def test_arrows_on_a_dataset_without_forces_say_why_none_show(static_origin):
    got = await _pure(static_origin, f"""async () => {{
      const fo = await import('{static_origin}/panes/forces.js');
      const gt = {{ show: true, modelKey: null }};
      return {{
        noForces: fo.noForcesReason(gt, {{ name: 'run7', has_forces: false }}),
        withForces: fo.noForcesReason(gt, {{ name: 'aspirin', has_forces: true }}),
        prediction: fo.noForcesReason({{ show: true, modelKey: 'm' }}, {{ has_forces: false }}),
        off: fo.noForcesReason({{ show: false, modelKey: null }}, {{ has_forces: false }}),
      }};
    }}""")
    assert got["noForces"].startswith("run7 has no forces in its file")
    assert got["withForces"] == got["prediction"] == got["off"] == ""


async def test_web_force_arrows_on_a_live_run_say_it_has_no_forces(tmp_path):
    """to_fix_v3: a dump has no forces; turning arrows on drew nothing and said nothing."""
    async with _live_page(_three_scored_frames(tmp_path)) as page:
        await page.evaluate("() => window.ffastApp._panes.forces.armShow()")
        toast = page.locator(".toast", has_text="has no forces in its file")
        await expect(toast).to_be_visible(timeout=5000)
        await expect(page.locator(".forces-note")).to_contain_text("has no forces in its file")


# ── ADR 0091 step 4: Run scores in the panel builder ────────────────────────

async def test_a_panel_has_quiet_notes_for_a_missing_analysis_value_and_no_popup(static_origin):
    got = await _pure(static_origin, f"""async () => {{
      const p = await import('{static_origin}/panels.js');
      const note = {{ failed: true, kind: 'no_analysis_value', metricId: 'm',
                      error: 'No bonds T² ÷ threshold for shard: only live runs have scores.' }};
      const bad = {{ failed: true, kind: 'failed', metricId: 'm', error: 'boom' }};
      const refs = [{{ datasetFp: 'a', modelFp: null }}];
      return {{
        popups: p.panelFailures(refs, [[note]], (id) => id).length,
        popupsForARealFailure: p.panelFailures(refs, [[bad]], (id) => id).length,
        notes: p.panelNotes([[note, note], [bad]]),
      }};
    }}""")
    assert got["popups"] == 0 and got["popupsForARealFailure"] == 1
    assert got["notes"] == ["No bonds T² ÷ threshold for shard: only live runs have scores."]


async def test_web_a_run_score_is_picked_under_run_scores_and_drawn_with_a_gap_and_a_line_at_one(tmp_path):
    from tests.ffast.live.test_live_run import _format2_run_folder

    run = _format2_run_folder(tmp_path, frames=3, scored=2)
    async with _live_page_without_scores_wait(run) as page:
        await page.locator("#tab-edit-btn").click()
        await page.locator("[data-edit=add-panel]").click()
        await page.get_by_role("button", name="Custom panel…").click()
        select = page.locator(".edit-modal select[data-role=y]")
        group = select.locator("optgroup")
        await expect(group).to_have_attribute("label", "Run scores")
        labels = await group.locator("option").all_inner_texts()
        assert any(t.startswith("bonds T² ÷ threshold") for t in labels)
        assert any(t.startswith("broken bonds") for t in labels)
        assert await select.locator("> option").count() > 1             # the others come first
        # A density panel lists each value's density under the same group.
        await page.locator(".edit-modal select[data-field=kind]").select_option("density")
        dens = page.locator(".edit-modal select[data-role=value] optgroup[label='Run scores'] option")
        assert "ffast.run_score_bonds_T2__value_kde" in [
            await o.get_attribute("value") for o in await dens.all()]
        await page.locator(".edit-modal select[data-field=kind]").select_option("timeline")
        select = page.locator(".edit-modal select[data-role=y]")
        await select.select_option("ffast.run_score_bonds_T2")
        await page.locator(".edit-modal input[data-field=title]").fill("Bonds T2")
        await page.get_by_role("button", name="Add", exact=True).click()
        panel = '.tabpanel.active .analysis-panel[data-title="Bonds T2"] .panel-plot'
        await page.wait_for_function("""(sel) => {
          const el = document.querySelector(sel);
          return el && el.data && el.data[0] && el.data[0].y.length === 3;
        }""", arg=panel, timeout=20000)
        got = await page.evaluate("""(sel) => {
          const el = document.querySelector(sel);
          return {y: Array.from(el.data[0].y).map((v) => (Number.isFinite(v) ? v : null)),
                  shapes: (el.layout.shapes || []).map((s) => [s.y0, s.y1, s.line.dash])};
        }""", panel)
        assert got["y"] == [0.5, 1.0, None]                              # a gap for the unscored frame
        assert got["shapes"] == [[1.0, 1.0, "dash"]]
        toasts = await page.locator(".toast").all_inner_texts()
        assert toasts == [], toasts      # a toast says what went wrong; show it


@contextlib.asynccontextmanager
async def _live_page_without_scores_wait(run, points=2):
    """Like `_live_page`, for a run whose score card has `points` points."""
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _open_run(page, run)
                await page.wait_for_function(SCORE_POINTS, arg=[ACTIVE, points], timeout=15000)
                yield page
            finally:
                await browser.close()


# ── ADR 0091 step 5: every panel showing a live run follows it ──────────────

async def _add_timeline(page, metric, title):
    """In the tab's edit mode: add a custom timeline of `metric` (leaves edit mode on)."""
    await page.locator("[data-edit=add-panel]").click()
    await page.get_by_role("button", name="Custom panel…").click()
    await page.locator(".edit-modal select[data-role=y]").select_option(metric)
    await page.locator(".edit-modal input[data-field=title]").fill(title)
    await page.get_by_role("button", name="Add", exact=True).click()


def _write_frame(run, step, seed):
    with open(run / rf.FRAMES, "a") as fh:
        fh.write(frame_text(step, random_frames(1, seed=seed)[0], ASPIRIN_ELEMENTS))


def _score_frame(run, frame, step):
    [adir] = list((run / rf.ANALYSIS_DIR).iterdir())
    rf.ScoresWriter(adir / rf.SCORES).append([{
        "segment": 0, "frame": frame, "step": step, "time": 0.05 * frame, "identity": "proven",
        "tracks": {"bonds": {"T2": 5.0, "SPE": 1.0}, "angles": {"T2": 2.5, "SPE": 9.0}},
        "exceedance": 0.7, "test": ["bonds", "T2"], "marked": False,
        "chem": {"broken": 0, "close": 0, "planarity": 0.1}}])


def _points(title):
    return f"""(n) => document.querySelector(
      '.tabpanel.active .analysis-panel[data-title="{title}"] .panel-plot')?.data?.[0]?.y?.length === n"""


async def test_web_every_panel_showing_a_live_run_follows_it_and_keeps_its_zoom(tmp_path):
    from tests.ffast.live.test_live_run import _format2_run_folder

    run = _format2_run_folder(tmp_path, frames=3, scored=3)
    async with _live_page_without_scores_wait(run, points=3) as page:
        await page.locator("#tab-edit-btn").click()
        await _add_timeline(page, "ffast.gyradius", "Gyradius")
        await _add_timeline(page, "ffast.run_score_worst", "Worst")
        await page.locator("[data-edit=save]").click()
        await expect(page.locator(".tabpanel.active .edit-bar")).to_have_count(0, timeout=15000)
        await page.wait_for_function(_points("Gyradius"), arg=3, timeout=20000)
        await page.wait_for_function(_points("Worst"), arg=3, timeout=20000)
        # Zoom the geometry timeline, then let the run grow.
        await page.evaluate("""() => {
          const el = document.querySelector('.tabpanel.active .analysis-panel[data-title="Gyradius"] .panel-plot');
          return Plotly.relayout(el, {'xaxis.range': [0.5, 1.5]});
        }""")
        _write_frame(run, 300, seed=11)
        await page.wait_for_function(_points("Gyradius"), arg=4, timeout=20000)
        await page.wait_for_function(_points("Worst"), arg=4, timeout=20000)     # frame 3: a gap
        last = "(t) => { const y = document.querySelector(`.tabpanel.active .analysis-panel[data-title=\"${t}\"] .panel-plot`).data[0].y; return y[y.length - 1]; }"
        assert await page.evaluate(f"!Number.isFinite(({last})('Worst'))")
        _score_frame(run, 3, 300)                       # its score comes later: the gap fills
        await page.wait_for_function(f"({last})('Worst') === 0.7", timeout=20000)
        rng = await page.evaluate("""() => document.querySelector(
          '.tabpanel.active .analysis-panel[data-title="Gyradius"] .panel-plot').layout.xaxis.range""")
        assert [round(v, 3) for v in rng] == [0.5, 1.5]                 # the zoom is kept


# ── ADR 0091 step 6: pause a tab ────────────────────────────────────────────

PAUSE = f"{ACTIVE} .analysis-controls [data-control=pause] button"
LAST_Y = """(t) => { const y = document.querySelector(
  `.tabpanel.active .analysis-panel[data-title="${t}"] .panel-plot`).data[0].y; return y.length; }"""


async def _tab_with_two_timelines(page):
    await page.locator("#tab-edit-btn").click()
    await _add_timeline(page, "ffast.gyradius", "Gyradius")
    await _add_timeline(page, "ffast.run_score_worst", "Worst")
    await page.locator("[data-edit=save]").click()
    await expect(page.locator(".tabpanel.active .edit-bar")).to_have_count(0, timeout=15000)
    await page.wait_for_function(_points("Gyradius"), arg=3, timeout=20000)
    await page.wait_for_function(_points("Worst"), arg=3, timeout=20000)


def _raise_alarm(run):
    run_json = rf.read_json(run / rf.RUN_JSON)
    [aid] = list((run / rf.ANALYSIS_DIR).iterdir())
    run_json["analyses"] = {aid.name: {"alarms": [{
        "kind": "ood", "onset": {"frame": 1, "step": 100, "time": 0.05},
        "confirmation": {"frame": 2, "step": 200, "time": 0.1}}]}}
    rf.write_json_atomic(run / rf.RUN_JSON, run_json)


async def test_web_a_paused_tab_stops_following_its_run_until_it_resumes(tmp_path):
    from tests.ffast.live.test_live_run import _format2_run_folder

    run = _format2_run_folder(tmp_path, frames=3, scored=3)
    async with _live_page_without_scores_wait(run, points=3) as page:
        await _tab_with_two_timelines(page)
        # A manually held frame stays held across Pause/Resume.
        await page.locator("#frame-slider").fill("0")
        await expect(page.locator("#frame-label")).to_have_text("0 / 2")
        button = page.locator(PAUSE)
        await expect(button).to_have_text("Pause")
        await button.click()
        await expect(button).to_have_text("Resume")

        _write_frame(run, 300, seed=11)
        _score_frame(run, 3, 300)
        run_json = rf.read_json(run / rf.RUN_JSON)
        run_json["producer"] = {"last_step": 300}
        rf.write_json_atomic(run / rf.RUN_JSON, run_json)
        # The Run card keeps up...
        await expect(page.locator(f"{ACTIVE} .live-status tbody td.num").first).to_have_text("300", timeout=15000)
        # ...the plots and the frame range do not. Give the page time to wrongly follow.
        await page.wait_for_timeout(1500)
        assert await page.evaluate(f"({LAST_Y})('Gyradius')") == 3
        assert await page.evaluate(f"({LAST_Y})('Worst')") == 3
        assert await page.evaluate(
            "(sel) => document.querySelector(sel + ' .analysis-panel[data-part=\"scores\"] .panel-plot').data[0].x.length",
            ACTIVE) == 3
        await expect(page.locator("#frame-label")).to_have_text("0 / 2")

        await button.click()                                  # resume: all catch up
        await expect(button).to_have_text("Pause")
        await page.wait_for_function(_points("Gyradius"), arg=4, timeout=20000)
        await page.wait_for_function(_points("Worst"), arg=4, timeout=20000)
        await page.wait_for_function(SCORE_POINTS, arg=[ACTIVE, 4], timeout=20000)
        await expect(page.locator("#frame-label")).to_have_text("0 / 3")


async def test_web_a_new_alarm_during_a_pause_turns_the_button_to_a_warning(tmp_path):
    from tests.ffast.live.test_live_run import _format2_run_folder

    run = _format2_run_folder(tmp_path, frames=3, scored=3)
    async with _live_page_without_scores_wait(run, points=3) as page:
        button = page.locator(PAUSE)
        await button.click()
        await expect(button).to_have_text("Resume")
        assert "warn" not in (await button.get_attribute("class") or "")
        _raise_alarm(run)
        await expect(button).to_have_text("Resume · new alarm", timeout=15000)
        assert "warn" in (await button.get_attribute("class") or "")
        await button.click()
        await expect(button).to_have_text("Pause")


async def test_web_a_pause_is_never_saved_nor_sent(tmp_path):
    from tests.ffast.live.test_live_run import _format2_run_folder

    run = _format2_run_folder(tmp_path, frames=3, scored=3)
    async with _live_page_without_scores_wait(run, points=3) as page:
        await page.locator(PAUSE).click()
        await expect(page.locator(PAUSE)).to_have_text("Resume")
        await page.wait_for_timeout(300)
        stored = await page.evaluate("() => JSON.stringify({...localStorage})")
        assert "paus" not in stored.lower()
        await page.reload(wait_until="networkidle")
        await _expect_connected(page)
        await expect(page.locator(PAUSE)).to_have_text("Pause", timeout=20000)


async def test_web_a_legend_click_hides_the_marks_it_names_and_they_stay_hidden(tmp_path):
    """The marks (ticks along the bottom) hid with their legend entry only until
    the plot was first drawn again: the plot then lost its legend listener and
    kept it lost. A hidden entry's marks stay hidden through the redraws a live
    plot gets, and come back when the entry does."""
    run = _alarmed_run_folder(tmp_path)
    shapes = """(sel) => {
      const el = document.querySelector(sel + ' .analysis-panel[data-part="scores"] .panel-plot');
      return (el.layout.shapes || []).filter((s) => s.type === 'path').map((s) => s.visible !== false);
    }"""
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _open_run(page, run)
                await page.wait_for_function(SCORE_POINTS, arg=[ACTIVE, 4], timeout=15000)
                legend = page.locator(f'{ACTIVE} .analysis-panel[data-part="scores"] .legend .traces')
                marked = legend.filter(has_text="marked")
                assert await page.evaluate(shapes, ACTIVE) == [True]
                await marked.locator(".legendtoggle").click()
                await page.wait_for_function(
                    "(sel) => document.querySelector(sel + ' .analysis-panel[data-part=\"scores\"] .panel-plot')"
                    ".layout.shapes.filter((s) => s.type === 'path').every((s) => s.visible === false)", arg=ACTIVE, timeout=5000)
                await page.wait_for_timeout(1500)       # a live plot is drawn again every second
                assert await page.evaluate(shapes, ACTIVE) == [False]
                await marked.locator(".legendtoggle").click()
                await page.wait_for_function(
                    "(sel) => document.querySelector(sel + ' .analysis-panel[data-part=\"scores\"] .panel-plot')"
                    ".layout.shapes.filter((s) => s.type === 'path').every((s) => s.visible !== false)", arg=ACTIVE, timeout=5000)
            finally:
                await browser.close()


async def test_web_the_legend_of_the_live_score_plot_can_be_hidden_from_its_menu(tmp_path):
    """The score plot opens with its legend (it names the marks and lines).
    ⋯ → Hide legend removes it and saves `legend = false` in the Live run
    layout; ⋯ → Show legend brings the automatic legend back and drops the field."""
    run = _alarmed_run_folder(tmp_path)
    tabs = tmp_path / "tabs"
    tabs.mkdir()
    async with _spawn_server("--tabs-dir", str(tabs)) as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _open_run(page, run)
                await page.wait_for_function(SCORE_POINTS, arg=[ACTIVE, 4], timeout=15000)
                scores = page.locator(f'{ACTIVE} .analysis-panel[data-part="scores"]')
                menu = page.locator("#panel-menu-list")
                legend = scores.locator(".legend .traces")
                await expect(legend).to_have_count(5)
                await scores.locator(".panel-more").click()
                await expect(menu.locator("[data-action='panel-legend']")).to_have_text("Hide legend")
                await menu.locator("[data-action='panel-legend']").click()
                await expect(legend).to_have_count(0, timeout=15000)
                assert any("legend = false" in f.read_text() for f in tabs.glob("*.toml"))
                await scores.locator(".panel-more").click()
                await expect(menu.locator("[data-action='panel-legend']")).to_have_text("Show legend")
                await menu.locator("[data-action='panel-legend']").click()
                await expect(legend).to_have_count(5, timeout=15000)
                assert not any("legend = false" in f.read_text() for f in tabs.glob("*.toml"))
            finally:
                await browser.close()


async def test_the_live_runs_dialog_greys_out_opening_a_run_while_a_tab_is_edited(static_origin):
    """Following a run changes the datasets, so while a tab is edited the Open
    buttons and "Open run folder…" say why not. Forget and Stop now stay."""
    row = {"run_id": "a", "name": "Live a", "run_dir": "/r/a", "exists": True, "last_step": 10,
           "planned_end_step": 100, "first_step": 0, "scorer_state": "scoring", "heartbeat_age_s": 3,
           "alarms": [], "end": None, "state": "running", "open": True}
    gone = {"run_id": "b", "name": "Live b", "run_dir": "/gone", "exists": False}
    got = await _pure(static_origin, f"""async () => {{
      const d = await import('{static_origin}/live_runs_dialog.js');
      const dlg = d.openLiveRunsDialog({{ ask() {{}}, open() {{}}, openByPath() {{}}, forget() {{}}, stop() {{}},
        whyNoOpen: () => 'Finish editing the tab first (Save or Cancel)' }});
      dlg.update({json.dumps([row, gone])});
      const q = (s) => document.querySelector(s);
      const out = {{
        open: [q('button.live-runs-open').disabled, q('button.live-runs-open').title],
        forget: q('button.live-runs-forget').disabled, stop: q('button.live-runs-stop').disabled,
        path: [...document.querySelectorAll('#live-runs-dialog .warning-modal-footer button')]
          .map((b) => [b.textContent, b.disabled]),
      }};
      dlg.close();
      return out;
    }}""")
    assert got["open"] == [True, "Finish editing the tab first (Save or Cancel)"]
    assert got["forget"] is False and got["stop"] is False
    assert got["path"] == [["Open run folder…", True], ["Close", False]]


async def test_web_while_a_live_tab_is_edited_its_panels_are_greyed_but_stop_on_alarm_works(tmp_path):
    """Panel contents are greyed in Edit mode so the frames stand out; the Run
    panel is not (Stop on alarm is a run safety control) and neither are the edit
    chrome's grips and buttons."""
    run = _three_scored_frames(tmp_path)
    tabs = tmp_path / "tabs"
    tabs.mkdir()
    async with _spawn_server("--tabs-dir", str(tabs)) as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _open_run(page, run)
                await page.wait_for_function(SCORE_POINTS, arg=[ACTIVE, 3], timeout=15000)
                await page.locator("#tab-edit-btn").click()
                await expect(page.locator(f"{ACTIVE} .edit-bar")).to_be_visible()
                inert = await page.evaluate("""(sel) => Object.fromEntries([...document.querySelectorAll(sel + ' .analysis-panel')]
                  .map((e) => [e.dataset.part || (e.classList.contains('panel-3d') ? '3d' : 'plot'), e.inert]))""", ACTIVE)
                assert inert["scores"] is True and inert["attribution"] is True and inert["3d"] is True
                assert inert["status"] is False
                tick = page.locator(f"{ACTIVE} .live-stop-on-alarm")
                await tick.check()
                await expect(tick).to_be_checked(timeout=10000)      # a run safety control works mid-edit
                assert rf.read_json(run / rf.CONTROL)["stop_on_alarm"] is True
                await page.locator(f"{ACTIVE} .edit-chrome .edit-head").first.hover()   # the frames stay live
                await page.locator("[data-edit=cancel]").click()
                still = await page.evaluate("""(sel) => [...document.querySelectorAll(sel + ' .analysis-panel')]
                  .some((e) => e.inert)""", ACTIVE)
                assert still is False
            finally:
                await browser.close()

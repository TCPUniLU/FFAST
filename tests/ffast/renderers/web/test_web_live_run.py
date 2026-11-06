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
    assert got["problem"] == "slow disk" and got["refused"] == "units lj"
    assert got["frames"] == [0, 1] and got["fp"] == "live:r1"
    assert got["x"] == [0, 100] and got["y"] == [0.2, 1.2] and got["xTitle"] == "Step"
    assert got["line"] == 1
    assert got["plainX"] == [0, 1] and got["plainShapes"] is None


ACTIVE = "#tabpanels .tabpanel.active"


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
                await _file_menu(page, "Open Live Run…")
                await page.locator("#text-ask input").fill(str(run))
                await page.locator("#text-ask input").press("Enter")

                # The run's tab is listed and opened: its 3D view beside its scores.
                tab = page.locator("#tabbar .tab", has_text="Live 12:00:00 node")
                await expect(tab).to_have_class(re.compile(r"\bactive\b"), timeout=15000)
                active = page.locator(ACTIVE)
                await expect(active.locator(".panel-3d")).to_have_count(1)
                caption = active.locator('.analysis-panel[data-kind="timeline"] .panel-caption-line')
                await expect(caption).to_contain_text("2 frames · 2 scored")
                # Status strip and events list are panels of the tab too.
                await expect(active.locator(".live-status .live-chip").first).to_contain_text("LAMMPS")
                await expect(active.locator(".live-event-list tr")).to_have_count(0)
                await expect(active.locator(".analysis-panel .panel-plot .main-svg").first).to_be_attached()
                await expect(active.locator(".sub-toggle")).to_have_count(0)
                await expect(page.locator("#dataset-list .obj-row")).to_have_count(1, timeout=15000)

                # LAMMPS writes another frame, the scorer scores it.
                with open(run / rf.FRAMES, "a") as fh:
                    fh.write(frame_text(200, random_frames(1, seed=9)[0], ASPIRIN_ELEMENTS))
                [adir] = list((run / rf.ANALYSIS_DIR).iterdir())
                rf.ScoresWriter(adir / rf.SCORES).append([{
                    "segment": 0, "frame": 2, "step": 200, "time": 0.1, "identity": "proven",
                    "tracks": {"bonds": {"T2": 2.0, "SPE": 0.5}}, "exceedance": 0.4,
                    "test": ["bonds", "T2"], "marked": False}])
                await expect(caption).to_contain_text("3 frames · 3 scored", timeout=15000)
                # The plot's caption comes with the run's update; the dataset's new
                # length comes in its own message. The click needs both.
                await expect(page.locator("#frame-label")).to_have_text("0 / 2", timeout=15000)

                # A click on the last point shows that frame in the 3D view.
                xy = await page.evaluate("""(sel) => {
                  const el = document.querySelector(sel + ' .analysis-panel .panel-plot');
                  const fl = el._fullLayout, tr = el.data[0], r = el.getBoundingClientRect();
                  const i = tr.x.length - 1;
                  return {x: r.left + fl.margin.l + fl.xaxis.l2p(tr.x[i]),
                          y: r.top + fl.margin.t + fl.yaxis.l2p(tr.y[i])};
                }""", ACTIVE)
                await page.mouse.move(xy["x"], xy["y"])
                await page.mouse.click(xy["x"], xy["y"])
                await expect(page.locator("#frame-label")).to_have_text("2 / 2", timeout=10000)
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
        chips: lr.liveRunChips(kw, now),
        stalled: lr.liveRunChips(stalled, now).map((c) => c.text),
        ended: lr.liveRunChips({{ ...kw, end: {{ state: 'terminated', stop_observed_step: 386400 }} }}, now).map((c) => c.text),
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
    chips = [(c["text"], c["tone"]) for c in got["chips"]]
    assert chips == [("LAMMPS step 412,300 / 1,000,000", ""), ("scored through 411,900", ""),
                     ("scorer heartbeat 3 s ago", "ok"), ("atom order proven", ""),
                     ("ALARM at step 386,000", "bad"), ("stop requested", "bad")]
    assert "scorer heartbeat 4 min ago — stalled?" in got["stalled"]
    assert "terminated; last saved frame at step 386,400" in got["ended"]
    assert got["rule"] == ("Alarm rule: 20 % of frames marked within 5 ps (100 frames), checked every "
                           "0.5 ps. False-alarm chance over the planned 1,000,000 steps: ≤ 1.8 % (estimated).")
    assert "≥ 0.04 % (estimated; frames taken as uncorrelated until 200 are scored)" in got["early"]
    o = got["overlays"]
    assert o["ticks"][0]["xs"] == [100, 300] and o["ticks"][1]["xs"] == [300]
    assert o["bands"] == [{"x0": 380000, "x1": 386000, "color": "danger"}]
    assert [(l["x"], l["label"]) for l in o["lines"]] == [
        (380000, "estimated onset"), (386000, "confirmed"), (386500, "stop observed")]
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
                await _file_menu(page, "Open Live Run…")
                await page.locator("#text-ask input").fill(str(run))
                await page.locator("#text-ask input").press("Enter")
                active = page.locator(ACTIVE)
                status = active.locator(".live-status")
                await expect(status).to_contain_text("ALARM at step 300", timeout=15000)
                await expect(status).to_contain_text("stopped at step 300")
                events = active.locator(".live-events")
                await expect(events).to_contain_text("Alarm · estimated onset step 100 · confirmed at step 300")
                await expect(events).to_contain_text("Stop observed at step 300")
                await expect(active.locator(".analysis-panel .panel-plot .main-svg").first).to_be_attached()
                notes = await page.evaluate("""(sel) => {
                  const el = document.querySelector(sel + ' .analysis-panel[data-kind="timeline"] .panel-plot');
                  return {notes: (el.layout.annotations || []).map((a) => a.text),
                          rects: (el.layout.shapes || []).filter((s) => s.type === 'rect').length,
                          traces: el.data.length};
                }""", ACTIVE)
                assert {"estimated onset", "confirmed", "stop observed"} <= set(notes["notes"])
                assert notes["rects"] == 1 and notes["traces"] == 2     # scores, marks (no chemistry flag)
            finally:
                await browser.close()


async def test_web_stop_now_asks_first_and_stop_on_alarm_switches(tmp_path):
    run = _run_folder(tmp_path, frames=2)
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _file_menu(page, "Open Live Run…")
                await page.locator("#text-ask input").fill(str(run))
                await page.locator("#text-ask input").press("Enter")
                active = page.locator(ACTIVE)
                tick = active.locator(".live-stop-on-alarm")
                await expect(tick).not_to_be_checked(timeout=15000)
                await tick.check()
                await expect(tick).to_be_checked(timeout=10000)
                assert rf.read_json(run / rf.CONTROL)["stop_on_alarm"] is True

                # Stop now asks first; Cancel leaves the run alone.
                await active.locator(".live-stop-now").click()
                dialog = page.locator(".ask-dialog")
                await expect(dialog).to_contain_text("Stop this run?")
                await dialog.get_by_role("button", name="Cancel").click()
                assert not (run / rf.STOP_REQUEST).exists()
                await active.locator(".live-stop-now").click()
                await page.locator(".ask-dialog").get_by_role("button", name="Stop run").click()
                await expect(active.locator(".live-stop-now")).to_have_text("Stop requested", timeout=10000)
                await expect(active.locator(".live-stop-now")).to_be_disabled()
                assert rf.read_json(run / rf.STOP_REQUEST)["by"] == "user"
                await expect(active.locator(".live-status")).to_contain_text("stop requested")
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
                await _file_menu(page, "Open Live Run…")
                await page.locator("#text-ask input").fill(str(run))
                await page.locator("#text-ask input").press("Enter")
                active = page.locator(ACTIVE)
                att = active.locator(".live-attribution")
                caption = active.locator('.analysis-panel[data-kind="timeline"] .panel-caption-line')
                await expect(caption).to_contain_text("3 frames · 3 scored", timeout=15000)
                await expect(page.locator("#frame-label")).to_have_text("0 / 2", timeout=10000)
                # The shown frame is explained without a click.
                await expect(att).to_contain_text("Step 0", timeout=15000)

                xy = await page.evaluate("""(sel) => {
                  const el = document.querySelector(sel + ' .analysis-panel[data-kind="timeline"] .panel-plot');
                  const fl = el._fullLayout, tr = el.data[0], r = el.getBoundingClientRect();
                  return {x: r.left + fl.margin.l + fl.xaxis.l2p(tr.x[2]),
                          y: r.top + fl.margin.t + fl.yaxis.l2p(tr.y[2])};
                }""", ACTIVE)
                await page.mouse.move(xy["x"], xy["y"])
                await page.mouse.click(xy["x"], xy["y"])
                await expect(page.locator("#frame-label")).to_have_text("2 / 2", timeout=10000)
                await expect(att).to_contain_text("Step 200", timeout=15000)
                await expect(att.locator("table.live-att-groups tr").nth(1)).to_be_visible()
                await expect(att).to_contain_text("do not add up to the score")
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


async def test_live_runs_rows_show_progress_heartbeat_alarm_and_end(static_origin):
    rows = [
        {"run_id": "a", "name": "Live 10:33:35 mel0314", "run_dir": "/r/a", "exists": True, "bundle": "aspirin_mace",
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
    assert round(a["lammpsShare"], 3) == 0.412 and a["scorer"] == {"text": "3 s ago", "tone": "ok"}
    assert a["alarm"] == {"text": "step 386,000", "tone": "bad"} and a["state"]["text"] == "running"
    assert a["sub"] == "aspirin_mace · /r/a" and a["canOpen"]
    assert b["scorer"] == {"text": "4 min ago — stalled?", "tone": "warn"}
    assert c["lammpsShare"] is None and c["scorer"]["text"] == "done"
    assert c["alarm"]["text"] == "chemistry step 58,000" and c["state"]["text"] == "terminated at step 61,500"
    assert d["state"]["text"] == "folder not found" and not d["canOpen"]


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
                await _file_menu(page, "Live Runs…")
                dialog = page.locator("#live-runs-dialog")
                rows = dialog.locator("table.live-runs-table tr[data-run-id]")
                await expect(rows).to_have_count(2, timeout=10000)
                await expect(rows.nth(0)).to_contain_text("Live 12:00:00 node")
                await expect(rows.nth(1)).to_contain_text("folder not found")
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
                await _file_menu(page, "Make Reference…")
                dialog = page.locator("#make-reference-dialog")
                await expect(dialog.locator("#make-ref-train option")).to_have_count(2)
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

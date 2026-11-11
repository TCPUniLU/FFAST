"""Scored items in the browser (ADR 0093): the list, the live panels, the dialog."""

from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright.async_api")

from playwright.async_api import async_playwright, expect  # noqa: E402

from ffast.protocol.rpc import pack  # noqa: E402
from tests.ffast.live.bundle_helpers import make_bundle, write_frames  # noqa: E402
from tests.ffast.renderers.web.test_web_runtime import (  # noqa: E402
    _connect_headless_client, _expect_connected, _spawn_server, _wait_for_event, _wait_for_server_ready,
)

pytestmark = pytest.mark.integration

ACTIVE = "#tabpanels .tabpanel.active"
CURVE_POINTS = """([sel, n]) => {
  const el = document.querySelector(sel + ' .analysis-panel[data-part="scores"] .panel-plot');
  return !!el && el.data?.[0]?.x?.length === n;
}"""


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """A bundle, a six-frame file, and score/reference folders the server will use."""
    monkeypatch.setenv("FFAST_ANALYSES", str(tmp_path / "analyses"))
    monkeypatch.setenv("FFAST_REFERENCES", str(tmp_path / "refs"))
    (tmp_path / "b").mkdir()
    bundle = make_bundle(tmp_path / "b")[0]
    xyz = write_frames(tmp_path / "f.xyz", 6, seed=3)
    return bundle, xyz


async def _load(ws_port, xyz):
    ws = await _connect_headless_client(ws_port)
    try:
        await ws.send(pack("LOAD_DATASET", (str(xyz), "ase (auto)"), {}))
        await _wait_for_event(ws, "REMOTE_DATASET_META", timeout=30)
    finally:
        await ws.send(pack("GRACEFUL_DISCONNECT", (), {}))
        await ws.close()


SCORE = """(a) => {
  const app = window.ffastApp;
  const fp = [...app._datasets.entries()].find(([, m]) => !m.scored && !m.is_sub)[0];
  app._scoreWanted = null;
  app._conn.send('SCORE_DATASET', { fingerprint: fp, ...a });
}"""


async def test_web_a_scored_item_shows_its_curve_and_its_menu_has_no_freeze(setup):
    bundle, xyz = setup
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        await _load(ws_port, xyz)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                rows = page.locator("#dataset-list .obj-row")
                await expect(rows).to_have_count(1, timeout=15000)
                await page.evaluate(SCORE, {"bundle": str(bundle), "frame_dt_fs": 500.0, "persist_ps": 5.0})
                # The item is listed under its dataset, and its tab opens.
                await expect(rows).to_have_count(2, timeout=15000)
                item = rows.nth(1)
                await expect(item.locator(".name")).to_contain_text("f · aspirin · 500 fs · 5 ps")
                await expect(page.locator("#tabbar .tab.active")).to_contain_text("Scores f", timeout=15000)
                await page.wait_for_function(CURVE_POINTS, arg=[ACTIVE, 6], timeout=30000)
                status = page.locator(f"{ACTIVE} .live-scored-table")
                await expect(status).to_contain_text("Scored from f", timeout=15000)
                await expect(status).to_contain_text("6 of 6 frames")
                await expect(status).to_contain_text("Done")
                # No stop controls for a scored item.
                await expect(page.locator(f"{ACTIVE} .live-stop-on-alarm")).to_have_count(0)

                fp = await item.get_attribute("data-fp")
                await page.locator(f"#dataset-list .obj-row[data-fp='{fp}'] .obj-menu-btn").click()
                menu = page.locator(".rail-menu")
                await expect(menu.locator("[data-action='dataset-info']")).to_be_visible()
                await expect(menu.locator("[data-action='dataset-rename']")).to_be_visible()
                await expect(menu.locator("[data-action='dataset-remove-scored']")).to_be_visible()
                await expect(menu.locator("[data-action='dataset-freeze']")).to_have_count(0)
                await expect(menu.locator("[data-action='dataset-delete']")).to_have_count(0)
                await expect(menu.locator("[data-action='dataset-continue-scoring']")).to_have_count(0)
                # Its parent is an ordinary dataset: Delete, and no Remove.
                await page.keyboard.press("Escape")
                await page.locator("#dataset-list .obj-row .obj-menu-btn").first.click()
                await expect(menu.locator("[data-action='dataset-delete']")).to_be_visible()
                await expect(menu.locator("[data-action='dataset-remove-scored']")).to_have_count(0)
            finally:
                await browser.close()


async def test_web_two_scored_items_of_one_dataset_are_compared_in_one_tab(setup):
    bundle, xyz = setup
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        await _load(ws_port, xyz)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                rows = page.locator("#dataset-list .obj-row")
                await expect(rows).to_have_count(1, timeout=15000)
                await page.evaluate(SCORE, {"bundle": str(bundle), "frame_dt_fs": 500.0, "persist_ps": 5.0})
                await expect(rows).to_have_count(2, timeout=15000)
                await page.wait_for_function(CURVE_POINTS, arg=[ACTIVE, 6], timeout=30000)
                await page.evaluate(SCORE, {"bundle": str(bundle), "frame_dt_fs": 500.0, "persist_ps": 2.0})
                await expect(rows).to_have_count(3, timeout=15000)
                # The second item has its own tab; tick the first item in it too.
                await expect(page.locator("#tabbar .tab.active")).to_contain_text("2 ps", timeout=15000)
                first = page.locator(f"{ACTIVE} .elem-btn", has_text="5 ps")
                await first.click()
                await expect(page.locator(f"{ACTIVE} .live-scored-table tbody tr")).to_have_count(2, timeout=15000)
                await page.wait_for_function(
                    """(sel) => document.querySelector(sel + ' .analysis-panel[data-part="scores"] .panel-plot')
                       ?.data?.filter((t) => t.x?.length === 6 && /ps/.test(t.name || '')).length === 2""",
                    arg=ACTIVE, timeout=30000)
            finally:
                await browser.close()


async def test_web_a_frame_the_reference_cannot_score_is_a_gap_with_its_reason_and_remove_hides_the_item(setup, tmp_path):
    from ase import Atoms
    from ase.io import read, write

    bundle, _ = setup
    frames = read(str(write_frames(tmp_path / "m.xyz", 5, seed=4)), index=":")
    frames[2] = Atoms("H2O", positions=[[0, 0, 0], [0.9, 0, 0], [0, 0.9, 0]])
    write(str(tmp_path / "mixed.xyz"), frames, format="extxyz")
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        await _load(ws_port, tmp_path / "mixed.xyz")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                rows = page.locator("#dataset-list .obj-row")
                await expect(rows).to_have_count(1, timeout=15000)
                await page.evaluate(SCORE, {"bundle": str(bundle), "frame_dt_fs": 500.0})
                await expect(rows).to_have_count(2, timeout=15000)
                status = page.locator(f"{ACTIVE} .live-scored-table")
                await expect(status).to_contain_text("4 of 5 frames", timeout=30000)
                await expect(status).to_contain_text("1 frame not scored")
                caption = page.locator(f"{ACTIVE} .analysis-panel[data-part='scores'] .panel-caption-line")
                await expect(caption).to_contain_text("Frame 2:")
                await expect(caption).to_contain_text(re.compile("atoms"))

                fp = await rows.nth(1).get_attribute("data-fp")
                await page.locator(f"#dataset-list .obj-row[data-fp='{fp}'] .obj-menu-btn").click()
                await page.locator(".rail-menu [data-action='dataset-remove-scored']").click()
                dialog = page.locator(".ask-dialog")
                await expect(dialog).to_contain_text("Its scores stay saved")
                await dialog.locator("button", has_text="Remove").click()
                await expect(rows).to_have_count(1, timeout=15000)
                # Scoring again with the same settings brings it back at once, nothing scored again.
                await page.evaluate(SCORE, {"bundle": str(bundle), "frame_dt_fs": 500.0})
                await expect(rows).to_have_count(2, timeout=15000)
                await expect(page.locator(f"{ACTIVE} .live-scored-table")).to_contain_text("4 of 5 frames", timeout=30000)
            finally:
                await browser.close()


# ── the dialog (ADR 0093 step 7) ────────────────────────────────────────────

async def _open_score_dialog(page, fp):
    await page.locator(f"#dataset-list .obj-row[data-fp='{fp}'] .obj-menu-btn").click()
    await page.locator(".rail-menu [data-action='dataset-score']").click()
    dialog = page.locator("#score-dialog")
    await expect(dialog).to_be_visible(timeout=15000)
    return dialog


async def test_web_the_score_dialog_scores_a_dataset_and_shows_saved_scores_the_second_time(setup, tmp_path):
    from ffast.live import references

    bundle, xyz = setup
    references.record_reference(bundle)
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        await _load(ws_port, xyz)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                rows = page.locator("#dataset-list .obj-row")
                await expect(rows).to_have_count(1, timeout=15000)
                fp = await rows.first.get_attribute("data-fp")
                dialog = await _open_score_dialog(page, fp)
                await expect(dialog.locator("#score-ref option")).to_have_count(1)
                await expect(dialog.locator("#score-ref")).to_contain_text("aspirin")
                # No time in this file: the field is empty and says what that means.
                await expect(dialog.locator("#score-time")).to_have_value("")
                await expect(dialog.locator(".score-note").first).to_contain_text("alarms are off")
                await expect(dialog.locator("#score-persist")).to_have_value("5")
                await expect(dialog.locator(".score-go")).to_have_text("Score")
                await dialog.locator("#score-persist").fill("0")
                await expect(dialog.locator(".make-ref-problem")).to_contain_text("persistence")
                await expect(dialog.locator(".score-go")).to_be_disabled()
                await dialog.locator("#score-persist").fill("5")
                await dialog.locator("#score-time").fill("500")
                await dialog.locator(".score-go").click()
                await expect(dialog).to_be_hidden()
                await expect(rows).to_have_count(2, timeout=15000)
                await expect(page.locator("#tabbar .tab.active")).to_contain_text("Scores f", timeout=15000)
                await page.wait_for_function(CURVE_POINTS, arg=[ACTIVE, 6], timeout=30000)
                await expect(page.locator(f"{ACTIVE} .live-scored-table")).to_contain_text("Done", timeout=15000)

                # The same settings again: the saved scores are shown, nothing is scored again.
                dialog = await _open_score_dialog(page, fp)
                await dialog.locator("#score-time").fill("500")
                await expect(dialog.locator(".score-go")).to_have_text("Show saved scores")
                await dialog.locator("#score-persist").fill("2")
                await expect(dialog.locator(".score-go")).to_have_text("Score")
                await dialog.locator("button", has_text="Cancel").click()
                await expect(dialog).to_be_hidden()
            finally:
                await browser.close()


async def test_web_the_score_dialog_fills_in_the_time_from_the_files_time_field(setup, tmp_path):
    from ase.io import read, write

    from ffast.live import references

    bundle, _ = setup
    references.record_reference(bundle)
    frames = read(str(write_frames(tmp_path / "t.xyz", 4, seed=6)), index=":")
    for i, a in enumerate(frames):
        a.info["time"] = 2.0 * i
    write(str(tmp_path / "timed.xyz"), frames, format="extxyz")
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        await _load(ws_port, tmp_path / "timed.xyz")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                rows = page.locator("#dataset-list .obj-row")
                await expect(rows).to_have_count(1, timeout=15000)
                dialog = await _open_score_dialog(page, await rows.first.get_attribute("data-fp"))
                await expect(dialog.locator("#score-time")).to_have_value("2")
                await expect(dialog.locator(".score-note").first).to_contain_text("fs, from the file's time field: check the unit")
            finally:
                await browser.close()


async def test_web_without_detectana_the_score_item_is_greyed_out_with_the_install_hint(setup):
    bundle, xyz = setup
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        await _load(ws_port, xyz)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                rows = page.locator("#dataset-list .obj-row")
                await expect(rows).to_have_count(1, timeout=15000)
                fp = await rows.first.get_attribute("data-fp")
                # Pretend the server said DetectAna is missing (the answer to SCORE_DIALOG_INFO).
                await page.evaluate("""(fp) => {
                  const app = window.ffastApp;
                  app._scoreInfo.set(fp, { ...app._scoreInfo.get(fp), detectana: 'DetectAna is not installed; install it with pip' });
                }""", fp)
                await page.locator(f"#dataset-list .obj-row[data-fp='{fp}'] .obj-menu-btn").click()
                item = page.locator(".rail-menu [data-action='dataset-score']")
                await expect(item).to_be_visible()
                await expect(item).to_be_disabled()
                await expect(item).to_have_attribute("title", re.compile("DetectAna is not installed"))
            finally:
                await browser.close()


async def test_the_score_dialogs_pure_helpers():
    import functools
    import http.server
    import threading
    from pathlib import Path

    static = Path(__file__).resolve().parents[4] / "ffast" / "renderers" / "web" / "static"
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(static))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(f"{origin}/jsconfig.json")
                got = await page.evaluate(f"""async () => {{
                  const m = await import('{origin}/score_dialog.js');
                  const lr = await import('{origin}/live_run.js');
                  const existing = [{{ bundle: '/b.bundle', settings: {{ frame_dt_ps: 0.0005, persist_ps: 5 }}, state: 'done' }}];
                  return {{
                    nums: ['', ' 2 ', '0', '-1', 'x'].map((t) => m.positiveNumber(t)),
                    ok: m.scoreProblem({{ info: {{}}, bundle: '/b', timeText: '', persistText: '5' }}),
                    noRef: m.scoreProblem({{ info: {{}}, bundle: '', timeText: '', persistText: '5' }}),
                    badTime: m.scoreProblem({{ info: {{}}, bundle: '/b', timeText: 'x', persistText: '5' }}),
                    badPersist: m.scoreProblem({{ info: {{}}, bundle: '/b', timeText: '', persistText: '' }}),
                    refusal: m.scoreProblem({{ info: {{ refusal: 'has a cell' }}, bundle: '/b', timeText: '', persistText: '5' }}),
                    nodet: m.scoreProblem({{ info: {{ detectana: 'not installed' }}, bundle: '/b', timeText: '', persistText: '5' }}),
                    same: !!m.existingItem(existing, '/b.bundle', 0.5, 5),
                    otherPersist: !!m.existingItem(existing, '/b.bundle', 0.5, 2),
                    otherBundle: !!m.existingItem(existing, '/c.bundle', 0.5, 5),
                    noTime: !!m.existingItem(existing, '/b.bundle', null, 5),
                    noneRecorded: (() => {{
                      // No recorded reference: the placeholder must not count as one.
                      const d = m.openScoreDialog({{ info: {{ fingerprint: 'f', references: [], persist_ps: 5 }},
                        send: () => {{}} }});
                      const disabled = document.querySelector('#score-dialog .score-go').disabled;
                      d.close();
                      return disabled;
                    }})(),
                    incomplete: (() => {{
                      const s = (state, frames, n) => ({{ kind: 'scored', state, n_frames: n,
                        series: {{ frame: [...Array(frames).keys()] }} }});
                      return [s('cancelled', 2, 5), s('failed', 2, 5), s('done', 5, 8), s('done', 5, 5),
                              s('scoring', 2, 5), s('refused', 0, 5)].map(lr.scoredIncomplete);
                    }})(),
                  }};
                }}""")
            finally:
                await browser.close()
    finally:
        server.shutdown()
    # empty → null; a positive number → itself; zero, negative and text → NaN (JSON null in Python)
    assert got["nums"][0] is None and got["nums"][1] == 2
    assert got["ok"] == "" and "Pick a reference" in got["noRef"]
    assert "time between frames" in got["badTime"] and "persistence" in got["badPersist"]
    assert got["refusal"] == "has a cell" and got["nodet"] == "not installed"
    assert got["same"] is True and got["otherPersist"] is False
    assert got["otherBundle"] is False and got["noTime"] is False
    assert got["noneRecorded"] is True
    # cancelled, failed part-way and a grown live run can continue; complete, running, refused cannot
    assert got["incomplete"] == [True, True, True, False, False, False]

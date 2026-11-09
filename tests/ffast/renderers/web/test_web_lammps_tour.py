"""Watch a LAMMPS run, through a real server, wrapper, scorer and browser."""

from pathlib import Path

import pytest
from playwright.async_api import async_playwright, expect

from ffast.live import run_folder as rf
from tests.ffast.renderers.web.test_web_runtime import (
    _expect_connected,
    _lammps_menu,
    _spawn_server,
    _wait_for_server_ready,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("pick", ["line", "band"])
async def test_watch_a_lammps_run_from_start_to_delete(
    tmp_path, monkeypatch, pick
):
    monkeypatch.setenv("FFAST_PRACTICE_RUNS", str(tmp_path / "practice"))
    monkeypatch.setenv("FFAST_LIVE_RUNS", str(tmp_path / "pointers"))
    monkeypatch.setenv("FFAST_REFERENCES", str(tmp_path / "references"))
    monkeypatch.setenv("FFAST_PRACTICE_SLEEP", "0.02")
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page(
                viewport={"width": 1500, "height": 1000}
            )
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}")
                await _expect_connected(page)
                await _lammps_menu(page, "Tour: Watch a LAMMPS run")
                title = page.locator(".tour-title")
                await expect(title).to_have_text("Start a practice run")
                await page.get_by_role(
                    "button", name="Start practice run", exact=True
                ).click()
                await expect(title).to_have_text(
                    "The run, one row", timeout=30000
                )
                active = page.locator(".tabpanel.active")
                await expect(active.locator(".practice-chip")).to_have_text(
                    "practice"
                )
                await expect(
                    active.locator(".live-status-table")
                ).to_contain_text("unverified", timeout=15000)
                for expected in [
                    "What LAMMPS writes",
                    "The score",
                    "Events",
                    "Turn on Stop on alarm",
                ]:
                    await page.locator(".tour-next").click()
                    await expect(title).to_have_text(expected)
                await active.locator(".live-stop-on-alarm").check()
                await expect(title).to_have_text("Heat it up", timeout=15000)
                heat = page.get_by_role(
                    "button", name="Heat it up", exact=True
                )
                await expect(heat).to_be_enabled(timeout=15000)
                await expect(active.locator(".live-state")).to_contain_text(
                    "Paused before the heat"
                )
                await heat.click()
                await expect(title).to_have_text("An alarm")
                await expect(page.locator(".tour-next")).to_be_visible(
                    timeout=30000
                )
                await page.locator(".tour-next").click()
                await expect(title).to_have_text("It stopped itself")
                await expect(page.locator(".tour-next")).to_be_visible(
                    timeout=30000
                )
                await expect(active.locator(".live-state")).to_contain_text(
                    "stopped at step"
                )
                [pointer] = list((tmp_path / "pointers").glob("*.json"))
                folder = Path(rf.read_json(pointer)["run_dir"])
                run = rf.read_json(folder / rf.RUN_JSON)
                assert run["end"]["state"] == "stopped"
                assert run["stop"]["by"] == "alarm"
                [progress] = run["analyses"].values()
                assert progress["alarms"][0]["kind"] == "chem"
                onset = progress["alarms"][0]["onset"]
                confirmation = progress["alarms"][0]["confirmation"]
                frame = (
                    onset["frame"]
                    if pick == "line"
                    else (onset["frame"] + confirmation["frame"]) // 2
                )
                await page.locator(".tour-next").click()
                await expect(title).to_have_text("Go to the onset")
                # Click the vertical line or band away from the score curve.
                plot = active.locator(
                    '.analysis-panel[data-title="Worst test ÷ its threshold, against step"] .panel-plot'
                )
                xy = await plot.evaluate(
                    """(el, frame) => {
                  const tr = el.data[0], i = frame;
                  const fl = el._fullLayout, r = el.getBoundingClientRect();
                  return { x: r.left + fl._size.l + fl.xaxis.l2p(tr.x[i]),
                           y: r.top + fl._size.t + fl._size.h * 0.5 };
                }""",
                    frame,
                )
                await page.mouse.click(xy["x"], xy["y"])
                await expect(page.locator(".tour-next")).to_be_visible()
                await expect(page.locator("#frame-label")).to_contain_text(
                    f"{frame} /", timeout=15000
                )
                await page.locator(".tour-next").click()
                await expect(title).to_have_text("What explains it")
                await expect(
                    active.locator('.analysis-panel[data-title="Attribution"]')
                ).to_contain_text("O", timeout=15000)
                await page.locator(".tour-next").click()
                await expect(title).to_have_text("Two kinds of alarm")
                await page.locator(".tour-next").click()
                await expect(title).to_have_text("Keep the practice run?")
                await page.screenshot(path="/tmp/ffast-lammps-tour.png")
                if pick == "band":
                    await page.get_by_role(
                        "button", name="Keep it", exact=True
                    ).click()
                    await expect(page.locator(".tour-box")).to_have_count(0)
                    assert folder.exists() and pointer.exists()
                    await _lammps_menu(page, "Live Runs…")
                    row = page.locator("#live-runs-dialog tr").filter(
                        has_text="practice"
                    )
                    await row.get_by_role(
                        "button", name="Delete practice run"
                    ).click()
                    await expect(
                        page.locator(".live-runs-body")
                    ).to_contain_text("No live runs yet")
                else:
                    await page.get_by_role(
                        "button", name="Delete practice run", exact=True
                    ).click()
                await expect(page.locator(".tour-box")).to_have_count(0)
                assert not folder.exists() and not pointer.exists()
                assert not errors
            finally:
                await browser.close()


async def test_a_scorer_refusal_before_analysis_reports_why_and_ends_the_practice(
    tmp_path, monkeypatch
):
    import asyncio
    import json
    import sys

    fake = tmp_path / "refusing_scorer.py"
    fake.write_text(
        "import sys; from ffast.live import run_folder as rf; "
        "rf.append_event(sys.argv[1], 'refused', reason='DetectAna unavailable for practice')"
    )
    monkeypatch.setenv("FFAST_PRACTICE_RUNS", str(tmp_path / "practice"))
    monkeypatch.setenv("FFAST_LIVE_RUNS", str(tmp_path / "pointers"))
    monkeypatch.setenv("FFAST_REFERENCES", str(tmp_path / "references"))
    monkeypatch.setenv(
        "FFAST_LIVE_SCORER_CMD", json.dumps([sys.executable, str(fake)])
    )
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(
                    viewport={"width": 1500, "height": 1000}
                )
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}")
                await _expect_connected(page)
                await _lammps_menu(page, "Tour: Watch a LAMMPS run")
                await page.get_by_role(
                    "button", name="Start practice run", exact=True
                ).click()
                await expect(page.locator(".tour-box")).to_have_count(
                    0, timeout=15000
                )
                await expect(page.locator("#status")).to_contain_text(
                    "DetectAna unavailable for practice"
                )
                [pointer] = list((tmp_path / "pointers").glob("*.json"))
                folder = Path(rf.read_json(pointer)["run_dir"])
                async with asyncio.timeout(15):
                    while not (rf.read_json(folder / rf.RUN_JSON) or {}).get(
                        "end"
                    ):
                        await asyncio.sleep(0.02)
                assert (
                    rf.read_json(folder / rf.RUN_JSON)["end"]["state"]
                    == "stopped"
                )
            finally:
                await browser.close()

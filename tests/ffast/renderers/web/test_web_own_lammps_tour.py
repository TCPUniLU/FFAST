"""Local LAMMPS tutorial through the real server, wrapper and scorer."""

import os
import sys
from pathlib import Path

import pytest
from playwright.async_api import async_playwright, expect

from tests.ffast.renderers.web.test_web_runtime import (
    _expect_connected,
    _lammps_menu,
    _spawn_server,
    _wait_for_server_ready,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[4]


def fake_lammps(folder):
    folder.mkdir()
    if os.environ.get("FFAST_TEST_LAMMPS"):
        exe = folder / "lmp"
        exe.symlink_to(os.environ["FFAST_TEST_LAMMPS"])
        return exe
    help_path = (
        ROOT
        / "tests/ffast/live/fixtures/run_with_ffast/lmp_help_constructed.txt"
    )
    source = ROOT / "examples/lammps/recorded-alarm"
    script = f"""#!{sys.executable}
import sys
from pathlib import Path
if '-h' in sys.argv:
    print(Path({str(help_path)!r}).read_text())
elif 'test.in' in sys.argv:
    for stage in 'snapshot reset_after_snapshot include run reset_after_dump run_again done'.split():
        print('FFAST-CHECK ' + stage)
else:
    from ffast.live.replay import main
    args = sys.argv[1:]
    i = args.index('-in')
    del args[i:i+2]
    raise SystemExit(main([{str(source)!r}, '--sleep', '0.02', '--gate-step', '10000'] + args))
"""
    exe = folder / "lmp"
    exe.write_text(script)
    exe.chmod(0o755)
    return exe


async def test_run_your_own_lammps_checks_reviews_starts_heats_and_stops(
    tmp_path, monkeypatch
):
    fake_lammps(tmp_path / "bin")
    monkeypatch.setenv(
        "PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}"
    )
    monkeypatch.setenv("FFAST_PRACTICE_RUNS", str(tmp_path / "practice"))
    monkeypatch.setenv("FFAST_LIVE_RUNS", str(tmp_path / "pointers"))
    monkeypatch.setenv("FFAST_REFERENCES", str(tmp_path / "references"))
    monkeypatch.setenv("FFAST_LAMMPS_DATA_RECORDS", str(tmp_path / "records"))
    monkeypatch.setenv("FFAST_RUN_WITH_FFAST", str(tmp_path / "memory"))
    monkeypatch.setenv("FFAST_PRACTICE_SLEEP", "0.02")
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                viewport={"width": 1500, "height": 1000}
            )
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}")
                await _expect_connected(page)
                await _lammps_menu(page, "Tour: Run your own LAMMPS")
                title = page.locator(".tour-title")
                await expect(title).to_have_text("Open Run with FFAST")
                await _lammps_menu(page, "Run with FFAST…")
                await expect(title).to_have_text("Choose the run folder")
                d = page.locator("#run-with-ffast-dialog")
                await expect(d.locator("#rwf-folder")).to_have_value(
                    str(tmp_path / "practice/tutorial-input")
                )
                await expect(page.locator(".tour-next")).to_be_visible()
                await page.locator(".tour-next").click()
                await expect(title).to_have_text("Run it here")
                await d.get_by_label("This computer", exact=True).check()
                await expect(title).to_have_text("Check")
                go = d.locator(".rwf-go")
                await expect(go).to_be_enabled()
                await go.click()
                await expect(d.locator("#rwf-builds")).to_contain_text(
                    "passed", timeout=30000
                )
                await page.locator(".tour-next").click()
                await expect(title).to_have_text("The include line")
                await go.click()
                await expect(
                    d.locator(".rwf-diff .add", has_text="include ")
                ).to_have_count(1)
                await page.locator(".tour-next").click()
                await expect(title).to_have_text(
                    "Snapshot, and two tutorial lines"
                )
                await expect(d.locator(".rwf-diff")).to_contain_text(
                    "write_dump"
                )
                await expect(d.locator(".rwf-diff")).to_contain_text(
                    "shell sleep"
                )
                await page.locator(".tour-next").click()
                await expect(title).to_have_text("The data copy")
                await d.get_by_role(
                    "tab", name="aspirin.ffast.data", exact=False
                ).click()
                await page.locator(".tour-next").click()
                await expect(title).to_have_text("Start it")
                await expect(go).to_have_text("Write copies and start")
                await go.click()
                await expect(title).to_have_text("Heat it up", timeout=30000)
                await expect(d).to_have_count(0)
                active = page.locator(".tabpanel.active")
                await expect(
                    active.locator(".live-stop-on-alarm")
                ).not_to_be_checked()
                heat = page.get_by_role(
                    "button", name="Heat it up", exact=True
                )
                await expect(heat).to_be_enabled(timeout=15000)
                await heat.click()
                await expect(title).to_have_text("An alarm")
                await expect(page.locator(".tour-next")).to_be_visible(
                    timeout=30000
                )
                await expect(
                    active.locator(".live-state")
                ).not_to_contain_text("Stopped")
                await page.locator(".tour-next").click()
                await expect(title).to_have_text("Stop it yourself")
                await active.get_by_role("button", name="Manage runs…").click()
                runs = page.locator("#live-runs-dialog")
                await runs.get_by_role("button", name="Stop now…").click()
                await page.locator(".ask-dialog").get_by_role(
                    "button", name="Stop run", exact=True
                ).click()
                await expect(page.locator(".tour-next")).to_be_visible(
                    timeout=30000
                )
                await page.locator(".tour-next").click()
                await expect(title).to_have_text("For your own runs")
                await expect(runs).to_have_count(0)
                await page.locator(".tour-next").click()
                await expect(title).to_have_text("Keep the practice run?")
                await page.get_by_role(
                    "button", name="Delete practice run", exact=True
                ).click()
                await expect(page.locator(".tour-layer")).to_have_count(0)
                assert errors == []
                assert not list((tmp_path / "pointers").glob("*.json"))
            finally:
                await browser.close()


@pytest.mark.parametrize("choice", ["path", "replay"])
async def test_no_lammps_offers_a_path_or_the_replay_tour(
    tmp_path, monkeypatch, choice
):
    exe = fake_lammps(tmp_path / "elsewhere")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("FFAST_PRACTICE_RUNS", str(tmp_path / "practice"))
    monkeypatch.setenv("FFAST_REFERENCES", str(tmp_path / "references"))
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                viewport={"width": 1500, "height": 1000}
            )
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}")
                await _expect_connected(page)
                await _lammps_menu(page, "Tour: Run your own LAMMPS")
                await _lammps_menu(page, "Run with FFAST…")
                await expect(page.locator(".tour-next")).to_be_visible()
                await page.locator(".tour-next").click()
                d = page.locator("#run-with-ffast-dialog")
                await d.get_by_label("This computer", exact=True).check()
                await d.locator(".rwf-go").click()
                await expect(d.locator("#rwf-builds")).to_contain_text(
                    "No LAMMPS found on PATH"
                )
                await expect(d.locator(".rwf-go")).to_be_disabled()
                if choice == "path":
                    await d.get_by_label("LAMMPS path", exact=True).fill(
                        str(exe)
                    )
                    await d.get_by_role(
                        "button", name="Check this path"
                    ).click()
                    await expect(d.locator("#rwf-builds")).to_contain_text(
                        "passed", timeout=30000
                    )
                    await expect(d.locator(".rwf-go")).to_be_enabled()
                    await expect(page.locator(".tour-next")).to_be_visible()
                else:
                    fallback = page.get_by_role(
                        "button", name="Take “Watch a LAMMPS run” instead"
                    )
                    await expect(fallback).to_be_enabled()
                    await fallback.click()
                    await expect(page.locator(".tour-title")).to_have_text(
                        "Start a practice run"
                    )
                    await expect(d).to_have_count(0)
            finally:
                await browser.close()


async def test_own_lammps_tour_is_unavailable_on_an_ssh_server(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SSH_CONNECTION", "192.0.2.1 1234 192.0.2.2 22")
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}")
                await _expect_connected(page)
                await page.locator("#lammps-menu-btn").click()
                own = page.get_by_role(
                    "menuitem", name="Tour: Run your own LAMMPS", exact=True
                )
                await expect(own).to_be_disabled()
                await expect(own).to_have_attribute(
                    "title",
                    "Needs FFAST running on this computer (Linux or macOS)",
                )
                await expect(
                    page.get_by_role(
                        "menuitem", name="Tour: Watch a LAMMPS run", exact=True
                    )
                ).to_be_enabled()
            finally:
                await browser.close()

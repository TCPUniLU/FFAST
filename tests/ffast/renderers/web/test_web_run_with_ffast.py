"""The "Run with FFAST" dialog in the browser (ADR 0086, plan step 5)."""

from __future__ import annotations

import functools
import http.server
import os
import re
import shutil
import threading
from pathlib import Path

import pytest

pytest.importorskip("playwright.async_api")

from playwright.async_api import async_playwright, expect  # noqa: E402

from ffast.live import run_folder as rf  # noqa: E402
from tests.ffast.live.bundle_helpers import make_bundle  # noqa: E402
from tests.ffast.live.test_live_run import _run_folder  # noqa: E402
from tests.ffast.renderers.web.test_web_runtime import (  # noqa: E402
    _expect_connected, _file_menu, _spawn_server, _wait_for_server_ready,
)

pytestmark = pytest.mark.integration

STATIC_DIR = Path(__file__).resolve().parents[4] / "ffast" / "renderers" / "web" / "static"
FIX = Path(__file__).resolve().parents[2] / "live" / "fixtures" / "run_with_ffast"
JOB_ID = "4242"


@pytest.fixture(scope="module")
def static_origin():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(STATIC_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


async def test_run_with_ffast_helpers(static_origin):
    script = f"""async () => {{
      const m = await import('{static_origin}/run_with_ffast_dialog.js');
      const diff = '--- a (original)\\n+++ b (copy)\\n@@ -1,2 +1,3 @@\\n units metal\\n-read_data a\\n+read_data b\\n+include x';
      return {{
        lines: m.diffLines(diff).map((l) => l.cls),
        label: m.copyLabel({{ kind: 'input', copy: '/w/run.ffast.in', added: 3, removed: 1 }}),
        data: m.copyLabel({{ kind: 'data', copy: '/w/run.ffast.data', atoms: 21 }}),
        empty: m.chooseProblem({{ input: '', job: 'j', bundle: 'b', data: 'none' }}),
        file: m.chooseProblem({{ input: 'i', job: 'j', bundle: 'b', data: 'file', dataPath: '' }}),
        space: m.chooseProblem({{ input: '/a b/i', job: 'j', bundle: 'b', data: 'none' }}),
        ok: m.chooseProblem({{ input: 'i', job: 'j', bundle: 'b', data: 'bundle' }}),
        states: ['queued', 'running', 'started', 'frame', 'ended'].map((s) =>
          m.jobStateText({{ job_id: '7', state: s, folder: '/r', reason: 'the job ended (FAILED) before its first frame' }})),
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
    assert got["lines"] == ["head", "head", "head", "ctx", "del", "add", "add"]
    assert got["label"] == "run.ffast.in (+3 −1)" and got["data"] == "run.ffast.data (new, 21 atoms)"
    assert got["empty"].startswith("Pick") and "structure file" in got["file"] and "spaces" in got["space"]
    assert got["ok"] == ""
    assert "queue" in got["states"][0] and "/r" in got["states"][2] and "FAILED" in got["states"][4]


def _fake_cluster(bin_dir: Path) -> None:
    """lmp that passes the module check, sbatch that answers a job id, squeue that says RUNNING."""
    bin_dir.mkdir()
    help_text = bin_dir / "lmp_help.txt"
    shutil.copy(FIX / "lmp_help_constructed.txt", help_text)
    scripts = {
        "lmp": f'#!/bin/sh\nif [ "$1" = "-h" ]; then cat "{help_text}"; exit 0; fi\n'
               'echo "LAMMPS (2 Aug 2023)"; for s in snapshot reset_after_snapshot include run '
               'reset_after_dump run_again done; do echo "FFAST-CHECK $s"; done\n',
        "sbatch": f'#!/bin/sh\npwd > "{bin_dir}/sbatch_cwd"; echo "$1" > "{bin_dir}/sbatch_script"\n'
                  f'env | grep -c "^SLURM_" > "{bin_dir}/sbatch_slurm_vars"\necho "Submitted batch job {JOB_ID}"\n',
        "squeue": "#!/bin/sh\necho RUNNING\n",
    }
    for name, text in scripts.items():
        (bin_dir / name).write_text(text)
        os.chmod(bin_dir / name, 0o755)


async def test_web_run_with_ffast_checks_reviews_submits_and_opens_the_run(tmp_path, monkeypatch):
    (tmp_path / "ref").mkdir()
    bundle_dir, include = make_bundle(tmp_path / "ref")
    work = tmp_path / "aspirin"
    work.mkdir()
    shutil.copy(FIX / "inputs" / "minimize.in", work / "run.in")
    (work / "job.sh").write_text("#!/bin/bash -l\n#SBATCH --ntasks=1\ncd $SLURM_SUBMIT_DIR\nsrun lmp -in run.in\n")
    fake = tmp_path / "bin"
    _fake_cluster(fake)
    pointers = tmp_path / "pointers"
    pointers.mkdir()
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FFAST_LIVE_RUNS", str(pointers))
    monkeypatch.setenv("FFAST_LAMMPS_DATA_RECORDS", str(tmp_path / "records"))
    monkeypatch.setenv("SLURM_JOB_ID", "1")          # the server's own job: must not leak into sbatch

    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1300, "height": 900})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _file_menu(page, "Run with FFAST…")
                d = page.locator("#run-with-ffast-dialog")
                go = d.locator(".rwf-go")
                await expect(go).to_be_disabled()
                # 1 Choose: one folder; FFAST finds the job script, its input, the data file
                # and the reference (the one this test made, recorded when it was made).
                await d.locator("#rwf-folder").fill(str(work))
                await d.locator("#rwf-folder").press("Enter")
                found = d.locator("#rwf-found")
                # wait for THIS folder's lines (the last folder used opens first)
                await expect(found).to_have_attribute("data-folder", str(work.resolve()), timeout=15000)
                await expect(found).to_contain_text("run by job.sh: srun lmp -in run.in")
                await expect(found).to_contain_text("aspirin.data")
                await expect(d.locator("#rwf-bundle")).to_have_value(str(bundle_dir.resolve()))
                await expect(go).to_be_enabled()
                await go.click()

                # 2 Check: the module, input and job checks; Review becomes possible.
                await expect(d.locator(".rwf-checks li.ok", has_text="2 Aug 2023")).to_have_count(1, timeout=60000)
                await expect(d.locator(".rwf-checks li", has_text="snapshot after line 3")).to_have_count(1)
                await expect(go).to_have_text("Review ›")
                await expect(go).to_be_enabled()
                await go.click()

                # 3 Review: three copies; the input diff shows the include.
                await expect(d.locator(".rwf-tab")).to_have_count(3)
                await expect(d.locator(".rwf-diff .add", has_text=f"include {include}")).to_have_count(1)
                await d.locator(".rwf-tab").nth(1).click()
                await expect(d.locator(".rwf-diff .add", has_text="ffast-lammps srun lmp -in run.ffast.in")).to_have_count(1)
                # No ffast-web launcher here: only the server's node can submit.
                await expect(d.locator('input[name="rwf-via"][value="laptop"]')).to_be_disabled()
                await expect(d.locator('input[name="rwf-via"][value="server"]')).to_be_checked()
                assert not (work / "run.ffast.in").exists()
                await go.click()
                await expect(d.locator(".rwf-progress")).to_contain_text(f"job {JOB_ID}", timeout=20000)

                # The wrapper starts the run in the job; its first frame opens the run's tab.
                run = _run_folder(tmp_path, frames=1)
                rf.write_json_atomic(pointers / f"{run.name}.json",
                                     {"run_id": run.name, "run_dir": str(run), "slurm_job_id": JOB_ID})
                await expect(d).to_have_count(0, timeout=20000)
                tab = page.locator("#tabbar .tab", has_text="Live 12:00:00 node")
                await expect(tab).to_have_class(re.compile(r"\bactive\b"), timeout=15000)
            finally:
                await browser.close()

    assert (work / "run.ffast.in").exists() and (work / "job.ffast.sh").exists()
    assert (work / "aspirin.ffast.data").exists()
    assert (fake / "sbatch_cwd").read_text().strip() == str(work.resolve())
    assert (fake / "sbatch_script").read_text().strip() == "job.ffast.sh"
    assert (fake / "sbatch_slurm_vars").read_text().strip() == "0"


async def test_web_a_refused_input_shows_the_reason_and_the_hand_edit(tmp_path, monkeypatch):
    (tmp_path / "ref").mkdir()
    bundle_dir, include = make_bundle(tmp_path / "ref")
    work = tmp_path / "w"
    work.mkdir()
    shutil.copy(FIX / "inputs" / "jump_loop.in", work / "run.in")
    (work / "job.sh").write_text("#!/bin/bash\n#SBATCH --ntasks=1\nsrun lmp -in run.in\n")
    fake = tmp_path / "bin"
    _fake_cluster(fake)
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ['PATH']}")
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1300, "height": 900})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _file_menu(page, "Run with FFAST…")
                d = page.locator("#run-with-ffast-dialog")
                await d.locator("#rwf-folder").fill(str(work))
                await d.locator("#rwf-folder").press("Enter")
                await expect(d.locator("#rwf-found")).to_have_attribute(
                    "data-folder", str(work.resolve()), timeout=15000)
                await d.locator("#rwf-bundle").select_option(str(bundle_dir.resolve()))
                await d.locator("#rwf-data-on").uncheck()
                await d.locator(".rwf-go").click()
                refusal = d.locator(".rwf-refusal")
                await expect(refusal).to_contain_text("line 10", timeout=30000)
                await expect(refusal.locator(".rwf-hand")).to_contain_text(f"include {include}")
                await expect(d.locator(".rwf-go")).to_be_disabled()
            finally:
                await browser.close()
    assert not (work / "run.ffast.in").exists()


async def test_web_the_file_browser_opens_above_the_dialog_and_fills_the_field(tmp_path):
    """The … button opened the server's file browser behind the Run-with-FFAST
    dialog, where it could not be used (user report 2026-11-06)."""
    work = tmp_path / "w"
    work.mkdir()
    (work / "run.in").write_text("units metal\nread_data a.data\nrun 10\n")
    (work / "job.sh").write_text("#!/bin/bash\n#SBATCH --ntasks=1\nsrun lmp -in run.in\n")
    elsewhere = tmp_path / "refs"
    toy = elsewhere / "toy.bundle"
    toy.mkdir(parents=True)
    (toy / "manifest.json").write_text('{"molecule": {"n_atoms": 3, "elements": ["O", "H", "H"]}}')
    (elsewhere / "toy.lmp").write_text("elements O H\n")
    async with _spawn_server() as (ws_port, web_port):
        await _wait_for_server_ready(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1300, "height": 900})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await _file_menu(page, "Run with FFAST…")
                d = page.locator("#run-with-ffast-dialog")
                await d.locator("#rwf-folder + button").click()
                await expect(page.locator("#fb-modal")).to_be_visible()
                on_top = await page.evaluate(
                    "() => document.getElementById('fb-modal').contains("
                    "document.elementFromPoint(innerWidth / 2, innerHeight / 2))")
                assert on_top, "the file browser is behind the dialog"
                await page.locator("#fb-path").fill(str(work))
                await page.locator("#fb-path").press("Enter")
                await expect(page.locator("#fb-path")).to_have_value(str(work))
                await page.locator("#fb-load").click()       # "Choose" this folder
                await expect(page.locator("#fb-modal")).to_be_hidden()
                await expect(d.locator("#rwf-folder")).to_have_value(str(work.resolve()))
                await expect(d.locator("#rwf-found")).to_contain_text("run by job.sh", timeout=15000)

                # "Other…" for the reference: the .bundle folder is one item, not a folder to open.
                await d.locator("#rwf-bundle").select_option("__other__")
                await expect(page.locator("#fb-modal")).to_be_visible()
                await page.locator("#fb-path").fill(str(elsewhere))
                await page.locator("#fb-path").press("Enter")
                item = page.locator(".fb-row.item", has_text="toy.bundle")
                await item.click()
                await expect(page.locator("#fb-path")).to_have_value(str(elsewhere))   # did not open it
                await page.locator("#fb-load").click()
                await expect(d.locator("#rwf-bundle")).to_have_value(str(toy))
                # A browser opened on its own (no dialog) keeps its usual place.
                await d.locator(".rwf-go").page.keyboard.press("Escape")
                await expect(d).to_have_count(0)
                assert not await page.evaluate("document.getElementById('fb-modal').classList.contains('over-dialog')")
            finally:
                await browser.close()

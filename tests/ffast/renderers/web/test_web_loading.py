"""The web loader against a real server: loader types, energy/force keys,
every Nth frame, .npz predictions, models (web parity plan 2026-10-29)."""
import re

import ase.io
import numpy as np
import pytest
from playwright.async_api import async_playwright, expect

from .test_web_runtime import (
    DATASET_PATH, PREDICTION_PATH, REPO_ROOT, _RECORD_SENDS, _expect_connected,
    _file_menu, _open_view, _preload_dataset, _preload_uniform_dataset,
    _shared_servers, _spawn_server, _wait_for_server_ready, shared_server,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def ffast_web_server():
    async with _spawn_server() as ports:
        yield ports


async def _go_to(page, folder):
    """In the open loader, list `folder`."""
    await page.locator("#fb-path").fill(str(folder))
    await page.locator("#fb-path").press("Enter")


async def _sent(page, event):
    """The kwargs of every `event` the page sent since _RECORD_SENDS ran."""
    return await page.evaluate(
        "(e) => window.__sent.filter(([name]) => name === e).map(([, kw]) => kw)", event)


async def test_web_memory_saving_mode_loads_and_forwards_the_choice(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await _expect_connected(page)
            await page.evaluate(_RECORD_SENDS)
            await _file_menu(page, "Load Dataset…")
            await _go_to(page, DATASET_PATH.parent)
            await page.locator("#fb-list .fb-row", has_text="dataset.xyz").click()
            await page.get_by_label("Memory", exact=True).select_option("saving")
            await page.locator("#fb-load").click()
            await expect(page.locator("#dataset-list .obj-row")).to_have_count(1, timeout=30000)
            assert (await _sent(page, "LOAD_DATASET"))[-1] == {"memory_mode": "saving"}
        finally:
            await browser.close()


async def test_web_the_type_list_is_the_servers_and_follows_the_file(shared_server, tmp_path):
    ws_port, web_port = shared_server
    await _wait_for_server_ready(ws_port)
    np.savez(tmp_path / "m.npz", x=np.zeros(1))
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await _expect_connected(page)
            await _file_menu(page, "Load Dataset…")
            values = await page.locator("#fb-type option").evaluate_all("(os) => os.map((o) => o.value)")
            assert values == ["ase (auto)", "sGDML"]
            await _go_to(page, tmp_path)
            await page.locator("#fb-list .fb-row", has_text="m.npz").click()
            await expect(page.locator("#fb-type")).to_have_value("sGDML")
            ase.io.write(tmp_path / "a.xyz", ase.io.read(DATASET_PATH, index=0))
            await _go_to(page, tmp_path)
            await page.locator("#fb-list .fb-row", has_text="a.xyz").click()
            await expect(page.locator("#fb-type")).to_have_value("ase (auto)")
            await page.locator("#fb-type").select_option("sGDML")
            await expect(page.locator("#fb-type")).to_have_value("sGDML")
        finally:
            await browser.close()


def _two_key_file(tmp_path):
    """10 frames with REF_ and MACE_ energies and forces."""
    frames = ase.io.read(DATASET_PATH, index=":10")
    for a in frames:
        a.info["MACE_energy"] = a.info["REF_energy"] + 0.1
        a.arrays["MACE_forces"] = a.arrays["REF_forces"] + 0.01
    path = tmp_path / "two_keys.xyz"
    ase.io.write(path, frames, format="extxyz")
    return path


async def test_web_a_dataset_with_two_keys_asks_which_one(ffast_web_server, tmp_path):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    _two_key_file(tmp_path)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await _expect_connected(page)
            await page.evaluate(_RECORD_SENDS)
            await _file_menu(page, "Load Dataset…")
            await _go_to(page, tmp_path)
            await page.locator("#fb-list .fb-row", has_text="two_keys.xyz").click()
            await expect(page.locator("#fb-keys")).to_be_visible()
            await expect(page.locator("#fb-ds-energy-key")).to_have_value("REF_energy")
            options = await page.locator("#fb-ds-energy-key option").evaluate_all(
                "(os) => os.map((o) => o.value)")
            assert "MACE_energy" in options
            await page.locator("#fb-ds-energy-key").select_option("MACE_energy")
            await page.locator("#fb-ds-force-key").select_option("MACE_forces")
            await page.locator("#fb-load").click()
            await expect(page.locator("#dataset-list .obj-row")).to_have_count(1, timeout=30000)
            assert await _sent(page, "LOAD_DATASET") == [
                {"selected_energy_key": "MACE_energy", "selected_force_key": "MACE_forces"}]
            await _file_menu(page, "Load Dataset…")
            await _go_to(page, DATASET_PATH.parent)
            await page.locator("#fb-list .fb-row", has_text="dataset.xyz").click()
            await expect(page.locator("#fb-load")).to_be_enabled()
            await expect(page.locator("#fb-keys")).to_be_hidden()
            await page.locator("#fb-load").click()
            assert (await _sent(page, "LOAD_DATASET"))[-1] == {}
        finally:
            await browser.close()


async def test_web_a_recent_dataset_loads_at_once_with_default_keys(ffast_web_server, tmp_path):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    _two_key_file(tmp_path)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await _expect_connected(page)
            await _file_menu(page, "Load Dataset…")
            await _go_to(page, tmp_path)
            await page.locator("#fb-list .fb-row", has_text="two_keys.xyz").click()
            await expect(page.locator("#fb-keys")).to_be_visible()
            await page.locator("#fb-load").click()
            await expect(page.locator("#dataset-list .obj-row")).to_have_count(1, timeout=30000)
            await page.evaluate(_RECORD_SENDS)
            await _file_menu(page, "Load Dataset…")
            await _go_to(page, DATASET_PATH.parent)
            await page.locator("#fb-list .fb-row", has_text="dataset.xyz").click()
            await page.locator(
                "#fb-recent .fb-recent-row", has_text=str(tmp_path / "two_keys.xyz")).click()
            await expect(page.locator("#fb-modal")).to_be_hidden()
            assert await _sent(page, "LOAD_DATASET") == [{}]
            assert len(await _sent(page, "PROBE_DATASET_KEYS")) == 1
        finally:
            await browser.close()


async def test_web_more_keys_of_a_file_load_as_predictions(ffast_web_server, tmp_path):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    _two_key_file(tmp_path)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 900})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await _expect_connected(page)
            await page.evaluate(_RECORD_SENDS)
            await _file_menu(page, "Load Dataset…")
            await _go_to(page, tmp_path)
            await page.locator("#fb-list .fb-row", has_text="two_keys.xyz").click()
            await expect(page.locator("#fb-keys")).to_be_visible()
            await page.locator("#fb-add-pred").click()
            rows = page.locator("#fb-pred-rows .fb-pred-row")
            await expect(rows.locator(".fb-pred-energy")).to_have_value("MACE_energy")
            await expect(rows.locator(".fb-pred-force")).to_have_value("MACE_forces")
            await expect(rows.locator(".fb-pred-name")).to_have_value("MACE")
            await page.locator("#fb-add-pred").click()
            await expect(rows).to_have_count(2)
            await rows.nth(1).get_by_role("button", name="Remove").click()
            await expect(rows).to_have_count(1)
            await page.locator("#fb-load").click()
            await expect(page.locator("#model-list .obj-row", has_text="MACE")).to_be_visible(timeout=30000)
            assert await _sent(page, "LOAD_DATASET") == [{
                "selected_energy_key": "REF_energy", "selected_force_key": "REF_forces",
                "prediction_keys": [["MACE_energy", "MACE_forces", "MACE"]]}]
        finally:
            await browser.close()


async def test_web_every_nth_frame_and_the_large_file_note(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 900})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await _expect_connected(page)
            await page.evaluate(_RECORD_SENDS)
            await page.evaluate("() => { window.ffastApp._browser._largeBytes = 1; }")
            await _file_menu(page, "Load Dataset…")
            await _go_to(page, DATASET_PATH.parent)
            await page.locator("#fb-list .fb-row", has_text="dataset.xyz").click()
            await expect(page.locator("#fb-large")).to_contain_text("100 frames", timeout=15000)
            await page.locator("#fb-frames").select_option("nth")      # Frames: Every Nth (ADR 0096)
            await page.locator("#fb-stride").fill("10")
            await expect(page.locator("#fb-large")).to_contain_text("~10")
            await page.locator("#fb-load").click()
            assert (await _sent(page, "LOAD_DATASET"))[-1] == {"slice_num": 10}
            await page.wait_for_function(
                "() => [...window.ffastApp._datasets.values()].some((m) => m.n === 10)", timeout=30000)
        finally:
            await browser.close()


async def test_web_an_npz_prediction_loads(ffast_web_server, tmp_path):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    dataset_fp = await _preload_uniform_dataset(ws_port, tmp_path)
    n_atoms = len(ase.io.read(DATASET_PATH, index=0))
    np.savez(tmp_path / "pred.npz", E=np.zeros(5), F=np.zeros((5, n_atoms, 3)))
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            await _open_view(page, ws_port, web_port, dataset_fp)
            await page.evaluate(_RECORD_SENDS)
            await _file_menu(page, "Load Prediction…")
            await _go_to(page, tmp_path)
            await page.locator("#fb-list .fb-row", has_text="pred.npz").click()
            # An .npz is the sGDML reader's file (ADR 0095 step 3): probed like any
            # other, its names E and F chosen at once.
            await expect(page.locator("#fb-energy-key")).to_have_value("E")
            await expect(page.locator("#fb-force-key")).to_have_value("F")
            await expect(page.locator("#fb-load")).to_be_enabled()
            await page.locator("#fb-load").click()
            await expect(page.locator("#model-list .obj-row", has_text="pred.npz")).to_be_visible(timeout=30000)
            assert len(await _sent(page, "PROBE_DATASET_KEYS")) == 1
            assert await _sent(page, "LOAD_PREDICTION") == [
                {"selected_energy_key": "E", "selected_force_key": "F", "reader": "sGDML"}]
        finally:
            await browser.close()


async def test_web_a_model_that_cannot_load_says_why(shared_server):
    ws_port, web_port = shared_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await _expect_connected(page)
            await page.evaluate(_RECORD_SENDS)
            await _file_menu(page, "Load Model…")
            await expect(page.locator("#fb-title")).to_have_text("Load Model")
            values = await page.locator("#fb-model-type option").evaluate_all(
                "(os) => os.map((o) => o.value)")
            assert "sGDML" in values
            await _go_to(page, REPO_ROOT)
            await page.locator("#fb-list .fb-row", has_text="README.md").click()
            await page.locator("#fb-model-type").select_option("sGDML")
            await page.locator("#fb-load").click()
            status = page.locator("#status")
            await expect(status).to_contain_text("Could not load README.md", timeout=30000)
            await expect(status).to_have_class(re.compile(r"\berror\b"))
            assert await _sent(page, "LOAD_MODEL") == [{}]
            await page.wait_for_timeout(1500)
            await expect(page.locator("#toasts .toast")).to_have_count(1)
        finally:
            await browser.close()


async def test_web_the_zero_model_loads_only_when_asked(ffast_web_server):
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    dataset_fp = await _preload_dataset(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            await _open_view(page, ws_port, web_port, dataset_fp)
            await expect(page.locator("#model-list .obj-row")).to_have_count(0)
            await _file_menu(page, "Load Zero Model")
            row = page.locator("#model-list .obj-row", has_text="Zero Model")
            await expect(row).to_be_visible(timeout=30000)
            await expect(row).not_to_have_class(re.compile(r"\bselected\b"))
            await expect(row).not_to_have_class(re.compile(r"\bdisabled\b"))
            assert await page.evaluate("() => window.ffastApp._currentModelFp") is None
            await expect(page.locator("#status")).to_contain_text('Model "Zero Model" loaded')
            await row.click()
            await expect(row).to_have_class(re.compile(r"\bselected\b"))
            await _file_menu(page, "Load Zero Model")
            await expect(page.locator("#status")).to_have_text(
                '"Zero Model" is already loaded', timeout=15000)
        finally:
            await browser.close()


async def test_web_another_task_ending_does_not_end_a_load(ffast_web_server):
    """ADR 0097: a load ends with its own final reply. Before, the next task
    that ended anywhere ended it as "already loaded", and the dataset that
    then arrived was not treated as this window's load."""
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 820})
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await _expect_connected(page)
            await page.evaluate("""async ([path, origin]) => {
                const { msgpack } = await import(`${origin}/msgpack.js`);
                const app = window.ffastApp;
                window.__signals = [];
                app.tourEvents.addEventListener('dataset-loaded', (e) => window.__signals.push(['loaded', e.detail.fp]));
                app.tourEvents.addEventListener('load-duplicate', () => window.__signals.push(['duplicate']));
                app._browser._startLoad({ kind: 'dataset', name: 'dataset.xyz', path },
                                        'LOAD_DATASET', {}, [path, 'ase (auto)']);
                // Some other task ends before this load has answered.
                const bytes = msgpack.encode({ event: 'TASK_DONE', args: ['other-task'], kwargs: {} });
                app._conn._dispatch(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), 0);
            }""", [str(DATASET_PATH), f"http://127.0.0.1:{web_port}"])
            await expect(page.locator("#status")).not_to_contain_text("already loaded")
            await expect(page.locator("#dataset-list .obj-row")).to_have_count(1, timeout=30000)
            await page.wait_for_function("() => window.__signals.length > 0")
            signals = await page.evaluate("() => window.__signals")
            assert signals[0][0] == "loaded"
            assert ["duplicate"] not in signals
            assert await page.evaluate("() => window.ffastApp._browser._pending") is None
        finally:
            await browser.close()


async def test_web_units_named_with_a_prediction_from_the_same_file_show_on_the_colourbar(
        ffast_web_server, tmp_path):
    """The steps of a real session (trace 2026-11-21 17:57): one load names
    both units and takes a prediction from the same file; the colourbar of
    the force error colouring then shows the force unit, not "[unit]"."""
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    _two_key_file(tmp_path)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 900})
        label = page.locator("#colorbar-label")
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await _expect_connected(page)
            await _file_menu(page, "Load Dataset…")
            await _go_to(page, tmp_path)
            await page.locator("#fb-list .fb-row", has_text="two_keys.xyz").click()
            await expect(page.locator("#fb-keys")).to_be_visible()
            await page.locator("#fb-add-pred").click()
            await page.locator("#fb-energy-unit").fill("eV")
            await page.locator("#fb-force-unit").fill("eV/Å")
            await page.locator("#fb-load").click()
            await expect(page.locator("#model-list .obj-row", has_text="MACE")).to_be_visible(timeout=30000)
            await page.locator("#model-list .obj-row", has_text="MACE").click()
            await page.locator("#quick-styles button", has_text="Force error").click()
            try:
                await expect(label).to_have_text(re.compile(r"\(eV/Å\)$"), timeout=15000)
            except AssertionError as exc:
                seen = await page.evaluate(
                    "() => { const a = window.ffastApp; return { focused: a._focusedDatasetFp(),"
                    " units: Object.fromEntries([...a._datasets].map(([k, m]) => [k, m.units])) }; }")
                raise AssertionError(f"{exc}\npage state: {seen}") from None
        finally:
            await browser.close()


async def test_web_a_file_path_typed_or_pasted_chooses_that_file(ffast_web_server):
    """Enter on a file's path, or the path pasted into the dialog, lists the
    file's folder and chooses the file as a click would; a path that does
    not exist says which part is missing."""
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 900})
        chosen = page.locator("#fb-list .fb-row.file.selected .name")
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await _expect_connected(page)
            await _file_menu(page, "Load Dataset…")
            await _go_to(page, DATASET_PATH)
            await expect(chosen).to_have_text(DATASET_PATH.name)
            await expect(page.locator("#fb-path")).to_have_value(str(DATASET_PATH.parent))
            await expect(page.locator("#fb-load")).to_be_enabled()

            await _go_to(page, REPO_ROOT)
            await expect(page.locator("#fb-path")).to_have_value(str(REPO_ROOT))
            await page.locator("#fb-list").evaluate(
                """(list, text) => { const data = new DataTransfer(); data.setData('text', text);
                  list.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true })); }""",
                str(PREDICTION_PATH))
            await expect(chosen).to_have_text(PREDICTION_PATH.name)

            await _go_to(page, DATASET_PATH.parent / "no_such_run" / "a.xyz")
            await expect(page.locator("#fb-error")).to_have_text(
                f'No "no_such_run" in {DATASET_PATH.parent}')
        finally:
            await browser.close()


async def test_web_the_file_list_sorts_by_a_clicked_column_and_shows_dates(ffast_web_server, tmp_path):
    """Name, Size and Modified order the list (folders stay first); the
    order is kept for the next opening; each file shows when it changed."""
    import os
    for name, size, age_days in [("big.xyz", 300, 9), ("mid.xyz", 200, 0), ("small.xyz", 100, 4)]:
        path = tmp_path / name
        path.write_bytes(b"x" * size)
        when = path.stat().st_mtime - age_days * 86400
        os.utime(path, (when, when))
    (tmp_path / "sub").mkdir()
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 900})
        names = page.locator("#fb-list .fb-row .name")
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await _expect_connected(page)
            await _file_menu(page, "Load Dataset…")
            await _go_to(page, tmp_path)
            await expect(names).to_have_text(["sub", "big.xyz", "mid.xyz", "small.xyz"])
            await expect(page.locator("#fb-list .fb-row.file", has_text="mid.xyz").locator(".when")).to_contain_text("Today")
            await page.locator("#fb-cols [data-sort='size']").click()
            await expect(names).to_have_text(["sub", "big.xyz", "mid.xyz", "small.xyz"])
            await page.locator("#fb-cols [data-sort='size']").click()
            await expect(names).to_have_text(["sub", "small.xyz", "mid.xyz", "big.xyz"])
            await page.locator("#fb-cols [data-sort='mtime']").click()
            await expect(names).to_have_text(["sub", "mid.xyz", "small.xyz", "big.xyz"])

            await page.locator("#fb-cancel").click()
            await _file_menu(page, "Load Dataset…")
            await _go_to(page, tmp_path)
            await expect(names).to_have_text(["sub", "mid.xyz", "small.xyz", "big.xyz"])
            await expect(page.locator("#fb-cols [data-sort='mtime']")).to_have_attribute("aria-sort", "descending")
        finally:
            await browser.close()


async def test_web_plainly_other_files_are_dimmed_but_choosable(ffast_web_server, tmp_path):
    """Loading a dataset, a picture or a log is dimmed (it is not a
    structure), yet can still be chosen; a model's list dims nothing."""
    (tmp_path / "a.xyz").write_text("x")
    (tmp_path / "plot.png").write_bytes(b"x")
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 900})
        png = page.locator("#fb-list .fb-row", has_text="plot.png")
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await _expect_connected(page)
            await _file_menu(page, "Load Dataset…")
            await _go_to(page, tmp_path)
            await expect(png).to_have_class(re.compile(r"\bother\b"))
            await expect(page.locator("#fb-list .fb-row", has_text="a.xyz")).not_to_have_class(re.compile(r"\bother\b"))
            await png.click()
            await expect(png).to_have_class(re.compile(r"\bselected\b"))
            await page.locator("#fb-cancel").click()
            await _file_menu(page, "Load Model…")
            await _go_to(page, tmp_path)
            await expect(png).not_to_have_class(re.compile(r"\bother\b"))
        finally:
            await browser.close()


async def test_web_typing_on_the_file_list_narrows_it(ffast_web_server, tmp_path):
    """Letters typed on the list keep the names containing them; Backspace
    takes one off, Escape clears the filter without closing the dialog, and
    Space on what is left chooses it."""
    for name in ["bead_00.xyz", "bead_01.xyz", "run.h5"]:
        (tmp_path / name).write_text("x")
    ws_port, web_port = ffast_web_server
    await _wait_for_server_ready(ws_port)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 900})
        names = page.locator("#fb-list .fb-row .name")
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await _expect_connected(page)
            await _file_menu(page, "Load Dataset…")
            await _go_to(page, tmp_path)
            await expect(names).to_have_count(3)
            await page.locator("#fb-list .fb-row").first.focus()
            await page.keyboard.type("bead")
            await expect(names).to_have_text(["bead_00.xyz", "bead_01.xyz"])
            await expect(page.locator("#fb-filter")).to_contain_text("2 of 3")
            await page.keyboard.type("_01")
            await expect(names).to_have_text(["bead_01.xyz"])
            await page.keyboard.press("Backspace")
            await expect(names).to_have_count(2)
            await page.keyboard.type("x")
            await expect(page.locator("#fb-list .fb-none")).to_be_visible()
            await page.keyboard.press("Escape")
            await expect(names).to_have_count(3)
            await expect(page.locator("#fb-modal")).to_be_visible()
            await expect(page.locator("#fb-filter")).to_be_hidden()
            await page.keyboard.type("run")
            await page.keyboard.press("Space")
            await expect(page.locator("#fb-list .fb-row.selected .name")).to_have_text("run.h5")
        finally:
            await browser.close()

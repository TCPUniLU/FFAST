"""The web client against the real example datasets in examples/data/.

Those folders are git-ignored (they are large and not part of the repository),
so every test here skips itself when its folder is missing. On a machine that
has them, they run the web features on real data: a fixed-size molecule, a
periodic crystal, a graphene sheet with an .npz prediction, and datasets whose
atoms change between frames.
"""
import re

import ase.io
import pytest
from playwright.async_api import async_playwright, expect

from ffast.protocol.rpc import pack

from .test_web_runtime import (
    REPO_ROOT, _RECORD_SENDS, _connect_headless_client, _expect_connected, _file_menu,
    _open_loupe, _open_section, _spawn_server, _wait_for_event, _wait_for_server_ready,
)

pytestmark = pytest.mark.integration

EXAMPLES = REPO_ROOT / "examples" / "data"


def example(folder, name):
    """A file of the real example data, or a skip when this machine lacks it."""
    path = EXAMPLES / folder / name
    if not path.is_file():
        pytest.skip(f"{path} is not here (git-ignored example data)")
    return path


@pytest.fixture
async def ffast_web_server():
    async with _spawn_server() as ports:
        yield ports


async def _load(ws_port, path, prediction=None, keys=None):
    """Load a dataset (and a prediction file against it) into the server;
    returns the dataset's fingerprint."""
    ws = await _connect_headless_client(ws_port)
    try:
        await ws.send(pack("LOAD_DATASET", (str(path), "ase (auto)"), {}))
        # A new window is first told about datasets already loaded: wait for this one.
        while True:
            meta = await _wait_for_event(ws, "REMOTE_DATASET_META", timeout=60)
            if meta["kwargs"].get("path") == str(path):
                break
        fp = meta["args"][0]
        if prediction:
            await ws.send(pack("LOAD_PREDICTION", (str(prediction), fp), keys or {}))
            await _wait_for_event(ws, "REMOTE_MODEL_META", timeout=60)
        return fp
    finally:
        await ws.send(pack("GRACEFUL_DISCONNECT", (), {}))
        await ws.close()


async def _page(p, web_port, ws_port):
    browser = await p.chromium.launch(headless=True)
    page = await browser.new_page(viewport={"width": 1200, "height": 900})
    return browser, page


async def _sent(page, event):
    return await page.evaluate(
        "(e) => window.__sent.filter(([name]) => name === e).map(([, kw]) => kw)", event)


async def test_three_atom_alignment_and_origin_com_on_a_real_molecule(ffast_web_server):
    """Same 87 atoms in every frame, so alignment is allowed (web parity plan 2026-10-29)."""
    ws_port, web_port = ffast_web_server
    fp = await _load(ws_port, example("fixed-sized-molecular", "md22_stachyose_sampled.xyz"))
    async with async_playwright() as p:
        browser, page = await _page(p, web_port, ws_port)
        try:
            await _open_loupe(page, ws_port, web_port, fp)
            await _open_section(page, "Alignment")
            align = page.locator(
                ".pane[data-pane='Alignment'] .ctl-row[data-label='3-atom frame align'] input")
            await expect(align).to_be_enabled()
            await align.check()
            await _open_section(page, "Camera")
            com = page.locator(".pane[data-pane='Camera'] .ctl-row[data-label='Origin COM'] input")
            await expect(com).not_to_be_checked()
            await com.check()
            await _open_section(page, "Alignment")
            await expect(align).not_to_be_checked()
        finally:
            await browser.close()


async def test_alignment_is_off_where_the_atoms_change_between_frames(ffast_web_server):
    ws_port, web_port = ffast_web_server
    fp = await _load(ws_port, example("variable-sized-periodic", "mptrj_sampled_over_200.extxyz"))
    async with async_playwright() as p:
        browser, page = await _page(p, web_port, ws_port)
        try:
            await _open_loupe(page, ws_port, web_port, fp)
            await _open_section(page, "Alignment")
            pane = page.locator(".pane[data-pane='Alignment']")
            await expect(pane.locator(".ctl-row[data-label='3-atom frame align']")).to_be_hidden()
            await expect(pane.locator(".ctl-hint")).to_contain_text("atoms change between frames")
        finally:
            await browser.close()


async def test_info_names_a_real_molecule_and_a_variable_dataset(ffast_web_server):
    ws_port, web_port = ffast_web_server
    fixed = await _load(ws_port, example("fixed-sized-molecular", "md22_stachyose_sampled.xyz"))
    variable = await _load(ws_port, example("variable-sized-periodic", "mptrj_sampled_over_200.extxyz"))
    async with async_playwright() as p:
        browser, page = await _page(p, web_port, ws_port)
        try:
            await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
            await _expect_connected(page)
            info = page.locator("#info-dialog")

            await page.locator(f"#dataset-list .obj-row[data-fp='{fixed}'] .obj-menu-btn").click()
            await page.locator(".rail-menu [data-action='dataset-info']").click()
            await expect(info).to_contain_text("273 frames")
            await expect(info).to_contain_text("C24H42O21")
            await expect(info.locator("tr", has_text="Atoms per structure")).to_contain_text("87")
            await page.keyboard.press("Escape")

            await page.locator(f"#dataset-list .obj-row[data-fp='{variable}'] .obj-menu-btn").click()
            await page.locator(".rail-menu [data-action='dataset-info']").click()
            await expect(info.locator("tr", has_text="Formula")).to_contain_text("varies")
            await expect(info.locator("tr", has_text="Atoms per structure")).to_contain_text("–")
        finally:
            await browser.close()


async def test_an_npz_prediction_loads_on_a_real_graphene_dataset(ffast_web_server):
    ws_port, web_port = ffast_web_server
    fp = await _load(ws_port, example("fixed-sized-subsystem", "graphene_sampled.xyz"))
    npz = example("fixed-sized-subsystem", "graphene_prediction.npz")
    async with async_playwright() as p:
        browser, page = await _page(p, web_port, ws_port)
        try:
            await _open_loupe(page, ws_port, web_port, fp)
            await page.evaluate(_RECORD_SENDS)
            await _file_menu(page, "Load Prediction…")
            await page.locator("#fb-path").fill(str(npz.parent))
            await page.locator("#fb-path").press("Enter")
            await expect(page.locator("#fb-path")).to_have_value(str(npz.parent))
            await page.locator("#fb-list .fb-row", has_text=npz.name).click()
            await expect(page.locator("#fb-force-key")).to_have_text("from the .npz file")
            await page.locator("#fb-load").click()
            await expect(page.locator("#model-list .obj-row", has_text=npz.name)).to_be_visible(timeout=60000)
            assert await _sent(page, "PROBE_DATASET_KEYS") == []
        finally:
            await browser.close()


async def test_a_prediction_file_loads_on_a_real_periodic_dataset(ffast_web_server):
    ws_port, web_port = ffast_web_server
    fp = await _load(ws_port, example("fixed-sized-periodic", "am26_subbed_100.extxyz"))
    pred = example("fixed-sized-periodic", "predictions_am26_subbed.xyz")
    async with async_playwright() as p:
        browser, page = await _page(p, web_port, ws_port)
        try:
            await _open_loupe(page, ws_port, web_port, fp)
            await _file_menu(page, "Load Prediction…")
            await page.locator("#fb-path").fill(str(pred.parent))
            await page.locator("#fb-path").press("Enter")
            await expect(page.locator("#fb-path")).to_have_value(str(pred.parent))
            await page.locator("#fb-list .fb-row", has_text=pred.name).click()
            await expect(page.locator("#fb-load")).to_be_enabled(timeout=15000)
            await page.locator("#fb-load").click()
            await expect(page.locator("#model-list .obj-row", has_text=pred.name)).to_be_visible(timeout=60000)
        finally:
            await browser.close()


async def test_the_unit_cell_checkbox_on_a_real_crystal(ffast_web_server):
    ws_port, web_port = ffast_web_server
    fp = await _load(ws_port, example("fixed-sized-periodic", "am26_subbed_100.extxyz"))
    async with async_playwright() as p:
        browser, page = await _page(p, web_port, ws_port)
        try:
            await _open_loupe(page, ws_port, web_port, fp)
            await page.evaluate(_RECORD_SENDS)
            await _open_section(page, "Display")
            box = page.locator(".pane[data-pane='Display'] .ctl-row[data-label='Show unit cell'] input")
            await box.uncheck()
            await box.check()
            commands = [kw for kw in await _sent(page, "VIEW_COMMAND")
                        if kw.get("feature") == "no_unit_cell"]
            assert [c["enabled"] for c in commands] == [True, False]
        finally:
            await browser.close()


@pytest.mark.parametrize("fmt", ["traj", "db"])
async def test_a_real_crystal_exports_in_an_ase_format(ffast_web_server, tmp_path, fmt):
    """216 atoms x 100 frames with a cell, written as an ASE trajectory or database."""
    ws_port, web_port = ffast_web_server
    source = example("fixed-sized-periodic", "am26_subbed_100.extxyz")
    fp = await _load(ws_port, source)
    async with async_playwright() as p:
        browser, page = await _page(p, web_port, ws_port)
        try:
            await _open_loupe(page, ws_port, web_port, fp)
            await _file_menu(page, "Export Selected Dataset…")
            await page.locator("#fb-path").fill(str(tmp_path))
            await page.locator("#fb-path").press("Enter")
            await expect(page.locator("#fb-path")).to_have_value(str(tmp_path))
            await page.locator("#fb-format").select_option(fmt)
            name = await page.locator("#fb-name").input_value()
            assert name.endswith(f".{fmt}")
            await page.locator("#fb-load").click()
            # "Exporting to …name.{fmt}…" shows before the file is written
            await expect(page.locator("#status")).to_contain_text(re.compile(rf"Exported .*\.{fmt}"), timeout=60000)
            frames = ase.io.read(str(tmp_path / name), index=":")
            assert len(frames) == 100 and len(frames[0]) == 216
        finally:
            await browser.close()


async def test_exporting_many_structures_to_poscar_is_refused_with_the_reason(ffast_web_server, tmp_path):
    """VASP takes one structure: a dataset of 100 is refused and the status says why."""
    ws_port, web_port = ffast_web_server
    fp = await _load(ws_port, example("fixed-sized-periodic", "am26_subbed_100.extxyz"))
    async with async_playwright() as p:
        browser, page = await _page(p, web_port, ws_port)
        try:
            await _open_loupe(page, ws_port, web_port, fp)
            await _file_menu(page, "Export Selected Dataset…")
            await page.locator("#fb-path").fill(str(tmp_path))
            await page.locator("#fb-path").press("Enter")
            await expect(page.locator("#fb-path")).to_have_value(str(tmp_path))
            await page.locator("#fb-format").select_option("vasp")
            await page.locator("#fb-load").click()
            status = page.locator("#status")
            await expect(status).to_contain_text("Export failed", timeout=60000)
            await expect(status).to_contain_text("only store 1")
            await expect(status).to_have_class(re.compile(r"\berror\b"))
        finally:
            await browser.close()

"""The keep boxes in the 3D Color by (ADR 0065 step 6): a box change recolours
at once, and atom ✓ structure ✗ on a dataset whose structures have the same
atoms gives each atom one colour on every frame."""
import json
import re

import ase
import ase.io
import numpy as np
import pytest
from ase.calculators.singlepoint import SinglePointCalculator
from playwright.async_api import async_playwright, expect

from ffast.protocol.rpc import pack

from .test_metrics_pane import _server
from .test_web_runtime import _connect_headless_client, _expect_connected, _open_section, _wait_for_event

pytestmark = pytest.mark.integration

# Records the per-atom values of every colouring the 3D view is sent.
_RECORD = """() => {
  const pane = window.ffastApp._panes.colorBy;
  const show = pane.setColorBy.bind(pane);
  window.__colours = [];
  pane.setColorBy = (c) => { if (c && c.values) window.__colours.push(Array.from(c.values)); show(c); };
}"""


def _uniform_files(tmp_path):
    """Four frames of the same three atoms (O, H, H), and a prediction."""
    rng = np.random.default_rng(3)
    dataset, prediction = [], []
    for _ in range(4):
        atoms = ase.Atoms("OH2", positions=rng.normal(size=(3, 3)))
        forces = rng.normal(size=(3, 3))
        atoms.calc = SinglePointCalculator(atoms, energy=float(rng.normal()), forces=forces)
        dataset.append(atoms)
        pred = ase.Atoms("OH2", positions=atoms.positions)
        pred.info["MACE_energy"] = float(rng.normal())
        pred.arrays["MACE_forces"] = forces + rng.normal(scale=0.5, size=(3, 3))
        prediction.append(pred)
    paths = tmp_path / "uniform.xyz", tmp_path / "uniform_pred.xyz"
    ase.io.write(paths[0], dataset, format="extxyz")
    ase.io.write(paths[1], prediction, format="extxyz")
    return paths


async def _load(ws_port, dataset_path, prediction_path):
    ws = await _connect_headless_client(ws_port)
    try:
        await ws.send(pack("LOAD_DATASET", (str(dataset_path), "ase (auto)"), {}))
        dataset_fp = (await _wait_for_event(ws, "REMOTE_DATASET_META", timeout=30))["args"][0]
        await ws.send(pack("LOAD_PREDICTION", (str(prediction_path), dataset_fp), {
            "selected_energy_key": "MACE_energy", "selected_force_key": "MACE_forces"}))
        model_fp = (await _wait_for_event(ws, "REMOTE_MODEL_META", timeout=30))["args"][0]
        return dataset_fp, model_fp
    finally:
        await ws.send(pack("GRACEFUL_DISCONNECT", (), {}))
        await ws.close()


async def _colours_after(page, action):
    """The colours the view shows once `action` (a coroutine factory) is done."""
    count = await page.evaluate("() => window.__colours.length")
    await action()
    await page.wait_for_function("(n) => window.__colours.length > n", arg=count)
    await page.wait_for_timeout(300)
    return json.loads(await page.evaluate("() => JSON.stringify(window.__colours.at(-1))"))


async def test_atom_without_structure_keeps_each_atoms_colour_on_every_frame(tmp_path):
    dataset_path, prediction_path = _uniform_files(tmp_path)
    cm, _metrics_dir = await _server(tmp_path)
    async with cm as (ws_port, web_port):
        dataset_fp, model_fp = await _load(ws_port, dataset_path, prediction_path)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1300, "height": 800})
            try:
                await page.goto(f"http://127.0.0.1:{web_port}/?port={ws_port}", wait_until="networkidle")
                await _expect_connected(page)
                await page.locator(f"#dataset-list .obj-row[data-fp='{dataset_fp}']").click()
                await page.locator(f"#model-list .obj-row[data-fp='{model_fp}']").click()
                await expect(page.locator("#overlay")).to_have_class(re.compile(r"\bhidden\b"))
                await _open_section(page, "Colour By")
                await page.evaluate(_RECORD)
                pane = page.locator(".pane[data-pane='Colour By']")
                coloring = pane.locator(".ctl-row[data-label='Coloring'] select")

                # the base colours through its first per-atom setting
                first = await _colours_after(page, lambda: coloring.select_option(label="Force Difference"))
                boxes = pane.locator(".colorby-boxes")
                await expect(boxes).to_be_visible()
                await expect(boxes.get_by_label("Keep atom")).to_be_checked()
                await expect(boxes.get_by_label("Keep structure")).to_be_checked()
                await expect(boxes.get_by_label("Keep x/y/z")).to_be_disabled()
                assert len(first) == 3
                second = await _colours_after(page, lambda: page.locator("#next-frame-btn").click())
                assert second != first                       # each frame its own errors

                # atom ✓ structure ✗: each atom's error averaged over the frames
                averaged = await _colours_after(page, lambda: boxes.get_by_label("Keep structure").uncheck())
                again = await _colours_after(page, lambda: page.locator("#next-frame-btn").click())
                assert again == pytest.approx(averaged)
                assert await coloring.input_value() == "Force Difference"
                source = await page.evaluate(
                    "() => window.ffastApp._focused().settings.colorBy.source")
                assert source == "metric:ffast.force_difference[atom;component;mae]"

                # the setting can be kept as a metric
                await pane.get_by_role("button", name="Save as metric").click()
                await expect(pane.locator(".mx-boxes-note")).to_contain_text("Saved")
            finally:
                await browser.close()

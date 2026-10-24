"""The keep boxes in the Metrics pane against a real server (ADR 0065 step 5):
open Force Difference, keep structure, pick RMSE, see Try at once, Create
the metric, plot it."""
import re

import pytest
from playwright.async_api import async_playwright, expect

from ffast.protocol.rpc import pack

from .test_metrics_pane import _list_shows, _open_metrics_pane, _pick, _server
from .test_web_runtime import (
    _connect_headless_client, _open_app, _preload_dataset_and_prediction, _wait_for_event,
)

pytestmark = pytest.mark.integration

BOX = "ffast.force_difference[structure;length;rmse]"


async def test_the_boxes_make_a_metric_that_tries_creates_and_plots(tmp_path):
    cm, metrics_dir = await _server(tmp_path)
    async with cm as (ws_port, web_port):
        await _preload_dataset_and_prediction(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1500, "height": 900})
            try:
                await _open_app(page, web_port, ws_port)
                pane = await _open_metrics_pane(page)
                await _pick(pane, "ffast.force_difference")
                boxes = pane.locator(".mx-boxes")
                await expect(boxes).to_be_visible()
                for box, ticked in (("atom", True), ("structure", True), ("element", False), ("x/y/z", True)):
                    check = boxes.get_by_label(f"Keep {box}")
                    await (expect(check).to_be_checked() if ticked else expect(check).not_to_be_checked())
                # element is greyed while atom is ticked, and says why
                await expect(boxes.get_by_label("Keep element")).to_be_disabled()
                await expect(boxes.locator(".kb-choice", has_text="element")).to_have_attribute(
                    "title", "element is greyed when atom is ticked")
                await expect(pane.locator(".mx-boxes-create")).to_be_hidden()

                await boxes.get_by_label("Keep atom").uncheck()
                await boxes.get_by_label("Keep x/y/z").uncheck()
                await boxes.get_by_label("Length").check()
                await boxes.get_by_label("Statistic").select_option("rmse")
                await expect(boxes.locator(".mx-source-code")).to_contain_text(
                    "rms(norm(D), by=structure)")
                await expect(pane.locator(".mx-boxes-note")).to_contain_text("per structure")
                # Try ran at once, with nothing saved
                await expect(pane.locator(".mx-try-head")).to_have_class(re.compile(r"\bgood\b"))
                assert not list(metrics_dir.glob("*.toml"))

                name = pane.get_by_label("New metric name")
                await expect(name).to_have_value("Force Difference · RMSE · per frame · length")
                await name.fill("My force RMSE")
                await pane.get_by_role("button", name="Create metric").click()
                await expect(pane.locator(".mx-message")).to_contain_text("Created")
                await _list_shows(pane, f".mx-group[data-origin='user'] .mx-item[data-id='{BOX}']")
                await expect(pane.locator(".mx-heading")).to_have_text("My force RMSE")
                assert (metrics_dir / f"{BOX}.toml").exists()

                await pane.get_by_role("button", name="Plot it").click()
                await expect(page.locator("#tabbar .tab.active")).to_have_text(re.compile("My metrics"))
                await page.wait_for_function(
                    "(id) => window.ffastApp._layoutTabs.find((t) => t.name === 'My metrics')"
                    ".panels.some((p) => p.metrics.y === id)", arg=BOX)
            finally:
                await browser.close()


async def test_the_same_setting_again_offers_the_metric_that_exists(tmp_path):
    cm, _metrics_dir = await _server(tmp_path)
    async with cm as (ws_port, _web_port):
        await _preload_dataset_and_prediction(ws_port)
        ws = await _connect_headless_client(ws_port)
        try:
            # Basic Errors already uses Force RMSE over the dataset
            setting = {"keep": [], "combine": "component", "statistic": "rmse"}
            await ws.send(pack("REQUEST_METRIC_BOXES", (), {
                "request_id": "1", "base": "ffast.force_difference", "setting": setting}))
            reply = (await _wait_for_event(ws, "METRIC_BOXES"))["kwargs"]
            assert reply["id"] == "ffast.force_difference[dataset;component;rmse]"
            assert reply["exists"] is True
            await ws.send(pack("REQUEST_METRIC_BOXES", (), {"request_id": "2", "metric_id": "ffast.force_rmse"}))
            reply = (await _wait_for_event(ws, "METRIC_BOXES"))["kwargs"]
            assert reply["error"].startswith("ffast.force_rmse no longer exists")
        finally:
            await ws.close()

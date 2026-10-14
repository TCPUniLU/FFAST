"""The Metrics pane against a real server (ADR 0057 steps 2-5).

One test drives the wire the way the pane does, headless: save, edit, ask for
the numbers, see them change, see another connection told. One drives the pane
in a browser: check as you type, Fix, Try, Save, and a second window seeing it.
"""
import re
import tomllib

import pytest
from playwright.async_api import async_playwright, expect

from ffast.protocol.rpc import pack

from .test_web_runtime import (
    _connect_headless_client,
    _open_app,
    _preload_dataset_and_prediction,
    _spawn_server,
    _wait_for_event,
)

pytestmark = pytest.mark.integration

METRIC = {"id": "user.double_e", "label": "Double energy", "unit": "energy",
          "expr": "2 * e", "vars": {"e": "reference.energies"}}


async def _server(tmp_path):
    metrics_dir = tmp_path / "metrics"
    config = tmp_path / "ffast.toml"
    config.write_text("")
    return _spawn_server("--metrics-dir", str(metrics_dir), "--tabs-dir", str(tmp_path / "tabs"),
                         "--config", str(config)), metrics_dir


async def _request_values(ws, dataset_fp, metric_id):
    await ws.send(pack("REQUEST_METRIC", (), {"metric_id": metric_id, "dataset_fp": dataset_fp}))
    reply = await _wait_for_event(ws, "METRIC_RESULT", timeout=30)
    assert reply["kwargs"]["ok"], reply["kwargs"]
    return reply["kwargs"]["values"]


async def test_a_saved_metric_is_computed_then_recomputed_when_edited(tmp_path):
    cm, metrics_dir = await _server(tmp_path)
    async with cm as (ws_port, _web_port):
        dataset_fp, _model_fp = await _preload_dataset_and_prediction(ws_port)
        ws = await _connect_headless_client(ws_port)
        other = await _connect_headless_client(ws_port)
        try:
            await ws.send(pack("SAVE_USER_METRIC", (), {"metric": METRIC}))
            saved = await _wait_for_event(ws, "USER_METRIC_SAVED")
            assert saved["kwargs"]["ok"], saved["kwargs"]
            # another window is told, and its catalog shows the metric as a user one
            await _wait_for_event(other, "METRICS_UPDATED")
            listed = (await _wait_for_event(other, "USER_METRICS"))["kwargs"]["metrics"]
            assert [(m["id"], m["status"], m["shape"]) for m in listed] == [("user.double_e", "ok", "N_frames")]
            await other.send(pack("REQUEST_METRIC_CATALOG", (), {}))
            catalog = (await _wait_for_event(other, "METRIC_CATALOG"))["kwargs"]["metrics"]
            mine = next(m for m in catalog if m["id"] == "user.double_e")
            assert (mine["origin"], mine["expr"]) == ("user", "2 * e")
            # the file is the project's [[metrics.expr]] format
            on_disk = tomllib.loads((metrics_dir / "user.double_e.toml").read_text())
            assert on_disk["metrics"]["expr"][0]["vars"] == {"e": "reference.energies"}

            first = await _request_values(ws, dataset_fp, "user.double_e")
            assert first == await _request_values(ws, dataset_fp, "user.double_e")   # cached

            edit = {**METRIC, "expr": "3 * e"}
            revision = listed[0]["revision"]
            await ws.send(pack("SAVE_USER_METRIC", (), {
                "metric": edit, "previous_id": "user.double_e", "base_revision": revision}))
            assert (await _wait_for_event(ws, "USER_METRIC_SAVED"))["kwargs"]["ok"]
            second = await _request_values(ws, dataset_fp, "user.double_e")
            assert second != first          # the outer cache was cleared for this metric

            # a save from the old version is a conflict, not a silent overwrite
            await ws.send(pack("SAVE_USER_METRIC", (), {
                "metric": {**METRIC, "expr": "4 * e"}, "previous_id": "user.double_e",
                "base_revision": revision}))
            conflict = (await _wait_for_event(ws, "USER_METRIC_SAVED"))["kwargs"]
            assert conflict["ok"] is False and conflict["conflict"] is True
        finally:
            await ws.close()
            await other.close()


async def test_try_reports_bad_values_before_anything_is_saved(tmp_path):
    cm, metrics_dir = await _server(tmp_path)
    async with cm as (ws_port, _web_port):
        dataset_fp, _ = await _preload_dataset_and_prediction(ws_port)
        ws = await _connect_headless_client(ws_port)
        try:
            bad = {"id": "user.bad", "expr": "1 / (e - e)", "vars": {"e": "reference.energies"}}
            await ws.send(pack("TRY_METRIC", (), {"metric": bad, "try_id": "t1", "dataset_fp": dataset_fp}))
            tried = (await _wait_for_event(ws, "METRIC_TRIED"))["kwargs"]
            assert tried["ok"] and tried["bad_count"] == tried["count"] > 0
            assert tried["bad_frames"][:2] == [0, 1]
            assert not (metrics_dir / "user.bad.toml").exists()     # nothing saved
            await ws.send(pack("CHECK_METRIC", (), {"metric": bad, "check_id": "9"}))
            checked = (await _wait_for_event(ws, "METRIC_CHECKED"))["kwargs"]
            assert checked == {"check_id": "9", "ok": True, "error": None, "shape": "N_frames"}
        finally:
            await ws.close()


async def _open_metrics_pane(page):
    pane = page.locator("[data-pane='Metrics']")
    if "collapsed" in (await pane.get_attribute("class") or ""):
        await pane.locator(".pane-header").click()
    return pane


async def test_the_pane_checks_fixes_tries_and_saves_a_formula(tmp_path):
    cm, metrics_dir = await _server(tmp_path)
    async with cm as (ws_port, web_port):
        await _preload_dataset_and_prediction(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1500, "height": 900})
            second = await browser.new_page(viewport={"width": 1500, "height": 900})
            try:
                await _open_app(page, web_port, ws_port)
                pane = await _open_metrics_pane(page)
                await expect(pane.locator(".mx-group[data-origin='built-in'] .mx-item").first).to_be_visible()
                await expect(pane.locator(".mx-group[data-origin='user']")).to_contain_text("None yet")

                await pane.get_by_role("button", name="New metric").click()
                formula = pane.get_by_label("Formula")
                await pane.get_by_label("Metric id").fill("user.e_per_atom")
                # a name nothing defines: said in red, with a one-click Fix
                await formula.fill("abs(ref_E) / n_atoms")
                await expect(pane.locator(".mx-check")).to_have_text("ref_E is not an input")
                await expect(pane.locator(".mx-primary", has_text="Save")).to_be_disabled()
                await pane.get_by_role("button", name=re.compile("^Fix")).click()
                await expect(pane.locator(".mx-check")).to_contain_text("per structure")
                await expect(pane.locator(".mx-var").first).to_contain_text("reference.energies")

                # Try: all frames, no save
                await pane.get_by_role("button", name="Try", exact=True).click()
                await expect(pane.locator(".mx-try-head")).to_contain_text("all good")
                assert not metrics_dir.exists() or not list(metrics_dir.glob("*.toml"))

                await pane.get_by_role("button", name="Save", exact=True).click()
                item = pane.locator(".mx-group[data-origin='user'] .mx-item[data-id='user.e_per_atom']")
                await expect(item).to_be_visible()
                assert (metrics_dir / "user.e_per_atom.toml").exists()

                # a second window sees it without reloading
                await _open_app(second, web_port, ws_port)
                pane2 = await _open_metrics_pane(second)
                await expect(pane2.locator(".mx-item[data-id='user.e_per_atom']")).to_be_visible()

                # the second window edits; the first, with unsaved edits, is told
                await item.click()
                await formula.fill("abs(ref_E) / n_atoms + 1")
                await pane2.locator(".mx-item[data-id='user.e_per_atom']").click()
                await pane2.get_by_label("Formula").fill("abs(ref_E) / n_atoms * 2")
                await pane2.get_by_role("button", name="Save", exact=True).click()
                await expect(pane.locator(".mx-conflict")).to_contain_text("Changed in another window")
                await pane.get_by_role("button", name="Reload").click()
                await expect(formula).to_have_value("abs(ref_E) / n_atoms * 2")

                # delete (two clicks) removes it everywhere
                await pane.get_by_role("button", name="Delete").click()
                await pane.get_by_role("button", name="Click again to delete").click()
                await expect(pane.locator(".mx-item[data-id='user.e_per_atom']")).to_have_count(0)
                await expect(pane2.locator(".mx-item[data-id='user.e_per_atom']")).to_have_count(0)
                assert not (metrics_dir / "user.e_per_atom.toml").exists()
            finally:
                await browser.close()

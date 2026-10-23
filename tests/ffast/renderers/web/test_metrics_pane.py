"""The Metrics pane against a real server (ADR 0057 steps 2-5).

One test drives the wire the way the pane does, headless: save, edit, ask for
the numbers, see them change, see another connection told. One drives the pane
in a browser: check as you type, Fix, Try, Save, and a second window seeing it.
"""
import re
import tomllib

import ase.io
import pytest
from playwright.async_api import async_playwright, expect

from ffast.protocol.rpc import pack

from .test_web_runtime import (
    _COMPARE_TOML,
    _INDEPENDENT,
    _RECORD_SENDS,
    DATASET_PATH,
    _connect_headless_client,
    _focus_3d,
    _load_more,
    _open_analysis_tab,
    _open_app,
    _open_compare,
    _panel_control,
    _preload_dataset_and_prediction,
    _spawn_server,
    _wait_for_event,
)

pytestmark = pytest.mark.integration

METRIC = {"id": "user.double_e", "label": "Double energy", "unit": "energy",
          "expr": "2 * e", "vars": {"e": "reference.energies"}}


async def _server(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    metrics_dir = tmp_path / "metrics"
    config = tmp_path / "ffast.toml"
    config.write_text("")
    return _spawn_server("--metrics-dir", str(metrics_dir), "--tabs-dir", str(tmp_path / "tabs"),
                         "--config", str(config)), metrics_dir


async def _request_values(ws, dataset_fp, metric_id):
    await ws.send(pack("REQUEST_METRIC", (), {"metric_id": metric_id, "request_id": "t1", "dataset_fp": dataset_fp}))
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
            assert checked == {"check_id": "9", "ok": True, "error": None, "shape": "N_frames",
                           "colour_problem": None}
        finally:
            await ws.close()


async def _open_metrics_pane(page):
    """Open the Metrics drawer from the top bar (it is on every tab)."""
    pane = page.locator("#metrics-drawer")
    if await pane.is_hidden():
        await page.locator("#metrics-btn").click()
    await expect(pane).to_be_visible()
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


async def _save_in_pane(pane, metric_id, before, ref_button, after):
    """New metric: id, text typed, an input clicked into the formula, more text; Save."""
    await pane.get_by_role("button", name="New metric").click()
    await pane.get_by_label("Metric id").fill(metric_id)
    formula = pane.get_by_label("Formula")
    await formula.fill(before)
    await pane.locator(f".mx-input[data-ref='{ref_button}']").click()
    await formula.press_sequentially(after)
    await expect(pane.locator(".mx-check.good")).to_be_visible()
    await pane.get_by_role("button", name="Save", exact=True).click()
    await expect(pane.locator(f".mx-item[data-id='{metric_id}']")).to_be_visible()


async def test_the_shortcuts_colour_the_atoms_and_plot_the_metric(tmp_path):
    cm, _ = await _server(tmp_path)
    async with cm as (ws_port, web_port):
        await _preload_dataset_and_prediction(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1500, "height": 900})
            try:
                await _open_app(page, web_port, ws_port)
                pane = await _open_metrics_pane(page)

                # per atom: Colour atoms by this -> the Colour By list and the colour bar follow
                await _save_in_pane(pane, "user.force_len", "norm(", "reference.forces", ")")
                await expect(pane.get_by_role("button", name="Plot it")).to_have_count(0)
                await expect(pane.locator(".mx-check")).to_contain_text("per atom")
                await pane.get_by_role("button", name="Colour atoms by this").click()
                coloring = page.locator(".pane[data-pane='Colour By'] .ctl-row[data-label='Coloring'] select")
                await expect(coloring).to_have_value("user.force_len")
                bar_max = page.locator("#colorbar .cb-max")
                await expect(page.locator("#colorbar")).not_to_have_class(re.compile(r"\bhidden\b"))
                before = float(await bar_max.inner_text())

                # editing the metric recolours the atoms: the colour bar's top doubles
                await pane.get_by_label("Formula").fill("2 * " + (await pane.get_by_label("Formula").input_value()))
                await pane.get_by_role("button", name="Save", exact=True).click()
                await expect(pane.locator(".mx-message")).to_contain_text("Saved")
                await page.wait_for_function(
                    "(before) => parseFloat(document.querySelector('#colorbar .cb-max').textContent) > before * 1.5",
                    arg=before)
                assert float(await bar_max.inner_text()) == pytest.approx(2 * before, rel=0.02)

                # per structure: Plot it -> a "My metrics" tab (the 3D tab has no plot)
                await _save_in_pane(pane, "user.double_e", "", "reference.energies", " * 2")
                await expect(pane.locator(".mx-check")).to_contain_text("per structure")
                await pane.get_by_role("button", name="Plot it").click()
                await expect(page.locator("#tabbar .tab", has_text="My metrics")).to_have_count(1)
                await expect(page.locator("#tabbar .tab.active")).to_have_text(re.compile("My metrics"))
                spec = await page.evaluate(
                    "() => window.ffastApp._layoutTabs.find((t) => t.name === 'My metrics').panels"
                    ".map((p) => [p.kind, p.metrics.y ?? null])")
                assert spec == [["3d", None], ["timeline", "user.double_e"]]
                # ... and the next one joins it, since it has a plot now
                pane = await _open_metrics_pane(page)
                await pane.locator(".mx-item[data-id='user.double_e']").click()
                await pane.get_by_role("button", name="Plot it").click()
                await page.wait_for_function(
                    "() => window.ffastApp._layoutTabs.find((t) => t.name === 'My metrics').panels.length === 3")
            finally:
                await browser.close()


async def test_a_session_carries_its_formulas_to_another_server(tmp_path):
    """Rules 15 and 16 end to end: server A saves a session whose tab plots a
    user metric; server B, which lacks it, is offered it on load and adds it;
    server C, which has the id under another formula, keeps both as <id>.2."""
    session = tmp_path / "session"
    tab = {"name": "Mine", "panels": [{"kind": "timeline", "row": 0, "col": 0, "title": "Doubled",
                                       "metrics": {"y": {"metric": "user.double_e"}}}]}

    cm, _ = await _server(tmp_path / "a")
    async with cm as (ws_port, _web_port):
        await _preload_dataset_and_prediction(ws_port)
        ws = await _connect_headless_client(ws_port)
        try:
            await ws.send(pack("SAVE_USER_METRIC", (), {"metric": METRIC}))
            assert (await _wait_for_event(ws, "USER_METRIC_SAVED"))["kwargs"]["ok"]
            await ws.send(pack("SAVE_TAB", (), {"tab": tab}))
            assert (await _wait_for_event(ws, "TAB_SAVED"))["kwargs"]["ok"]
            await ws.send(pack("SAVE_SESSION", (), {"path": str(session)}))
            assert (await _wait_for_event(ws, "SESSION_SAVED"))["kwargs"]["ok"]
        finally:
            await ws.close()
    carried = tomllib.loads((session / "user_metrics.toml").read_text())["metrics"]["expr"]
    assert [(m["id"], m["expr"]) for m in carried] == [("user.double_e", "2 * e")]

    cm, metrics_b = await _server(tmp_path / "b")
    async with cm as (ws_port, _web_port):
        ws = await _connect_headless_client(ws_port)
        try:
            await ws.send(pack("LOAD_SESSION", (), {"path": str(session)}))
            assert (await _wait_for_event(ws, "SESSION_LOADED", timeout=60))["kwargs"]["ok"]
            offer = (await _wait_for_event(ws, "SESSION_METRICS"))["kwargs"]
            assert [(m["id"], m["clash_with"]) for m in offer["metrics"]] == [("user.double_e", None)]
            await ws.send(pack("ADD_SESSION_METRICS", (), {"offer_id": offer["offer_id"]}))
            assert (await _wait_for_event(ws, "USER_METRIC_SAVED"))["kwargs"]["ok"]
            assert (metrics_b / "user.double_e.toml").exists()
        finally:
            await ws.close()

    cm, metrics_c = await _server(tmp_path / "c")
    metrics_c.mkdir(parents=True)
    (metrics_c / "user.double_e.toml").write_text(
        '[[metrics.expr]]\nid = "user.double_e"\nexpr = "7 * e"\n\n  [metrics.expr.vars]\n  e = "reference.energies"\n')
    async with cm as (ws_port, _web_port):
        ws = await _connect_headless_client(ws_port)
        try:
            await ws.send(pack("LOAD_SESSION", (), {"path": str(session)}))
            assert (await _wait_for_event(ws, "SESSION_LOADED", timeout=60))["kwargs"]["ok"]
            offer = (await _wait_for_event(ws, "SESSION_METRICS"))["kwargs"]
            assert [(m["id"], m["clash_with"]) for m in offer["metrics"]] == [
                ("user.double_e.2", "user.double_e")]
            await ws.send(pack("ADD_SESSION_METRICS", (), {"offer_id": offer["offer_id"]}))
            assert (await _wait_for_event(ws, "USER_METRIC_SAVED"))["kwargs"]["ok"]
            kept = tomllib.loads((metrics_c / "user.double_e.toml").read_text())["metrics"]["expr"][0]
            assert kept["expr"] == "7 * e"                                    # the server's, untouched
            assert (metrics_c / "user.double_e.2.toml").exists()
        finally:
            await ws.close()


async def test_a_panel_says_which_metric_is_missing(tmp_path):
    """ADR 0057 rule 15: a tab file naming a metric this server lacks still shows,
    and its panel says what is missing instead of staying empty."""
    tabs_dir = tmp_path / "tabs"
    tabs_dir.mkdir(parents=True)
    (tabs_dir / "gone.toml").write_text(
        '[[tabs]]\nname = "Gone"\n[[tabs.panels]]\nkind = "table"\nrow = 0\ncol = 0\n'
        'title = "G"\n[tabs.panels.metrics.value]\nmetric = "mine.gone"\n')
    cm, _ = await _server(tmp_path)
    async with cm as (ws_port, web_port):
        await _preload_dataset_and_prediction(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1500, "height": 900})
            try:
                await _open_app(page, web_port, ws_port)
                await _open_analysis_tab(page, "Gone")
                await expect(page.locator(".tabpanel.active .grid-slot .panel-msg")).to_have_text(
                    "Metric mine.gone is not on this server.")
            finally:
                await browser.close()


async def test_the_pane_acts_on_the_focused_view_not_the_rails_dataset(tmp_path):
    """ADR 0060 step 5: the rail is on A, an independent panel showing B is
    focused. Try runs on B and says so; Colour atoms colours the panel's view;
    a bad-frame link moves the panel (whose frame is its own), not the main view."""
    config = tmp_path / "ffast.toml"
    config.write_text(_COMPARE_TOML)
    second = tmp_path / "second.xyz"
    ase.io.write(second, ase.io.read(DATASET_PATH, index=":30"), format="extxyz")
    metrics_dir = tmp_path / "metrics"
    cm = _spawn_server("--metrics-dir", str(metrics_dir), "--tabs-dir", str(tmp_path / "tabs"),
                       "--config", str(config))
    async with cm as (ws_port, web_port):
        a_fp, _model_fp = await _preload_dataset_and_prediction(ws_port)
        (other,) = await _load_more(
            ws_port, {a_fp}, ("LOAD_DATASET", (str(second), "ase (auto)"), {}))
        b_fp, b_name = other["args"][0], other["kwargs"]["name"]
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1500, "height": 900})
            try:
                await _open_compare(page, ws_port, web_port, a_fp)
                await _focus_3d(page, 1)
                await page.locator(_panel_control("Dataset")).select_option(b_fp)
                await page.wait_for_function(f"(fp) => {_INDEPENDENT}.datasetFp === fp", arg=b_fp)
                assert await page.evaluate("() => window.ffastApp._currentDatasetFp") == a_fp
                await page.evaluate(_RECORD_SENDS)

                pane = await _open_metrics_pane(page)
                await _save_in_pane(pane, "user.force_len", "norm(", "reference.forces", ")")
                await pane.get_by_role("button", name="Try", exact=True).click()
                await expect(pane.locator(".mx-try-head")).to_contain_text(f"— on {b_name}")
                sent = await page.evaluate("() => window.__sent")
                tries = [kw for event, kw in sent if event == "TRY_METRIC"]
                assert [kw["dataset_fp"] for kw in tries] == [b_fp]

                await pane.get_by_role("button", name="Colour atoms by this").click()
                await page.wait_for_function(
                    """() => window.__sent.some(([event, kw]) => event === 'VIEW_COMMAND'
                       && kw.type === 'SET_PARAMETER' && kw.parameter === 'source'
                       && kw.value === 'metric:user.force_len')""")
                sent = await page.evaluate("() => window.__sent")
                colours = [kw["view_id"] for event, kw in sent
                           if event == "VIEW_COMMAND" and kw.get("parameter") == "source"
                           and kw.get("value") == "metric:user.force_len"]
                assert colours and all(view_id.startswith("ind-") for view_id in colours)

                # a bad-frame link moves the panel, whose frame is its own
                await page.locator(_panel_control("Follow main view's frame", "input")).uncheck()
                await pane.get_by_role("button", name="New metric").click()
                await pane.get_by_label("Metric id").fill("user.bad")
                await pane.get_by_label("Formula").fill("1 / (ref_E - ref_E)")
                await pane.get_by_role("button", name=re.compile("^Fix")).click()
                await pane.get_by_role("button", name="Try", exact=True).click()
                link = pane.locator(".mx-try-bad .mx-link", has_text=re.compile("^1$"))
                await expect(link).to_have_count(1)
                await link.click()
                await page.wait_for_function(f"() => {_INDEPENDENT}.frame === 1")
                assert await page.evaluate("() => window.ffastApp._main.current.frame") == 0
            finally:
                await browser.close()


async def test_editing_a_metric_recolours_every_view_coloured_by_it(tmp_path):
    """ADR 0060 step 6: the main view and an independent panel are both
    coloured by one user metric; saving a new formula sends both the source
    again, not only the focused one."""
    config = tmp_path / "ffast.toml"
    config.write_text(_COMPARE_TOML)
    cm = _spawn_server("--metrics-dir", str(tmp_path / "metrics"), "--tabs-dir", str(tmp_path / "tabs"),
                       "--config", str(config))
    async with cm as (ws_port, web_port):
        dataset_fp, _model_fp = await _preload_dataset_and_prediction(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            # wide enough that Compare keeps two columns beside the Metrics drawer
            page = await browser.new_page(viewport={"width": 1900, "height": 900})
            try:
                await _open_compare(page, ws_port, web_port, dataset_fp)
                pane = await _open_metrics_pane(page)
                await _save_in_pane(pane, "user.force_len", "norm(", "reference.forces", ")")
                for index in (0, 1):
                    await _focus_3d(page, index)
                    await pane.get_by_role("button", name="Colour atoms by this").click()
                await page.evaluate(_RECORD_SENDS)

                await pane.get_by_label("Formula").fill("2 * " + (await pane.get_by_label("Formula").input_value()))
                await pane.get_by_role("button", name="Save", exact=True).click()
                await page.wait_for_function(
                    """() => new Set(window.__sent
                         .filter(([event, kw]) => event === 'VIEW_COMMAND' && kw.parameter === 'source'
                                 && kw.value === 'metric:user.force_len')
                         .map(([, kw]) => kw.view_id.split('-')[0])).size === 2""")
            finally:
                await browser.close()


async def test_the_metrics_drawer_works_on_a_tab_with_no_3d_panel(tmp_path):
    """The metric editor is not a 3D setting: the top bar opens it on a tab of
    plots only. Its own picker starts from the selected dataset and
    prediction; choosing "No prediction" there drops the prediction inputs
    without touching the left list. Open or closed survives a reload."""
    cm, _metrics_dir = await _server(tmp_path)
    async with cm as (ws_port, web_port):
        dataset_fp, model_fp = await _preload_dataset_and_prediction(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1500, "height": 900})
            try:
                await _open_app(page, web_port, ws_port)
                await page.locator(f"#model-list .obj-row[data-fp='{model_fp}']").click()
                await _open_analysis_tab(page, "Basic Errors")
                await expect(page.locator(".tabpanel.active .panel-3d")).to_have_count(0)
                assert await page.locator("#loupe-sidebar [data-pane='Metrics']").count() == 0

                pane = await _open_metrics_pane(page)
                await expect(page.locator("#metrics-btn")).to_have_attribute("aria-pressed", "true")
                await expect(page.locator("#metrics-dataset")).to_have_value(dataset_fp)
                await expect(page.locator("#metrics-model")).to_have_value(model_fp)
                await pane.get_by_role("button", name="New metric").click()
                prediction_input = pane.locator(".mx-input[data-ref='prediction.forces']")
                await expect(prediction_input).to_have_count(1)

                await page.locator("#metrics-model").select_option("")
                await expect(prediction_input).to_have_count(0)
                await expect(pane.locator(".mx-input[data-ref='reference.forces']")).to_have_count(1)
                await expect(page.locator(f"#model-list .obj-row[data-fp='{model_fp}']")).to_have_class(
                    re.compile(r"\bselected\b"))   # the left list keeps its prediction

                await page.reload(wait_until="networkidle")
                await expect(page.locator("#metrics-drawer")).to_be_visible()
                await page.locator("#metrics-close").click()
                await expect(page.locator("#metrics-drawer")).to_be_hidden()
            finally:
                await browser.close()


async def test_every_python_metric_shows_its_code_and_duplicates_as_a_formula(tmp_path):
    """ADR 0057 amendment: a built-in shows its Python code; Duplicate gives
    the exact formula where there is one (force_mae, and force_mae_per_structure
    with by=, ADR 0064), and otherwise a formula over the metric itself
    (force_mae_per_structure_smoothed), which saves."""
    cm, metrics_dir = await _server(tmp_path)
    async with cm as (ws_port, web_port):
        await _preload_dataset_and_prediction(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1500, "height": 900})
            try:
                await _open_app(page, web_port, ws_port)
                pane = await _open_metrics_pane(page)
                formula = pane.get_by_label("Formula")

                await pane.locator(".mx-item[data-id='ffast.force_mae']").click()
                await pane.locator(".mx-source summary").click()
                await expect(pane.locator(".mx-source-code")).to_contain_text("def force_mae(")
                await pane.get_by_role("button", name="Duplicate").click()
                await expect(formula).to_have_value("norm(pred_F - ref_F)")
                await expect(pane.locator(".mx-check")).to_contain_text("per atom")

                await pane.locator(".mx-item[data-id='ffast.force_mae_per_structure']").click()
                await pane.get_by_role("button", name="Duplicate").click()
                await expect(formula).to_have_value("mean(norm(pred_F - ref_F), by=structure)")
                await expect(pane.locator(".mx-check")).to_contain_text("per structure")

                await pane.locator(".mx-item[data-id='ffast.force_mae_per_structure_smoothed']").click()
                await expect(pane.locator(".mx-notice")).to_contain_text("takes this metric as its input")
                await pane.get_by_role("button", name="Duplicate").click()
                await expect(formula).to_have_value("force_mae_per_structure_smoothed")
                await expect(pane.locator(".mx-check")).to_contain_text("per structure")
                await formula.fill("1000 * force_mae_per_structure_smoothed")
                await pane.get_by_role("button", name="Save", exact=True).click()
                await expect(pane.locator(".mx-group[data-origin='user']")).to_contain_text(
                    "user.force_mae_per_structure_smoothed_copy")
                assert (metrics_dir / "user.force_mae_per_structure_smoothed_copy.toml").exists()
            finally:
                await browser.close()


async def test_h_only_and_per_element_formulas_are_tried_saved_and_plotted(tmp_path):
    """ADR 0064 step 6: by=structure with where=element("H"). The example
    data has one structure without hydrogen (C6Cl3F3): it has no value. A
    per-element formula plots as bars (the amendment)."""
    import ase.io as aio
    no_h = [i for i, a in enumerate(aio.read(DATASET_PATH, index=":")) if "H" not in a.symbols]
    assert len(no_h) == 1
    cm, metrics_dir = await _server(tmp_path)
    async with cm as (ws_port, web_port):
        await _preload_dataset_and_prediction(ws_port)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1500, "height": 900})
            try:
                await _open_app(page, web_port, ws_port)
                pane = await _open_metrics_pane(page)
                await pane.get_by_role("button", name="New metric").click()
                await pane.get_by_label("Metric id").fill("user.h_force")
                formula = pane.get_by_label("Formula")

                # a dataset-wide word: the hint says colouring refuses it
                await formula.fill("norm(pred_F - ref_F) / max(norm(pred_F - ref_F), by=all)")
                for _ in range(2):
                    await pane.get_by_role("button", name=re.compile("^Fix")).click()
                await expect(pane.locator(".mx-check")).to_have_text(
                    "per atom → by=all can't be used for colouring yet: colouring sees one frame")

                await formula.fill('mean(norm(pred_F - ref_F), by=structure, where=element("H"))')
                await expect(pane.locator(".mx-check")).to_contain_text("per structure")

                await pane.get_by_role("button", name="Try", exact=True).click()
                await expect(pane.locator(".mx-try-head")).to_contain_text("all good")
                await expect(pane.locator(".mx-try-gaps")).to_have_text("1 structure has no value")

                await pane.get_by_role("button", name="Save", exact=True).click()
                await expect(pane.locator(".mx-item[data-id='user.h_force']")).to_be_visible()
                text = (metrics_dir / "user.h_force.toml").read_text()
                assert tomllib.loads(text)["metrics"]["expr"][0]["vars"] == {
                    "pred_F": "prediction.forces", "ref_F": "reference.forces"}

                await pane.get_by_role("button", name="Plot it").click()
                await expect(page.locator("#tabbar .tab.active")).to_have_text(re.compile("My metrics"))
                gaps = await page.wait_for_function(
                    "() => { const el = [...document.querySelectorAll('.tabpanel.active .js-plotly-plot')]"
                    "  .find((d) => d.data && d.data.length && d.data[0].y && d.data[0].y.length === 100);"
                    "  if (!el) return null;"
                    "  return Array.from(el.data[0].y).flatMap((v, i) => Number.isFinite(v) ? [] : [i]); }")
                assert await gaps.json_value() == no_h

                # per element: Plot it adds bars, one per element (ADR 0064 amendment)
                pane = await _open_metrics_pane(page)
                await pane.get_by_role("button", name="New metric").click()
                await pane.get_by_label("Metric id").fill("user.f_by_element")
                await pane.get_by_label("Formula").fill("mean(norm(pred_F - ref_F), by=element)")
                for _ in range(2):
                    await pane.get_by_role("button", name=re.compile("^Fix")).click()
                await expect(pane.locator(".mx-check")).to_contain_text("per element")
                await pane.get_by_role("button", name="Save", exact=True).click()
                await expect(pane.locator(".mx-item[data-id='user.f_by_element']")).to_be_visible()
                await pane.get_by_role("button", name="Plot it").click()
                n_elements = len({z for a in aio.read(DATASET_PATH, index=":") for z in a.numbers})
                bars = await page.wait_for_function(
                    "() => { const el = [...document.querySelectorAll('.tabpanel.active .js-plotly-plot')]"
                    "  .find((d) => d.data && d.data.length && d.data[0].type === 'bar');"
                    "  return el ? [el.data[0].x.length, el.data[0].y.filter(Number.isFinite).length] : null; }")
                assert await bars.json_value() == [n_elements, n_elements]
            finally:
                await browser.close()


async def test_a_bad_value_where_leaves_out_is_ignored_with_a_warning(tmp_path):
    """ADR 0064 amendment: a broken carbon force does not stop an H-only
    formula; Try and the plotted panel say it was ignored, and where."""
    import numpy as np
    frames = ase.io.read(DATASET_PATH, index=":20")
    carbon = next(i for i, z in enumerate(frames[3].numbers) if z == 6)
    assert 1 in frames[3].numbers
    frames[3].arrays["REF_forces"][carbon] = np.nan
    broken = tmp_path / "broken.xyz"
    ase.io.write(broken, frames, format="extxyz")
    cm, _metrics_dir = await _server(tmp_path)
    async with cm as (ws_port, web_port):
        await _load_more(ws_port, set(), ("LOAD_DATASET", (str(broken), "ase (auto)"), {}))
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1500, "height": 900})
            try:
                await _open_app(page, web_port, ws_port)
                pane = await _open_metrics_pane(page)
                await pane.get_by_role("button", name="New metric").click()
                await pane.get_by_label("Metric id").fill("user.h_ref")
                await pane.get_by_label("Formula").fill('mean(norm(ref_F), by=structure, where=element("H"))')
                await pane.get_by_role("button", name=re.compile("^Fix")).click()
                await expect(pane.locator(".mx-check")).to_contain_text("per structure")
                sentence = "1 bad value in rows left out by where= was ignored (frame 3)"

                await pane.get_by_role("button", name="Try", exact=True).click()
                await expect(pane.locator(".mx-try-head")).to_contain_text("all good")
                await expect(pane.locator(".mx-try-ignored")).to_have_text(f"⚠ {sentence}")

                await pane.get_by_role("button", name="Save", exact=True).click()
                await expect(pane.locator(".mx-item[data-id='user.h_ref']")).to_be_visible()
                await pane.get_by_role("button", name="Plot it").click()
                mark = page.locator(".tabpanel.active .analysis-panel .panel-warning")
                await expect(mark).to_have_attribute("title", sentence)
            finally:
                await browser.close()

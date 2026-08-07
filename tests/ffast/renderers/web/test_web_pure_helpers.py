"""Unit tests for the web client's pure helpers (ADR 0050).

Before the ``RemoteBrowser`` / ``SessionOps`` extraction, every line of the web
client was reachable only through the full runtime — a live ffast server, a
WebSocket, and a WebGL context — so decision rules like "may the Load button be
pressed?" were testable only by clicking through a browser.

The extracted helpers are pure functions and are exercised here directly. A
static file server provides an http origin (ES modules cannot be imported from
``file://``), a blank page imports the modules, and every case is evaluated in
one batch. No ffast server, no socket, no canvas — and no npm, so ADR 0045's
zero-build stance is untouched. ``page.evaluate`` is the same mechanism the
runtime tests already use to read app internals.

All cases run in a single browser launch and the results are asserted
individually, so one slow launch buys granular failures. The collector is sync
(``asyncio.run`` inside a module-scoped fixture) because pytest-asyncio's auto
mode gives each test its own loop, which a module-scoped async fixture cannot
outlive.
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import json
import re
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

STATIC_DIR = (
    Path(__file__).resolve().parents[4]
    / "ffast" / "renderers" / "web" / "static"
)

# name -> JS expression, evaluated with remote_browser.js as `rb` and
# session_ops.js as `so`.
CASES = {
    # ── rb.joinPath ─────────────────────────────────────────────────────────
    "join_plain": "rb.joinPath('/data', 'a.xyz')",
    "join_trailing_sep": "rb.joinPath('/data/', 'a.xyz')",
    # the server sends path: null before the first listing resolves
    "join_no_dir": "rb.joinPath(null, 'a.xyz')",

    # ── rb.canLoad ──────────────────────────────────────────────────────────
    "load_no_selection": "rb.canLoad({selected: null, mode: 'dataset'})",
    "load_dataset": "rb.canLoad({selected: 'a.xyz', mode: 'dataset'})",
    "load_pred_no_force_key":
        "rb.canLoad({selected: 'p.xyz', mode: 'prediction', forceKey: '', targetDataset: 'fp'})",
    "load_pred_no_target":
        "rb.canLoad({selected: 'p.xyz', mode: 'prediction', forceKey: 'F', targetDataset: ''})",
    "load_pred_complete":
        "rb.canLoad({selected: 'p.xyz', mode: 'prediction', forceKey: 'F', targetDataset: 'fp'})",

    # ── rb.keyOptions ───────────────────────────────────────────────────────
    "keys_named": "rb.keyOptions(['E1', 'E2']).map(o => o.value)",
    "keys_calculator_only":
        "rb.keyOptions([], {calculator: true, calculatorValue: 'forces'})"
        ".map(o => [o.value, o.label])",
    "keys_none_first":
        "rb.keyOptions(['E1'], {allowNone: true}).map(o => o.value)",

    # ── so.sessionStatus ────────────────────────────────────────────────────
    "sess_saved": "so.sessionStatus('save', {ok: true, path: '/tmp/s'})",
    "sess_loaded": "so.sessionStatus('load', {ok: true, path: '/tmp/s'})",
    "sess_save_failed":
        "so.sessionStatus('save', {ok: false, path: '/tmp/s', error: 'disk full'})",

    # ── so.exportStatus / so.safeName ───────────────────────────────────────
    "export_ok": "so.exportStatus({ok: true, n: 12, path: '/tmp/a.extxyz'})",
    "export_failed_no_error": "so.exportStatus({ok: false})",
    "safe_name_hostile": "so.safeName('my data/set:1.xyz')",
    "safe_name_empty": "so.safeName('///', 'subset')",
    "safe_name_missing": "so.safeName(null)",

    # ── cm.gradientCss — the colourbar derives from the atoms' LUT (ADR 0052) ──
    "grad_viridis": "cm.gradientCss('viridis')",
    "grad_unknown": "cm.gradientCss('not_a_colormap')",
    # Every colormap the colour-by pane offers must produce a bar.
    "grad_all_offered": (
        "['viridis','inferno','plasma','coolwarm','hot','bwr','force_error']"
        ".map(n => cm.gradientCss(n).split(', ').length)"
    ),
    # The bar's stops, sampled through mapColorBy at the same fractions. Equal
    # ⇒ bar and molecule cannot disagree about what a value looks like.
    # ── an.pairSeries — dataset x prediction comparison scope ────────────────
    "pair_one_each":
        "an.pairSeries([{fp:'d1',name:'aspirin'}], [{fp:'m1',name:'MACE'}])"
        ".map(s => [s.datasetFp, s.modelFp, s.name])",
    "pair_three_models_one_dataset":
        "an.pairSeries([{fp:'d1',name:'aspirin'}],"
        " [{fp:'m1',name:'MACE'},{fp:'m2',name:'SchNet'},{fp:'m3',name:'NequIP'}])"
        ".map(s => s.name)",
    "pair_two_datasets_two_models":
        "an.pairSeries([{fp:'d1',name:'aspirin'},{fp:'d2',name:'ethanol'}],"
        " [{fp:'m1',name:'MACE'},{fp:'m2',name:'SchNet'}])"
        ".map(s => s.name)",
    "pair_skips_inapplicable":
        "an.pairSeries([{fp:'d1',name:'aspirin'},{fp:'d2',name:'ethanol'}],"
        " [{fp:'m1',name:'MACE',datasetFps:['d1']}]).map(s => s.datasetFp)",
    "pair_empty_dataset_fps_allows_any":
        "an.pairSeries([{fp:'d1',name:'aspirin'}],"
        " [{fp:'m1',name:'MACE',datasetFps:[]}]).length",
    "pair_no_model_is_reference_only":
        "an.pairSeries([{fp:'d1',name:'aspirin'}], [])"
        ".map(s => [s.modelFp, s.name])",
    "pair_null_model_entry":
        "an.pairSeries([{fp:'d1',name:'aspirin'}], [null]).map(s => s.modelFp)",
    "pair_no_dataset_is_empty":
        "an.pairSeries([], [{fp:'m1',name:'MACE'}]).length",

    # ── pn.buildPanel — one trace per series ────────────────────────────────
    # A tiny per-frame metric result, the shape decodeNdarray produces.
    "panel_timeline_two_series": (
        "(() => {"
        "  const r = (v) => ({nd: {values: v, shape: [v.length]}});"
        "  const b = pn.buildPanel({kind:'timeline'}, ["
        "    {name:'MACE',   datasetFp:'d1', modelFp:'m1', data:{y: r([1,2,3])}},"
        "    {name:'SchNet', datasetFp:'d1', modelFp:'m2', data:{y: r([4,5,6])}},"
        "  ], {});"
        "  return {n: b.traces.length, names: b.traces.map(t => t.name),"
        "          colors: b.traces.map(t => t.line.color),"
        "          legend: b.layout.showlegend,"
        "          curveSeries: b.subInfo.curveSeries};"
        "})()"
    ),
    "panel_timeline_one_series_no_legend": (
        "(() => {"
        "  const r = (v) => ({nd: {values: v, shape: [v.length]}});"
        "  const b = pn.buildPanel({kind:'timeline'},"
        "    [{name:'MACE', data:{y: r([1,2,3])}}], {});"
        "  return {n: b.traces.length, legend: b.layout.showlegend};"
        "})()"
    ),
    "panel_timeline_skips_empty_series": (
        "(() => {"
        "  const r = (v) => ({nd: {values: v, shape: [v.length]}});"
        "  const b = pn.buildPanel({kind:'timeline'}, ["
        "    {name:'MACE',   data:{y: r([1,2,3])}},"
        "    {name:'broken', data:{y: null}},"
        "    {name:'SchNet', data:{y: r([7,8,9])}},"
        "  ], {});"
        "  return {names: b.traces.map(t => t.name),"
        "          curveSeries: b.subInfo.curveSeries};"
        "})()"
    ),
    "panel_scatter_diagonal_is_last_and_excluded": (
        "(() => {"
        "  const r = (v) => ({nd: {values: v, shape: [v.length]}});"
        "  const b = pn.buildPanel({kind:'scatter', diagonal:true}, ["
        "    {name:'MACE',   data:{x: r([0,1]), y: r([0,1])}},"
        "    {name:'SchNet', data:{x: r([0,2]), y: r([0,2])}},"
        "  ], {perFrame:true});"
        "  return {n: b.traces.length,"
        "          dataCurveCount: b.subInfo.dataCurveCount,"
        "          lastIsDiagonal: b.traces[2].showlegend === false,"
        "          diagRange: [b.traces[2].x[0], b.traces[2].x[1]]};"
        "})()"
    ),
    "panel_density_fill_only_when_alone": (
        "(() => {"
        "  const r2 = () => ({nd: {values: [0,1,2, 3,4,5], shape: [2,3]}});"
        "  const one = pn.buildPanel({kind:'density'},"
        "    [{name:'A', data:{value: r2()}}], {});"
        "  const two = pn.buildPanel({kind:'density'},"
        "    [{name:'A', data:{value: r2()}}, {name:'B', data:{value: r2()}}], {});"
        "  return {oneFill: one.traces[0].fill || null,"
        "          twoFill: two.traces[0].fill || null,"
        "          twoNames: two.traces.map(t => t.name)};"
        "})()"
    ),
    # table = the desktop's model x dataset grid (rows predictions, cols datasets)
    "panel_table_grid": (
        "(() => {"
        "  const s = (v) => ({nd: {values: [v], shape: [1]}});"
        "  const mk = (d,dn,m,mn,v) => ({datasetFp:d, datasetName:dn, modelFp:m,"
        "     modelName:mn, name:mn, data:{value: s(v)}});"
        "  const html = pn.buildPanel({kind:'table', precision:2, title:'MAE'}, ["
        "    mk('d1','aspirin','m1','MACE',1), mk('d2','ethanol','m1','MACE',2),"
        "    mk('d1','aspirin','m2','SchNet',3), mk('d2','ethanol','m2','SchNet',4),"
        "  ], {}).html;"
        "  const doc = new DOMParser().parseFromString(html, 'text/html');"
        "  return {"
        "    head: [...doc.querySelectorAll('thead th')].map(e => e.textContent),"
        "    rows: [...doc.querySelectorAll('tbody tr')].map("
        "      tr => [...tr.children].map(td => td.textContent)),"
        "  };"
        "})()"
    ),
    "panel_table_missing_pair_is_dashed": (
        "(() => {"
        "  const s = (v) => ({nd: {values: [v], shape: [1]}});"
        "  const mk = (d,dn,m,mn,v) => ({datasetFp:d, datasetName:dn, modelFp:m,"
        "     modelName:mn, name:mn, data:{value: s(v)}});"
        "  const html = pn.buildPanel({kind:'table', precision:2}, ["
        "    mk('d1','aspirin','m1','MACE',1), mk('d2','ethanol','m2','SchNet',4),"
        "  ], {}).html;"
        "  const doc = new DOMParser().parseFromString(html, 'text/html');"
        "  return [...doc.querySelectorAll('tbody tr')].map("
        "    tr => [...tr.children].map(td => td.textContent));"
        "})()"
    ),
    # grouped_table: one element selected -> rows are the series (compare models)
    "panel_grouped_table_single_element_rows_are_series": (
        "(() => {"
        "  const a = (v) => ({nd: {values: v, shape: [v.length]}});"
        "  const mk = (mn, mae, rmse) => ({name: mn, modelName: mn,"
        "     data:{mae: a(mae), rmse: a(rmse)}});"
        "  const html = pn.buildPanel({kind:'grouped_table', precision:2}, ["
        "    mk('MACE',   [0.1, 0.2], [0.5, 0.6]),"
        "    mk('SchNet', [0.3, 0.4], [0.7, 0.8]),"
        "  ], {elementOrder:[1,6], selectedElements:[6]}).html;"
        "  const doc = new DOMParser().parseFromString(html, 'text/html');"
        "  return {"
        "    head: [...doc.querySelectorAll('thead th')].map(e => e.textContent),"
        "    rows: [...doc.querySelectorAll('tbody tr')].map("
        "      tr => [...tr.children].map(td => td.textContent)),"
        "  };"
        "})()"
    ),
    # grouped_table: several elements -> rows are elements, first series only
    "panel_grouped_table_multi_element_rows_are_elements": (
        "(() => {"
        "  const a = (v) => ({nd: {values: v, shape: [v.length]}});"
        "  const html = pn.buildPanel({kind:'grouped_table', precision:2}, ["
        "    {name:'MACE',   data:{mae: a([0.1,0.2]), rmse: a([0.5,0.6])}},"
        "    {name:'SchNet', data:{mae: a([9.1,9.2]), rmse: a([9.5,9.6])}},"
        "  ], {elementOrder:[1,6], selectedElements:[1,6]}).html;"
        "  const doc = new DOMParser().parseFromString(html, 'text/html');"
        "  return [...doc.querySelectorAll('tbody tr')].map("
        "    tr => [...tr.children].map(td => td.textContent));"
        "})()"
    ),
    # grouped_density colour channel: elements when >1 selected, else series
    "panel_grouped_density_colour_channel": (
        "(() => {"
        "  const g = () => ({nd: {values:"
        "     [0,1, 2,3,  4,5, 6,7], shape: [2,2,2]}});"
        "  const two = [{name:'MACE', data:{value: g()}},"
        "               {name:'SchNet', data:{value: g()}}];"
        "  const byElem = pn.buildPanel({kind:'grouped_density'}, two,"
        "    {elementOrder:[1,6], selectedElements:[1,6]});"
        "  const bySeries = pn.buildPanel({kind:'grouped_density'}, two,"
        "    {elementOrder:[1,6], selectedElements:[6]});"
        "  return {"
        "    elemNames: byElem.traces.map(t => t.name),"
        "    elemColors: byElem.traces.map(t => t.line.color),"
        "    seriesNames: bySeries.traces.map(t => t.name),"
        "    seriesColors: bySeries.traces.map(t => t.line.color),"
        "  };"
        "})()"
    ),
    "panel_unknown_kind_is_null": "pn.buildPanel({kind:'nope'}, [], {})",

    "grad_matches_mapped": (
        "(() => {"
        "  const out = {};"
        "  for (const n of ['viridis','coolwarm','force_error','hot']) {"
        "    const bar = cm.gradientCss(n).split(', ');"
        "    const k = bar.length;"
        "    const values = Array.from({length: k}, (_, i) => i / (k - 1));"
        "    const mapped = cm.mapColorBy({values, colormap: n, vmin: 0, vmax: 1})"
        "      .map(cm.rgbToHex);"
        "    out[n] = JSON.stringify(bar) === JSON.stringify(mapped);"
        "  }"
        "  return out;"
        "})()"
    ),
}


@pytest.fixture(scope="module")
def results():
    """Evaluate every case once, in one browser, and return {name: value}."""
    playwright_api = pytest.importorskip("playwright.async_api")

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(STATIC_DIR)
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"

    script = (
        "async () => {\n"
        f"  const rb = await import('{origin}/remote_browser.js');\n"
        f"  const so = await import('{origin}/session_ops.js');\n"
        f"  const cm = await import('{origin}/colormap.js');\n"
        f"  const pn = await import('{origin}/panels.js');\n"
        f"  const an = await import('{origin}/analysis.js');\n"
        "  const out = {};\n"
        + "".join(
            f"  out[{json.dumps(name)}] = {expr};\n" for name, expr in CASES.items()
        )
        + "  return out;\n}"
    )

    async def _collect():
        async with playwright_api.async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            # Same-origin document so the dynamic imports are not cross-origin.
            await page.goto(f"{origin}/events.js")
            await page.set_content("<!doctype html><title>helpers</title>")
            try:
                out = await page.evaluate(script)
            finally:
                await browser.close()
            assert not errors, f"page errors: {errors}"
            return out

    try:
        yield asyncio.run(_collect())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ── remote_browser.joinPath ─────────────────────────────────────────────────

def test_join_path_adds_a_separator(results):
    assert results["join_plain"] == "/data/a.xyz"


def test_join_path_does_not_double_a_trailing_separator(results):
    assert results["join_trailing_sep"] == "/data/a.xyz"


def test_join_path_without_a_directory_returns_the_name(results):
    assert results["join_no_dir"] == "a.xyz"


# ── remote_browser.canLoad ──────────────────────────────────────────────────

def test_can_load_needs_a_selection(results):
    assert results["load_no_selection"] is False


def test_can_load_dataset_needs_only_a_selection(results):
    assert results["load_dataset"] is True


def test_can_load_prediction_needs_a_force_key(results):
    """Force arrows are the point of loading a prediction."""
    assert results["load_pred_no_force_key"] is False


def test_can_load_prediction_needs_a_target_dataset(results):
    """A prediction attaches to an already-loaded dataset."""
    assert results["load_pred_no_target"] is False


def test_can_load_prediction_with_both(results):
    assert results["load_pred_complete"] is True


# ── remote_browser.keyOptions ───────────────────────────────────────────────

def test_key_options_lists_probed_keys(results):
    assert results["keys_named"] == ["E1", "E2"]


def test_key_options_offers_the_calculator_when_no_named_keys_exist(results):
    """A plain MACE/DFT extxyz dump probes empty but has_calculator_*=true.

    The option's value must be the literal 'forces' the ASE loader maps to the
    calculator, or such a file looks unloadable.
    """
    assert results["keys_calculator_only"] == [["forces", "calculator (built-in)"]]


def test_key_options_none_entry_comes_first_when_allowed(results):
    assert results["keys_none_first"] == ["", "E1"]


# ── session_ops.sessionStatus ───────────────────────────────────────────────

def test_session_status_reports_a_successful_save(results):
    assert results["sess_saved"] == {"text": "Saved session to /tmp/s", "kind": "connected"}


def test_session_status_reports_a_successful_load(results):
    assert results["sess_loaded"] == {"text": "Loaded session from /tmp/s", "kind": "connected"}


def test_session_status_surfaces_the_server_error(results):
    """The replaced TASK_DONE guess had no error to report — only ok/not-ok."""
    r = results["sess_save_failed"]
    assert r["kind"] == "error"
    assert "disk full" in r["text"]
    assert "/tmp/s" in r["text"]


# ── session_ops.exportStatus / safeName ─────────────────────────────────────

def test_export_status_counts_structures(results):
    assert results["export_ok"] == {
        "text": "Exported 12 structure(s) → /tmp/a.extxyz", "kind": "connected",
    }


def test_export_status_failure_without_an_error_still_reads_sensibly(results):
    r = results["export_failed_no_error"]
    assert r["kind"] == "error"
    assert "undefined" not in r["text"]


def test_safe_name_strips_filesystem_hostile_characters(results):
    assert results["safe_name_hostile"] == "my_data_set_1.xyz"


def test_safe_name_falls_back_when_nothing_survives(results):
    assert results["safe_name_empty"] == "subset"


def test_safe_name_falls_back_on_a_missing_name(results):
    assert results["safe_name_missing"] == "ffast"


# ── colormap.gradientCss (ADR 0052) ─────────────────────────────────────────

def test_gradient_css_is_a_css_colour_stop_list(results):
    stops = results["grad_viridis"].split(", ")
    assert len(stops) > 1
    assert all(re.fullmatch(r"#[0-9a-f]{6}", s) for s in stops)


def test_gradient_css_falls_back_to_viridis(results):
    """A missing colourbar is worse than a wrong-palette one — matches the
    behaviour of the hand-written table this replaced."""
    assert results["grad_unknown"] == results["grad_viridis"]


def test_gradient_css_covers_every_colormap_the_pane_offers(results):
    """The pane's dropdown and the LUT are separate lists; a name in one and
    not the other silently drew a viridis bar for a non-viridis molecule."""
    assert all(n > 1 for n in results["grad_all_offered"])


def test_the_colourbar_matches_the_colours_the_atoms_get(results):
    """The bar carried the true matplotlib hexes while atoms were drawn from
    compact approximations, so the two disagreed about what a value looked
    like. Deriving the bar from the same stops makes that impossible.
    """
    assert results["grad_matches_mapped"] == {
        "viridis": True, "coolwarm": True, "force_error": True, "hot": True,
    }


# ── analysis.pairSeries — the comparison scope (ADR 0053) ───────────────────

def test_pair_series_one_dataset_one_prediction(results):
    assert results["pair_one_each"] == [["d1", "m1", "MACE"]]


def test_pair_series_names_predictions_alone_against_one_dataset(results):
    """Four models on one dataset must not be four copies of its name."""
    assert results["pair_three_models_one_dataset"] == ["MACE", "SchNet", "NequIP"]


def test_pair_series_adds_the_dataset_once_several_are_in_play(results):
    """Dataset-major order, and the name disambiguates — the desktop's rule."""
    assert results["pair_two_datasets_two_models"] == [
        "aspirin & MACE", "aspirin & SchNet",
        "ethanol & MACE", "ethanol & SchNet",
    ]


def test_pair_series_skips_a_prediction_that_does_not_apply(results):
    """A prediction belongs to the dataset it was computed for."""
    assert results["pair_skips_inapplicable"] == ["d1"]


def test_pair_series_treats_no_declared_datasets_as_applies_to_any(results):
    assert results["pair_empty_dataset_fps_allows_any"] == 1


def test_pair_series_with_no_prediction_is_reference_only(results):
    """Reference-only metrics still draw; the series is named for the dataset."""
    assert results["pair_no_model_is_reference_only"] == [[None, "aspirin"]]


def test_pair_series_accepts_an_explicit_null_prediction(results):
    assert results["pair_null_model_entry"] == [None]


def test_pair_series_without_a_dataset_draws_nothing(results):
    assert results["pair_no_dataset_is_empty"] == 0


# ── panels.buildPanel — one trace per series ────────────────────────────────

def test_timeline_draws_one_trace_per_series(results):
    r = results["panel_timeline_two_series"]
    assert r["n"] == 2
    assert r["names"] == ["MACE", "SchNet"]
    assert r["colors"][0] != r["colors"][1]
    assert r["legend"] is True
    assert r["curveSeries"] == [0, 1]


def test_timeline_with_one_series_keeps_the_legend_off(results):
    """The single-prediction panel must look exactly as it did before."""
    r = results["panel_timeline_one_series_no_legend"]
    assert r == {"n": 1, "legend": False}


def test_a_series_with_no_data_is_skipped_not_drawn_as_a_gap(results):
    """One prediction that cannot compute a panel must not cost the others."""
    r = results["panel_timeline_skips_empty_series"]
    assert r["names"] == ["MACE", "SchNet"]
    # curveSeries still points at the ORIGINAL series indices, so subbing a
    # curve resolves to the right dataset even with a hole before it.
    assert r["curveSeries"] == [0, 2]


def test_scatter_diagonal_spans_every_series_and_stays_unselectable(results):
    r = results["panel_scatter_diagonal_is_last_and_excluded"]
    assert r["n"] == 3                 # two data traces + the diagonal
    assert r["dataCurveCount"] == 2     # box-select ignores the diagonal
    assert r["lastIsDiagonal"] is True
    assert r["diagRange"] == [0, 2]     # combined range, not just series 0's


def test_density_fill_is_a_single_series_affordance(results):
    """Overlapping filled curves read as mud, so the fill drops when comparing."""
    r = results["panel_density_fill_only_when_alone"]
    assert r["oneFill"] == "tozeroy"
    assert r["twoFill"] is None
    assert r["twoNames"] == ["A", "B"]


def test_table_is_a_prediction_by_dataset_grid(results):
    """The desktop's TableKind: rows are predictions, columns are datasets.

    This replaces a one-cell table whose comment conceded the shortcut.
    """
    r = results["panel_table_grid"]
    assert r["head"] == ["MAE", "aspirin", "ethanol"]
    assert r["rows"] == [
        ["MACE", "1.00", "2.00"],
        ["SchNet", "3.00", "4.00"],
    ]


def test_table_shows_a_dash_for_a_pair_that_was_not_computed(results):
    assert results["panel_table_missing_pair_is_dashed"] == [
        ["MACE", "1.00", "—"],
        ["SchNet", "—", "4.00"],
    ]


def test_grouped_table_rows_are_series_when_one_element_is_selected(results):
    """Single-element mode is how you compare predictions per element, so the
    element moves into the column header (desktop `table_top_header`).
    """
    r = results["panel_grouped_table_single_element_rows_are_series"]
    assert r["head"] == ["Object", "C MAE", "C RMSE"]
    # Element C is index 1 of elementOrder [1, 6], so each row reads that slot.
    assert r["rows"] == [
        ["MACE", "0.20", "0.60"],
        ["SchNet", "0.40", "0.80"],
    ]


def test_grouped_table_rows_are_elements_when_several_are_selected(results):
    """Desktop parity: multi-element mode reads the first series only —
    element x series is not a shape either client has.
    """
    assert results["panel_grouped_table_multi_element_rows_are_elements"] == [
        ["H", "0.10", "0.50"],
        ["C", "0.20", "0.60"],
    ]


def test_grouped_density_gives_colour_to_elements_or_to_series(results):
    """`GroupedDensityKind.atom_mode`: >1 element selected → colour means
    element and the label carries the series; 1 element → colour means series.
    """
    r = results["panel_grouped_density_colour_channel"]
    assert r["elemNames"] == ["H — MACE", "C — MACE", "H — SchNet", "C — SchNet"]
    assert r["elemColors"][0] == r["elemColors"][2]   # both H
    assert r["elemColors"][0] != r["elemColors"][1]   # H vs C
    assert r["seriesNames"] == ["C — MACE", "C — SchNet"]
    assert r["seriesColors"][0] != r["seriesColors"][1]


def test_an_unknown_panel_kind_builds_nothing(results):
    assert results["panel_unknown_kind_is_null"] is None

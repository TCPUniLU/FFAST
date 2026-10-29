"""Unit tests for the sidebar panes' `show(state)` (ADR 0060 step 4).

Each pane shows the settings of one view and writes every change of its
controls into that settings object, so a focus switch is one `show` call and
nothing is saved on leaving. Same mechanism as ``test_web_pure_helpers.py``:
a static file server, a blank page, every case evaluated in one batch.
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

STATIC_DIR = (
    Path(__file__).resolve().parents[4]
    / "ffast" / "renderers" / "web" / "static"
)

PRELUDE = r"""
  // `defaultPrediction` is a question, not a change: not recorded
  const mk = (mod, name, cbs = {defaultPrediction: () => null}) => {
    const host = document.createElement('div');
    document.body.append(host);
    const calls = [];
    const cb = new Proxy(cbs, { get: (t, k) => (k in t ? t[k] : (...a) => { calls.push([k, ...a]); }) });
    return { pane: mod[name](host, cb), host, calls };
  };
  const ctl = (host, label) => host.querySelector(`.ctl-row[data-label="${label}"] :is(input,select,textarea)`);
  const val = (el) => (el.type === 'checkbox' ? el.checked : el.value);
  const fire = (el, v) => {
    if (el.type === 'checkbox') el.checked = v; else el.value = v;
    el.dispatchEvent(new Event(['range', 'color'].includes(el.type) ? 'input' : 'change', { bubbles: true }));
  };
"""

CASES = {
    # ── force vectors (to_fix_v2: arming the Force tool) ────────────────────
    "force_arm": """(() => { const seen = [];
      const {pane, host} = mk(fp, 'createForcesPane', {onApply: (s) => seen.push(s.show), getModels: () => new Map()});
      const shown0 = val(ctl(host, 'Show force vectors'));
      pane.armShow(); const shown1 = val(ctl(host, 'Show force vectors'));
      pane.armShow();
      return {shown0, shown1, applies: seen}; })()""",
    "force_frames": """(() => { const seen = [];
      const {pane} = mk(fp, 'createForcesPane', {onApply: (s) => seen.push(s), getModels: () => new Map()});
      pane.setPickedIndices([7]);
      pane.setPickedIndices([1, 2], 4);
      const onFour = pane.pickedIndices(4), onFive = pane.pickedIndices(5), shared = pane.pickedIndices();
      pane.setPickedIndices([], 4);
      return {onFour, onFive, shared, byFrame: seen[1].atomIndicesByFrame, afterClear: seen[2].atomIndicesByFrame,
        pickedAfterClear: pane.pickedIndices(4)}; })()""",
    "force_picks": """(() => { const seen = [];
      const {pane} = mk(fp, 'createForcesPane', {onApply: (s) => seen.push(s), getModels: () => new Map()});
      pane.setPickedIndices([3, 5]);
      return {picked: pane.pickedIndices(), filter: seen[0].filterEnabled, atoms: seen[0].atomIndices}; })()""",

    # ── display ─────────────────────────────────────────────────────────────
    "display_show": """(() => { const {pane, host, calls} = mk(dp, 'createDisplayPane');
      pane.show({atomSize: 2, hide: 'C', highlight: '0 1', unitCell: false});
      return {controls: ['Atom size', 'Hide atoms', 'Highlight atoms', 'Show unit cell'].map((l) => val(ctl(host, l))),
              calls: calls.length, scale: pane.atomScale()}; })()""",
    "display_write": """(() => { const {pane, host, calls} = mk(dp, 'createDisplayPane');
      const s = {atomSize: 1, hide: '', highlight: '', unitCell: true}; pane.show(s);
      fire(ctl(host, 'Atom size'), '3'); fire(ctl(host, 'Hide atoms'), 'H'); fire(ctl(host, 'Highlight atoms'), '0 4');
      fire(ctl(host, 'Show unit cell'), false);
      return {s, calls, scale: pane.atomScale()}; })()""",
    "display_isolated": """(() => { const {pane, host} = mk(dp, 'createDisplayPane');
      const s = {atomSize: 1, hide: '', highlight: '', unitCell: true};
      const t = {atomSize: 2, hide: 'C', highlight: '', unitCell: true};
      pane.show(s); pane.show(t); fire(ctl(host, 'Atom size'), '5'); fire(ctl(host, 'Hide atoms'), 'N');
      return [s, t]; })()""",
    # ── camera ──────────────────────────────────────────────────────────────
    "camera_show": """(() => { const {pane, host, calls} = mk(cp, 'createCameraPane');
      pane.show({ortho: true, gizmo: true, background: '#112233'});
      return {controls: ['Orthographic', 'Axes gizmo', 'Background'].map((l) => val(ctl(host, l))), calls}; })()""",
    "camera_write": """(() => { const {pane, host} = mk(cp, 'createCameraPane');
      const s = {ortho: false, gizmo: false, background: '#000000'}; pane.show(s);
      fire(ctl(host, 'Orthographic'), true); fire(ctl(host, 'Axes gizmo'), true); fire(ctl(host, 'Background'), '#abcdef');
      return s; })()""",
    "camera_isolated": """(() => { const {pane, host} = mk(cp, 'createCameraPane');
      const s = {ortho: false, gizmo: false, background: '#000000'};
      const t = {ortho: true, gizmo: false, background: '#111111'};
      pane.show(s); pane.show(t); fire(ctl(host, 'Axes gizmo'), true); return [s, t]; })()""",
    # ── export ──────────────────────────────────────────────────────────────
    "export_show_write": """(() => { const {pane, host} = mk(xp, 'createExportPane');
      const s = {background: '#123456'}; pane.show(s); const shown = val(ctl(host, 'Background'));
      fire(ctl(host, 'Background'), '#654321'); const typed = s.background;
      pane.setBackground('#0000ff');
      return {shown, typed, set: s.background, control: val(ctl(host, 'Background'))}; })()""",
    "export_isolated": """(() => { const {pane, host} = mk(xp, 'createExportPane');
      const s = {background: '#000000'}; const t = {background: '#111111'};
      pane.show(s); pane.show(t); fire(ctl(host, 'Background'), '#222222'); return [s, t]; })()""",
    # ── extract ─────────────────────────────────────────────────────────────
    "extract_show_write": """(() => { const {pane, host} = mk(ep, 'createExtractPane');
      const s = {indices: '0 1'}; pane.show(s); const shown = val(ctl(host, 'Indices'));
      fire(ctl(host, 'Indices'), 'C -H'); const typed = s.indices;
      pane.setPickedIndices([4, 5]); const picked = s.indices;
      pane.clearInput();
      return {shown, typed, picked, cleared: s.indices, control: val(ctl(host, 'Indices')), tokens: pane.getTokens()}; })()""",
    "extract_isolated": """(() => { const {pane, host} = mk(ep, 'createExtractPane');
      const s = {indices: ''}; const t = {indices: '7'};
      pane.show(s); pane.show(t); pane.setPickedIndices([1]); return [s, t]; })()""",
    "extract_dataset_and_result": """(() => { const {pane, host} = mk(ep, 'createExtractPane');
      const box = ctl(host, 'Indices'); const pane_el = host.firstElementChild;
      const hint = () => { const h = host.querySelector('.ctl-hint');
        return [h.textContent, h.classList.contains('ok'), h.style.display !== 'none']; };
      pane.setDataset({is_sub: false, variable: true}); const variable = [box.placeholder, pane_el.style.display];
      pane.setDataset({is_sub: false}); const uniform = box.placeholder;
      pane.setDataset({is_sub: true, frozen: false}); const live = pane_el.style.display;
      pane.showResult({ok: true, text: 'C in 812 of 1000 frames'}); const made = hint();
      pane.showResult({ok: false, error: 'N is not in this dataset.'}); const refused = hint();
      return {variable, uniform, live, made, refused}; })()""",
    # ── bonds ───────────────────────────────────────────────────────────────
    "bonds_show": """(() => { const {pane, host, calls} = mk(bp, 'createBondsPane', {getDynamicBondPairs: () => [[0, 1]]});
      pane.show({width: 40, color: '#ff0000', bondType: 'Fixed', fixedIndices: [[0, 1], [2, 3]]});
      return {controls: ['Bond width', 'Bond colour', 'Bonds Type', 'Bond indices'].map((l) => val(ctl(host, l))), calls}; })()""",
    "bonds_write": """(() => { const {pane, host, calls} = mk(bp, 'createBondsPane', {getDynamicBondPairs: () => [[0, 1]]});
      const s = {width: 100, color: '#404040', bondType: 'Dynamic', fixedIndices: []}; pane.show(s);
      fire(ctl(host, 'Bond width'), '50'); fire(ctl(host, 'Bond colour'), '#00ff00');
      fire(ctl(host, 'Bonds Type'), 'Fixed'); fire(ctl(host, 'Bond indices'), '0-1, 2-5');
      const typed = JSON.parse(JSON.stringify(s));
      pane.toggleBondPair(7, 6);
      return {typed, toggled: s, applied: calls.filter((c) => c[0] === 'onApply').length}; })()""",
    "bonds_isolated": """(() => { const {pane, host} = mk(bp, 'createBondsPane', {getDynamicBondPairs: () => []});
      const s = {width: 100, color: '#404040', bondType: 'Dynamic', fixedIndices: []};
      const t = {width: 30, color: '#404040', bondType: 'Dynamic', fixedIndices: []};
      pane.show(s); pane.show(t); fire(ctl(host, 'Bond width'), '60'); pane.toggleBondPair(1, 2); return [s, t]; })()""",
    # ── align ───────────────────────────────────────────────────────────────
    "align_show": """(() => { const {pane, host, calls} = mk(ap, 'createAlignPane');
      pane.show({kabsch: true, heavyOnly: false, atomAlign: false, idxInput: '', indices: []});
      return {controls: ['Kabsch align', 'Heavy atoms only', '3-atom frame align', 'Reference atoms'].map((l) => val(ctl(host, l))), calls}; })()""",
    "align_write": """(() => { const {pane, host} = mk(ap, 'createAlignPane');
      const s = {kabsch: false, heavyOnly: true, atomAlign: false, idxInput: '', indices: []}; pane.show(s);
      fire(ctl(host, 'Kabsch align'), true); fire(ctl(host, 'Heavy atoms only'), false);
      const kabsch = JSON.parse(JSON.stringify(s));
      fire(ctl(host, '3-atom frame align'), true); const exclusive = JSON.parse(JSON.stringify(s));
      fire(ctl(host, 'Reference atoms'), '4 5 6 7'); const typed = JSON.parse(JSON.stringify(s));
      pane.setPickedIndices([1, 2, 3]);
      return {kabsch, exclusive, typed, picked: s}; })()""",
    "align_enable_mode": """(() => { const {pane, host} = mk(ap, 'createAlignPane');
      const s = {kabsch: true, heavyOnly: true, atomAlign: false, idxInput: '', indices: []}; pane.show(s);
      pane.enableAtomAlignMode(); return s; })()""",
    "align_partial_picks_reset": """(() => { const {pane, host} = mk(ap, 'createAlignPane');
      const s = {kabsch: false, heavyOnly: true, atomAlign: true, idxInput: '1 2', indices: [1, 2]}; pane.show(s);
      return [s, val(ctl(host, 'Reference atoms'))]; })()""",
    "align_isolated": """(() => { const {pane, host} = mk(ap, 'createAlignPane');
      const s = {kabsch: false, heavyOnly: true, atomAlign: false, idxInput: '', indices: []};
      const t = {kabsch: false, heavyOnly: true, atomAlign: false, idxInput: '', indices: []};
      pane.show(s); pane.show(t); fire(ctl(host, 'Kabsch align'), true); return [s, t]; })()""",
    "align_variable": """(() => { const {pane, host, calls} = mk(ap, 'createAlignPane');
      const s = {kabsch: true, heavyOnly: true, atomAlign: false, idxInput: '', indices: []}; pane.show(s);
      pane.setDataset({variable: true});
      const rows = ['Kabsch align', '3-atom frame align'].map((l) => ctl(host, l).closest('.ctl-row').classList.contains('needs'));
      pane.enableAtomAlignMode(); const off = JSON.parse(JSON.stringify(s));
      pane.setDataset({variable: false});
      const back = ['Kabsch align', '3-atom frame align'].map((l) => ctl(host, l).closest('.ctl-row').classList.contains('needs'));
      return {off, rows, back, calls}; })()""",
    # ── colour by ───────────────────────────────────────────────────────────
    "color_show": """(() => { const {pane, host, calls} = mk(cb, 'createColorByPane');
      pane.setMetricCatalog(CAT); pane.refreshModels(new Map([['fp1', {name: 'MACE'}]]));
      pane.show({source: 'metric:m.a', prediction: 'fp1', colormap: 'plasma', norm: 'None'});
      return {controls: ['Coloring', 'Prediction', 'Colormap'].map((l) => val(ctl(host, l))), calls}; })()""",
    "color_write": """(() => { const {pane, host, calls} = mk(cb, 'createColorByPane');
      pane.setMetricCatalog(CAT); pane.refreshModels(new Map([['fp1', {name: 'MACE'}]]));
      const s = {source: 'element', prediction: null, colormap: 'viridis', norm: 'None'}; pane.show(s);
      fire(ctl(host, 'Coloring'), 'A metric'); const source = s.source;
      fire(ctl(host, 'Prediction'), 'MACE'); fire(ctl(host, 'Colormap'), 'inferno');
      fire(ctl(host, 'Coloring'), 'Displacement');
      return {source, s, calls}; })()""",
    "color_catalog_late": """(() => { const {pane, host} = mk(cb, 'createColorByPane');
      const s = {source: 'metric:m.a', prediction: null, colormap: 'viridis', norm: 'None'}; pane.show(s);
      const before = [val(ctl(host, 'Coloring')), s.source];
      pane.setMetricCatalog(CAT);
      return {before, after: [val(ctl(host, 'Coloring')), s.source]}; })()""",
    "color_metric_gone": """(() => { const {pane, host} = mk(cb, 'createColorByPane');
      pane.setMetricCatalog(CAT);
      const s = {source: 'metric:m.a', prediction: null, colormap: 'viridis', norm: 'None'}; pane.show(s);
      pane.setMetricCatalog([]);
      return {coloring: val(ctl(host, 'Coloring')), source: s.source,
              note: host.querySelector('.colorby-missing').textContent}; })()""",
    "color_choose_metric": """(() => { const {pane, host, calls} = mk(cb, 'createColorByPane');
      pane.setMetricCatalog(CAT);
      const s = {source: 'element', prediction: null, colormap: 'viridis', norm: 'None'}; pane.show(s);
      const ok = pane.chooseMetric('m.a'); const missing = pane.chooseMetric('zzz');
      return {ok, missing, source: s.source, coloring: val(ctl(host, 'Coloring')), calls}; })()""",
    "color_prediction_helpers": """(() => { const {pane, host} = mk(cb, 'createColorByPane');
      pane.setMetricCatalog(FCAT); pane.refreshModels(new Map([['fp1', {name: 'MACE'}]]));
      const s = {source: 'metric:ffast.force_reference[atom+structure;length]', prediction: null, colormap: 'viridis', norm: 'None'}; pane.show(s);
      pane.setPrediction('fp1'); const set = [s.prediction, pane.predictionOf(), val(ctl(host, 'Prediction'))];
      pane.refreshModels(new Map()); const gone = [s.prediction, pane.predictionOf(), val(ctl(host, 'Prediction'))];
      return {set, gone}; })()""",
    "color_norm": """(() => { const {pane, host, calls} = mk(cb, 'createColorByPane');
      pane.setMetricCatalog(CAT);
      const s = {source: 'metric:m.a', prediction: null, colormap: 'viridis', norm: 'log'}; pane.show(s);
      const shown = val(ctl(host, 'norm')); fire(ctl(host, 'norm'), 'None');
      const t = {source: 'metric:m.a', prediction: null, colormap: 'viridis', norm: 'None'}; pane.show(t);
      fire(ctl(host, 'norm'), 'log');
      return {shown, s, t, calls: calls.filter((c) => c[0] === 'onMetricParam')}; })()""",
    "color_forces": """(() => { const {pane, host, calls} = mk(cb, 'createColorByPane');
      pane.setMetricCatalog(FCAT); pane.refreshModels(new Map([['fp1', {name: 'MACE'}]]));
      const opts = (l) => [...ctl(host, l).options].map((o) => o.value);
      const s = {source: 'metric:ffast.force_prediction[atom+structure;length]', prediction: 'fp1', colormap: 'viridis', norm: 'None'};
      pane.show(s);
      const old = [val(ctl(host, 'Coloring')), val(ctl(host, 'Prediction')), opts('Prediction')];
      fire(ctl(host, 'Prediction'), 'Ground Truth'); const toTruth = [s.source, s.prediction, calls.splice(0)];
      fire(ctl(host, 'Prediction'), 'MACE'); const toModel = [s.source, s.prediction, calls.splice(0)];
      pane.setColorBy({values: [1], colormap: 'viridis', vmin: 0, vmax: 1, label: 'Predicted Forces · per atom · length', unit: ''});
      const bar = pane.legend.querySelector('#colorbar-label');
      const t = {source: 'metric:ffast.force_reference[atom+structure;component;mae]', prediction: 'fp1', colormap: 'viridis', norm: 'None'};
      pane.show(t);
      return {coloring: opts('Coloring'), old, toTruth, toModel, bar: bar.textContent,
              truth: val(ctl(host, 'Prediction'))}; })()""",
    "color_prediction_rows": """(() => { const {pane, host, calls} = mk(cb, 'createColorByPane');
      pane.setMetricCatalog(FCAT);
      const opts = () => [...ctl(host, 'Prediction').options].map((o) => o.value);
      const row = () => ctl(host, 'Prediction').closest('.ctl-row');
      const hid = () => row().classList.contains('needs');
      const s = {source: 'element', prediction: null, colormap: 'viridis', norm: 'None'}; pane.show(s);
      const none = [hid(), row().dataset.needs ?? null];
      fire(ctl(host, 'Coloring'), 'A metric'); const waiting = [hid(), row().dataset.needs ?? null];
      pane.refreshModels(new Map([['fp1', {name: 'MACE'}], ['fp2', {name: 'NequIP'}]]));
      const error = [hid(), opts(), val(ctl(host, 'Prediction'))];
      fire(ctl(host, 'Coloring'), 'Reference only'); const reference = [hid(), row().dataset.needs];
      calls.splice(0); fire(ctl(host, 'Coloring'), 'A metric');
      return {none, waiting, error, reference, picked: calls.filter((c) => c[0] !== 'boxes'), s}; })()""",
    "color_isolated": """(() => { const {pane, host} = mk(cb, 'createColorByPane');
      const s = {source: 'element', prediction: null, colormap: 'viridis', norm: 'None'};
      const t = {source: 'displacement', prediction: null, colormap: 'hot', norm: 'None'};
      pane.show(s); pane.show(t); fire(ctl(host, 'Colormap'), 'bwr'); return [s, t]; })()""",
}


@pytest.fixture(scope="module")
def results():
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
        f"  const dp = await import('{origin}/panes/display.js');\n"
        f"  const cp = await import('{origin}/panes/camera.js');\n"
        f"  const xp = await import('{origin}/panes/export.js');\n"
        f"  const ep = await import('{origin}/panes/extract.js');\n"
        f"  const bp = await import('{origin}/panes/bonds.js');\n"
        f"  const ap = await import('{origin}/panes/align.js');\n"
        f"  const cb = await import('{origin}/panes/colorby.js');\n"
        f"  const fp = await import('{origin}/panes/forces.js');\n"
        "  const CAT = [{id: 'm.a', label: 'A metric', shape: 'N_atoms', inputs: ['prediction.forces'],\n"
        "    parameters: {norm: {type: 'choice', choices: ['None', 'log'], default: 'None'}}}];\n"
        "  const FCAT = [...CAT, {id: 'm.ref', label: 'Reference only', shape: 'N_atoms', inputs: ['reference.positions']},\n"
        "    {id: 'ffast.force_reference', label: 'Reference Forces', shape: '(N_atoms, xyz)', inputs: ['reference.forces']},\n"
        "    {id: 'ffast.force_prediction', label: 'Predicted Forces', shape: '(N_atoms, xyz)', inputs: ['prediction.forces']}];\n"
        + PRELUDE
        + "  const out = {};\n"
        + "".join(
            f"  try {{ out[{json.dumps(name)}] = {expr}; }}"
            f" catch (e) {{ out[{json.dumps(name)}] = {{error: String(e)}}; }}\n"
            for name, expr in CASES.items()
        )
        + "  return out;\n}"
    )

    async def _collect():
        async with playwright_api.async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            await page.goto(f"{origin}/events.js")
            await page.set_content("<!doctype html><title>panes</title>")
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


# ── display ─────────────────────────────────────────────────────────────────

def test_display_shows_its_settings_without_sending_anything(results):
    r = results["display_show"]
    assert r["controls"] == ["2", "C", "0 1", False]
    assert r["calls"] == 0
    assert r["scale"] == 2


def test_display_writes_every_change_into_its_settings(results):
    r = results["display_write"]
    assert r["s"] == {"atomSize": 3, "hide": "H", "highlight": "0 4", "unitCell": False}
    assert [c[0] for c in r["calls"]] == ["onAtomSize", "onHideAtoms", "onHighlight", "onUnitCell"]
    assert r["scale"] == 3


def test_display_changes_after_a_new_show_leave_the_old_settings_alone(results):
    s, t = results["display_isolated"]
    assert s == {"atomSize": 1, "hide": "", "highlight": "", "unitCell": True}
    assert t["atomSize"] == 5 and t["hide"] == "N"


# ── camera ──────────────────────────────────────────────────────────────────

def test_camera_shows_its_settings_and_applies_them_to_the_renderers(results):
    r = results["camera_show"]
    assert r["controls"] == [True, True, "#112233"]
    assert r["calls"] == [["onOrtho", True], ["onGizmo", True], ["onBackground", "#112233"]]


def test_camera_writes_every_change_into_its_settings(results):
    assert results["camera_write"] == {"ortho": True, "gizmo": True, "background": "#abcdef"}


def test_camera_changes_after_a_new_show_leave_the_old_settings_alone(results):
    s, t = results["camera_isolated"]
    assert s == {"ortho": False, "gizmo": False, "background": "#000000"}
    assert t["gizmo"] is True


# ── export ──────────────────────────────────────────────────────────────────

def test_export_shows_and_writes_its_background(results):
    r = results["export_show_write"]
    assert r == {"shown": "#123456", "typed": "#654321", "set": "#0000ff", "control": "#0000ff"}


def test_export_changes_after_a_new_show_leave_the_old_settings_alone(results):
    s, t = results["export_isolated"]
    assert s["background"] == "#000000" and t["background"] == "#222222"


# ── extract ─────────────────────────────────────────────────────────────────

def test_extract_shows_writes_and_clears_its_indices(results):
    r = results["extract_show_write"]
    assert r == {"shown": "0 1", "typed": "C -H", "picked": "4 5", "cleared": "",
                 "control": "", "tokens": []}


def test_extract_changes_after_a_new_show_leave_the_old_settings_alone(results):
    s, t = results["extract_isolated"]
    assert s == {"indices": ""} and t == {"indices": "1"}


def test_extract_takes_elements_only_where_structures_differ_and_shows_the_answer(results):
    """ADR 0067: atom numbers are refused there; the server's answer shows."""
    r = results["extract_dataset_and_result"]
    assert r["variable"] == ["Elements, e.g. C  or  -H", ""]
    assert r["uniform"] == "e.g. 0 1 2  or  C  -H"
    assert r["live"] == "none"
    assert r["made"] == ["Extracted C in 812 of 1000 frames", True, True]
    assert r["refused"] == ["N is not in this dataset.", False, True]


# ── bonds ───────────────────────────────────────────────────────────────────

def test_bonds_show_draws_the_look_and_sends_no_bond_type(results):
    r = results["bonds_show"]
    assert r["controls"] == ["40", "#ff0000", "Fixed", "0-1\n2-3"]
    assert r["calls"] == [["onStyle", 40, "#ff0000", True]]


def test_bonds_write_every_change_into_their_settings(results):
    r = results["bonds_write"]
    assert r["typed"] == {"width": 50, "color": "#00ff00", "bondType": "Fixed", "fixedIndices": [[0, 1], [2, 5]]}
    assert r["toggled"]["fixedIndices"] == [[0, 1], [2, 5], [6, 7]]
    assert r["toggled"]["bondType"] == "Fixed"
    assert r["applied"] == 3


def test_bonds_changes_after_a_new_show_leave_the_old_settings_alone(results):
    s, t = results["bonds_isolated"]
    assert s == {"width": 100, "color": "#404040", "bondType": "Dynamic", "fixedIndices": []}
    assert t["width"] == 60 and t["fixedIndices"] == [[1, 2]] and t["bondType"] == "Fixed"


# ── align ───────────────────────────────────────────────────────────────────

def test_align_shows_its_settings_without_sending_anything(results):
    r = results["align_show"]
    assert r["controls"] == [True, False, False, ""]
    assert r["calls"] == []


def test_align_writes_every_change_into_its_settings(results):
    r = results["align_write"]
    assert r["kabsch"]["kabsch"] is True and r["kabsch"]["heavyOnly"] is False
    assert r["exclusive"]["atomAlign"] is True and r["exclusive"]["kabsch"] is False
    assert r["typed"]["indices"] == [4, 5, 6] and r["typed"]["idxInput"] == "4 5 6"
    assert r["picked"]["indices"] == [1, 2, 3] and r["picked"]["idxInput"] == "1 2 3"


def test_enabling_atom_align_mode_turns_kabsch_off_in_the_settings(results):
    s = results["align_enable_mode"]
    assert s["atomAlign"] is True and s["kabsch"] is False


def test_align_forgets_a_partial_pick_set_when_shown(results):
    s, control = results["align_partial_picks_reset"]
    assert s["indices"] == [] and s["idxInput"] == "" and control == ""


def test_align_changes_after_a_new_show_leave_the_old_settings_alone(results):
    s, t = results["align_isolated"]
    assert s["kabsch"] is False and t["kabsch"] is True



def test_align_is_off_on_a_variable_dataset(results):
    r = results["align_variable"]
    assert r["off"]["kabsch"] is False and r["off"]["atomAlign"] is False
    assert r["rows"] == [True, True] and r["back"] == [False, False]
    assert ["onKabsch", False, True] in r["calls"]

# ── colour by ───────────────────────────────────────────────────────────────

def test_color_by_shows_its_settings_without_sending_anything(results):
    r = results["color_show"]
    assert r["controls"] == ["A metric", "MACE", "plasma"]
    # asking the server for the metric's keep boxes (ADR 0065) changes nothing
    assert [c for c in r["calls"] if c[0] != "boxes"] == []


def test_color_by_writes_every_change_into_its_settings(results):
    r = results["color_write"]
    assert r["source"] == "metric:m.a"
    assert r["s"] == {"source": "displacement", "prediction": "fp1", "colormap": "inferno", "norm": "None"}
    # an error gets the first prediction before its source; picking the one
    # already shown sends it again
    assert [c[0] for c in r["calls"] if c[0] != "boxes"] == [
        "onPredictionChange", "onSourceChange", "onPredictionChange", "onColormapChange", "onSourceChange"]


def test_color_by_keeps_a_metric_source_until_the_catalog_arrives(results):
    r = results["color_catalog_late"]
    assert r["before"] == ["Elements", "metric:m.a"]
    assert r["after"] == ["A metric", "metric:m.a"]


def test_color_by_falls_back_to_elements_and_says_so_when_the_metric_is_gone(results):
    r = results["color_metric_gone"]
    assert r["coloring"] == "Elements" and r["source"] == "element"
    assert "m.a is not on this server" in r["note"]


def test_choosing_a_metric_writes_the_source(results):
    r = results["color_choose_metric"]
    assert r["ok"] is True and r["missing"] is False
    assert r["source"] == "metric:m.a" and r["coloring"] == "A metric"
    assert [c[:2] for c in r["calls"] if c[0] != "boxes"] == [["onSourceChange", "metric:m.a"]]


def test_forces_go_back_to_ground_truth_when_their_prediction_goes(results):
    r = results["color_prediction_helpers"]
    assert r["set"] == ["fp1", "fp1", "MACE"]
    assert r["gone"] == ["fp1", None, "Ground Truth"]


def test_color_by_keeps_the_norm_of_a_metric_in_the_settings(results):
    r = results["color_norm"]
    assert r["shown"] == "log"
    assert r["s"]["norm"] == "None"
    assert r["t"]["norm"] == "log"
    assert r["calls"] == [["onMetricParam", "norm", "None"], ["onMetricParam", "norm", "log"]]


def test_one_forces_entry_replaces_the_two_pass_throughs(results):
    r = results["color_forces"]
    assert "Forces" in r["coloring"]
    assert "Reference Forces" not in r["coloring"] and "Predicted Forces" not in r["coloring"]
    # an old view coloured by the predicted forces opens as Forces of that model
    assert r["old"] == ["Forces", "MACE", ["Ground Truth", "MACE"]]


def test_the_prediction_chooses_whose_forces(results):
    r = results["color_forces"]
    src, pred, calls = r["toTruth"]
    assert src == "metric:ffast.force_reference[atom+structure;length]" and pred is None
    # the dataset's own forces need no model: the source goes first
    assert [c for c in calls if c[0] != "boxes"] == [
        ["onSourceChange", src], ["onPredictionChange", None]]
    src, pred, calls = r["toModel"]
    assert src == "metric:ffast.force_prediction[atom+structure;length]" and pred == "fp1"
    assert [c for c in calls if c[0] != "boxes"] == [
        ["onPredictionChange", "fp1"], ["onSourceChange", src]]


def test_the_forces_colour_bar_reads_magnitude(results):
    r = results["color_forces"]
    assert r["bar"].startswith("Forces · Magnitude")
    # an old view coloured by the reference forces opens on Ground Truth
    assert r["truth"] == "Ground Truth"


def test_the_prediction_row_offers_what_the_colouring_reads(results):
    r = results["color_prediction_rows"]
    # hidden rows wait with a reason, which the sidebar search shows
    assert r["none"] == [True, "Choose a metric colouring first"]   # elements
    assert r["waiting"] == [True, "Load a prediction first"]
    assert r["error"] == [False, ["MACE", "NequIP"], "MACE"]  # an error: no Ground Truth
    assert r["reference"] == [True, "This colouring reads the dataset alone"]
    # choosing an error again gives it the prediction it shows, before its source
    assert [c[:2] for c in r["picked"]] == [["onPredictionChange", "fp1"], ["onSourceChange", "metric:m.a"]]


def test_color_by_changes_after_a_new_show_leave_the_old_settings_alone(results):
    s, t = results["color_isolated"]
    assert s["colormap"] == "viridis" and t["colormap"] == "bwr"


# ── force vectors (to_fix_v2) ───────────────────────────────────────────────

def test_arming_force_turns_the_arrows_on_once(results):
    r = results["force_arm"]
    assert r == {"shown0": False, "shown1": True, "applies": [True]}


def test_force_picks_are_kept_and_turn_the_filter_on(results):
    r = results["force_picks"]
    assert r == {"picked": [3, 5], "filter": True, "atoms": [3, 5]}


def test_force_picks_belong_to_their_frame_and_the_shared_set_is_kept(results):
    r = results["force_frames"]
    assert r == {"onFour": [1, 2], "onFive": [], "shared": [7], "byFrame": {"4": [1, 2]},
                 "afterClear": {}, "pickedAfterClear": []}

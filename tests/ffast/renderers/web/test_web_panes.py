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
  const mk = (mod, name, cbs = {}) => {
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

"""Unit tests for ``view_settings.js`` and the settings ``MainViews`` gives
each view (ADR 0060 step 4a)."""

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

CASES = {
    "defaults": "vs.defaultSettings()",
    "defaults_fresh": "vs.defaultSettings() !== vs.defaultSettings()",
    "copy_is_deep": "(() => { const a = vs.defaultSettings(); const b = vs.copySettings(a);"
                    " b.bonds.fixedIndices.push([0, 1]); b.forces.atomIndices.push(3); b.colorBy.source = 'displacement';"
                    " return [a.bonds.fixedIndices, a.forces.atomIndices, a.colorBy.source, b.bonds.fixedIndices]; })()",
    "subset_inherits": "(() => { const meta = {P: {}, S: {parent: 'P', parent_frames: [0, 2]}, A: {parent: 'P', parent_frames: null}};"
                       " const m = new vw.MainViews({renderers: null, parentOf: (fp) => meta[fp]?.parent && meta[fp].parent_frames ? meta[fp].parent : null});"
                       " const p = m.ensure('P'); p.settings.display.atomSize = 2; p.settings.colorBy.source = 'displacement';"
                       " const s = m.ensure('S'); const a = m.ensure('A'); s.settings.display.atomSize = 3;"
                       " return [s.settings === p.settings, p.settings.display.atomSize, s.settings.colorBy.source, a.settings.colorBy.source, a.settings.display.atomSize]; })()",
    "subset_no_parent_view": "(() => { const m = new vw.MainViews({renderers: null, parentOf: () => 'P'});"
                             " return m.ensure('S').settings.colorBy.source; })()",
    "start_default": "vs.startSettings(null, {modelFp: 'M', catalog: []})",
    "start_look": "vs.startSettings({colour_by: 'force_error', colormap: 'plasma', atom_size: 2, bond_width: 50,"
                  " bond_colour: '#ff0000', force_arrows: true, force_length: 20, force_normalised: false},"
                  " {modelFp: 'M', catalog: [{id: 'force_error', shape: 'N_atoms'}]})",
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
        f"  const vw = await import('{origin}/views.js');\n"
        f"  const vs = await import('{origin}/view_settings.js');\n"
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
            await page.goto(f"{origin}/events.js")
            await page.set_content("<!doctype html><title>settings</title>")
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


def test_defaults_are_todays_defaults(results):
    assert results["defaults"] == {
        "colorBy": {"source": "element", "prediction": None, "colormap": "force_error", "norm": "None",
                    "vmin": None, "vmax": None},
        "camera": {"ortho": False, "gizmo": False, "background": "#000000"},
        "display": {"atomSize": 1, "hide": "", "highlight": "", "unitCell": True},
        "bonds": {"width": 100, "color": "#404040", "bondType": "Dynamic", "fixedIndices": []},
        "extract": {"indices": ""},
        "export": {"background": "#000000"},
        "align": {"kabsch": False, "heavyOnly": True, "atomAlign": False, "idxInput": "", "indices": []},
        "forces": {"show": False, "modelKey": None, "length": 10, "normalised": True,
                   "onlyForces": False, "filterEnabled": False, "atomIndices": []},
        "view": {"originCenterOfMass": True, "videoFPS": 30, "videoSkipFrames": 0},
    }


def test_every_default_is_a_fresh_object(results):
    assert results["defaults_fresh"] is True


def test_a_copy_is_deep(results):
    original_bonds, original_atoms, original_source, copy_bonds = results["copy_is_deep"]
    assert original_bonds == [] and original_atoms == [] and original_source == "element"
    assert copy_bonds == [[0, 1]]


def test_a_frame_subset_starts_from_its_parents_settings_an_atom_subset_does_not(results):
    shared, parent_size, subset_source, atom_subset_source, atom_subset_size = results["subset_inherits"]
    assert shared is False
    assert parent_size == 2            # the subset's own change stays its own
    assert subset_source == "displacement"
    assert atom_subset_source == "element" and atom_subset_size == 1


def test_a_subset_whose_parent_has_no_view_starts_from_the_defaults(results):
    assert results["subset_no_parent_view"] == "element"


def test_an_unset_start_is_the_defaults(results):
    assert results["start_default"]["colorBy"]["source"] == "element"
    assert results["start_default"]["bonds"]["width"] == 100
    assert results["start_default"]["forces"]["show"] is False


def test_a_start_look_fills_every_section_it_names(results):
    s = results["start_look"]
    assert s["colorBy"] == {"source": "metric:force_error", "prediction": "M", "colormap": "plasma", "norm": "None",
                          "vmin": None, "vmax": None}
    assert s["display"]["atomSize"] == 2
    assert s["bonds"]["width"] == 50 and s["bonds"]["color"] == "#ff0000"
    assert s["forces"]["show"] is True and s["forces"]["modelKey"] == "M"
    assert s["forces"]["length"] == 20 and s["forces"]["normalised"] is False

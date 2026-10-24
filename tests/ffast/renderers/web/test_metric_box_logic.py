"""The keep boxes' decisions in the browser (ADR 0065): which box can be
ticked, and the setting a click gives. Pure functions of ``metric_logic.js``,
evaluated in one browser launch as in test_metric_logic.py; the allowed
settings come from ``ffast.metrics.boxes``, as the server sends them."""
from __future__ import annotations

import asyncio
import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

from ffast.metrics import dims
from ffast.metrics.boxes import settings_for

pytestmark = pytest.mark.integration

STATIC_DIR = (
    Path(__file__).resolve().parents[4] / "ffast" / "renderers" / "web" / "static"
)

FORCE = json.dumps([s.to_dict() for s in settings_for((dims.N_atoms, dims.xyz))])
ENERGY = json.dumps([s.to_dict() for s in settings_for((dims.N_frames,))])
BASE = "{keep: ['atom', 'structure', 'xyz'], combine: null, statistic: null}"
PER_FRAME = "{keep: ['structure'], combine: 'component', statistic: 'mae'}"
HINTS = "{atom: 'this metric is already averaged per frame', xyz: 'this metric has no x/y/z'}"

CASES = {
    # unticking x/y/z on a base: Combine and Statistic get their first choices
    "untick_xyz": f"ml.toggleBox({BASE}, 'xyz', {FORCE})",
    # unticking atom from atom + structure: averaged per frame
    "untick_atom": f"ml.toggleBox({{keep: ['atom', 'structure'], combine: 'length', statistic: null}}, 'atom', {FORCE})",
    # Combine and Statistic carry over when they still apply
    "per_frame_to_element": f"ml.toggleBox({{keep: ['structure'], combine: 'length', statistic: 'rmse'}}, 'element', {FORCE})",
    "element_off_with_atom": f"ml.boxAvailable({{keep: ['atom', 'structure'], combine: 'length', statistic: null}}, 'element', {FORCE})",
    "element_hint_with_atom": "ml.boxHint({keep: ['atom'], combine: 'length', statistic: 'mae'}, 'element', {})",
    "energy_atom": f"ml.boxAvailable({{keep: ['structure'], combine: null, statistic: null}}, 'atom', {ENERGY})",
    "energy_atom_hint": f"ml.boxHint({{keep: ['structure'], combine: null, statistic: null}}, 'atom', {HINTS})",
    "energy_structure_off": f"ml.toggleBox({{keep: ['structure'], combine: null, statistic: null}}, 'structure', {ENERGY})",
    "to_length": f"ml.chooseSetting({PER_FRAME}, 'combine', 'length', {FORCE})",
    # Mean (signed) has no length: picking it on a length moves to per component
    "mean_on_length": f"ml.chooseSetting({{keep: ['structure'], combine: 'length', statistic: 'rmse'}}, 'statistic', 'mean', {FORCE})",
    "statistic_choices_on_length": f"ml.statisticChoices({{keep: ['structure'], combine: 'length', statistic: 'rmse'}}, {FORCE})",
    "no_statistic_on_base": f"ml.statisticChoices({BASE}, {FORCE})",
    "combine_choices_on_base": f"ml.combineChoices({BASE}, {FORCE})",
    "same": f"ml.sameSetting({PER_FRAME}, {{keep: ['structure'], combine: 'component', statistic: 'mae'}})",
    "colour_settings": f"ml.colourSettings({FORCE})",
    "first_colour": f"ml.firstColourSetting({FORCE})",
    "box_base": "ml.parseBoxId('ffast.force_difference[atom;length;mae]')?.base",
    "not_a_box": "ml.parseBoxId('ffast.force_difference')",
    # a setting a tab registered is not listed; one saved by Create metric is
    "listed": (
        "(() => { const g = ml.groupMetrics(["
        "{id: 'ffast.force_difference', label: 'Force Difference', origin: 'built-in'},"
        "{id: 'ffast.force_difference[dataset;component;mae]', label: 'a', origin: 'built-in'},"
        "{id: 'ffast.force_difference[structure;length;rmse]', label: 'Kept', origin: 'user'}],"
        "[{id: 'ffast.force_difference[structure;length;rmse]', label: 'Kept', status: 'ok'}]);"
        " return Object.fromEntries(Object.entries(g).map(([k, v]) => [k, v.map((m) => m.id)])); })()"
    ),
}


@pytest.fixture(scope="module")
def results():
    playwright_api = pytest.importorskip("playwright.async_api")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(STATIC_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    script = (
        "async () => {\n"
        f"  const ml = await import('{origin}/metric_logic.js');\n"
        "  const out = {};\n"
        + "".join(f"  out[{json.dumps(n)}] = {e};\n" for n, e in CASES.items())
        + "  return out;\n}"
    )

    async def _collect():
        async with playwright_api.async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
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


def test_unticking_xyz_picks_per_component_and_no_statistic_yet(results):
    # per atom, per component: the mean of |x|, |y|, |z| needs a Statistic
    assert results["untick_xyz"] == {"keep": ["atom", "structure"], "combine": "component", "statistic": "mae"}


def test_unticking_atom_averages_per_frame_and_asks_for_a_statistic(results):
    assert results["untick_atom"] == {"keep": ["structure"], "combine": "length", "statistic": "mae"}


def test_combine_and_statistic_carry_over(results):
    assert results["per_frame_to_element"] == {
        "keep": ["structure", "element"], "combine": "length", "statistic": "rmse"}


def test_element_is_greyed_while_atom_is_ticked_and_says_so(results):
    assert results["element_off_with_atom"] is False
    assert results["element_hint_with_atom"] == "element is greyed when atom is ticked"


def test_a_per_frame_metric_greys_atom_with_the_servers_hint(results):
    assert results["energy_atom"] is False
    assert results["energy_atom_hint"] == "this metric is already averaged per frame"


def test_unticking_structure_on_energy_gives_one_number(results):
    assert results["energy_structure_off"] == {"keep": [], "combine": None, "statistic": "mae"}


def test_choosing_combine_and_statistic(results):
    assert results["to_length"] == {"keep": ["structure"], "combine": "length", "statistic": "mae"}
    assert results["mean_on_length"] == {"keep": ["structure"], "combine": "component", "statistic": "mean"}


def test_the_choices_offered(results):
    assert results["statistic_choices_on_length"] == ["mae", "rmse", "max", "mean"]
    assert results["no_statistic_on_base"] == []
    assert results["combine_choices_on_base"] == []


def test_two_settings_compare_by_value(results):
    assert results["same"] is True


def test_colouring_takes_only_settings_with_a_value_per_atom(results):
    keeps = {tuple(s["keep"]) for s in results["colour_settings"]}
    assert ("atom", "structure") in keeps and ("atom",) in keeps and ("element",) in keeps
    assert ("structure", "element") in keeps        # the frame's row of the table
    assert not any("xyz" in k or k in {("structure",), ()} for k in keeps)
    assert results["first_colour"] == {"keep": ["atom", "structure"], "combine": "component", "statistic": "mae"}


def test_a_box_id_names_its_base(results):
    assert results["box_base"] == "ffast.force_difference"
    assert results["not_a_box"] is None


def test_the_list_shows_a_box_setting_only_once_saved(results):
    assert results["listed"] == {
        "built-in": ["ffast.force_difference"], "project": [],
        "user": ["ffast.force_difference[structure;length;rmse]"]}

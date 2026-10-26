"""The 3D colour bar's geometry (to_fix 17), as pure functions.

``colorbar_layout.js`` has no DOM, so each rule is evaluated here in one
browser launch, the way test_metric_logic.py does: a static file server gives
an http origin for the ES module, and every case runs in one batch.
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

STATIC_DIR = Path(__file__).resolve().parents[4] / "ffast" / "renderers" / "web" / "static"

SIZES = "{bar: {w: 14, h: 200}, name: {w: 80, h: 16}, max: {w: 30, h: 14}, min: {w: 40, h: 14}}"

CASES = {
    "vertical": f"cl.preset('vertical', {{x: 10, y: 20}}, {SIZES})",
    "horizontal": f"cl.preset('horizontal', {{x: 10, y: 20}}, {SIZES})",
    "turns": ("[cl.nextRotation('bar', 0), cl.nextRotation('bar', 90), cl.nextRotation('bar', -90),"
              " cl.nextRotation('name', 0), cl.nextRotation('name', -90), cl.nextRotation('max', 7)]"),
    "turned": "[cl.turned({w: 3, h: 5}, 0), cl.turned({w: 3, h: 5}, 90), cl.turned({w: 3, h: 5}, -90)]",
    # a resized panel keeps the pieces where they were, as fractions
    "fractions": (
        "(() => { const layout = cl.cleanLayout({pieces: {bar: {x: 0.1, y: 0.2, rot: 0}, name: {x: 0.5, y: 0.5, rot: -90},"
        " max: {x: 0.2, y: 0.2, rot: 0}, min: {x: 0.2, y: 0.6, rot: 0}}});"
        " const t = {name: {w: 80, h: 16}, max: {w: 30, h: 14}, min: {w: 40, h: 14}};"
        " const a = cl.boxesFor(layout, {w: 400, h: 300}, t); const b = cl.boxesFor(layout, {w: 800, h: 600}, t);"
        " return [[a.bar.x, a.bar.y, a.bar.w, a.bar.h], [b.bar.x, b.bar.y, b.bar.w, b.bar.h], [a.name.w, a.name.h],"
        "   cl.toFractions({bar: {x: 80, y: 120, rot: 90}}, {w: 800, h: 600})]; })()"),
    "default_layout": (
        "(() => { const t = {name: {w: 80, h: 16}, max: {w: 30, h: 14}, min: {w: 40, h: 14}};"
        " const b = cl.boxesFor(cl.defaultLayout(), {w: 1000, h: 500}, t);"
        " return Object.fromEntries(Object.entries(b).map(([k, v]) => [k, v.rot])); })()"),
    "inside": "[cl.keepInside({x: 390, y: -5, w: 20, h: 10}, {w: 400, h: 300}), cl.keepInside({x: 5, y: 5, w: 500, h: 10}, {w: 400, h: 300})]",
    "snap": ("[cl.snapDrag({x: 0, y: 0, w: 10, h: 10}, [{x: 100, y: 50, w: 20, h: 20}], 93, 3),"
             " cl.snapDrag({x: 0, y: 0, w: 10, h: 10}, [{x: 100, y: 50, w: 20, h: 20}], 40, 30),"
             " cl.snapDrag({x: 0, y: 0, w: 10, h: 10}, [{x: 100, y: 100, w: 20, h: 20}], 105, 0)]"),
    "rotate_one": "cl.rotateOne('bar', {x: 10, y: 10, w: 14, h: 200, rot: 0}, {w: 14, h: 200})",
    # a piece above the block's centre goes to its right
    "rotate_block": ("cl.rotateBlock({bar: {x: 0, y: 0, w: 20, h: 20, rot: 0}, max: {x: 0, y: 40, w: 20, h: 20, rot: 0}},"
                     " {bar: {w: 20, h: 20}, max: {w: 20, h: 20}})"),
    "select": ("(() => { const groups = [['bar', 'min']]; return ["
               " cl.press([], 'bar', false, groups), cl.press([], 'name', false, groups),"
               " cl.press(['name'], 'max', true, groups), cl.press(['name', 'max'], 'max', true, groups),"
               " cl.press(['name', 'max'], 'max', false, groups),"
               " cl.peers('min', groups, []), cl.peers('name', groups, ['name', 'max']), cl.peers('name', groups, []),"
               " cl.groupSelection(groups, ['min', 'name']), cl.groupSelection(groups, ['name']),"
               " cl.ungroup(groups, 'min')]; })()"),
    "clean": ("[cl.cleanLayout(null), cl.cleanLayout({orientation: 'sideways', scale: 99, groups: [['bar'], ['bar', 'zz'], ['min', 'max', 'max']],"
              " pieces: {bar: {x: 0.1, y: 0.1, rot: 45}}})]"),
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
        f"  const cl = await import('{origin}/colorbar_layout.js');\n"
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


def test_the_vertical_preset_puts_the_values_beside_the_bar_and_turns_the_name(results):
    r = results["vertical"]
    assert r["bar"] == {"x": 10, "y": 20, "rot": 0}
    assert r["max"] == {"x": 30, "y": 20, "rot": 0}                # top of the bar, 6 px beside it
    assert r["min"] == {"x": 30, "y": 206, "rot": 0}               # bottom of the bar
    assert r["name"] == {"x": 76, "y": 80, "rot": -90}             # beyond the values, centred


def test_the_horizontal_preset_puts_the_values_under_the_ends_and_the_name_below(results):
    r = results["horizontal"]
    assert r["bar"] == {"x": 10, "y": 20, "rot": 90}
    assert r["min"] == {"x": 10, "y": 40, "rot": 0}
    assert r["max"] == {"x": 180, "y": 40, "rot": 0}               # right end of the 200 px bar
    assert r["name"] == {"x": 70, "y": 60, "rot": 0}


def test_pieces_turn_like_the_desktops(results):
    # the name reads upright or bottom to top only; the others go 0, 90, -90
    assert results["turns"] == [90, -90, 0, -90, 0, 0]
    assert results["turned"] == [{"w": 3, "h": 5}, {"w": 5, "h": 3}, {"w": 5, "h": 3}]


def test_places_are_fractions_of_the_panel(results):
    small, big, name, fractions = results["fractions"]
    assert small[:2] == [40, 60] and big[:2] == [80, 120]
    assert small[3] == 120 and big[3] == 240         # the bar's length follows the panel
    assert name == [16, 80]                          # turned -90: width and height swap
    assert fractions == {"bar": {"x": 0.1, "y": 0.2, "rot": 90}}


def test_nothing_saved_gives_the_vertical_preset(results):
    assert results["default_layout"] == {"bar": 0, "name": -90, "max": 0, "min": 0}


def test_a_piece_stays_inside_a_panel_made_smaller(results):
    a, b = results["inside"]
    assert (a["x"], a["y"]) == (380, 0)
    assert (b["x"], b["y"]) == (0, 5)


def test_a_drag_snaps_to_another_pieces_edges_and_centre(results):
    near, far, centre = results["snap"]
    # the nearest line wins: centre 98 -> the other's left edge 100; 13 is far from 50
    assert near == {"dx": 95, "dy": 3, "vx": 100, "hy": None}
    assert far == {"dx": 40, "dy": 30, "vx": None, "hy": None}
    # centre 110 lines up with the other's centre
    assert centre["dx"] == 105 and centre["vx"] == 110


def test_one_piece_turns_about_its_own_centre(results):
    assert results["rotate_one"] == {"x": -83, "y": 103, "w": 200, "h": 14, "rot": 90}


def test_a_group_turns_as_one_block(results):
    r = results["rotate_block"]
    # block centre (10, 30): bar was above it, so a clockwise quarter turn
    # takes it to the right; max was below, so it goes left
    assert r["bar"] == {"x": 20, "y": 20, "w": 20, "h": 20, "rot": 90}
    assert r["max"] == {"x": -20, "y": 20, "w": 20, "h": 20, "rot": 90}


def test_selection_and_groups_follow_the_desktop(results):
    (group_press, single, add, remove, keep, grouped, selection, alone,
     regroup, too_few, ungrouped) = results["select"]
    assert group_press == ["bar", "min"]         # a press selects the whole group
    assert single == ["name"]
    assert add == ["name", "max"] and remove == ["name"]
    assert keep == ["name", "max"]               # a press inside the selection keeps it, to drag it
    assert grouped == ["bar", "min"] and selection == ["name", "max"] and alone == ["name"]
    assert regroup == [["min", "name"]]          # a piece is in one group only
    assert too_few == [["bar", "min"]]
    assert ungrouped == []


def test_a_stored_layout_from_elsewhere_cannot_break_the_page(results):
    empty, odd = results["clean"]
    assert empty["orientation"] == "vertical" and empty["pieces"] is None
    assert odd["orientation"] == "vertical" and odd["scale"] == 1
    assert odd["pieces"] is None                 # incomplete or a 45° turn: the default
    assert odd["groups"] == [["min", "max"]]

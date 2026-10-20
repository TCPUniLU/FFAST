"""Unit tests for ``views.js``: ``View`` and ``MainViews`` (ADR 0060 step 1).

Same mechanism as ``test_web_pure_helpers.py``: a static file server, a blank
page, ``import()`` of the module, every case evaluated in one batch.
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

CASES = {
    "ensure_twice_same": "(() => { const m = new vw.MainViews({renderers: R}); return m.ensure('A') === m.ensure('A'); })()",
    "ensure_names": "(() => { const m = new vw.MainViews({renderers: R}); return [m.ensure('A').viewId, m.ensure('B').viewId]; })()",
    "ensure_dataset": "(() => { const m = new vw.MainViews({renderers: R}); return m.ensure('A').datasetFp; })()",
    "get_unknown": "(() => { const m = new vw.MainViews({renderers: R}); return m.get('A') === undefined; })()",
    "point_current": "(() => { const m = new vw.MainViews({renderers: R}); const a = m.ensure('A'); const b = m.ensure('B');"
                     " m.point(b); return [m.current === b, m.current === a]; })()",
    "current_starts_null": "new vw.MainViews({renderers: R}).current === null",
    "clear_empties": "(() => { const m = new vw.MainViews({renderers: R}); m.point(m.ensure('A')); m.clear();"
                     " return [m.current === null, m.get('A') === undefined, m.byViewId('view-0')]; })()",
    "clear_keeps_counter": "(() => { const m = new vw.MainViews({renderers: R}); m.ensure('A'); m.clear(); return m.ensure('A').viewId; })()",
    "command_unopened": "new vw.View({viewId: 'view-0'}).command({a: 1})",
    "command_versions": "(() => { const v = new vw.View({viewId: 'view-0'}); v.openedPair = 'A|';"
                        " return [v.command({a: 1}), v.command({b: 2}), v.version]; })()",
    "by_view_id": "(() => { const m = new vw.MainViews({renderers: R}); m.ensure('A'); const b = m.ensure('B');"
                  " return [m.byViewId('view-1') === b, m.byViewId('ind-3'), m.byViewId('nope')]; })()",
    "drop_returns_view": "(() => { const m = new vw.MainViews({renderers: R}); const a = m.ensure('A');"
                         " const d = m.drop('A'); return [d === a, m.byViewId('view-0'), m.get('A') === undefined, m.ensure('A').viewId]; })()",
    "drop_unknown": "new vw.MainViews({renderers: R}).drop('A') === null",
    "drop_current_unpoints": "(() => { const m = new vw.MainViews({renderers: R}); m.point(m.ensure('A')); m.drop('A'); return m.current === null; })()",
    "shared_renderers": "(() => { const m = new vw.MainViews({renderers: R}); return [m.ensure('A').renderers === R, m.ensure('B').renderers === R]; })()",
    "is_main": "[new vw.View({viewId: 'view-4'}).isMain, new vw.View({viewId: 'ind-4'}).isMain]",
    "is_open": "(() => { const v = new vw.View({viewId: 'view-0'}); const a = v.isOpen; v.openedPair = 'A|'; return [a, v.isOpen]; })()",
    "view_defaults": "(() => { const v = new vw.View({viewId: 'view-0'}); return [v.datasetFp, v.modelFp, v.version, v.frame, v.openedPair, v.framed, v.frameOnSnapshot, v.settings, v.renderers]; })()",
    "independent_is_view": "(() => { const v = new v3.IndependentView('T#0', {}, 'ind-1', () => {});"
                           " v.openedPair = 'A|'; return [v instanceof vw.View, v.frame, v.isMain, v.isOpen, v.command({x: 1}).view_id]; })()",
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
        f"  const v3 = await import('{origin}/views3d.js');\n"
        "  const R = {tag: 'renderers'};\n"
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
            await page.set_content(
                "<!doctype html><title>views</title>"
                '<script type="importmap">{"imports": {"three": '
                f'"{origin}/vendor/three/three.module.min.js"}}}}</script>'
            )
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


def test_ensure_twice_gives_the_same_view(results):
    assert results["ensure_twice_same"] is True


def test_ensure_names_views_in_order_of_first_use(results):
    assert results["ensure_names"] == ["view-0", "view-1"]


def test_ensure_records_the_dataset(results):
    assert results["ensure_dataset"] == "A"


def test_get_of_an_unknown_dataset_is_undefined(results):
    assert results["get_unknown"] is True


def test_point_makes_a_view_current(results):
    assert results["point_current"] == [True, False]


def test_current_starts_null(results):
    assert results["current_starts_null"] is True


def test_clear_forgets_every_view(results):
    assert results["clear_empties"] == [True, True, None]


def test_clear_does_not_reuse_view_numbers(results):
    assert results["clear_keeps_counter"] == "view-1"


def test_command_of_a_view_never_opened_is_null(results):
    assert results["command_unopened"] is None


def test_command_stamps_and_advances_the_version(results):
    first, second, version = results["command_versions"]
    assert first == {"view_id": "view-0", "view_version": 0, "a": 1}
    assert second == {"view_id": "view-0", "view_version": 1, "b": 2}
    assert version == 2


def test_by_view_id_finds_main_views_only(results):
    assert results["by_view_id"] == [True, None, None]


def test_drop_returns_the_view_and_a_return_gets_a_new_server_view(results):
    same, gone, undefined, again = results["drop_returns_view"]
    assert same is True and gone is None and undefined is True
    assert again == "view-1"


def test_drop_of_an_unknown_dataset_is_null(results):
    assert results["drop_unknown"] is True


def test_dropping_the_current_view_leaves_none_current(results):
    assert results["drop_current_unpoints"] is True


def test_every_main_view_shares_the_one_renderers(results):
    assert results["shared_renderers"] == [True, True]


def test_is_main_reads_the_view_id(results):
    assert results["is_main"] == [True, False]


def test_is_open_follows_opened_pair(results):
    assert results["is_open"] == [False, True]


def test_a_new_view_is_empty(results):
    assert results["view_defaults"] == [None, None, 0, 0, "", False, None, None, None]


def test_an_independent_view_is_a_view(results):
    is_view, frame, is_main, is_open, view_id = results["independent_is_view"]
    assert is_view is True and frame is None and is_main is False and is_open is True
    assert view_id == "ind-1"

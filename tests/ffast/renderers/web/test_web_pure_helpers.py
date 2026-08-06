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

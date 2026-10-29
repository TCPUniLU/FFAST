"""Unit test for the browser's ``MetricClient`` (ADR 0061 decision 1).

Same zero-build method as ``test_web_pure_helpers.py``: a static file server, a
blank page, ``import()`` of the module inside ``page.evaluate``. The connection
is a fake: ``send(event, kwargs)`` records, ``on(event, fn)`` stores the handler,
and the test plays the server by calling the stored METRIC_RESULT handler.
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

STATIC_DIR = (
    Path(__file__).resolve().parents[4] / "ffast" / "renderers" / "web" / "static"
)

SCRIPT = """
async (origin) => {
  const { MetricClient } = await import(`${origin}/metrics.js`);
  const make = () => {
    const sent = [], handlers = {};
    const conn = { send: (event, kw) => sent.push([event, kw]), on: (e, fn) => { handlers[e] = fn; } };
    const client = new MetricClient(conn);
    const reply = (rid, metric) => handlers.METRIC_RESULT({ ok: false }, [rid, metric]);
    return { sent, client, reply };
  };
  const PENDING = Symbol('pending');
  const state = (p) => Promise.race([p, new Promise((r) => setTimeout(() => r(PENDING), 0))]);
  const out = {};

  { // ids are unique, and `key` is not on the wire
    const { sent, client } = make();
    client.request('a.x', { datasetFp: 'd1' });
    client.request('a.y', { datasetFp: 'd1' });
    out.ids = sent.map(([, kw]) => kw.request_id);
    out.keys = sent.map(([, kw]) => 'key' in kw);
    out.events = sent.map(([e]) => e);
  }
  { // two identical calls send one message and both resolve on its reply
    const { sent, client, reply } = make();
    const p1 = client.request('a.x', { params: { b: 2, a: 1 }, datasetFp: 'd1' });
    const p2 = client.request('a.x', { params: { a: 1, b: 2 }, datasetFp: 'd1' });
    out.sentIdentical = sent.length;
    reply(sent[0][1].request_id, 'a.x');
    out.identicalResolved = [await p1, await p2];
  }
  { // after forgetInFlight() an identical call sends a new message
    const { sent, client, reply } = make();
    const p1 = client.request('a.x', { datasetFp: 'd1' });
    client.forgetInFlight();
    const p2 = client.request('a.x', { datasetFp: 'd1' });
    out.sentAfterForget = sent.length;
    // the first reply still resolves the first request, not the second
    reply(sent[0][1].request_id, 'a.x');
    out.firstResolved = (await state(p1)) === null;
    out.secondStillWaiting = (await state(p2)) === PENDING;
    reply(sent[1][1].request_id, 'a.x');
    out.secondResolved = (await state(p2)) === null;
  }
  { // a reply resolves only its own request, in any order
    const { sent, client, reply } = make();
    const p1 = client.request('a.x', { datasetFp: 'd1' });
    const p2 = client.request('a.y', { datasetFp: 'd1' });
    reply(sent[1][1].request_id, 'a.y');
    out.laterFirst = [(await state(p1)) === PENDING, (await state(p2)) === null];
  }
  { // the label is free again once the request settled
    const { sent, client, reply } = make();
    const p1 = client.request('a.x', { datasetFp: 'd1' });
    reply(sent[0][1].request_id, 'a.x');
    await p1;
    client.request('a.x', { datasetFp: 'd1' });
    out.sentAfterSettled = sent.length;
  }
  return out;
}
"""


@pytest.fixture(scope="module")
def results():
    playwright_api = pytest.importorskip("playwright.async_api")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(STATIC_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"

    async def _collect():
        async with playwright_api.async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            await page.goto(f"{origin}/events.js")
            await page.set_content("<!doctype html><title>metric client</title>")
            try:
                out = await page.evaluate(SCRIPT, origin)
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


def test_each_request_gets_its_own_id_and_no_key(results):
    ids = results["ids"]
    assert len(ids) == 2 and ids[0] != ids[1] and all(ids)
    assert results["keys"] == [False, False]
    assert results["events"] == ["REQUEST_METRIC", "REQUEST_METRIC"]


def test_identical_requests_in_flight_send_one_message(results):
    assert results["sentIdentical"] == 1
    assert results["identicalResolved"] == [None, None]


def test_after_forget_in_flight_an_identical_request_is_sent_again(results):
    assert results["sentAfterForget"] == 2
    assert results["firstResolved"] and results["secondStillWaiting"] and results["secondResolved"]


def test_each_reply_resolves_its_own_request(results):
    assert results["laterFirst"] == [True, True]


def test_a_settled_request_no_longer_merges_later_ones(results):
    assert results["sentAfterSettled"] == 2

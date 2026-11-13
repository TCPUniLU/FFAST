"""The browser matches every reply to its request (ADR 0097).

``RequestTable`` and ``FFastConnection`` run in a blank page (zero-build, as in
``test_metric_client.py``). The socket is a fake ``WebSocket`` that answers the
handshake; the test plays the server by delivering encoded messages.
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
  const { msgpack } = await import(`${origin}/msgpack.js`);
  const { FFastConnection } = await import(`${origin}/connection.js`);
  const { RequestTable } = await import(`${origin}/requests.js`);
  const PENDING = Symbol('pending');
  // Settle handlers go on at once, as a caller's await would: a rejection
  // nobody has picked up yet is a page error.
  const track = (p) => p.then((v) => ({ ok: v }), (e) => ({ err: e.message, lost: !!e.connectionLost }));
  const state = async (p) => {
    const r = await Promise.race([p, new Promise((r) => setTimeout(() => r(PENDING), 0))]);
    return r === PENDING ? 'pending' : r;
  };

  class FakeWS {
    static OPEN = 1;
    static last = null;
    constructor(url) {
      this.url = url; this.readyState = 0; this.sent = []; FakeWS.last = this;
      setTimeout(() => { this.readyState = 1; this.onopen?.(); }, 0);
    }
    send(data) {
      if (data === 'ping') { this._deliver('pong'); return; }
      const msg = msgpack.decode(data);
      this.sent.push(msg);
      if (msg.event === 'HELLO')
        this.deliver({ event: 'HELLO_ACK', args: [], kwargs: { role: 'CONTROLLING' } });
    }
    _deliver(data) { setTimeout(() => this.onmessage?.({ data }), 0); }
    deliver(msg) {
      const bytes = msgpack.encode(msg);
      this._deliver(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
    }
    close() { this.readyState = 3; setTimeout(() => this.onclose?.({}), 0); }
    drop() { this.readyState = 3; this.onclose?.({}); }
  }
  globalThis.WebSocket = FakeWS;
  const settle = () => new Promise((r) => setTimeout(r, 5));

  const open = async () => {
    const conn = new FFastConnection('ws://fake', null);
    await conn.connect();
    const ws = FakeWS.last;
    ws.sent.length = 0;
    return { conn, ws };
  };
  const out = {};

  { // every message is numbered, numbers are unique
    const { conn, ws } = await open();
    conn.send('A', {}); conn.send('B', {});
    out.ids = ws.sent.map((m) => m.id);
    out.role = conn.role;
  }
  { // the final reply resolves its own request, not another's
    const { conn, ws } = await open();
    const p1 = track(conn.request('SAVE_TAB', { name: 'x' }));
    const p2 = track(conn.request('SAVE_TAB', { name: 'y' }));
    const [id1, id2] = ws.sent.map((m) => m.id);
    ws.deliver({ event: 'TAB_SAVED', args: [], kwargs: { name: 'y' }, reply_to: id2, final: true });
    await settle();
    out.secondFirst = [await state(p1), await state(p2)];
    ws.deliver({ event: 'TAB_SAVED', args: [], kwargs: { name: 'x' }, reply_to: id1, final: true });
    await settle();
    out.firstLater = await state(p1);
    out.waitingAfter = conn.waiting;
  }
  { // progress goes to onProgress; the request stays open until final
    const { conn, ws } = await open();
    const seen = [];
    const p = track(conn.request('PROBE', {}, [], { onProgress: (r) => seen.push(r.kwargs.n) }));
    const id = ws.sent[0].id;
    ws.deliver({ event: 'P', args: [], kwargs: { n: 1 }, reply_to: id, final: false });
    ws.deliver({ event: 'P', args: [], kwargs: { n: 2 }, reply_to: id, final: false });
    await settle();
    out.progressPending = await state(p);
    ws.deliver({ event: 'P', args: [], kwargs: { n: 3 }, reply_to: id, final: true });
    await settle();
    out.progress = seen;
    out.progressDone = await state(p);
  }
  { // an error rejects with its text
    const { conn, ws } = await open();
    const p = track(conn.request('SAVE_TAB', {}));
    ws.deliver({ event: 'TAB_SAVED', args: [], kwargs: {}, reply_to: ws.sent[0].id,
                 final: true, error: 'disk full' });
    await settle();
    out.error = await state(p);
  }
  { // a reply nobody waits for, and a plain message, go to the event handler
    const { conn, ws } = await open();
    const heard = [];
    conn.on('TAB_SAVED', (kw) => heard.push(kw.name));
    ws.deliver({ event: 'TAB_SAVED', args: [], kwargs: { name: 'other' }, reply_to: 999, final: true });
    ws.deliver({ event: 'TAB_SAVED', args: [], kwargs: { name: 'plain' } });
    await settle();
    out.fallThrough = heard;
  }
  { // a dropped socket fails every waiting request and tells listeners
    const { conn, ws } = await open();
    let told = 0;
    conn.onDisconnect(() => told++);
    const p1 = track(conn.request('A', {}));
    const p2 = track(conn.request('B', {}));
    ws.drop();
    await settle();
    out.dropped = [await state(p1), await state(p2)];
    out.told = told;
    out.afterDrop = await state(track(conn.request('C', {})));
    out.sendAfterDrop = conn.send('D', {});
  }
  { // close() fails waiting requests but is not a lost connection
    const { conn } = await open();
    let told = 0;
    conn.onDisconnect(() => told++);
    const p = track(conn.request('A', {}));
    conn.close();
    await settle();
    out.closed = await state(p);
    out.toldOnClose = told;
  }
  { // retries wait 1, 2, 4 … 30 s and stop once a try succeeds
    const { Reconnector } = await import(`${origin}/reconnect.js`);
    const timers = []; const waits = []; let tries = 0; let okOn = 4;
    const r = new Reconnector({
      tryConnect: async () => (++tries === okOn),
      onWait: (s) => waits.push(s),
      setTimer: (fn, ms) => { timers.push(fn); return timers.length; },
      clearTimer: () => {},
    });
    r.start(); r.start();   // a second start changes nothing
    for (let i = 0; i < 3; i++) { timers.shift()(); await settle(); }
    out.retryWaits = waits.slice();
    out.retryTries = tries;
    timers.shift()(); await settle();
    out.retryActiveAfterSuccess = r.active;
    out.retryTriesAfterSuccess = tries;
  }
  { // the wait tops out at 30 s; now() tries at once; stop() ends it
    const { Reconnector } = await import(`${origin}/reconnect.js`);
    const timers = []; const waits = []; let tries = 0;
    const r = new Reconnector({
      tryConnect: async () => { tries++; return false; },
      onWait: (s) => waits.push(s),
      setTimer: (fn) => { timers.push(fn); return timers.length; },
      clearTimer: () => {},
    });
    r.start();
    for (let i = 0; i < 7; i++) { timers.shift()(); await settle(); }
    out.cappedWaits = waits.slice();
    r.now(); await settle();
    out.triesAfterNow = tries;
    r.stop();
    out.activeAfterStop = r.active;
  }
  { // the table alone: an unknown reply_to is not consumed
    const t = new RequestTable();
    out.tableUnknown = t.answer({ event: 'X', reply_to: 4, final: true });
    out.tableNoReplyTo = t.answer({ event: 'X' });
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
            await page.set_content("<!doctype html><title>requests</title>")
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


def test_every_message_gets_its_own_number(results):
    assert results["role"] == "CONTROLLING"
    assert len(results["ids"]) == 2 and results["ids"][0] != results["ids"][1]


def test_each_final_reply_resolves_its_own_request(results):
    first, second = results["secondFirst"]
    assert first == "pending"
    assert second == {"ok": {"event": "TAB_SAVED", "kwargs": {"name": "y"}, "args": []}}
    assert results["firstLater"]["ok"]["kwargs"] == {"name": "x"}
    assert results["waitingAfter"] == 0


def test_progress_comes_before_the_final_reply(results):
    assert results["progressPending"] == "pending"
    assert results["progress"] == [1, 2]
    assert results["progressDone"]["ok"]["kwargs"] == {"n": 3}


def test_an_error_reply_rejects_with_its_text(results):
    assert results["error"] == {"err": "disk full", "lost": False}


def test_replies_nobody_waits_for_reach_the_event_handler(results):
    assert results["fallThrough"] == ["other", "plain"]


def test_a_dropped_connection_fails_every_waiting_request(results):
    lost = {"err": "connection lost", "lost": True}
    assert results["dropped"] == [lost, lost]
    assert results["told"] == 1
    assert results["afterDrop"] == lost
    assert results["sendAfterDrop"] is None


def test_closing_on_purpose_is_not_a_lost_connection(results):
    assert results["closed"] == {"err": "connection closed", "lost": True}
    assert results["toldOnClose"] == 0


def test_the_table_ignores_replies_it_does_not_hold(results):
    assert results["tableUnknown"] is False
    assert results["tableNoReplyTo"] is False


def test_retries_back_off_and_stop_when_connected(results):
    assert results["retryWaits"] == [1, 2, 4, 8]
    assert results["retryTries"] == 3
    assert results["retryActiveAfterSuccess"] is False
    assert results["retryTriesAfterSuccess"] == 4


def test_retry_waits_top_out_and_now_does_not_wait(results):
    assert results["cappedWaits"] == [1, 2, 4, 8, 16, 30, 30, 30]
    assert results["triesAfterNow"] == 8
    assert results["activeAfterStop"] is False

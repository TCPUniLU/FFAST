/**
 * WebSocket connection with HELLO handshake.
 */
import { IN, OUT } from './events.js';
import { traceTo } from './trace.js';
import { now, startTiming, stopTiming, timingWanted } from './timing.js';


import { msgpack } from './msgpack.js';

export class FFastConnection {
  constructor(wsUrl, token, readOnly = false) {
    this._url = wsUrl;
    this._token = token || null;
    this._readOnly = !!readOnly;
    this._ws = null;
    this._handlers = new Map();
    this.role = 'READ_ONLY';
    /** The server's code fingerprint from HELLO_ACK; null from an older server. */
    this.serverCodeFingerprint = null;
    /** Whether the server writes a trace this page should report to. */
    this.trace = false;
    /** The timing log (ADR 0072), or null when this page keeps none. */
    this.timing = null;
    this._pingTimer = null;
  }

  connect() {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(this._url);
      ws.binaryType = 'arraybuffer';
      this._ws = ws;

      // Buffer messages until handshake is complete
      const buf = [];
      let handshakeDone = false;

      ws.onmessage = (evt) => {
        if (handshakeDone) this._dispatch(evt.data, now());
        else buf.push(evt.data);
      };
      ws.onerror = () => { if (!handshakeDone) reject(new Error('WebSocket error')); };
      ws.onclose = () => { if (!handshakeDone) reject(new Error('WebSocket closed during handshake')); };

      ws.onopen = async () => {
        try {
          this.role = await this._handshake(buf);
          handshakeDone = true;
          ws.onclose = null;
          // process any messages that arrived during handshake
          for (const data of buf) this._dispatch(data, now());
          buf.length = 0;
          if (this.trace) traceTo(this);
          resolve(this.role);
        } catch (e) {
          reject(e);
        }
      };
    });
  }

  async _handshake(buf) {
    // 1. ping / pong
    this._ws.send('ping');
    await this._pollBuf(buf, d => typeof d === 'string' && d === 'pong', 10000);

    // 2. HELLO
    this._ws.send(msgpack.encode({
      event: 'HELLO', args: [], kwargs: {
        protocol_version: '1.0',
        renderer: 'webgl',
        session_token: this._token,
        supported_codecs: ['raw'],
        features: timingWanted() ? ['timing'] : [],
        read_only: this._readOnly,
      },
    }));

    // 3. HELLO_ACK (5 s timeout for backward compat)
    let role = 'READ_ONLY';
    try {
      const ackData = await this._pollBuf(
        buf,
        d => {
          if (!(d instanceof ArrayBuffer)) return false;
          try { return msgpack.decode(d)?.event === 'HELLO_ACK'; }
          catch { return false; }
        },
        5000,
      );
      const ack = msgpack.decode(ackData)?.kwargs || {};
      role = ack.role || 'READ_ONLY';
      this.serverCodeFingerprint = ack.code_fingerprint || null;
      this.trace = !!ack.trace;
      if (timingWanted()) this._startTiming((ack.features || []).includes('timing'));
    } catch (_) {
      console.warn('FFAST: no HELLO_ACK received — using READ_ONLY (backward compat)');
    }
    return role;
  }

  _pollBuf(buf, predicate, timeout) {
    return new Promise((resolve, reject) => {
      const deadline = Date.now() + timeout;
      const tick = () => {
        const i = buf.findIndex(predicate);
        if (i >= 0) { resolve(buf.splice(i, 1)[0]); return; }
        if (Date.now() > deadline) { reject(new Error('Timeout')); return; }
        setTimeout(tick, 10);
      };
      tick();
    });
  }

  _dispatch(data, received) {
    let event = '?';
    let reply = null;
    try {
      const msg = data instanceof ArrayBuffer
        ? msgpack.decode(data)
        : null;
      if (!msg || !msg.event) return;
      event = msg.event;
      if (this.timing) {
        if (event === IN.TIMING) { this.timing.note(msg.kwargs || {}, received); return; }
        reply = this.timing.received(event, data.byteLength, received);
      }
      const h = this._handlers.get(msg.event);
      if (h) h(msg.kwargs || {}, msg.args || []);
    } catch (e) {
      console.warn(`FFAST: dispatch error in the ${event} handler`, e);
    } finally {
      if (reply) {
        const log = this.timing;
        log.handled(reply, now());
        // Two frames: the first runs before the browser paints the change.
        requestAnimationFrame(() => requestAnimationFrame(() => log.drawn(reply, now())));
      }
    }
  }

  send(event, kwargs = {}, args = []) {
    if (this._ws?.readyState !== WebSocket.OPEN) return;
    // The trace's own lines are not timed: they follow every click.
    if (this.timing && event !== OUT.TRACE) {
      const id = this.timing.sent(event, now());
      this._ws.send(msgpack.encode({ event, args, kwargs, id }));
    } else {
      this._ws.send(msgpack.encode({ event, args, kwargs }));
    }
  }

  /** Keep a timing log; with ``serverTimed`` also number messages and ask the
   * server's clock: five times now, then every ten seconds (the quickest
   * answer lines the clocks up best). */
  _startTiming(serverTimed) {
    this.timing = startTiming();
    this.timing.serverTimed = serverTimed;
    this.timing.input(`connected to ${this._url}`
      + (serverTimed ? '' : ' (this server sends no timing notes)'), now());
    if (!serverTimed) return;
    const ping = () => {
      if (this._ws?.readyState === WebSocket.OPEN)
        this._ws.send(msgpack.encode({ event: OUT.TIMING_PING, args: [], kwargs: { t0: now() } }));
    };
    let first = 5;
    const early = setInterval(() => { ping(); if (--first <= 0) clearInterval(early); }, 200);
    this._pingTimer = setInterval(ping, 10000);
  }

  on(event, handler)   { this._handlers.set(event, handler); }
  off(event)           { this._handlers.delete(event); }

  close() {
    if (this.trace) traceTo(null);
    clearInterval(this._pingTimer);
    stopTiming(this.timing);
    this.timing = null;
    this.send(OUT.GRACEFUL_DISCONNECT, {});
    this._ws?.close();
  }
}

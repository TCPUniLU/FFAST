/**
 * WebSocket connection with HELLO handshake.
 *
 * Every message it sends is numbered (`id`); `request()` waits for the replies
 * that name that number (ADR 0097). After the handshake it keeps listening for
 * the socket closing: waiting requests then fail, and `onDisconnect` listeners
 * hear it.
 */
import { IN, OUT } from './events.js';
import { traceTo } from './trace.js';
import { now, startTiming, stopTiming, timingWanted } from './timing.js';
import { RequestTable } from './requests.js';


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
    this.localLammps = false;
    this.trace = false;
    /** The timing log (ADR 0072), or null when this page keeps none. */
    this.timing = null;
    this._pingTimer = null;
    /** The number the next message gets (ADR 0097). */
    this._nextId = 1;
    this._requests = new RequestTable();
    this._disconnectListeners = [];
    /** Set by close(): a drop the page asked for is not a lost connection. */
    this._closing = false;
    // Heartbeat (ADR 0097): a socket that stalls without closing (laptop
    // sleep, a Wi-Fi switch) is found by hearing nothing for a while. Any
    // message counts; the page asks every `heartbeatEveryMs`.
    this.heartbeatEveryMs = 15000;
    this.silentLimitMs = 60000;
    this._lastHeard = 0;
    this._heartbeatTimer = null;
  }

  connect() {
    return new Promise((resolve, reject) => {
      window.ffastStartup?.('socket', this._url);
      const ws = new WebSocket(this._url);
      ws.binaryType = 'arraybuffer';
      this._ws = ws;

      // Buffer messages until handshake is complete
      const buf = [];
      let handshakeDone = false;

      ws.onmessage = (evt) => {
        this._lastHeard = Date.now();
        if (handshakeDone) this._dispatch(evt.data, now());
        else buf.push(evt.data);
      };
      ws.onerror = () => {
        window.ffastStartup?.('failed');
        if (!handshakeDone) reject(new Error('WebSocket error'));
      };
      ws.onclose = () => { if (!handshakeDone) reject(new Error('WebSocket closed during handshake')); };

      ws.onopen = async () => {
        window.ffastStartup?.('open');
        try {
          this.role = await this._handshake(buf);
          handshakeDone = true;
          ws.onclose = (evt) => this._lost(evt);
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
        features: timingWanted() ? ['timing', 'heartbeat'] : ['heartbeat'],
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
      this.localLammps = !!ack.local_lammps;
      this.trace = !!ack.trace;
      if (timingWanted()) this._startTiming((ack.features || []).includes('timing'));
      // An older server does not answer heartbeats: then none are sent.
      if ((ack.features || []).includes('heartbeat')) this._startHeartbeat();
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
      if (this._requests.answer(msg)) return;
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

  /** Send one message; returns its number, or null when the socket is not
   * open (nothing is sent). */
  send(event, kwargs = {}, args = []) {
    if (this._ws?.readyState !== WebSocket.OPEN) return null;
    const id = this._nextId++;
    // The trace's own lines and the heartbeat are not timed: they are not
    // the user's.
    if (this.timing && event !== OUT.TRACE && event !== OUT.HEARTBEAT)
      this.timing.sent(event, now(), id);
    this._ws.send(msgpack.encode({ event, args, kwargs, id }));
    return id;
  }

  /** Send a request and wait for its final reply (ADR 0097). Resolves with
   * `{event, kwargs, args}`; rejects with the reply's `error` text, or with
   * "connection lost" (`err.connectionLost`) when the socket closes first.
   * `onProgress(reply)` hears the replies before the final one. */
  request(event, kwargs = {}, args = [], { onProgress = null } = {}) {
    const id = this.send(event, kwargs, args);
    if (id == null) {
      const err = new Error('connection lost');
      err.connectionLost = true;
      return Promise.reject(err);
    }
    return this._requests.add(id, event, { onProgress });
  }

  /** `fn()` runs when the connection drops without close() being called. */
  onDisconnect(fn) { this._disconnectListeners.push(fn); }

  /** How many requests wait for their final reply. */
  get waiting() { return this._requests.size; }

  _startHeartbeat() {
    this._lastHeard = Date.now();
    this._heartbeatTimer = setInterval(() => {
      if (Date.now() - this._lastHeard > this.silentLimitMs) {
        // Stalled: the socket may never report it, so stop waiting on it.
        const ws = this._ws;
        if (ws) { ws.onclose = null; ws.onmessage = null; try { ws.close(); } catch (_) { /* already gone */ } }
        this._lost();
        return;
      }
      // The answer only needs to arrive; any message resets the clock.
      this.request(OUT.HEARTBEAT).catch(() => {});
    }, this.heartbeatEveryMs);
  }

  _lost() {
    clearInterval(this._heartbeatTimer);
    this._heartbeatTimer = null;
    clearInterval(this._pingTimer);
    this._pingTimer = null;
    this._requests.failAll(this._closing ? 'connection closed' : 'connection lost');
    if (this._closing) return;
    for (const fn of this._disconnectListeners) {
      try { fn(); } catch (e) { console.warn('FFAST: disconnect listener failed', e); }
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
    this._closing = true;
    if (this.trace) traceTo(null);
    clearInterval(this._pingTimer);
    clearInterval(this._heartbeatTimer);
    this._heartbeatTimer = null;
    stopTiming(this.timing);
    this.timing = null;
    this.send(OUT.GRACEFUL_DISCONNECT, {});
    this._ws?.close();
    this._requests.failAll('connection closed');
  }
}

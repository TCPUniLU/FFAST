/**
 * WebSocket connection with HELLO handshake.
 */

import { msgpack } from './msgpack.js';

export class FFastConnection {
  constructor(wsUrl, token, readOnly = false) {
    this._url = wsUrl;
    this._token = token || null;
    this._readOnly = !!readOnly;
    this._ws = null;
    this._handlers = new Map();
    this.role = 'READ_ONLY';
    // Whether the server advertised multi-client support in HELLO_ACK (ADR
    // 0044 Phase 1+): every connection gets its own outbound queue, session,
    // and view namespace. A pop-out only opens its own live controller
    // connection when this is true — otherwise it falls back to the
    // BroadcastChannel satellite mirror (ADR 0043) for an older, single-
    // client server.
    this.multiClient = false;
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
        if (handshakeDone) this._dispatch(evt.data);
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
          for (const data of buf) this._dispatch(data);
          buf.length = 0;
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
        features: [],
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
      this.multiClient = (ack.features || []).includes('multi_client');
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

  _dispatch(data) {
    try {
      const msg = data instanceof ArrayBuffer
        ? msgpack.decode(data)
        : null;
      if (!msg || !msg.event) return;
      const h = this._handlers.get(msg.event);
      if (h) h(msg.kwargs || {}, msg.args || []);
    } catch (e) {
      console.warn('FFAST: dispatch error', e);
    }
  }

  send(event, kwargs = {}, args = []) {
    if (this._ws?.readyState === WebSocket.OPEN)
      this._ws.send(msgpack.encode({ event, args, kwargs }));
  }

  on(event, handler)   { this._handlers.set(event, handler); }
  off(event)           { this._handlers.delete(event); }

  close() {
    this.send('GRACEFUL_DISCONNECT', {});
    this._ws?.close();
  }
}

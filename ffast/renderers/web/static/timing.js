/**
 * The timing log (ADR 0072): where the time goes between a click and its
 * result on screen.
 *
 * A page opened by ffast-web numbers each message it sends. The server tells
 * it, in TIMING messages, when it read each message and finished handling it,
 * and, after each message it sends, when that one was queued and sent, how
 * big it was and which numbered message caused it. TIMING_PING gives the
 * server's clock, so its times line up with the page's.
 *
 * Messages sent and received after a click (or key, or change) belong to that
 * click until things go quiet. Then the log writes one block per click:
 *
 *   10:42:01.120 click button "Energy shift": 1840 ms until the last reply was on screen
 *       sent 3, received 5 (3.2 MB) · round trip 24 ms · page busy 420 ms
 *       → REQUEST_METRIC #12  at server +950 ms (≈12 network, ≈938 waiting) · handled in 600 ms
 *       ← METRIC_RESULT for #12 (1.1 MB)  queued +1540 · sent after 20 · reached page after 160 · page 30 · on screen +1840
 *
 * "waiting" is time the message sat behind earlier work: the server handles
 * one message at a time. "reached page after" is the trip back plus any time
 * the page was too busy to read it (see "page busy"); "page" is the page's
 * handler for it. The lines go to the launcher (POST /timing), which
 * writes them to ~/.ffast/logs/timing-<date-time>.log.
 */
import { describe } from './trace.js';

/** Quiet this long (ms) and a click's block is written. */
export const QUIET_MS = 1000;
/** A click still busy after this long (ms) is written anyway. */
const MAX_MS = 120000;
/** Detail lines per click, at most. */
const MAX_LINES = 40;

export const now = () => performance.timeOrigin + performance.now();

export function fmtBytes(n) {
  if (n >= 1048576) return `${(n / 1048576).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

function clock(t) {
  const d = new Date(t);
  const p = (x, n = 2) => String(x).padStart(n, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`;
}

const r = (x) => Math.round(x);

/**
 * The log's bookkeeping, with every time passed in, so it runs without a
 * socket or a screen. ``write(lines)`` gets each finished block.
 */
export class TimingLog {
  constructor(write) {
    this._write = write;
    this._nextId = 1;
    this._messages = new Map();   // id → {id, event, sent, action, read?, done?}
    this._actions = [];           // open actions, oldest first
    this._last = null;            // the reply received last, waiting for its note
    this._busy = [];              // [start, end] of long tasks in the page
    this.serverTimed = false;     // the server sends TIMING notes
    this.rtt = null;              // best round trip seen (ms)
    this.offset = 0;              // server clock − page clock (ms)
  }

  /** A click, key or change: what follows belongs to it. */
  input(label, t) {
    const newest = this._actions.at(-1);
    // Nothing happened since the last input: the new one replaces it.
    if (newest && !newest.messages.length && !newest.replies.length) this._actions.pop();
    this._actions.push(this._action(label, t));
  }

  _action(label, t) {
    return { label, start: t, last: t, messages: [], replies: [] };
  }

  /** The action new traffic belongs to: the newest open one, or a new "(no input)" one. */
  _current(t) {
    if (!this._actions.length) this._actions.push(this._action(null, t));
    return this._actions.at(-1);
  }

  /** A message is being sent; returns its number. */
  sent(event, t) {
    const action = this._current(t);
    const msg = { id: this._nextId++, event, sent: t, action, serverTimed: this.serverTimed };
    this._messages.set(msg.id, msg);
    action.messages.push(msg);
    action.last = t;
    return msg.id;
  }

  /** A message arrived (``bytes`` long); returns its record for ``handled``/``drawn``. */
  received(event, bytes, t) {
    const action = this._current(t);
    const reply = { event, bytes, recv: t, action };
    action.replies.push(reply);
    action.last = t;
    this._last = reply;
    return reply;
  }

  /** The page's handler for ``reply`` returned. */
  handled(reply, t) {
    reply.handled = t;
    reply.action.last = Math.max(reply.action.last, t);
  }

  /** ``reply``'s result reached the screen (the frame after its handler). */
  drawn(reply, t) {
    reply.drawn = t;
    reply.action.last = Math.max(reply.action.last, t);
  }

  /** A long task kept the page busy from ``start`` for ``duration`` ms. */
  busy(start, duration) {
    this._busy.push([start, start + duration]);
    if (this._busy.length > 500) this._busy.shift();
  }

  /** A TIMING message from the server. */
  note(kw, t) {
    if (kw.kind === 'pong') {
      const rtt = t - kw.t0;
      if (this.rtt === null || rtt <= this.rtt) {
        this.rtt = rtt;
        this.offset = kw.server - (kw.t0 + rtt / 2);
      }
    } else if (kw.kind === 'handled') {
      const msg = this._messages.get(kw.id);
      if (msg) { msg.read = kw.read; msg.done = kw.done; }
    } else if (kw.kind === 'sent' && this._last) {
      const reply = this._last;
      this._last = null;
      Object.assign(reply, { cause: kw.cause, queued: kw.queued, sentAt: kw.sent, bytes: kw.bytes });
      // A reply to an earlier click belongs to that click.
      const owner = this._messages.get(kw.cause)?.action;
      if (owner && owner !== reply.action) {
        reply.action.replies.splice(reply.action.replies.indexOf(reply), 1);
        reply.action = owner;
        owner.replies.push(reply);
        owner.last = Math.max(owner.last, reply.drawn ?? reply.handled ?? reply.recv);
      }
    }
  }

  /** Write every action that has gone quiet by ``t``. */
  tick(t) {
    const keep = [];
    for (const action of this._actions) {
      const empty = !action.messages.length && !action.replies.length;
      const waiting = action.messages.some((m) => m.serverTimed && m.done === undefined);
      const quiet = t - action.last >= QUIET_MS && !waiting;
      if (empty && t - action.start >= QUIET_MS) continue;
      if (!empty && (quiet || t - action.start >= MAX_MS)) this._finish(action);
      else keep.push(action);
    }
    this._actions = keep;
  }

  /** Write everything still open (the page is closing). */
  flush() {
    for (const action of this._actions) {
      if (action.messages.length || action.replies.length) this._finish(action);
    }
    this._actions = [];
  }

  _finish(action) {
    for (const m of action.messages) this._messages.delete(m.id);
    this._write(this.lines(action));
  }

  /** ``server`` time on the page's clock. */
  _page(server) { return server - this.offset; }

  /** The block of lines for one action. */
  lines(a) {
    const end = Math.max(a.last, ...a.replies.map((x) => x.drawn ?? x.handled ?? x.recv));
    const bytes = a.replies.reduce((s, x) => s + (x.bytes || 0), 0);
    const busy = this._busy.reduce((s, [b, e]) => s + Math.max(0, Math.min(e, end) - Math.max(b, a.start)), 0);
    const what = a.label ?? '(no input)';
    const out = [
      `${clock(a.start)} ${what}: ${r(end - a.start)} ms until the last reply was on screen`,
      `    sent ${a.messages.length}, received ${a.replies.length} (${fmtBytes(bytes)})`
        + (this.rtt !== null ? ` · round trip ${r(this.rtt)} ms` : '')
        + (busy ? ` · page busy ${r(busy)} ms` : ''),
    ];
    const at = (t) => { const d = r(t - a.start); return d < 0 ? `${d}` : `+${d}`; };
    const detail = [];
    for (const m of a.messages) {
      let line = `    → ${m.event} #${m.id}  sent ${at(m.sent)}`;
      if (m.read !== undefined) {
        const toServer = this._page(m.read) - m.sent;
        const network = this.rtt !== null ? Math.min(toServer, this.rtt / 2) : null;
        line = `    → ${m.event} #${m.id}  at server ${at(this._page(m.read))} ms`
          + (network !== null ? ` (≈${r(network)} network, ≈${r(toServer - network)} waiting)` : '')
          + ` · handled in ${r(m.done - m.read)} ms`;
      }
      detail.push([m.sent, line]);
    }
    for (const x of a.replies) {
      const cause = x.cause ? ` for #${x.cause}` : '';
      let line = `    ← ${x.event}${cause} (${fmtBytes(x.bytes || 0)})`;
      if (x.sentAt !== undefined) {
        line += `  queued ${at(this._page(x.queued))} · sent after ${r(x.sentAt - x.queued)}`
          + ` · reached page after ${r(x.recv - this._page(x.sentAt))}`;
      } else {
        line += `  arrived ${at(x.recv)}`;
      }
      if (x.handled !== undefined) line += ` · page ${r(x.handled - x.recv)}`;
      if (x.drawn !== undefined) line += ` · on screen ${at(x.drawn)}`;
      detail.push([x.recv, line]);
    }
    detail.sort((p, q) => p[0] - q[0]);
    out.push(...detail.slice(0, MAX_LINES).map(([, line]) => line));
    if (detail.length > MAX_LINES) out.push(`    … and ${detail.length - MAX_LINES} more`);
    return out;
  }
}

// ── the page's side: where lines go, and the inputs that start an action ──

let _launch = null;
const _logs = new Set();
let _pending = [];
let _timer = null;
let _dead = false;

/** Turn the log on for a page opened by ffast-web (``launch`` = its launch id). */
export function setTimingSink(launch) {
  _launch = launch || null;
}

/** Whether pages should ask the server for timing notes. */
export function timingWanted() {
  return !!_launch && !_dead;
}

/** Queue lines for the launcher's log file; sent in batches. */
export function writeLines(lines) {
  if (!timingWanted()) return;
  _pending.push(...lines);
  if (!_timer) _timer = setTimeout(_post, 2000);
}

function _post(keepalive = false) {
  _timer = null;
  if (!_pending.length || !timingWanted()) return;
  const lines = _pending;
  _pending = [];
  fetch('/timing', {
    method: 'POST',
    keepalive,
    headers: { 'X-FFAST-Launch': _launch, 'Content-Type': 'application/json' },
    body: JSON.stringify({ lines }),
  }).then((res) => {
    // A launcher without the timing log (older): stop asking.
    if (res.status === 404 || res.status === 403) _dead = true;
  }).catch(() => {});
}

/** Start logging for a connection; returns its TimingLog. */
export function startTiming() {
  const log = new TimingLog(writeLines);
  _logs.add(log);
  _install();
  return log;
}

/** Stop logging for ``log`` and write what it still holds. */
export function stopTiming(log) {
  if (!log) return;
  log.flush();
  _logs.delete(log);
}

let _installed = false;

function _install() {
  if (_installed) return;
  _installed = true;
  // Only what the user did: code that fires a change or click starts nothing.
  const input = (e, label) => {
    if (!e.isTrusted) return;
    const t = now();
    for (const log of _logs) log.input(label, t);
  };
  // Capture phase: the input is seen before its handler sends anything.
  // pointerup, not click: a drag that selects part of a plot ends in one.
  document.addEventListener('pointerup', (e) => input(e, `click ${describe(e.target)}`), true);
  document.addEventListener('change', (e) => input(e, `change ${describe(e.target)}`), true);
  document.addEventListener('keydown', (e) => {
    if (/^(Shift|Control|Alt|Meta|CapsLock)$/.test(e.key)) return;
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
    if (typing && !(e.ctrlKey || e.metaKey || e.altKey) && !/^(Enter|Escape)$/.test(e.key)) return;
    input(e, `key ${e.key} on ${describe(e.target)}`);
  }, true);
  setInterval(() => { const t = now(); for (const log of _logs) log.tick(t); }, 250);
  window.addEventListener('pagehide', () => {
    for (const log of _logs) log.flush();
    _post(true);
  });
  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) {
        for (const log of _logs) log.busy(performance.timeOrigin + e.startTime, e.duration);
      }
    }).observe({ type: 'longtask' });
  } catch (_) { /* no long-task timing in this browser */ }
}

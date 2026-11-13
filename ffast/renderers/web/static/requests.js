/**
 * The requests this page is waiting on (ADR 0097).
 *
 * Every message the page sends has a number (`id`). A request's answers carry
 * that number back as `reply_to`; progress comes first, then exactly one
 * `final` reply, which carries either a result or `error`. A request has no
 * time limit: it ends with its final reply, or fails when the connection
 * drops (`failAll`).
 *
 * Pure bookkeeping, no socket: FFastConnection owns one.
 */

/** A request's outcome as the final reply's kwargs, never a rejection: a
 * failed request gives its kwargs with `error` set, and a dropped connection
 * also sets `connectionLost`. For callers that show an error the same way,
 * wherever it came from. */
export function answerOf(asked) {
  return asked.then(
    (reply) => reply.kwargs,
    (err) => ({
      ...(err?.reply?.kwargs || {}),
      error: err?.message || String(err),
      connectionLost: !!err?.connectionLost,
    }),
  );
}

export class RequestTable {
  constructor() {
    /** id → {resolve, reject, onProgress, event} */
    this._waiting = new Map();
  }

  /** Wait for the answers to message `id`. Resolves with the final reply
   * `{kwargs, args, event}`; rejects with an Error carrying its text. */
  add(id, event, { onProgress = null } = {}) {
    return new Promise((resolve, reject) => {
      this._waiting.set(id, { resolve, reject, onProgress, event });
    });
  }

  /** A decoded message arrived. Returns true when it answered a request
   * this table holds (then nobody else should handle it). */
  answer(msg) {
    if (msg?.reply_to == null) return false;
    const w = this._waiting.get(msg.reply_to);
    if (!w) return false;
    const reply = { event: msg.event, kwargs: msg.kwargs || {}, args: msg.args || [] };
    if (msg.error != null) {
      this._waiting.delete(msg.reply_to);
      const err = new Error(String(msg.error));
      err.reply = reply;
      w.reject(err);
    } else if (msg.final === false) {
      try { w.onProgress?.(reply); }
      catch (e) { console.warn(`FFAST: progress handler for ${w.event} failed`, e); }
    } else {
      this._waiting.delete(msg.reply_to);
      w.resolve(reply);
    }
    return true;
  }

  /** The connection is gone: every waiting request fails with `reason`. */
  failAll(reason = 'connection lost') {
    const waiting = [...this._waiting.values()];
    this._waiting.clear();
    for (const w of waiting) {
      const err = new Error(reason);
      err.connectionLost = true;
      w.reject(err);
    }
  }

  get size() { return this._waiting.size; }
}

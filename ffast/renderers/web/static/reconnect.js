/**
 * Retries a lost connection by itself (ADR 0097 decision 3).
 *
 * Waits 1 s, 2 s, 4 s … up to 30 s between tries, until a try succeeds or
 * `stop()` is called. `now()` tries at once (the banner's "Reconnect now").
 * A try that is already running is not started twice.
 *
 * Pure scheduling: the page passes `tryConnect` (resolves true on success),
 * and hears each wait through `onWait(seconds)`. Timers are injectable so a
 * test needs no real waiting.
 */

export const RETRY_DELAYS_S = [1, 2, 4, 8, 16, 30];

export class Reconnector {
  constructor({ tryConnect, onWait = () => {}, onTrying = () => {},
                setTimer = (fn, ms) => setTimeout(fn, ms),
                clearTimer = (t) => clearTimeout(t) }) {
    this._tryConnect = tryConnect;
    this._onWait = onWait;
    this._onTrying = onTrying;
    this._setTimer = setTimer;
    this._clearTimer = clearTimer;
    this._timer = null;
    this._attempt = 0;
    this._active = false;
    this._trying = false;
  }

  get active() { return this._active; }

  /** The connection was lost: start retrying. */
  start() {
    if (this._active) return;
    this._active = true;
    this._attempt = 0;
    this._schedule();
  }

  /** Try now instead of waiting. */
  now() {
    if (!this._active) return;
    this._clear();
    this._try();
  }

  /** Give up retrying (connected, or the user chose another way). */
  stop() {
    this._active = false;
    this._clear();
  }

  _schedule() {
    const s = RETRY_DELAYS_S[Math.min(this._attempt, RETRY_DELAYS_S.length - 1)];
    this._attempt += 1;
    this._onWait(s);
    this._timer = this._setTimer(() => { this._timer = null; this._try(); }, s * 1000);
  }

  async _try() {
    if (!this._active || this._trying) return;
    this._trying = true;
    this._onTrying();
    let ok = false;
    try { ok = await this._tryConnect(); }
    catch (_) { ok = false; }
    finally { this._trying = false; }
    if (!this._active) return;
    if (ok) this.stop();
    else this._schedule();
  }

  _clear() {
    if (this._timer !== null) this._clearTimer(this._timer);
    this._timer = null;
  }
}

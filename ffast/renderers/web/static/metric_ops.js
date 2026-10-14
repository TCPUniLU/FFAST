/**
 * User metrics over the wire (ADR 0057): the list, save / delete / export, the
 * check of a formula as it is typed, the inputs a formula can use, and Try.
 *
 * Saving and deleting answer the window that asked (USER_METRIC_SAVED), in the
 * order the requests went, so each waits its turn in a queue — the same
 * arrangement as tab_ops.js. The check and the inputs are different: the
 * editor only wants the *latest* answer, so each request carries a number and
 * an older answer is dropped. A Try has its own id, which the progress
 * messages and the result carry.
 */

import { OUT } from './events.js';

export class MetricOps {
  /** @param {{send: (event: string, kwargs?: object) => void}} deps */
  constructor({ send }) {
    this._send = send;
    this._saved = [];       // resolvers waiting for USER_METRIC_SAVED, oldest first
    this._exported = [];    // resolvers waiting for USER_METRIC_EXPORTED
    this._checkSeq = 0;
    this._trySeq = 0;
    /** @type {{id: string, resolve: Function, onProgress: Function|null}|null} */
    this._try = null;
    /** Handlers the pane sets. */
    this.onList = () => {};
    this.onCheck = () => {};
    this.onInputs = () => {};
  }

  requestList() { this._send(OUT.REQUEST_USER_METRICS, {}); }

  /** Save a new metric (`previousId` null) or an edit that keeps the id.
   * `baseRevision` is the version the editor started from; `overwrite` saves
   * over a newer one.
   * @returns {Promise<{ok: boolean, action: string, id: string, error: string|null, conflict: boolean}>} */
  save(metric, { previousId = null, baseRevision = null, overwrite = false } = {}) {
    return this._ask(this._saved, OUT.SAVE_USER_METRIC, {
      metric, previous_id: previousId, base_revision: baseRevision, overwrite,
    });
  }

  remove(id) { return this._ask(this._saved, OUT.DELETE_USER_METRIC, { id }); }

  /** @returns {Promise<{id: string, toml: string|null, error: string|null}>} */
  exportToml(id) { return this._ask(this._exported, OUT.EXPORT_USER_METRIC, { id }); }

  /** Check a formula as typed; the answer goes to `onCheck` unless a newer
   * check was sent since. */
  check(metric) {
    this._send(OUT.CHECK_METRIC, { metric, check_id: String(++this._checkSeq) });
  }

  /** What a formula can use for this dataset and prediction. */
  requestInputs(datasetFp, modelFp) {
    this._inputsFor = { datasetFp, modelFp };
    this._send(OUT.REQUEST_METRIC_INPUTS, { dataset_fp: datasetFp, model_fp: modelFp });
  }

  /**
   * Try a formula on every frame of the dataset and prediction, without saving.
   * One Try at a time: starting another stops the first.
   * @returns {Promise<object>} the METRIC_TRIED payload
   */
  tryIt(metric, datasetFp, modelFp, onProgress = null) {
    this.cancelTry();
    const id = `try-${++this._trySeq}`;
    return new Promise((resolve) => {
      this._try = { id, resolve, onProgress };
      this._send(OUT.TRY_METRIC, { metric, try_id: id, dataset_fp: datasetFp, model_fp: modelFp });
    });
  }

  /** Stop the running Try; its promise then resolves with `cancelled: true`. */
  cancelTry() {
    if (this._try) this._send(OUT.CANCEL_TRY_METRIC, { try_id: this._try.id });
  }

  get trying() { return this._try !== null; }

  // ── incoming ──────────────────────────────────────────────────────────────
  onSaved(kw) { this._saved.shift()?.(kw); }

  onExported(kw) { this._exported.shift()?.(kw); }

  onChecked(kw) {
    if (kw.check_id === String(this._checkSeq)) this.onCheck(kw);
  }

  onInputsReply(kw) {
    const want = this._inputsFor;
    if (want && kw.dataset_fp === want.datasetFp && kw.model_fp === want.modelFp) this.onInputs(kw.inputs || []);
  }

  /** TASK_PROGRESS names the Try by `args[0]`. */
  onProgress(kw, args) {
    if (this._try && args?.[0] === this._try.id) this._try.onProgress?.(kw);
  }

  onTried(kw) {
    if (!this._try || kw.try_id !== this._try.id) return;
    const { resolve } = this._try;
    this._try = null;
    resolve(kw);
  }

  /** A disconnect answers nothing: whoever waits hears it failed. */
  reset() {
    for (const resolve of this._saved.splice(0)) resolve({ ok: false, error: 'Disconnected', conflict: false });
    for (const resolve of this._exported.splice(0)) resolve({ toml: null, error: 'Disconnected' });
    if (this._try) {
      const { resolve } = this._try;
      this._try = null;
      resolve({ ok: false, cancelled: false, error: 'Disconnected' });
    }
  }

  _ask(queue, event, kwargs) {
    return new Promise((resolve) => {
      queue.push(resolve);
      this._send(event, kwargs);
    });
  }
}

/**
 * Metric channel client (ADR 0045 Phase 3).
 *
 * Wraps the one WebSocket connection with a promise-per-request API over the
 * already-wired `REQUEST_METRIC` → `METRIC_RESULT` server channel (Stage 4a).
 * The analysis panels use it to fetch each Panel's computed metric array for the
 * current dataset/prediction.
 *
 * Correlation & caching: we send the request's **cache key** as `key`, in the
 * server's `CacheKey` format `identity__model__dataset` (SEP `__`, NIL `nil`;
 * see ffast/cache/keys.py). The server rejects a malformed key, uses it as both
 * the compute cache slot and the correlation id (echoed back as `METRIC_RESULT`
 * `args[0]`). The metric id + params travel separately, so the key's identity
 * token is just a deterministic slot label — we fold params into it the same
 * shape the desktop does (`metric__p<...>`), without needing an md5 in the
 * browser. Because the key is deterministic per (metric, params, model,
 * dataset), two windows issuing the same request share it and the second hits
 * the cache (PRD story 71); and two identical in-flight requests within one
 * window share it, so each key holds a *list* of waiters resolved together.
 * Replies are per-connection (server `_emit` → this socket), so a window only
 * ever sees its own results.
 */

import { decodeNdarray } from './ndarray.js';

const NIL = 'nil';

/** Build the server-format cache key `identity__model__dataset`. Params fold
 * into the identity as a deterministic, sorted `metric__p<k=v,...>` token — the
 * server only requires a valid 3-segment key and uses it as an opaque slot. */
function cacheKey(metricId, params, modelFp, datasetFp) {
  let identity = metricId;
  const names = Object.keys(params || {}).sort();
  if (names.length) {
    const enc = names.map((k) => `${k}=${JSON.stringify(params[k])}`).join(',');
    identity = `${metricId}__p${enc}`;
  }
  return `${identity}__${modelFp || NIL}__${datasetFp || NIL}`;
}

/** @typedef {{values: number[], shape: number[]}} DecodedArray */
/** @typedef {{nd: DecodedArray|null, shape: string, unit: string, dtype: string}} MetricResult */

const _TIMEOUT_MS = 120000;

export class MetricClient {
  /** @param {import('./connection.js').FFastConnection} conn */
  constructor(conn) {
    this._conn = conn;
    /** @type {Map<string, Array<{resolve: Function, timer: any}>>} */
    this._waiters = new Map();
    conn.on('METRIC_RESULT', (kw, args) => this._onResult(kw, args));
  }

  /**
   * Request a metric's computed array.
   * @param {string} metricId concrete (already resolved) metric id
   * @param {{params?: object, modelFp?: string|null, datasetFp?: string|null}} [opts]
   * @returns {Promise<MetricResult|null>} null when the server can't compute it
   *   (e.g. a client-only model — `ok:false`) or the request times out.
   */
  request(metricId, { params = {}, modelFp = null, datasetFp = null } = {}) {
    return new Promise((resolve) => {
      const key = cacheKey(metricId, params, modelFp, datasetFp);
      const timer = setTimeout(() => {
        this._drop(key, resolve);
        resolve(null);
      }, _TIMEOUT_MS);
      const list = this._waiters.get(key) || [];
      list.push({ resolve, timer });
      this._waiters.set(key, list);
      this._conn.send('REQUEST_METRIC', {
        metric_id: metricId,
        key,                      // echoed back as args[0] → 1:1 correlation
        params,
        model_fp: modelFp,
        dataset_fp: datasetFp,
      });
    });
  }

  _drop(key, resolve) {
    const list = this._waiters.get(key);
    if (!list) return;
    const i = list.findIndex((w) => w.resolve === resolve);
    if (i >= 0) list.splice(i, 1);
    if (list.length === 0) this._waiters.delete(key);
  }

  _onResult(kw, args) {
    const key = args && args[0];
    const list = key != null ? this._waiters.get(key) : null;
    if (!list || list.length === 0) return;
    this._waiters.delete(key);
    const payload = kw.ok
      ? { nd: decodeNdarray(kw.values), shape: kw.shape, unit: kw.unit, dtype: kw.dtype }
      : null;
    for (const w of list) {
      clearTimeout(w.timer);
      w.resolve(payload);
    }
  }
}

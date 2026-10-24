/**
 * Metric channel client (ADR 0045 Phase 3).
 *
 * Wraps the one WebSocket connection with a promise-per-request API over the
 * already-wired `REQUEST_METRIC` → `METRIC_RESULT` server channel (Stage 4a).
 * The analysis panels use it to fetch each Panel's computed metric array for the
 * current dataset/prediction.
 *
 * Correlation: every request carries a `request_id` (a counter: "m1", "m2", ...)
 * and the reply echoes it as `METRIC_RESULT` `args[0]`. The id is an opaque
 * label. The server builds the cache key itself and it never crosses the wire
 * (ADR 0061), so the browser cannot name — or overwrite — a stored result.
 *
 * Merging: two identical requests in flight at the same time (same metric,
 * params, model and dataset) share one message and one promise. On
 * `METRICS_UPDATED` the app calls `forgetInFlight()`, so a request made after a
 * metric change never joins one made before it; the earlier reply still
 * resolves the earlier request. Replies are per-connection (server `_emit` →
 * this socket), so a window only ever sees its own results.
 */

import { decodeNdarray } from './ndarray.js';
import { IN, OUT } from './events.js';

/** The label identical in-flight requests are merged by. */
function requestLabel(metricId, params, modelFp, datasetFp) {
  const p = params || {};
  const sorted = Object.keys(p).sort().map((k) => [k, p[k]]);
  return JSON.stringify([metricId, sorted, modelFp || null, datasetFp || null]);
}

/** @typedef {{values: number[], shape: number[]}} DecodedArray */
/** @typedef {{nd: DecodedArray|null, shape: string, unit: string, dtype: string, warnings: string[], metricId: string}} MetricResult */
/** A metric that failed, and why (ADR 0066 rule 6).
 * @typedef {{nd: null, failed: true, error: string, metricId: string}} MetricFailure */

const _TIMEOUT_MS = 120000;

export class MetricClient {
  /** @param {import('./connection.js').FFastConnection} conn */
  constructor(conn) {
    this._conn = conn;
    this._nextId = 0;
    /** @type {Map<string, {resolve: Function, timer: any}>} */
    this._byId = new Map();
    /** @type {Map<string, Promise<MetricResult|MetricFailure|null>>} */
    this._inFlight = new Map();
    conn.on(IN.METRIC_RESULT, (kw, args) => this._onResult(kw, args));
  }

  /**
   * Request a metric's computed array.
   * @param {string} metricId concrete (already resolved) metric id
   * @param {{params?: object, modelFp?: string|null, datasetFp?: string|null}} [opts]
   * @returns {Promise<MetricResult|MetricFailure|null>} a failure when the
   *   metric ran and failed; null when the server can't compute it (e.g. a
   *   client-only model, no data — `ok:false` without an error) or the
   *   request times out.
   */
  request(metricId, { params = {}, modelFp = null, datasetFp = null } = {}) {
    const label = requestLabel(metricId, params, modelFp, datasetFp);
    const joined = this._inFlight.get(label);
    if (joined) return joined;

    const requestId = `m${++this._nextId}`;
    const promise = new Promise((resolve) => {
      const timer = setTimeout(() => {
        this._byId.delete(requestId);
        resolve(null);
      }, _TIMEOUT_MS);
      this._byId.set(requestId, { resolve, timer });
    }).finally(() => {
      if (this._inFlight.get(label) === promise) this._inFlight.delete(label);
    });
    this._inFlight.set(label, promise);
    this._conn.send(OUT.REQUEST_METRIC, {
      metric_id: metricId,
      request_id: requestId,    // echoed back as args[0] → 1:1 correlation
      params,
      model_fp: modelFp,
      dataset_fp: datasetFp,
    });
    return promise;
  }

  /** Stop merging new requests into ones already sent. Replies still resolve
   * through their request id. Call when the metric table changed. */
  forgetInFlight() {
    this._inFlight.clear();
  }

  _onResult(kw, args) {
    const requestId = args && args[0];
    const waiter = requestId != null ? this._byId.get(requestId) : null;
    if (!waiter) return;
    this._byId.delete(requestId);
    clearTimeout(waiter.timer);
    waiter.resolve(
      kw.ok
        ? { nd: decodeNdarray(kw.values), shape: kw.shape, unit: kw.unit, dtype: kw.dtype,
            warnings: kw.warnings || [], metricId: kw.metric_id }
        : kw.error
          ? { nd: null, failed: true, error: kw.error, metricId: args[1] }
          : null,
    );
  }
}

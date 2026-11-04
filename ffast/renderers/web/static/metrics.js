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
 * @typedef {{nd: null, failed: true, error: string, kind: string, metricId: string}} MetricFailure
 *   `kind`: needs_prediction, failed, missing_input, temporary or object_gone (ADR 0085). */

export class MetricClient {
  /** @param {import('./connection.js').FFastConnection} conn */
  constructor(conn) {
    this._conn = conn;
    this._nextId = 0;
    /** @type {Map<string, {resolve: Function}>} */
    this._byId = new Map();
    /** @type {Map<string, {requestId: string, label: string, holders: number,
     *   promise: Promise<MetricResult|MetricFailure|null>}>} label → request in flight */
    this._inFlight = new Map();
    conn.on(IN.METRIC_RESULT, (kw, args) => this._onResult(kw, args));
  }

  /**
   * Request a metric's computed array.
   * @param {string} metricId concrete (already resolved) metric id
   * @param {{params?: object, modelFp?: string|null, datasetFp?: string|null,
   *   signal?: AbortSignal|null}} [opts] `signal`: abort it when the result is
   *   no longer wanted (the panel was redrawn or closed). A request every asker
   *   has let go of is cancelled on the server (ADR 0085) and resolves null.
   * @returns {Promise<MetricResult|MetricFailure|null>} a failure when the
   *   metric ran and failed; null when the server can't compute it (e.g. a
   *   client-only model, no data — `ok:false` without an error).
   *
   * No clock gives up on a request: the server answers every one, also when
   * its reply cannot be built, and a metric on a large dataset takes minutes
   * (force metrics on 1.77M structures: 127-340 s; a 120 s limit drew
   * "No data", 2026-11-02).
   */
  request(metricId, { params = {}, modelFp = null, datasetFp = null, signal = null } = {}) {
    const label = requestLabel(metricId, params, modelFp, datasetFp);
    let entry = this._inFlight.get(label);
    if (!entry) {
      const requestId = `m${++this._nextId}`;
      entry = { requestId, label, holders: 0, promise: null };
      const made = entry;
      entry.promise = new Promise((resolve) => {
        this._byId.set(requestId, { resolve });
      }).finally(() => {
        if (this._inFlight.get(label) === made) this._inFlight.delete(label);
      });
      this._inFlight.set(label, entry);
      this._conn.send(OUT.REQUEST_METRIC, {
        metric_id: metricId,
        request_id: requestId,    // echoed back as args[0] → 1:1 correlation
        params,
        model_fp: modelFp,
        dataset_fp: datasetFp,
      });
    }
    entry.holders += 1;           // an asker with no signal holds it to the end
    if (signal) {
      const held = entry;
      if (signal.aborted) this._release(held);
      else signal.addEventListener('abort', () => this._release(held), { once: true });
    }
    return entry.promise;
  }

  /** One asker let go of `entry`; with none left and no answer yet, the
   * server is told to stop and the askers get null. */
  _release(entry) {
    entry.holders -= 1;
    if (entry.holders > 0) return;
    const waiter = this._byId.get(entry.requestId);
    if (!waiter) return;          // already answered
    this._byId.delete(entry.requestId);
    if (this._inFlight.get(entry.label) === entry) this._inFlight.delete(entry.label);
    this._conn.send(OUT.CANCEL_METRIC, { request_id: entry.requestId });
    waiter.resolve(null);
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
    waiter.resolve(
      kw.ok
        ? { nd: decodeNdarray(kw.values), shape: kw.shape, unit: kw.unit, dtype: kw.dtype,
            warnings: kw.warnings || [], metricId: kw.metric_id }
        : kw.error
          ? { nd: null, failed: true, error: kw.error, kind: kw.kind || 'failed', metricId: args[1] }
          : null,
    );
  }
}

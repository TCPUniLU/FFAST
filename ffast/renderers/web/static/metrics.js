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
 * Keeping (2026-11-13): the page keeps the results it was sent, up to
 * KEEP_VALUES numbers, and asks again naming the one it holds (`have`, the
 * server's tag for it). While that is still the stored result the server
 * answers "unchanged" with no values: a tab shown again, or a panel drawn
 * again, waits for one short reply instead of megabytes. The server decides,
 * so a result it dropped (a reload, a moved subset, a grown dataset, an
 * edited metric) is sent again.
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

/** Kept results hold at most this many numbers in all (8 bytes each). */
export const KEEP_VALUES = 32_000_000;

/** @typedef {{values: number[], shape: number[]}} DecodedArray */
/** @typedef {{nd: DecodedArray|null, shape: string, unit: string, dtype: string, warnings: string[], metricId: string}} MetricResult */
/** A metric that failed, and why (ADR 0066 rule 6).
 * @typedef {{nd: null, failed: true, error: string, kind: string, metricId: string}} MetricFailure
 *   `kind`: needs_prediction, failed, missing_input, temporary or object_gone (ADR 0085). */

/** A METRIC_RESULT's fields as the page holds them: a result, a failure
 * that says why, or null (no result and nothing to say). */
function decodeResult(kw, metricId) {
  if (kw.ok) {
    return { nd: kw.values ? decodeNdarray(kw.values) : null, shape: kw.shape, unit: kw.unit,
      dtype: kw.dtype, warnings: kw.warnings || [], metricId: kw.metric_id };
  }
  return kw.error
    ? { nd: null, failed: true, error: kw.error, kind: kw.kind || 'failed', metricId }
    : null;
}

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
    /** @type {Map<string, {tag: string, result: MetricResult, size: number}>}
     *   label → the result kept for it, least recently used first */
    this._kept = new Map();
    this._keptSize = 0;
    conn.on(IN.METRIC_RESULT, (kw, args) => this._onResult(kw, args));
    /** @type {Map<string, {resolve: Function, metrics: Object<string, string>}>} view request id → waiter */
    this._views = new Map();
    conn.on(IN.PLOT_VIEW, (kw, args) => this._onView(kw, args));
  }

  /**
   * The points of a plot's metrics that it can show (ADR 0099): the whole
   * results when they are small, else the points kept for this range and
   * size, with `idx` their frame numbers and `n` the full length.
   * @param {{kind: 'line'|'points', metrics: Object<string, string>,
   *   params?: Object<string, object>, modelFp?: string|null, datasetFp?: string|null,
   *   xRange?: number[]|null, yRange?: number[]|null, width: number, height: number,
   *   signal?: AbortSignal|null}} ask
   * @returns {Promise<{results: Object<string, MetricResult|MetricFailure|null>,
   *   idx: number[]|null, n: number|null}|null>} null when let go of
   */
  view({ kind, metrics, params = {}, modelFp = null, datasetFp = null,
    xRange = null, yRange = null, width, height, signal = null }) {
    const requestId = `v${++this._nextId}`;
    const promise = new Promise((resolve) => this._views.set(requestId, { resolve, metrics }));
    this._conn.send(OUT.REQUEST_PLOT_VIEW, {
      request_id: requestId, kind, metrics, params,
      model_fp: modelFp, dataset_fp: datasetFp,
      ...(xRange ? { x_range: xRange } : {}),
      ...(yRange ? { y_range: yRange } : {}),
      width: Math.round(width), height: Math.round(height),
    });
    if (signal) {
      const letGo = () => {
        const waiter = this._views.get(requestId);
        if (!waiter) return;                // already answered
        this._views.delete(requestId);
        this._conn.send(OUT.CANCEL_METRIC, { request_id: requestId });
        waiter.resolve(null);
      };
      if (signal.aborted) letGo();
      else signal.addEventListener('abort', letGo, { once: true });
    }
    return promise;
  }

  _onView(kw, args) {
    const requestId = args && args[0];
    const waiter = requestId != null ? this._views.get(requestId) : null;
    if (!waiter) return;
    this._views.delete(requestId);
    const results = {};
    // A failure names no metric: the role's metric is the one asked for.
    for (const [role, r] of Object.entries(kw.results || {})) results[role] = decodeResult(r, waiter.metrics[role]);
    const idx = kw.idx ? decodeNdarray(kw.idx)?.values || null : null;
    waiter.resolve({ results, idx, n: idx ? kw.n : null });
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
      const kept = this._kept.get(label) || null;
      entry.promise = new Promise((resolve) => {
        this._byId.set(requestId, { resolve, label, kept });
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
        ...(kept ? { have: kept.tag } : {}),
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
    if (kw.ok && kw.unchanged && waiter.kept && kw.tag === waiter.kept.tag) {
      this._keep(waiter.label, waiter.kept.tag, waiter.kept.result);   // used again: kept longest
      waiter.resolve(waiter.kept.result);
      return;
    }
    const result = kw.ok && !kw.unchanged ? decodeResult(kw, args[1]) : null;
    if (result && kw.tag) this._keep(waiter.label, kw.tag, result);
    else this._drop(waiter.label);
    waiter.resolve(result || decodeResult({ ...kw, ok: false }, args[1]));
  }

  /** Keep `result` for `label`, dropping the least recently used beyond KEEP_VALUES. */
  _keep(label, tag, result) {
    this._drop(label);
    const size = result.nd?.values?.length || 0;
    if (size > KEEP_VALUES) return;
    this._kept.set(label, { tag, result, size });
    this._keptSize += size;
    for (const [old, entry] of this._kept) {
      if (this._keptSize <= KEEP_VALUES) break;
      this._kept.delete(old);
      this._keptSize -= entry.size;
    }
  }

  _drop(label) {
    const entry = this._kept.get(label);
    if (!entry) return;
    this._kept.delete(label);
    this._keptSize -= entry.size;
  }
}

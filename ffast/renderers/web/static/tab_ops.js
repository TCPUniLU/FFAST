/**
 * User tabs over the wire (ADR 0056 rules 9, 10, 16): save, delete (for a
 * tab that replaces another, "Reset to original"), hide or show, and export.
 * Each is a request (ADR 0097): the server's final reply (TAB_SAVED,
 * TAB_EXPORTED) names it, then the new layout goes to every window as
 * TAB_LAYOUT. A failed request still resolves, with `ok: false` and `error`,
 * so callers read one shape.
 */

import { OUT } from './events.js';

/** Turn a request's outcome into the reply's kwargs; a failure keeps its
 * kwargs and says why in `error`. Exported for metric_ops.js. */
export function askFor(request, event, kwargs, failed) {
  return request(event, kwargs).then(
    (reply) => reply.kwargs,
    (err) => ({
      ...failed,
      ...(err?.reply?.kwargs || {}),
      ok: false,
      error: err?.connectionLost ? 'Disconnected' : (err?.message || String(err)),
    }),
  );
}

export class TabOps {
  /** @param {{request: (event: string, kwargs?: object) => Promise<{kwargs: object}>}} deps */
  constructor({ request }) {
    this._request = request;
  }

  /** Save a tab in the authoring form of a tab file; `previousName` is the
   * tab it was edited from, or null for a new tab.
   * @returns {Promise<{ok: boolean, action: string, name: string, error: string|null}>} */
  save(tab, previousName = null) {
    return this._saved(OUT.SAVE_TAB, { tab, previous_name: previousName });
  }

  remove(name) { return this._saved(OUT.DELETE_TAB, { name }); }

  setHidden(name, hidden) { return this._saved(OUT.HIDE_TAB, { name, hidden }); }

  /** Put the whole tab bar in this order; `[]` brings back the default (ADR 0070). */
  setOrder(names) { return this._saved(OUT.SET_TAB_ORDER, { names }); }

  /** @returns {Promise<{name: string, toml: string|null, error: string|null}>} */
  exportToml(name) { return askFor(this._request, OUT.EXPORT_TAB, { name }, { toml: null }); }

  /** Nothing to do: a request still waiting fails with its connection. */
  reset() {}

  _saved(event, kwargs) { return askFor(this._request, event, kwargs, {}); }
}

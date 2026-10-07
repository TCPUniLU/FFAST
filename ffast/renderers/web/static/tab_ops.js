/**
 * User tabs over the wire (ADR 0056 rules 9, 10, 16): save, delete (for a
 * tab that replaces another, "Reset to original"), hide or show, and export.
 * The server answers the window that asked (TAB_SAVED, TAB_EXPORTED) and then
 * sends the new layout to every window as TAB_LAYOUT. Answers come back in
 * the order the requests went, so each waits its turn in a queue.
 */

import { OUT } from './events.js';

export class TabOps {
  /** @param {{send: (event: string, kwargs?: object) => void}} deps */
  constructor({ send }) {
    this._send = send;
    this._saved = [];      // resolvers waiting for TAB_SAVED, oldest first
    this._exported = [];   // resolvers waiting for TAB_EXPORTED
  }

  /** Save a tab in the authoring form of a tab file; `previousName` is the
   * tab it was edited from, or null for a new tab.
   * @returns {Promise<{ok: boolean, action: string, name: string, error: string|null}>} */
  save(tab, previousName = null) {
    return this._ask(this._saved, OUT.SAVE_TAB, { tab, previous_name: previousName });
  }

  remove(name) { return this._ask(this._saved, OUT.DELETE_TAB, { name }); }

  setHidden(name, hidden) { return this._ask(this._saved, OUT.HIDE_TAB, { name, hidden }); }

  /** @returns {Promise<{name: string, toml: string|null, error: string|null}>} */
  exportToml(name) { return this._ask(this._exported, OUT.EXPORT_TAB, { name }); }

  onSaved(kw) { this._saved.shift()?.(kw); }

  onExported(kw) { this._exported.shift()?.(kw); }

  /** A disconnect answers nothing: whoever waits hears it failed. */
  reset() {
    for (const resolve of this._saved.splice(0))
      resolve({ ok: false, error: 'Disconnected' });
    for (const resolve of this._exported.splice(0))
      resolve({ toml: null, error: 'Disconnected' });
  }

  _ask(queue, event, kwargs) {
    return new Promise((resolve) => {
      queue.push(resolve);
      this._send(event, kwargs);
    });
  }
}

/** Optional reader setup on the connected cluster, before a file load (ADR 0101). */
import { OUT } from './events.js';
import { answerOf } from './requests.js';
import { labelBy, trapFocus } from './dialogs.js';

const SKIP_KEY = 'ffast.fast-reader.skip.';

export class FastReaderPrompt {
  constructor({ cluster, current }) {
    this._cluster = cluster;
    this._current = current;
    this._generation = 0;
  }

  /** Synchronous for local connections and unrelated file types. */
  beforeLoad(path, kind) {
    const cluster = this._cluster();
    if (!cluster || !['dataset', 'prediction'].includes(kind) || !/\.(extxyz|xyz)$/i.test(path || '')) return true;
    if (this._pending) return this._pending;
    const generation = this._generation;
    const pending = this._offer(cluster, generation);
    this._pending = pending;
    const clear = () => { if (this._pending === pending) this._pending = null; };
    pending.then(clear, clear);
    return pending;
  }

  cancel() {
    this._generation += 1;
    this._close?.(false);
    this._pending = null;
  }

  async _offer({ connection, name }, generation) {
    const key = SKIP_KEY + name;
    let skip = false;
    try { skip = localStorage.getItem(key) === 'true'; } catch (_) { /* private browsing */ }
    const request = (action, opts = {}) => answerOf(connection.request(OUT.FAST_READER, { action }, [], opts));
    const state = await request(skip ? 'skip' : 'offer');
    if (generation !== this._generation || !this._current(connection) || state.connectionLost) return false;
    if (state.error) throw new Error(state.error);
    if (state.ready || (!state.offer && !state.busy)) return true;
    return this._dialog({ state, key, connection, request });
  }

  _dialog({ state, key, connection, request }) {
    return new Promise((resolve) => {
      const modal = document.createElement('div');
      modal.id = 'fast-reader-dialog';
      modal.className = 'warning-modal';
      modal.setAttribute('role', 'dialog');
      modal.setAttribute('aria-modal', 'true');
      const panel = document.createElement('div');
      panel.className = 'warning-modal-panel';
      const head = document.createElement('div');
      head.className = 'warning-modal-title';
      head.textContent = state.busy ? 'Installing the fast reader' : 'Install the fast trajectory reader?';
      labelBy(modal, head);
      const message = document.createElement('p');
      message.className = 'warning-modal-message';
      message.id = 'fast-reader-message';
      modal.setAttribute('aria-describedby', message.id);
      message.textContent = state.managed
        ? `The optional reader ${state.missing ? 'is missing' : 'needs an update'} on this cluster. It can speed up .xyz and .extxyz loading. FFAST will try a matching download, then a source build if needed. Building requires Rust on the cluster.`
        : 'The fast reader is unavailable in this manually managed server environment. In its Python environment, run:\n\nffast install-fast-reader\n\nThen restart the server. You can load this file with Python now.';
      const checkRow = document.createElement('label');
      checkRow.className = 'warning-modal-check';
      const check = document.createElement('input');
      check.type = 'checkbox';
      checkRow.append(check, ' Don’t ask again for this cluster');
      const progress = document.createElement('p');
      progress.className = 'warning-modal-message';
      progress.setAttribute('role', 'status');
      progress.setAttribute('aria-live', 'polite');
      progress.hidden = true;
      const details = document.createElement('details');
      details.className = 'fast-reader-details';
      details.hidden = true;
      const summary = document.createElement('summary');
      summary.textContent = 'Installation details';
      const log = document.createElement('pre');
      details.append(summary, log);
      const footer = document.createElement('div');
      footer.className = 'warning-modal-footer';
      panel.append(head, message, checkRow, progress, details, footer);
      modal.append(panel);
      const untrap = trapFocus(modal);
      let ended = false;
      const finish = (load) => {
        if (ended) return;
        ended = true;
        modal.remove(); untrap();
        if (this._close === finish) this._close = null;
        resolve(load && this._current(connection));
      };
      this._close = finish;
      const remember = () => {
        if (check.checked) {
          try { localStorage.setItem(key, 'true'); } catch (_) { /* session choice still applies */ }
        }
      };
      const button = (label, fn) => {
        const b = document.createElement('button');
        b.textContent = label;
        b.addEventListener('click', fn);
        footer.append(b);
        return b;
      };
      const python = () => { remember(); finish(true); };
      const write = ({ kwargs }) => {
        if (ended || !kwargs.line) return;
        progress.textContent = kwargs.line;
        log.textContent = [...log.textContent.split('\n'), kwargs.line].slice(-100).join('\n').trimStart();
        log.scrollTop = log.scrollHeight;
      };
      const install = async (action) => {
        remember();
        head.textContent = 'Installing the fast reader';
        message.textContent = 'Installing in this cluster’s server environment. Cancelling this load leaves the installation running on the cluster.';
        checkRow.hidden = true;
        progress.hidden = false;
        progress.textContent = action === 'wait' ? 'Waiting for the installation started in another window…' : 'Preparing installation…';
        details.hidden = false;
        footer.replaceChildren();
        button('Cancel load', () => finish(false)).focus();
        const result = await request(action, { onProgress: write });
        if (ended) return;
        if (!this._current(connection) || result.connectionLost) { finish(false); return; }
        if (result.ready) { finish(true); return; }
        head.textContent = 'Continue with the Python reader?';
        message.textContent = result.error || 'The fast reader is still unavailable. You can load this file with the Python reader.';
        progress.hidden = true;
        footer.replaceChildren();
        button('Cancel load', () => finish(false));
        button('Continue with Python', () => finish(true)).focus();
      };
      button('Cancel load', () => finish(false));
      const fallback = button('Load with Python', python);
      if (state.managed) button('Install and load', () => install('install'));
      modal.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') { e.stopPropagation(); finish(false); }
      });
      document.body.append(modal);
      fallback.focus();
      if (state.busy) install('wait');
    });
  }
}

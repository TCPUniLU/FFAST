/**
 * Server-side write operations: save session, load session, export subset,
 * plus the path prompt they share (ADR 0050).
 *
 * All three write on the *server*, which has no native file dialog reachable
 * from a browser, so each collects a path through one small modal and then
 * reports the outcome the server sends back. PNG export is here too, as the one
 * export that is genuinely client-side (the canvas is local).
 *
 * These were five methods and two fields on `FFastApp`, and the session half
 * carried a correlation bug this module's design removes: completion was
 * inferred from whichever `TASK_DONE` arrived next, so a dataset load finishing
 * while a save was in flight reported "Saved session". The server now sends
 * `SESSION_SAVED` / `SESSION_LOADED` naming the operation and its path.
 */

import { OUT } from './events.js';

/**
 * Status line for a finished session operation.
 *
 * Pure, so the message matrix is testable without a browser.
 * @param {'save'|'load'} kind
 * @param {{ok?: boolean, path?: string, error?: string}} result
 * @returns {{text: string, kind: 'connected'|'error'}}
 */
export function sessionStatus(kind, result) {
  const saving = kind === 'save';
  const path = result?.path || '';
  if (result?.ok) {
    return {
      text: `${saving ? 'Saved' : 'Loaded'} session ${saving ? 'to' : 'from'} ${path}`,
      kind: 'connected',
    };
  }
  const why = result?.error ? `: ${result.error}` : '';
  return { text: `Session ${saving ? 'save' : 'load'} failed for ${path}${why}`, kind: 'error' };
}

/** Status line for a finished subset export. Pure. */
export function exportStatus(result) {
  if (result?.ok) {
    return { text: `Exported ${result.n} structure(s) → ${result.path}`, kind: 'connected' };
  }
  return { text: `Export failed: ${result?.error ?? 'unknown error'}`, kind: 'error' };
}

/** Filesystem-safe stem for a downloaded/exported file. Pure. */
export function safeName(name, fallback = 'ffast') {
  const cleaned = String(name ?? '').replace(/[^\w.-]+/g, '_').replace(/^_+|_+$/g, '');
  return cleaned || fallback;
}

const $ = (id) => document.getElementById(id);

export class SessionOps {
  /**
   * @param {{
   *   send: (event: string, kwargs?: object, args?: any[]) => void,
   *   setStatus: (text: string, kind?: string) => void,
   *   getCurrentDatasetFp: () => string | null,
   *   getDatasetMeta: (fp: string) => {name?: string} | undefined,
   *   capturePng: (opts: {transparent: boolean, background: string}) => string,
   * }} ports
   */
  constructor(ports) {
    this._ports = ports;
    this._onConfirm = null;
  }

  /** Wire the shared path modal's own controls. Called once, at app setup. */
  bindControls() {
    $('path-cancel').addEventListener('click', () => this._closePathModal());
    $('path-ok').addEventListener('click', () => this._confirmPathModal());
    $('path-modal').addEventListener('click', (e) => {
      if (e.target.id === 'path-modal') this._closePathModal();
    });
    $('path-input').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') this._confirmPathModal();
      if (e.key === 'Escape') this._closePathModal();
    });
  }

  saveSession() {
    this._openPathModal({
      title: 'Save Session',
      okLabel: 'Save',
      defaultValue: '~/ffast-session',
      onConfirm: (path) => {
        this._ports.send(OUT.SAVE_SESSION, { path });
        this._ports.setStatus(`Saving session to ${path}…`, 'connected');
      },
    });
  }

  loadSession() {
    this._openPathModal({
      title: 'Load Session',
      okLabel: 'Load',
      defaultValue: '~/ffast-session',
      onConfirm: (path) => {
        this._ports.send(OUT.LOAD_SESSION, { path });
        this._ports.setStatus(`Loading session from ${path}…`, 'connected');
      },
    });
  }

  /** The selected object-rail dataset is written server-side as extxyz. */
  exportSelectedDataset() {
    const fp = this._ports.getCurrentDatasetFp();
    if (!fp) return;
    const base = safeName(this._ports.getDatasetMeta(fp)?.name, 'subset');
    this._openPathModal({
      title: 'Export Subset (extxyz)',
      okLabel: 'Export',
      defaultValue: `~/${base}.extxyz`,
      onConfirm: (path) => {
        this._ports.send(OUT.EXPORT_SUBSET, { fingerprint: fp, path });
        this._ports.setStatus(`Exporting to ${path}…`, 'connected');
      },
    });
  }

  /** @param {{transparent: boolean, background: string}} opts */
  exportPng({ transparent, background }) {
    const url = this._ports.capturePng({ transparent, background });
    const fp = this._ports.getCurrentDatasetFp();
    const name = safeName(fp ? this._ports.getDatasetMeta(fp)?.name : null);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${name}_${transparent ? 'transparent' : 'opaque'}.png`;
    a.click();
    this._ports.setStatus('PNG exported', 'connected');
  }

  /** @param {'save'|'load'} kind */
  onSessionResult(kind, kw) {
    const { text, kind: statusKind } = sessionStatus(kind, kw);
    this._ports.setStatus(text, statusKind);
  }

  onSubsetExported(kw) {
    const { text, kind } = exportStatus(kw);
    this._ports.setStatus(text, kind);
  }

  /** Close the modal without acting — used when the connection drops. */
  reset() {
    this._closePathModal();
  }

  // ── internals ───────────────────────────────────────────────────────────

  _openPathModal({ title, okLabel, defaultValue = '', onConfirm }) {
    this._onConfirm = onConfirm;
    $('path-title').textContent = title;
    $('path-ok').textContent = okLabel;
    $('path-error').style.display = 'none';
    const input = $('path-input');
    input.value = defaultValue;
    $('path-modal').classList.remove('hidden');
    input.focus();
    input.select();
  }

  _closePathModal() {
    $('path-modal').classList.add('hidden');
    this._onConfirm = null;
  }

  _confirmPathModal() {
    const path = $('path-input').value.trim();
    if (!path) {
      const err = $('path-error');
      err.textContent = 'Enter a path first';
      err.style.display = 'block';
      return;
    }
    const onConfirm = this._onConfirm;
    this._closePathModal();
    onConfirm?.(path);
  }
}

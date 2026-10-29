/**
 * Server-side write operations: save session, load session, export subset
 * (ADR 0050).
 *
 * All three write on the *server*, which has no native file dialog reachable
 * from a browser, so each chooses its place in the file browser (the ports'
 * `pick`) and then reports the outcome the server sends back. PNG export is here too, as the one
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
    // An element subset's frames without its elements are not written (ADR 0067).
    const skipped = result.skipped
      ? ` (${result.skipped} empty frame${result.skipped === 1 ? '' : 's'} skipped)` : '';
    return { text: `Exported ${result.n} structure(s) → ${result.path}${skipped}`, kind: 'connected' };
  }
  return { text: `Export failed: ${result?.error ?? 'unknown error'}`, kind: 'error' };
}

/** What Export can write (the desktop's list, UI/SideBar.py saveDataset). */
export const EXPORT_FORMATS = [
  { format: 'extxyz', label: 'Extended XYZ', ext: '.extxyz' },
  { format: 'xyz', label: 'XYZ', ext: '.xyz' },
  { format: 'db', label: 'ASE database', ext: '.db' },
  { format: 'traj', label: 'ASE trajectory', ext: '.traj' },
  { format: 'vasp', label: 'VASP POSCAR (one structure)', ext: '.vasp' },
  { format: 'dftb', label: 'DFTB+ gen (one structure)', ext: '.gen' },
  { format: 'sgdml', label: 'sGDML', ext: '.npz' },
];

/** `name` with its ending replaced by `ext` (one ending only). Pure. */
export function withExtension(name, ext) {
  const stem = String(name ?? '').replace(/\.[A-Za-z0-9]+$/, '');
  return stem + ext;
}

/** Filesystem-safe stem for a downloaded/exported file. Pure. */
export function safeName(name, fallback = 'ffast') {
  const cleaned = String(name ?? '').replace(/[^\w.-]+/g, '_').replace(/^_+|_+$/g, '');
  return cleaned || fallback;
}

export class SessionOps {
  /**
   * @param {{
   *   send: (event: string, kwargs?: object, args?: any[]) => void,
   *   setStatus: (text: string, kind?: string) => void,
   *   getCurrentDatasetFp: () => string | null,
   *   getDatasetMeta: (fp: string) => {name?: string} | undefined,
   *   capturePng: (opts: {transparent: boolean, background: string}) => Promise<string>,
   *   pick: (opts: object) => void,
   * }} ports
   */
  constructor(ports) {
    this._ports = ports;
  }

  saveSession() {
    this._ports.pick({
      mode: 'save-folder',
      title: 'Save Session',
      okLabel: 'Save',
      name: 'ffast-session',
      onPick: (path) => {
        this._ports.send(OUT.SAVE_SESSION, { path });
        this._ports.setStatus(`Saving session to ${path}…`, 'connected');
      },
    });
  }

  loadSession() {
    this._ports.pick({
      mode: 'open-folder',
      title: 'Load Session',
      okLabel: 'Load',
      onPick: (path) => {
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
    this._ports.pick({
      mode: 'save-file',
      title: 'Export Dataset',
      okLabel: 'Export',
      name: `${base}.extxyz`,
      formats: EXPORT_FORMATS,
      format: 'extxyz',
      onPick: (path, { format }) => {
        this._ports.send(OUT.EXPORT_SUBSET, { fingerprint: fp, path, format });
        this._ports.setStatus(`Exporting to ${path}…`, 'connected');
      },
    });
  }

  /** @param {{transparent: boolean, background: string}} opts */
  async exportPng({ transparent, background }) {
    const url = await this._ports.capturePng({ transparent, background });
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
}

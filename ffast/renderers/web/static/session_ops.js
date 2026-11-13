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
import { answerOf } from './requests.js';

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

/**
 * What a format cannot hold, said before writing (ADR 0095, decision 11): for
 * the Export dialog's tick boxes, the reasons to grey some out, and a reason
 * to refuse the export altogether. The server asks the same of the same
 * words (`ffast/readers/writing.py`, `check_writable`). Pure.
 * @param {{label?: string, can_write?: object}} info the format
 * @param {{key: string, checked: boolean}[]} items the reference and the predictions
 * @param {{variable?: boolean, n?: number}} dataset
 * @returns {{disabled: Object<string,string>, blocked: string|null, note: string|null}}
 *   `note` says what is left out without refusing (a format with no forces).
 */
export function exportPlan(info, items, { variable = false, n = 0 } = {}) {
  const can = info?.can_write || {};
  const label = info?.label || 'This format';
  const disabled = {};
  let blocked = null;
  if (can.variable_atoms === false && variable) {
    blocked = `An ${label} file needs the same atoms in the same order in every structure, `
      + "and this dataset's structures differ.";
  } else if (can.frames != null && n > can.frames) {
    blocked = `${label} holds ${can.frames === 1 ? 'one structure' : `${can.frames} structures`}; `
      + `this dataset has ${n}.`;
  }
  const ticked = items.filter((i) => i.checked);
  // A limit greys the other boxes once it is reached and blocks above it. The
  // page does not know which set has forces, so every ticked box counts; the
  // server counts exactly (check_writable).
  const limitTo = (limit, what) => {
    const sets = limit === 1 ? 'set' : 'sets';
    if (ticked.length > limit) {
      blocked ||= `${label} holds ${limit} ${sets} of ${what}; ${ticked.length} are chosen.`;
    } else if (ticked.length === limit) {
      for (const i of items.filter((x) => !x.checked)) {
        disabled[i.key] ||= `${label} holds ${limit} ${sets} of ${what}: untick one first.`;
      }
    }
  };
  let note = null;
  if (can.energies === 0) {
    for (const i of items) disabled[i.key] = `${label} holds no energies or forces.`;
  } else {
    if (can.energies != null) limitTo(can.energies, 'energies and forces');
    if (can.forces === 0) {
      if (ticked.length) note = `${label} holds no forces; they are left out.`;
    } else if (can.forces != null) {
      limitTo(can.forces, 'forces');
    }
  }
  return { disabled, blocked, note };
}

/** The status line of a write in progress (SUBSET_EXPORTED with `progress`), else null. Pure. */
export function exportProgress(result) {
  if (!result?.progress) return null;
  const [done, total] = result.progress;
  return {
    text: `Exporting: ${done.toLocaleString('en-US')} of ${total.toLocaleString('en-US')} structures…`,
    kind: 'connected',
  };
}

/** What Export can write when the server lists none. */
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
        this._ports.setStatus(`Saving session to ${path}…`, 'connected');
        this._ask('save', OUT.SAVE_SESSION, { path });
      },
    });
  }

  loadSession() {
    this._ports.pick({
      mode: 'open-folder',
      title: 'Load Session',
      okLabel: 'Load',
      onPick: (path) => {
        this._ports.setStatus(`Loading session from ${path}…`, 'connected');
        this._ask('load', OUT.LOAD_SESSION, { path });
      },
    });
  }

  /** The selected object-rail dataset is written server-side: as extxyz unless
   * another of the server's formats is chosen, with the reference values and
   * the predictions ticked (ADR 0095). */
  exportSelectedDataset() {
    const fp = this._ports.getCurrentDatasetFp();
    if (fp) this.exportDataset(fp);
  }

  /** The Export dialog for dataset `fp` (a rail row's Export…). With
   * `prediction`, only that prediction is ticked; else the reference values. */
  exportDataset(fp, { prediction = null } = {}) {
    const meta = this._ports.getDatasetMeta(fp);
    const base = safeName(meta?.name, 'subset');
    const predictions = this._ports.getPredictions?.(fp) ?? [];
    this._ports.pick({
      mode: 'save-file',
      title: 'Export Dataset',
      okLabel: 'Export',
      name: `${base}.extxyz`,
      formats: 'writers',
      format: 'extxyz',
      items: [{ key: 'reference', label: 'Reference values', checked: !prediction },
        ...predictions.map((p) => ({ key: p.fp, label: p.name || p.fp.slice(0, 8), checked: p.fp === prediction }))],
      dataset: { variable: Boolean(meta?.variable), n: meta?.n ?? 0 },
      onPick: (path, { format, items }) => {
        const kwargs = { fingerprint: fp, path, format };
        if (items) {
          kwargs.reference = items.includes('reference');
          kwargs.predictions = items.filter((k) => k !== 'reference');
        }
        this._ports.setStatus(`Exporting to ${path}…`, 'connected');
        // A long write reports how far it is (progress), then ends (ADR 0097).
        const onProgress = (reply) => this.onSubsetExported(reply.kwargs);
        answerOf(this._ports.request(OUT.EXPORT_SUBSET, kwargs, [], { onProgress }))
          .then((kw) => { if (!kw.connectionLost) this.onSubsetExported({ ok: !kw.error, ...kw }); });
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

  /** A save or load, followed to its own final reply (ADR 0097). */
  _ask(kind, event, kwargs) {
    answerOf(this._ports.request(event, kwargs)).then((kw) => {
      if (!kw.connectionLost) this.onSessionResult(kind, { ok: !kw.error, ...kw });
    });
  }

  /** @param {'save'|'load'} kind */
  onSessionResult(kind, kw) {
    const { text, kind: statusKind } = sessionStatus(kind, kw);
    this._ports.setStatus(text, statusKind);
  }

  onSubsetExported(kw) {
    const { text, kind } = exportProgress(kw) ?? exportStatus(kw);
    this._ports.setStatus(text, kind);
  }
}

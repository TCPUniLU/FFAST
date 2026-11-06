/**
 * The "Make reference" dialog (ADR 0086): fit a reference bundle on one
 * loaded dataset and calibrate its thresholds on another, on the server, as
 * `ffast bundle` does. The bundle and its LAMMPS include file are written on
 * the server; the dialog then shows the two lines to add.
 */
import { labelBy, trapFocus } from './dialogs.js';
import { helpToggle } from './help.js';

export const DEFAULT_FOLDER = '~/ffast-references';

/** Why the chosen pair cannot make a reference, or '' (pure, for tests). */
export function referenceProblem(trainFp, validFp, datasets) {
  if (datasets.length < 2) {
    return 'Needs two datasets: thresholds are calibrated on frames the model was not fitted on. '
      + 'Load a validation set, or split this one into subsets first.';
  }
  if (!trainFp || !validFp) return 'Pick a training and a validation dataset.';
  if (trainFp === validFp) return 'Training and validation must be different datasets.';
  return '';
}

/** A bundle name from a dataset name: "aspirin train.xyz" → "aspirin_train". */
export function referenceName(datasetName) {
  const stem = String(datasetName || 'reference').replace(/\.[A-Za-z0-9]+$/, '');
  return stem.replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^_+|_+$/g, '') || 'reference';
}

/**
 * @param {{datasets: Array<{fp: string, name: string, n: number}>,
 *   send: (kw: object) => void, pickFolder?: (onPick: (path: string) => void) => void}} o
 * @returns {{done: (kw: object) => void, close: () => void}}
 */
export function openMakeReferenceDialog({ datasets, send, pickFolder = null }) {
  document.getElementById('make-reference-dialog')?.remove();
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  const modal = el('div', 'warning-modal');
  modal.id = 'make-reference-dialog';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  const panel = el('div', 'warning-modal-panel');
  const head = el('div', 'warning-modal-title', 'Make reference');
  labelBy(modal, head);
  const body = el('div', 'make-ref-body');

  const select = (id, index) => {
    const s = el('select', 'make-ref-select');
    s.id = id;
    if (datasets.length < 2 && index === 1) {
      s.appendChild(el('option', '', '— only one dataset loaded —'));
      s.disabled = true;
      return s;
    }
    for (const d of datasets) {
      const o = el('option', '', `${d.name} · ${Number(d.n || 0).toLocaleString('en-US')} frames`);
      o.value = d.fp;
      s.appendChild(o);
    }
    if (datasets[index]) s.value = datasets[index].fp;
    return s;
  };
  const train = select('make-ref-train', 0);
  const valid = select('make-ref-valid', 1);
  const name = el('input', 'make-ref-name');
  name.id = 'make-ref-name';
  name.value = referenceName(datasets[0]?.name);
  const folder = el('input', 'make-ref-folder');
  folder.id = 'make-ref-folder';
  folder.value = DEFAULT_FOLDER;
  const browse = el('button', '', '…');
  browse.type = 'button';
  browse.title = 'Choose a folder on the server';
  browse.hidden = !pickFolder;
  browse.addEventListener('click', () => pickFolder?.((path) => { folder.value = path; check(); }));
  // Small print behind a "?", as in the settings sidebar.
  const settingsHelp = helpToggle('False-alarm rate per frame 1 % · tracks: bonds, angles, dihedrals, ring · '
    + 'alarm when 20 % of frames are marked within the persistence time (5 ps; set per input with '
    + 'ffast_persist_ps).', { subject: 'the detection settings' });
  settingsHelp.note.classList.add('make-ref-settings');
  const settingsHead = el('div', 'make-ref-settings-head');
  settingsHead.append(el('span', '', 'Detection settings'), settingsHelp.button);
  const settings = [settingsHead, settingsHelp.note];
  const problem = el('div', 'make-ref-problem');
  const result = el('div', 'make-ref-result');

  const row = (label, ...field) => {
    const l = el('label', 'make-ref-row');
    l.append(el('span', '', label), ...field);
    return l;
  };
  body.append(row('Training frames', train), row('Validation frames', valid), row('Name', name),
    row('Save in', folder, browse), ...settings, problem, result);

  const foot = el('div', 'warning-modal-footer');
  const cancel = el('button', '', 'Cancel');
  const make = el('button', 'make-ref-go', 'Make reference');
  foot.append(cancel, make);
  panel.append(head, body, foot);
  modal.appendChild(panel);

  const check = () => {
    let p = referenceProblem(train.value, valid.disabled ? '' : valid.value, datasets);
    if (!p && !name.value.trim()) p = 'Give the bundle a name.';
    if (!p && /\s/.test(name.value.trim() + folder.value.trim())) p = 'LAMMPS cannot read paths with spaces.';
    problem.textContent = p;
    make.disabled = !!p;
    return !p;
  };
  for (const f of [train, valid, name, folder]) f.addEventListener('input', check);
  train.addEventListener('change', () => {
    const d = datasets.find((x) => x.fp === train.value);
    if (d) name.value = referenceName(d.name);
    check();
  });
  check();

  let untrap = () => {};
  const close = () => { modal.remove(); untrap(); };
  cancel.addEventListener('click', close);
  modal.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  make.addEventListener('click', () => {
    if (!check()) return;
    make.disabled = true;
    result.replaceChildren(el('div', 'make-ref-wait', 'Fitting the reference on the server…'));
    send({ train: train.value, valid: valid.value, name: name.value.trim(), folder: folder.value.trim() });
  });
  untrap = trapFocus(modal);
  document.body.appendChild(modal);
  train.focus();

  /** REFERENCE_MADE: the paths written and the lines to add, or why not. */
  function done(kw) {
    if (!modal.isConnected) return;
    if (kw.error) {
      result.replaceChildren(el('div', 'make-ref-error', kw.error));
      make.disabled = false;
      return;
    }
    const copyLine = (text) => {
      const line = el('div', 'make-ref-line');
      const code = el('code', '', text);
      const btn = el('button', '', 'Copy');
      btn.type = 'button';
      btn.addEventListener('click', () => navigator.clipboard?.writeText(text));
      line.append(code, btn);
      return line;
    };
    result.replaceChildren(
      el('div', 'make-ref-ok', `Reference written: ${kw.bundle} (${kw.n_train} training, ${kw.n_valid} `
        + `validation frames, ${kw.tests.length} tests).`),
      el('div', '', 'Add to the LAMMPS input, after the atoms exist:'), copyLine(kw.lines[0]),
      el('div', '', 'Run the job with:'), copyLine(kw.lines[1]));
    cancel.textContent = 'Close';
  }
  return { done, close };
}

/**
 * The "Score against reference" dialog (ADR 0093): score one loaded dataset (or a
 * live run's frames so far) with a reference bundle, on the server. It asks for the
 * reference, the time between frames and the persistence, then sends SCORE_DATASET;
 * the scored item appears in the list at once and fills in.
 */
import { labelBy, trapFocus } from './dialogs.js';

export const DEFAULT_PERSIST_PS = 5;
const TIME_FROM_FILE = 'fs, from the file\'s time field: check the unit';
const TIME_EMPTY = 'empty: frames are marked, alarms are off';

/** A number typed in a field: the number, null when empty, NaN when it is not a positive number. */
export function positiveNumber(text) {
  const t = String(text ?? '').trim();
  if (!t) return null;
  const v = Number(t);
  return Number.isFinite(v) && v > 0 ? v : NaN;
}

/** Why Score cannot go ahead, or '' (pure, for tests). */
export function scoreProblem({ info, bundle, timeText, persistText }) {
  if (info?.detectana) return info.detectana;
  if (info?.refusal) return info.refusal;
  if (!bundle) return 'Pick a reference.';
  if (Number.isNaN(positiveNumber(timeText))) return 'The time between frames must be a positive number, or empty.';
  const persist = positiveNumber(persistText);
  if (persist === null || Number.isNaN(persist)) return 'The persistence must be a positive number of ps.';
  return '';
}

/**
 * The scored item already made with these settings (hidden ones too), or null.
 * `existing` is the dialog info's list; `timeFs` null for no time.
 */
export function existingItem(existing, bundle, timeFs, persistPs) {
  const close = (a, b) => (a === null || b === null ? a === b : Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(a), Math.abs(b)));
  return (existing || []).find((x) => x.bundle === bundle
    && close(x.settings?.frame_dt_ps == null ? null : x.settings.frame_dt_ps * 1000, timeFs)
    && close(Number(x.settings?.persist_ps ?? DEFAULT_PERSIST_PS), persistPs)) || null;
}

/**
 * @param {{info: object, send: (kw: object) => void,
 *   pickBundle?: (onPick: (path: string) => void) => void}} o
 *   `info` is the SCORE_DIALOG_INFO_RESULT of the dataset; `send` gets the SCORE_DATASET payload.
 * @returns {{close: () => void}}
 */
export function openScoreDialog({ info, send, pickBundle = null }) {
  document.getElementById('score-dialog')?.remove();
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  const modal = el('div', 'warning-modal');
  modal.id = 'score-dialog';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  const panel = el('div', 'warning-modal-panel');
  const head = el('div', 'warning-modal-title', 'Score against reference');
  labelBy(modal, head);
  const body = el('div', 'make-ref-body');

  const references = (info.references || []).filter((r) => !r.problem);
  const ref = el('select', 'make-ref-select');
  ref.id = 'score-ref';
  const addReference = (bundle, text) => {
    const o = el('option', '', text);
    o.value = bundle;
    ref.appendChild(o);
    return o;
  };
  for (const r of references) {
    addReference(r.bundle, `${r.name}${r.formula ? ` · ${r.formula}` : ''} · ${r.bundle}`);
  }
  if (!references.length) {
    // An empty value: without it the option's text would be sent as the bundle path.
    const none = el('option', '', '— none recorded: use Browse… —');
    none.value = '';
    ref.appendChild(none);
  }
  const browse = el('button', 'score-browse', 'Browse…');
  browse.type = 'button';
  browse.title = 'Choose a .bundle folder on the server';
  browse.hidden = !pickBundle;
  browse.addEventListener('click', () => pickBundle?.((path) => {
    const known = [...ref.options].find((o) => o.value === path);
    if (known) ref.value = path;
    else { addReference(path, path); ref.value = path; }
    check();
  }));

  const time = el('input', 'score-time');
  time.id = 'score-time';
  time.setAttribute('inputmode', 'decimal');
  time.value = info.frame_dt_fs == null ? '' : String(info.frame_dt_fs);
  const timeNote = el('div', 'score-note');
  const persist = el('input', 'score-persist');
  persist.id = 'score-persist';
  persist.setAttribute('inputmode', 'decimal');
  persist.value = String(info.persist_ps ?? DEFAULT_PERSIST_PS);

  const row = (label, ...field) => {
    const l = el('label', 'make-ref-row');
    l.append(el('span', '', label), ...field);
    return l;
  };
  const problem = el('div', 'make-ref-problem');
  const hint = el('div', 'score-note');
  body.append(
    el('div', 'score-dataset', `${info.name} · ${Number(info.n_frames || 0).toLocaleString('en-US')} frames`),
    row('Reference', ref, browse), row('Time between frames (fs)', time), timeNote,
    row('Persistence (ps)', persist), problem, hint);

  const foot = el('div', 'warning-modal-footer');
  const cancel = el('button', '', 'Cancel');
  const go = el('button', 'score-go', 'Score');
  foot.append(cancel, go);
  panel.append(head, body, foot);
  modal.appendChild(panel);

  const values = () => ({
    bundle: ref.value && ref.selectedOptions[0]?.value ? ref.value : '',
    timeFs: positiveNumber(time.value),
    persist: positiveNumber(persist.value),
  });
  const check = () => {
    const v = values();
    timeNote.textContent = time.value.trim() === '' ? TIME_EMPTY
      : info.frame_dt_fs != null && Number(time.value) === info.frame_dt_fs ? TIME_FROM_FILE : '';
    const p = scoreProblem({ info, bundle: v.bundle, timeText: time.value, persistText: persist.value });
    problem.textContent = p;
    const saved = !p ? existingItem(info.existing, v.bundle, v.timeFs, v.persist) : null;
    go.textContent = saved ? 'Show saved scores' : 'Score';
    hint.textContent = saved && saved.state !== 'done'
      ? 'Scored before and not finished: the missing frames are scored now.' : '';
    go.disabled = !!p;
    return !p;
  };
  for (const f of [ref, time, persist]) f.addEventListener('input', check);
  check();

  let untrap = () => {};
  const close = () => { modal.remove(); untrap(); };
  cancel.addEventListener('click', close);
  modal.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  go.addEventListener('click', () => {
    if (!check()) return;
    const v = values();
    send({ fingerprint: info.fingerprint, bundle: v.bundle, frame_dt_fs: v.timeFs, persist_ps: v.persist });
    close();
  });
  untrap = trapFocus(modal);
  document.body.appendChild(modal);
  ref.focus();
  return { close };
}

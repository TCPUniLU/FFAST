/**
 * Metrics pane (ADR 0057): define your own metrics as formulas while the app
 * runs. Lists every metric by origin (built-in, project, user); the editor
 * below it checks the formula as you type, tries it on the whole dataset
 * without saving, and saves it on the server so every window sees it.
 *
 * Only user metrics are editable. A built-in or project formula metric shows
 * its formula and offers Duplicate, which starts a new user metric from it.
 * The decisions (names, suggestions, Fix, what a draft saves as) live in
 * metric_logic.js; the wire is metric_ops.js. This file draws and wires.
 */

import { icon } from '../icons.js';
import {
  ID_PREFIX, completeAt, duplicateDraft, fixFor, formatStat, groupInputs, groupMetrics,
  idProblem, insertAt, isDirty, noValueLine, renameVariable, shapeHint, shortcutFor, sideMark,
  suggestions, toDraft, toPayload, tryHeadline, undefinedNames, uniqueName, valueNames,
  variableNameProblem,
} from '../metric_logic.js';

const ORIGIN_TITLES = { 'built-in': 'Built-in', project: 'Project', user: 'User' };
const CHECK_DELAY_MS = 150;

/** @param {string} tag @param {string} [cls] @param {string} [text] */
function el(tag, cls = '', text = '') {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

/**
 * @param {HTMLElement} body the Metrics drawer's body (app.js), on every tab
 * @param {{
 *   ops: import('../metric_ops.js').MetricOps,
 *   getContext: () => {datasetFp: string|null, modelFp: string|null},
 *   nameOf?: (context: {datasetFp: string|null, modelFp: string|null}) => string,
 *   onGoToFrame?: (frame: number, context: {datasetFp: string|null, modelFp: string|null}) => void,
 *   onColourAtoms?: (metricId: string) => void,
 *   onPlot?: (metricId: string) => void,
 * }} callbacks
 */
export function createMetricsPane(body, callbacks) {
  const { ops } = callbacks;

  // ── state ────────────────────────────────────────────────────────────────
  let catalog = [];
  let userMetrics = [];
  let metricUsers = {};       // METRIC_USERS: metric id → who uses it
  let inputs = [];
  let selected = null;        // the list entry being shown, or null
  let isNew = false;          // a metric not saved yet (New, or Duplicate)
  let draft = null;           // the formula being edited
  let saved = null;           // the user entry the draft was loaded from
  let check = { ok: null, error: '', shape: null };   // the server's last word
  let conflict = false;       // the server's copy changed under unsaved edits
  let conflictText = '';      // what the server said: changed, or deleted, in another window
  let message = { text: '', error: false };
  let tryResult = null;
  let tryProgress = null;
  let tryContext = null;   // the data the shown Try ran on
  let exported = null;
  let filter = '';
  let deleteArmed = false;
  let checkTimer = null;
  let caret = [0, 0];

  // ── layout ───────────────────────────────────────────────────────────────
  const toolbar = el('div', 'mx-toolbar');
  const newBtn = el('button', '', 'New metric');
  const search = el('input');
  search.type = 'search';
  search.placeholder = 'Filter metrics';
  search.setAttribute('aria-label', 'Filter metrics');
  toolbar.append(newBtn, search);
  const listEl = el('div', 'mx-list');
  const editor = el('div', 'mx-editor');
  editor.hidden = true;
  body.append(toolbar, listEl, editor);

  newBtn.addEventListener('click', () => startNew());
  search.addEventListener('input', () => { filter = search.value.trim().toLowerCase(); renderList(); });

  // editor parts (built once, filled by `renderEditor`)
  const heading = el('div', 'mx-heading');
  const notice = el('div', 'mx-notice');
  // A Python metric's code, read only (the server sends it in the catalog).
  const sourceBox = el('details', 'mx-source');
  const sourceSummary = el('summary', '', 'Python code');
  const sourceCopy = el('button', 'mx-source-copy', 'Copy');
  sourceCopy.type = 'button';
  const sourceCode = el('pre', 'mx-source-code');
  sourceBox.append(sourceSummary, sourceCode, sourceCopy);
  sourceCopy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(sourceCode.textContent);
      sourceCopy.textContent = 'Copied';
    } catch {
      // No clipboard here (an insecure page): select the code to copy by hand.
      window.getSelection()?.selectAllChildren(sourceCode);
    }
  });
  const idInput = el('input'); idInput.type = 'text'; idInput.setAttribute('aria-label', 'Metric id');
  const labelInput = el('input'); labelInput.type = 'text'; labelInput.setAttribute('aria-label', 'Label');
  const unitInput = el('input'); unitInput.type = 'text'; unitInput.setAttribute('aria-label', 'Unit');
  const fieldRows = el('div', 'mx-fields');
  for (const [text, input] of [['Id', idInput], ['Label', labelInput], ['Unit', unitInput]]) {
    const r = el('label', 'mx-field');
    r.append(el('span', '', text), input);
    fieldRows.appendChild(r);
  }
  const formula = el('textarea', 'mx-formula');
  formula.rows = 3;
  formula.spellcheck = false;
  formula.setAttribute('aria-label', 'Formula');
  formula.placeholder = 'e.g. norm(pred_F - ref_F)';
  const suggest = el('div', 'mx-suggest');
  suggest.hidden = true;
  const varsEl = el('div', 'mx-vars');
  const checkEl = el('div', 'mx-check');
  const fixBtn = el('button', 'mx-fix', 'Fix');
  fixBtn.hidden = true;
  const checkRow = el('div', 'mx-check-row');
  checkRow.append(checkEl, fixBtn);
  const inputSearch = el('input'); inputSearch.type = 'search'; inputSearch.placeholder = 'Search inputs';
  inputSearch.setAttribute('aria-label', 'Search inputs');
  const inputsEl = el('div', 'mx-inputs');
  const actions = el('div', 'mx-actions');
  const tryBtn = el('button', '', 'Try');
  const saveBtn = el('button', 'mx-primary', 'Save');
  const dupBtn = el('button', '', 'Duplicate');
  const delBtn = el('button', '', 'Delete');
  const exportBtn = el('button', '', 'Export');
  actions.append(tryBtn, saveBtn, dupBtn, delBtn, exportBtn);
  const shortcutsEl = el('div', 'mx-shortcuts');
  const conflictEl = el('div', 'mx-conflict');
  const tryEl = el('div', 'mx-try');
  const usedByEl = el('div', 'mx-usedby');
  const messageEl = el('div', 'mx-message');
  const exportEl = el('div', 'mx-export');
  editor.append(heading, notice, sourceBox, fieldRows, formula, suggest, varsEl, checkRow,
    el('div', 'mx-subhead', 'Inputs'), inputSearch, inputsEl,
    actions, shortcutsEl, conflictEl, tryEl, usedByEl, messageEl, exportEl);

  // ── derived ──────────────────────────────────────────────────────────────
  const readOnly = () => !!selected && !isNew && selected.origin !== 'user';
  const takenIds = () => [...catalog.map((m) => m.id), ...userMetrics.map((m) => m.id)];
  const varNames = () => draft.vars.map((v) => v.name);
  const context = () => callbacks.getContext?.() || { datasetFp: null, modelFp: null };

  /** What stops a save, in words; '' when nothing does. */
  function problem() {
    if (!draft) return '';
    if (isNew) { const p = idProblem(draft.id, takenIds()); if (p) return p; }
    if (!draft.expr.trim()) return 'Type a formula';
    const bad = undefinedNames(draft.expr, varNames());
    if (bad.length) return `${bad[0]} is not an input`;
    if (check.ok === false) return check.error;
    return '';
  }

  // ── list ─────────────────────────────────────────────────────────────────
  function renderList() {
    listEl.replaceChildren();
    const groups = groupMetrics(catalog, userMetrics, metricUsers);
    for (const origin of ['user', 'project', 'built-in']) {
      const items = groups[origin].filter((m) =>
        !filter || `${m.id} ${m.label}`.toLowerCase().includes(filter));
      if (origin !== 'user' && !items.length) continue;
      const group = el('div', 'mx-group');
      group.dataset.origin = origin;
      group.appendChild(el('div', 'mx-group-title', ORIGIN_TITLES[origin]));
      if (!items.length) group.appendChild(el('div', 'mx-empty', filter ? 'No match' : 'None yet — use New metric'));
      for (const m of items) {
        const item = el('button', 'mx-item');
        item.type = 'button';
        item.dataset.id = m.id;
        item.classList.toggle('selected', !isNew && selected?.id === m.id);
        item.append(el('span', 'mx-item-label', m.label), el('span', 'mx-item-id', m.id));
        if (m.status === 'hidden') item.append(el('span', 'mx-badge', 'hidden'));
        else if (m.status === 'error') item.append(el('span', 'mx-badge bad', 'error'));
        item.addEventListener('click', () => select(m));
        group.appendChild(item);
      }
      listEl.appendChild(group);
    }
  }

  // ── selecting ────────────────────────────────────────────────────────────
  function select(entry) {
    selected = entry;
    isNew = false;
    saved = entry.origin === 'user' ? entry : null;
    draft = entry.expr != null ? toDraft(entry) : null;
    resetEditorState();
    renderList();
    renderEditor();
    if (draft) scheduleCheck(true);
  }

  function startNew(from = null) {
    selected = null;
    isNew = true;
    saved = null;
    draft = from ? { ...from, vars: from.vars.map((v) => ({ ...v })) }
      : { id: ID_PREFIX, label: '', unit: '', expr: '', vars: [] };
    resetEditorState();
    renderList();
    renderEditor();
    if (draft.expr) scheduleCheck(true);
    (from ? idInput : formula).focus();
  }

  function duplicate() {
    if (!selected) return;
    const base = duplicateDraft(selected);
    const tail = selected.id.split('.').pop();
    let id = `${ID_PREFIX}${tail}_copy`;
    for (let n = 2; takenIds().includes(id); n++) id = `${ID_PREFIX}${tail}_copy${n}`;
    startNew({ ...base, id, label: base.label ? `${base.label} (copy)` : '' });
  }

  function resetEditorState() {
    check = { ok: null, error: '', shape: null };
    conflict = false;
    message = { text: '', error: false };
    tryResult = null;
    tryProgress = null;
    exported = null;
    deleteArmed = false;
    suggest.hidden = true;
  }

  // ── editor ───────────────────────────────────────────────────────────────
  function renderEditor() {
    editor.hidden = !selected && !isNew;
    if (editor.hidden) return;
    const ro = readOnly();
    const title = isNew ? 'New metric' : `${selected.label}`;
    heading.textContent = title;
    heading.dataset.id = isNew ? '' : selected.id;
    notice.textContent = '';
    if (!isNew && selected.status === 'hidden') notice.textContent = selected.message;
    else if (!isNew && selected.status === 'error') notice.textContent = `Does not compile: ${selected.message}`;
    else if (ro && selected.expr == null && selected.equivalent_expr)
      notice.textContent = 'A Python metric: read only. Duplicate makes a formula that computes the same, for you to edit.';
    else if (ro && selected.expr == null)
      notice.textContent = 'A Python metric: it averages, totals or smooths, which a formula cannot do yet. '
        + 'Duplicate makes a formula that takes this metric as its input, for you to change.';
    else if (ro) notice.textContent = `A ${ORIGIN_TITLES[selected.origin].toLowerCase()} metric: read only. Duplicate makes a copy you can edit.`;
    notice.hidden = !notice.textContent;
    const source = !isNew && selected?.expr == null ? selected?.source : null;
    sourceBox.hidden = !source;
    if (source && sourceCode.textContent !== source) {
      sourceCode.textContent = source;
      sourceCopy.textContent = 'Copy';
    }

    fieldRows.hidden = !draft;
    formula.hidden = !draft;
    checkRow.hidden = !draft || ro;
    for (const part of document.querySelectorAll('.mx-subhead')) part.hidden = !draft || ro;
    inputSearch.hidden = inputsEl.hidden = !draft || ro;
    if (draft) {
      idInput.value = draft.id;
      idInput.readOnly = !isNew;
      labelInput.value = draft.label;
      unitInput.value = draft.unit;
      labelInput.readOnly = unitInput.readOnly = ro;
      if (formula.value !== draft.expr) formula.value = draft.expr;
      formula.readOnly = ro;
    }
    renderVars();
    renderCheck();
    renderInputs();
    renderButtons();
    renderConflict();
    renderTry();
    renderMessage();
    renderUsedBy();
    renderExport();
  }

  function renderVars() {
    varsEl.replaceChildren();
    if (!draft || (!draft.vars.length && readOnly())) { varsEl.hidden = true; return; }
    varsEl.hidden = false;
    const used = new Set(valueNames(draft.expr));
    for (const [i, v] of draft.vars.entries()) {
      const r = el('div', 'mx-var');
      r.classList.toggle('unused', !used.has(v.name));
      const name = el('input'); name.type = 'text'; name.value = v.name;
      name.setAttribute('aria-label', `Name for ${v.ref}`);
      name.readOnly = readOnly();
      name.addEventListener('change', () => renameVar(i, name.value.trim(), name));
      const ref = el('span', 'mx-var-ref', v.ref);
      ref.title = v.ref;
      const arrow = el('span', 'mx-var-arrow');
      arrow.innerHTML = icon('chevron-right');
      r.append(name, arrow, ref);
      if (!readOnly()) {
        const rm = el('button', 'mx-var-remove');
        rm.innerHTML = icon('close');
        rm.setAttribute('aria-label', 'Remove this variable');
        rm.title = 'Remove this variable';
        rm.type = 'button';
        rm.addEventListener('click', () => { draft.vars.splice(i, 1); changed(); });
        r.appendChild(rm);
      }
      varsEl.appendChild(r);
    }
  }

  function renameVar(i, to, nameInput) {
    const from = draft.vars[i].name;
    if (to === from) return;
    const why = variableNameProblem(to, varNames().filter((_, j) => j !== i));
    if (why) { nameInput.value = from; setMessage(why, true); return; }
    draft.expr = renameVariable(draft.expr, from, to);
    draft.vars[i].name = to;
    formula.value = draft.expr;
    changed();
  }

  function renderCheck() {
    fixBtn.hidden = true;
    checkEl.className = 'mx-check';
    if (!draft || readOnly()) return;
    if (!draft.expr.trim()) { checkEl.textContent = ''; return; }
    const bad = undefinedNames(draft.expr, varNames());
    if (bad.length) {
      checkEl.textContent = `${bad[0]} is not an input`;
      checkEl.classList.add('bad');
      const fix = fixFor(bad[0], inputs);
      if (fix) {
        fixBtn.hidden = false;
        fixBtn.textContent = `Fix: use ${fix.ref}`;
        fixBtn.onclick = () => { addVar(fix, bad[0]); changed(); };
      }
      return;
    }
    if (check.ok === false) { checkEl.textContent = check.error; checkEl.classList.add('bad'); }
    else if (check.ok === true) { checkEl.textContent = shapeHint(check.shape); checkEl.classList.add('good'); }
    else checkEl.textContent = 'Checking…';
  }

  function renderInputs() {
    inputsEl.replaceChildren();
    if (!draft || readOnly()) return;
    const groups = groupInputs(inputs, inputSearch.value);
    if (!groups.length) {
      inputsEl.appendChild(el('div', 'mx-empty', inputs.length ? 'No input matches' : 'Select a dataset to list its inputs'));
      return;
    }
    for (const g of groups) {
      inputsEl.appendChild(el('div', 'mx-group-title', g.group));
      for (const i of g.items) {
        const b = el('button', 'mx-input');
        b.type = 'button';
        b.dataset.ref = i.ref;
        b.title = `${i.ref}${sideMark(i) ? ` (${sideMark(i)})` : ''}`;
        b.append(el('span', 'mx-input-name', i.name), el('span', 'mx-input-ref', i.label || i.ref));
        if (i.only) b.append(el('span', 'mx-badge', sideMark(i)));
        // keep the caret in the formula: clicking a button would move focus away
        b.addEventListener('mousedown', (e) => e.preventDefault());
        b.addEventListener('click', () => insertInput(i));
        inputsEl.appendChild(b);
      }
    }
  }

  function renderButtons() {
    const ro = readOnly();
    const user = !isNew && selected?.origin === 'user';
    tryBtn.hidden = !draft || ro;
    saveBtn.hidden = !draft || ro || (selected?.status === 'hidden');
    // a user file in error or hidden still has its formula to copy
    dupBtn.hidden = isNew || !selected || (selected.expr == null && selected.status !== 'ok');
    delBtn.hidden = !user;
    exportBtn.hidden = !user || selected.status !== 'ok';
    tryBtn.textContent = ops.trying ? 'Cancel' : 'Try';
    tryBtn.classList.toggle('active', ops.trying);
    const why = problem();
    saveBtn.disabled = !!why || (!isNew && !isDirty(draft, saved) && !conflict);
    saveBtn.title = why || (isDirty(draft, saved) ? 'Save for every window' : 'Nothing changed');
    tryBtn.disabled = !ops.trying && (!!undefinedNames(draft?.expr || '', draft ? varNames() : []).length || check.ok === false || !draft?.expr.trim());
    const users = selected?.used_by || [];
    delBtn.disabled = users.length > 0;
    delBtn.title = users.length ? `Used by ${users.join(', ')}` : '';
    delBtn.textContent = deleteArmed ? 'Click again to delete' : 'Delete';
    delBtn.classList.toggle('active', deleteArmed);
    // rule 13: one shortcut, by the shape of the result
    shortcutsEl.replaceChildren();
    const shape = selected?.shape;
    const kind = !isNew && selected?.status === 'ok' ? shortcutFor(shape) : null;
    if (kind === 'colour' && callbacks.onColourAtoms) {
      const b = el('button', '', 'Colour atoms by this');
      b.addEventListener('click', () => callbacks.onColourAtoms(selected.id));
      shortcutsEl.appendChild(b);
    } else if (kind === 'plot' && callbacks.onPlot) {
      const b = el('button', '', 'Plot it');
      b.addEventListener('click', () => callbacks.onPlot(selected.id));
      shortcutsEl.appendChild(b);
    }
    shortcutsEl.hidden = !shortcutsEl.childElementCount;
  }

  function renderConflict() {
    conflictEl.replaceChildren();
    conflictEl.hidden = !conflict;
    if (!conflict) return;
    const deleted = conflictText.startsWith('Deleted');
    conflictEl.append(el('span', '', deleted ? 'Deleted in another window' : 'Changed in another window'));
    const reload = el('button', '', deleted ? 'Discard' : 'Reload');
    reload.title = deleted ? 'Drop my edits' : 'Drop my edits and show the saved version';
    reload.addEventListener('click', reloadFromServer);
    const over = el('button', 'mx-primary', deleted ? 'Save anyway' : 'Overwrite');
    over.title = deleted ? 'Make the metric again from my edits' : 'Save my version over the other window\'s';
    over.addEventListener('click', () => save(true));
    conflictEl.append(reload, over);
  }

  function renderTry() {
    tryEl.replaceChildren();
    tryEl.hidden = !tryProgress && !tryResult;
    if (ops.trying && tryProgress) {
      const bar = el('progress');
      bar.max = 1;
      bar.value = tryProgress.progress ?? 0;
      tryEl.append(bar, el('div', 'mx-try-note', tryProgress.message || 'Trying…'));
      return;
    }
    if (!tryResult) return;
    const r = tryResult;
    tryEl.append(el('div', r.ok && !r.bad_count ? 'mx-try-head good' : 'mx-try-head bad', tryHeadline(r, callbacks.nameOf?.(tryContext) ?? '')));
    if (!r.ok || r.cancelled) return;
    const stats = el('div', 'mx-try-stats');
    for (const [k, v] of [['min', r.min], ['mean', r.mean], ['max', r.max]])
      stats.append(el('span', '', `${k} ${formatStat(v)}`));
    tryEl.appendChild(stats);
    const gaps = noValueLine(r);
    if (gaps) tryEl.appendChild(el('div', 'mx-try-note mx-try-gaps', gaps));
    if (r.bad_frames?.length) {
      const words = el('div', 'mx-try-bad');
      words.append(document.createTextNode('first bad frames: '));
      for (const f of r.bad_frames) {
        const a = el('button', 'mx-link', String(f));
        a.type = 'button';
        a.title = `Show frame ${f}`;
        a.addEventListener('click', () => callbacks.onGoToFrame?.(f, tryContext));
        words.append(a, document.createTextNode(' '));
      }
      if (r.bad_count > r.bad_frames.length) words.append(document.createTextNode('…'));
      tryEl.appendChild(words);
    }
    if (r.histogram) tryEl.appendChild(histogramSvg(r.histogram));
  }

  function renderMessage() {
    messageEl.textContent = message.text;
    messageEl.classList.toggle('bad', message.error);
    messageEl.hidden = !message.text;
  }

  function renderUsedBy() {
    const users = selected?.used_by || [];
    usedByEl.hidden = isNew || !selected || selected.status !== 'ok';
    usedByEl.textContent = users.length ? `Used by: ${users.join('; ')}` : 'Not used by anything';
  }

  function renderExport() {
    exportEl.replaceChildren();
    exportEl.hidden = !exported;
    if (!exported) return;
    const pre = el('textarea', 'mx-export-text');
    pre.readOnly = true;
    pre.rows = 8;
    pre.value = exported;
    const copy = el('button', '', 'Copy');
    copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(exported); setMessage('Copied. Paste it into your project\'s ffast.toml.'); }
      catch { pre.select(); setMessage('Select all and copy by hand.'); }
    });
    exportEl.append(el('div', 'mx-subhead', 'Paste into ffast.toml'), pre, copy);
  }

  function setMessage(text, error = false) {
    message = { text, error };
    renderMessage();
  }

  // ── editing the draft ────────────────────────────────────────────────────
  /** After any change to the draft: redraw what depends on it, check again. */
  function changed() {
    deleteArmed = false;
    renderVars();
    renderCheck();
    renderButtons();
    renderInputs();
    scheduleCheck();
  }

  function scheduleCheck(now = false) {
    clearTimeout(checkTimer);
    if (!draft || readOnly()) return;
    check = { ok: null, error: '', shape: null };
    renderCheck();
    renderButtons();
    const send = () => {
      if (!draft.expr.trim() || undefinedNames(draft.expr, varNames()).length) return;
      const payload = toPayload(draft);
      // the id may not be typed yet; the check is about the formula
      if (isNew && idProblem(payload.id)) payload.id = `${ID_PREFIX}draft`;
      ops.check(payload);
    };
    if (now) send(); else checkTimer = setTimeout(send, CHECK_DELAY_MS);
  }

  function addVar(input, wantedName = null) {
    const existing = draft.vars.find((v) => v.ref === input.ref);
    if (existing) return existing.name;
    const name = uniqueName(wantedName || input.name, varNames());
    draft.vars.push({ name, ref: input.ref });
    return name;
  }

  function insertInput(input) {
    if (readOnly()) return;
    const name = addVar(input);
    const [s, e] = caret;
    const out = insertAt(draft.expr, s, e, name);
    draft.expr = out.text;
    formula.value = out.text;
    caret = [out.caret, out.caret];
    formula.focus();
    formula.setSelectionRange(out.caret, out.caret);
    changed();
  }

  function applySuggestion(item) {
    const start = formula.selectionStart;
    if (item.kind === 'input') {
      const input = inputs.find((i) => i.ref === item.ref);
      if (input) addVar(input, item.text);
    }
    const out = completeAt(draft.expr, start, item.kind === 'function' ? `${item.text}(` : item.text);
    draft.expr = out.text;
    formula.value = out.text;
    formula.setSelectionRange(out.caret, out.caret);
    caret = [out.caret, out.caret];
    suggest.hidden = true;
    changed();
  }

  let suggestIndex = 0;
  function showSuggestions() {
    if (!draft || readOnly()) { suggest.hidden = true; return; }
    const items = suggestions(formula.value, formula.selectionStart, varNames(), inputs);
    suggest.replaceChildren();
    suggest.hidden = !items.length;
    suggestIndex = 0;
    for (const [n, item] of items.entries()) {
      const b = el('button', 'mx-suggest-item');
      b.type = 'button';
      b.classList.toggle('selected', n === 0);
      b.dataset.kind = item.kind;
      b.append(el('span', '', item.text), el('span', 'mx-input-ref', item.kind === 'input' ? item.ref : item.kind));
      b.addEventListener('mousedown', (e) => { e.preventDefault(); applySuggestion(item); });
      suggest.appendChild(b);
    }
    suggest._items = items;
  }

  formula.addEventListener('input', () => {
    draft.expr = formula.value;
    caret = [formula.selectionStart, formula.selectionEnd];
    showSuggestions();
    changed();
  });
  for (const ev of ['keyup', 'click', 'select'])
    formula.addEventListener(ev, () => { caret = [formula.selectionStart, formula.selectionEnd]; });
  formula.addEventListener('blur', () => { setTimeout(() => { suggest.hidden = true; }, 120); });
  formula.addEventListener('keydown', (e) => {
    const items = suggest._items || [];
    if (suggest.hidden || !items.length) return;
    const move = (d) => {
      e.preventDefault();
      suggestIndex = (suggestIndex + d + items.length) % items.length;
      [...suggest.children].forEach((c, n) => c.classList.toggle('selected', n === suggestIndex));
    };
    if (e.key === 'ArrowDown') move(1);
    else if (e.key === 'ArrowUp') move(-1);
    else if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) { e.preventDefault(); applySuggestion(items[suggestIndex]); }
    else if (e.key === 'Escape') { e.preventDefault(); suggest.hidden = true; }
  });

  idInput.addEventListener('input', () => { draft.id = idInput.value.trim(); renderCheck(); renderButtons(); setMessage(isNew ? idProblem(draft.id, takenIds()) : '', true); });
  labelInput.addEventListener('input', () => { draft.label = labelInput.value; renderButtons(); });
  unitInput.addEventListener('input', () => { draft.unit = unitInput.value; renderButtons(); });
  inputSearch.addEventListener('input', renderInputs);

  // ── actions ──────────────────────────────────────────────────────────────
  async function save(overwrite = false) {
    if (!draft || problem()) return;
    saveBtn.disabled = true;
    const r = await ops.save(toPayload(draft), {
      previousId: isNew ? null : draft.id, baseRevision: saved?.revision ?? null, overwrite,
    });
    if (r.ok) {
      conflict = false;
      setMessage('Saved. Every window now has it.');
      pendingSelect = r.id;
      isNew = false;
      selectById(r.id);
    } else if (r.conflict) {
      conflict = true;
      conflictText = r.error || '';
      renderConflict();
      renderButtons();
    } else {
      setMessage(r.error || 'Could not save', true);
      renderButtons();
    }
  }

  let pendingSelect = null;
  function selectById(id) {
    const entry = findEntry(id);
    if (entry) { const keep = message; select(entry); setMessage(keep.text, keep.error); pendingSelect = null; }
  }
  function findEntry(id) {
    const groups = groupMetrics(catalog, userMetrics, metricUsers);
    return Object.values(groups).flat().find((m) => m.id === id) || null;
  }

  function reloadFromServer() {
    const latest = findEntry(selected.id);
    if (latest) select(latest);
    else { selected = null; draft = null; saved = null; conflict = false; renderEditor(); }
  }

  async function runTry() {
    if (ops.trying) { ops.cancelTry(); return; }
    const { datasetFp, modelFp } = context();
    if (!datasetFp) { setMessage('Select a dataset to try the formula on', true); return; }
    tryContext = { datasetFp, modelFp };
    tryResult = null;
    tryProgress = { progress: 0, message: 'Starting…' };
    setMessage('');
    renderTry();
    renderButtons();
    const payload = toPayload(draft);
    if (isNew && idProblem(payload.id)) payload.id = `${ID_PREFIX}draft`;
    const result = await ops.tryIt(payload, datasetFp, modelFp, (kw) => { tryProgress = kw; renderTry(); });
    tryProgress = null;
    tryResult = result;
    renderTry();
    renderButtons();
  }

  async function runDelete() {
    if (!deleteArmed) {
      deleteArmed = true;
      renderButtons();
      setTimeout(() => { if (deleteArmed) { deleteArmed = false; renderButtons(); } }, 4000);
      return;
    }
    deleteArmed = false;
    const id = selected.id;
    const r = await ops.remove(id);
    if (r.ok) { selected = null; draft = null; saved = null; renderEditor(); setMessage(`Deleted ${id}`); }
    else setMessage(r.error || 'Could not delete', true);
    renderButtons();
  }

  async function runExport() {
    const r = await ops.exportToml(selected.id);
    if (r.error) setMessage(r.error, true);
    else { exported = r.toml; renderExport(); }
  }

  tryBtn.addEventListener('click', runTry);
  saveBtn.addEventListener('click', () => save(false));
  dupBtn.addEventListener('click', duplicate);
  delBtn.addEventListener('click', runDelete);
  exportBtn.addEventListener('click', runExport);

  // ── from the server ──────────────────────────────────────────────────────
  ops.onCheck = (kw) => {
    check = { ok: kw.ok, error: kw.error || '', shape: kw.shape };
    renderCheck();
    renderButtons();
  };
  ops.onInputs = (list) => {
    inputs = list;
    if (!editor.hidden) { renderInputs(); renderCheck(); }
  };

  function refresh() {
    // Keep an open edit through a refresh. If the server's copy moved on while
    // the draft has unsaved changes, say so now (rule 6) rather than at Save.
    if (selected && !isNew) {
      const latest = findEntry(selected.id);
      if (!latest) {
        if (selected.origin === 'user') {
          isNew = !!draft;
          saved = null;
          if (draft) setMessage('Deleted in another window; saving would make it again', true);
          else { selected = null; }
        } else selected = null;
      } else {
        const moved = saved && latest.revision && latest.revision !== saved.revision;
        if (moved && draft && isDirty(draft, saved)) { conflict = true; conflictText = ''; }
        else if (moved) { selected = latest; saved = latest; draft = toDraft(latest); scheduleCheck(true); }
        else { selected = latest; if (latest.origin === 'user') saved = latest; }
      }
    }
    renderList();
    renderEditor();
    if (pendingSelect) selectById(pendingSelect);
  }

  return {
    /** METRIC_CATALOG entries. */
    setCatalog(entries) { catalog = entries || []; refresh(); },
    /** USER_METRICS entries. */
    setUserMetrics(entries) { userMetrics = entries || []; refresh(); },
    /** METRIC_USERS map (the "Used by" lines). */
    setMetricUsers(map) { metricUsers = map || {}; refresh(); },
    /** The dataset and prediction selected in the object list changed. */
    contextChanged() {
      const { datasetFp, modelFp } = context();
      inputs = [];
      if (datasetFp) ops.requestInputs(datasetFp, modelFp);
      if (!editor.hidden) { renderInputs(); renderCheck(); }
    },
    /** A disconnect: nothing the pane holds is current. */
    disconnected() { inputs = []; userMetrics = []; metricUsers = {}; catalog = []; selected = null; isNew = false; draft = null; refresh(); },
  };
}

/** A small bar chart of a Try's values. */
function histogramSvg({ edges, counts }) {
  const ns = 'http://www.w3.org/2000/svg';
  const w = 200, h = 40;
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
  svg.setAttribute('class', 'mx-histogram');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', `Histogram from ${formatStat(edges[0])} to ${formatStat(edges[edges.length - 1])}`);
  const top = Math.max(...counts, 1);
  const bw = w / counts.length;
  counts.forEach((c, i) => {
    const bh = (c / top) * (h - 2);
    const r = document.createElementNS(ns, 'rect');
    r.setAttribute('x', String(i * bw + 0.5));
    r.setAttribute('y', String(h - bh));
    r.setAttribute('width', String(Math.max(bw - 1, 1)));
    r.setAttribute('height', String(bh));
    svg.appendChild(r);
  });
  return svg;
}

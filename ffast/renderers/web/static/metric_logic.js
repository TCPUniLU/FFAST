/**
 * The Metrics pane's decisions, as pure functions (ADR 0057).
 *
 * No DOM and no socket, so each rule is checked on its own
 * (tests/ffast/renderers/web/test_web_pure_helpers.py): which names a formula
 * uses, what to suggest while typing, when "Fix" is offered, what a draft saves
 * as, how a Try's numbers read. `panes/metrics.js` draws; this file decides.
 */

/** Functions a formula may call (ffast/metrics/expr.py). */
export const FORMULA_FUNCTIONS = [
  'abs', 'sqrt', 'exp', 'log', 'log10', 'clip', 'where', 'minimum', 'maximum',
  'sign', 'norm', 'x', 'y', 'z',
];
/** Names that need no `vars` row. */
export const FORMULA_CONSTANTS = ['pi', 'n_atoms'];

const IDENT = /[A-Za-z_][A-Za-z0-9_]*/g;
const ID_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$/;

/** Default prefix of a new metric's id (the user may retype all of it). */
export const ID_PREFIX = 'user.';

/**
 * The names a formula uses as values, in order of first use. A name followed by
 * "(" is a function call, not a value (so `x(F)` uses F, not x).
 * @param {string} expr @returns {string[]}
 */
export function valueNames(expr) {
  const out = [];
  for (const m of expr.matchAll(IDENT)) {
    const after = expr.slice(m.index + m[0].length);
    if (/^\s*\(/.test(after)) continue;
    // `1e5` / `2e-3`: the "e" belongs to the number
    if (m.index > 0 && /[0-9.]/.test(expr[m.index - 1]) && /^e\d*$/.test(m[0])) continue;
    if (!out.includes(m[0])) out.push(m[0]);
  }
  return out;
}

/**
 * Names the formula uses that nothing defines: not a row of the variable table,
 * not `pi` / `n_atoms`.
 * @param {string} expr @param {string[]} varNames @returns {string[]}
 */
export function undefinedNames(expr, varNames) {
  return valueNames(expr).filter((n) => !varNames.includes(n) && !FORMULA_CONSTANTS.includes(n));
}

/**
 * The one input a name most plausibly means, or null when none or several do.
 * Matches, in order: the suggested short name (`ref_E`), then the label, then
 * the end of the ref — each ignoring case. The first rule that finds anything
 * decides, and only if it finds exactly one.
 * @param {string} name @param {Array<{ref: string, name: string, label: string}>} inputs
 */
export function fixFor(name, inputs) {
  const q = name.toLowerCase();
  const rules = [
    (i) => i.name.toLowerCase() === q,
    (i) => (i.label || '').toLowerCase() === q,
    (i) => i.ref.toLowerCase().split('.').pop() === q,
  ];
  for (const rule of rules) {
    const hits = inputs.filter(rule);
    if (hits.length === 1) return hits[0];
    if (hits.length > 1) return null;
  }
  return null;
}

/** `name`, or `name_2`, `name_3` … the first not in `taken`. */
export function uniqueName(name, taken) {
  if (!taken.includes(name)) return name;
  for (let n = 2; ; n++) if (!taken.includes(`${name}_${n}`)) return `${name}_${n}`;
}

/**
 * Insert `text` at the selection of a text box.
 * @returns {{text: string, caret: number}}
 */
export function insertAt(value, start, end, text) {
  return { text: value.slice(0, start) + text + value.slice(end), caret: start + text.length };
}

/**
 * Replace the identifier the caret sits in or just after.
 * @returns {{text: string, caret: number}}
 */
export function completeAt(value, caret, name) {
  const { start } = identifierBefore(value, caret);
  return insertAt(value, start, caret, name);
}

/** The identifier ending at `caret` ('' when the caret does not follow one). */
export function identifierBefore(value, caret) {
  const m = /[A-Za-z_][A-Za-z0-9_]*$/.exec(value.slice(0, caret));
  return m ? { prefix: m[0], start: caret - m[0].length } : { prefix: '', start: caret };
}

/**
 * What typing offers while the caret follows part of a name: the formula's own
 * variables, then the inputs' short names, then functions. Empty when nothing
 * has been typed, or the only candidate is what is already there.
 * @param {string} value @param {number} caret @param {string[]} varNames
 * @param {Array<{name: string, ref: string, label: string}>} inputs
 * @returns {Array<{text: string, kind: 'variable'|'input'|'function', ref?: string}>}
 */
export function suggestions(value, caret, varNames, inputs) {
  const { prefix } = identifierBefore(value, caret);
  if (!prefix) return [];
  const q = prefix.toLowerCase();
  const out = [];
  const seen = new Set();
  const add = (item) => {
    if (seen.has(item.text) || item.text === prefix) return;
    seen.add(item.text);
    out.push(item);
  };
  for (const n of varNames) if (n.toLowerCase().startsWith(q)) add({ text: n, kind: 'variable' });
  for (const i of inputs)
    if (i.name.toLowerCase().startsWith(q) && !varNames.includes(i.name))
      add({ text: i.name, kind: 'input', ref: i.ref });
  for (const f of FORMULA_FUNCTIONS) if (f.startsWith(q)) add({ text: f, kind: 'function' });
  return out.slice(0, 8);
}

/**
 * Inputs matching a search, in their groups. Matches the ref, the short name
 * and the label, ignoring case.
 * @param {Array<{group: string, ref: string, name: string, label: string}>} inputs
 * @param {string} query @returns {Array<{group: string, items: object[]}>}
 */
export function groupInputs(inputs, query = '') {
  const q = query.trim().toLowerCase();
  const order = ['Reference', 'Prediction', 'File keys', 'Metrics'];
  const groups = new Map(order.map((g) => [g, []]));
  for (const i of inputs) {
    if (q && ![i.ref, i.name, i.label].some((t) => (t || '').toLowerCase().includes(q))) continue;
    if (!groups.has(i.group)) groups.set(i.group, []);
    groups.get(i.group).push(i);
  }
  return [...groups].filter(([, items]) => items.length).map(([group, items]) => ({ group, items }));
}

/** What an input's side mark says, or '' when it exists on both sides. */
export function sideMark(input) {
  return input.only ? `${input.only} only` : '';
}

/** `user.` followed by nothing is not yet an id; neither is anything with a bad part. */
export function idProblem(id, takenIds = []) {
  if (!id || id === ID_PREFIX) return 'Type an id, such as user.energy_per_atom';
  if (!ID_PATTERN.test(id))
    return 'An id is letters, digits and underscores in dot-separated parts, such as mylab.e_per_atom';
  if (takenIds.includes(id)) return `The id ${id} is already taken`;
  return '';
}

/**
 * A draft as SAVE_USER_METRIC takes it: the authoring form of a
 * `[[metrics.expr]]` entry. Variables the formula does not use are left out.
 * @param {{id: string, label: string, unit: string, expr: string, vars: Array<{name: string, ref: string}>}} draft
 */
export function toPayload(draft) {
  const used = new Set(valueNames(draft.expr));
  const vars = {};
  for (const v of draft.vars) if (used.has(v.name) && v.name && v.ref) vars[v.name] = v.ref;
  return { id: draft.id, expr: draft.expr, vars, label: draft.label || '', unit: draft.unit || '' };
}

/** The draft behind a stored metric (variables as rows, in the order written). */
export function toDraft(metric) {
  return {
    id: metric.id, label: metric.label || '', unit: metric.unit || '', expr: metric.expr || '',
    vars: Object.entries(metric.vars || {}).map(([name, ref]) => ({ name, ref })),
  };
}

/** Whether a draft differs from what was loaded (what Save and the unsaved mark follow). */
export function isDirty(draft, saved) {
  if (!saved) return !!(draft.expr || draft.vars.length || draft.label || draft.unit);
  return JSON.stringify(toPayload(draft)) !== JSON.stringify(toPayload(toDraft(saved)));
}

/**
 * The shortcut a result shape offers (rule 13): per atom colours the main view,
 * per structure plots.
 * @param {string|null|undefined} shape @returns {'colour'|'plot'|null}
 */
export function shortcutFor(shape) {
  if (shape === 'N_atoms') return 'colour';
  if (shape === 'N_frames') return 'plot';
  return null;
}

/** The grey words for a good check: where the result can go. */
export function shapeHint(shape) {
  const where = { N_atoms: 'per atom → can colour atoms', N_frames: 'per structure → can be plotted',
    scalar: 'one number for the dataset' }[shape];
  return where ? `${where}` : `result shape ${shape}`;
}

/** A number for a statistic: short, and `—` for none. */
export function formatStat(value) {
  if (value == null || !Number.isFinite(value)) return '—';
  const a = Math.abs(value);
  if (a !== 0 && (a >= 1e6 || a < 1e-3)) return value.toExponential(3);
  return String(Number(value.toPrecision(6)));
}

/** The one line a finished Try opens with. */
export function tryHeadline(r) {
  if (r.cancelled) return 'Stopped';
  if (!r.ok) return r.error || 'Could not try the formula';
  if (r.bad_count) return `${r.bad_count} bad value${r.bad_count === 1 ? '' : 's'} of ${r.count} — saving would fail the metric`;
  return `${r.count} value${r.count === 1 ? '' : 's'}, all good`;
}

/**
 * Metrics grouped for the list: built-in, project, then user. User entries come
 * from the user list (which also holds hidden and broken ones); everything else
 * from the catalog.
 * @param {Array<object>} catalog @param {Array<object>} userMetrics
 */
export function groupMetrics(catalog, userMetrics) {
  const entry = (m, origin, extra = {}) => ({
    id: m.id, label: m.label || m.id, origin, shape: m.shape ?? null, expr: m.expr ?? null,
    vars: m.vars ?? null, unit: m.unit ?? '', used_by: m.used_by ?? [], status: 'ok', message: '',
    revision: null, ...extra,
  });
  const byId = new Map(userMetrics.map((u) => [u.id, u]));
  const out = { 'built-in': [], project: [], user: [] };
  for (const m of catalog) {
    if (m.id.includes('__')) continue;          // made for a plot, not a metric to pick
    const u = m.origin === 'user' ? byId.get(m.id) : null;
    if (u) out.user.push(entry(m, 'user', { ...u, label: u.label || m.label, shape: u.shape ?? m.shape }));
    else out[m.origin === 'project' ? 'project' : m.origin === 'user' ? 'user' : 'built-in'].push(entry(m, m.origin));
  }
  for (const u of userMetrics)
    if (u.status !== 'ok') out.user.push(entry(u, 'user', { ...u, label: u.label || u.id }));
  for (const list of Object.values(out)) list.sort((a, b) => a.label.localeCompare(b.label));
  return out;
}

/** Frames of a Try's bad values, as the pane's clickable words. */
export function badFrameWords(frames, count) {
  if (!frames.length) return '';
  const more = count > frames.length ? ' …' : '';
  return `first bad frames: ${frames.join(', ')}${more}`;
}

/**
 * The formula with one variable renamed. Only the name used as a value changes:
 * `x(F)` calls the function x whatever a variable is called.
 * @param {string} expr @param {string} from @param {string} to
 */
export function renameVariable(expr, from, to) {
  return expr.replace(IDENT, (name, offset) => {
    if (name !== from) return name;
    if (/^\s*\(/.test(expr.slice(offset + name.length))) return name;
    return to;
  });
}

/** Whether a name can be a variable: an identifier that is not a function or a constant. */
export function variableNameProblem(name, otherNames = []) {
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) return 'A name is letters, digits and underscores, not starting with a digit';
  if (FORMULA_CONSTANTS.includes(name)) return `${name} is built in`;
  if (otherNames.includes(name)) return `${name} is already a variable`;
  return '';
}

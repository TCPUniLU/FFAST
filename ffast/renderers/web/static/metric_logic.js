/**
 * The Metrics pane's decisions, as pure functions (ADR 0057).
 *
 * No DOM and no socket, so each rule is checked on its own
 * (tests/ffast/renderers/web/test_web_pure_helpers.py): which names a formula
 * uses, what to suggest while typing, when "Fix" is offered, what a draft saves
 * as, how a Try's numbers read. `panes/metrics.js` draws; this file decides.
 */

/** Functions a formula may call (ffast/metrics/expr.py); the reductions
 * and `element` come from ADR 0064. */
export const FORMULA_FUNCTIONS = [
  'abs', 'sqrt', 'exp', 'log', 'log10', 'clip', 'where', 'minimum', 'maximum',
  'sign', 'norm', 'x', 'y', 'z',
  'mean', 'sum', 'min', 'max', 'rms', 'std', 'mean_parts', 'sum_parts', 'max_parts', 'element',
];
/** The words `by=` takes, alone or as the pair `by=(structure, element)`;
 * `atom` from ADR 0065. */
export const BY_WORDS = ['structure', 'element', 'atom', 'all'];
/** Text that ends where a by= word goes: `by=`, `by=(` or `by=(structure, `. */
const BY_BEFORE = /\bby\s*=\s*(\(\s*([A-Za-z_]+\s*,\s*)?)?$/;
/** Names that need no `vars` row. */
export const FORMULA_CONSTANTS = ['pi', 'n_atoms'];

const IDENT = /[A-Za-z_][A-Za-z0-9_]*/g;
const ID_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$/;

/** Default prefix of a new metric's id (the user may retype all of it). */
export const ID_PREFIX = 'user.';

/**
 * Where the formula uses a name as a value: each match of an identifier that
 * is not a function call (`x(F)` uses F, not x), not the keyword before `=`
 * in `by=` / `where=`, not the word after `by=`, and not text in quotes (the
 * symbols in `element("H")`).
 * @param {string} expr @returns {RegExpMatchArray[]}
 */
function valueMatches(expr) {
  // Text in quotes is blanked out, keeping every other character in place.
  const bare = expr.replace(/"[^"]*"?|'[^']*'?/g, (t) => ' '.repeat(t.length));
  const out = [];
  for (const m of bare.matchAll(IDENT)) {
    const after = bare.slice(m.index + m[0].length);
    if (/^\s*\(/.test(after)) continue;
    if (/^\s*=(?!=)/.test(after)) continue;
    if (BY_WORDS.includes(m[0]) && BY_BEFORE.test(bare.slice(0, m.index))) continue;
    // `1e5` / `2e-3`: the "e" belongs to the number
    if (m.index > 0 && /[0-9.]/.test(expr[m.index - 1]) && /^e\d*$/.test(m[0])) continue;
    out.push(m);
  }
  return out;
}

/**
 * The names a formula uses as values, in order of first use (see
 * `valueMatches` for what is not a value).
 * @param {string} expr @returns {string[]}
 */
export function valueNames(expr) {
  return [...new Set(valueMatches(expr).map((m) => m[0]))];
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
 * @returns {Array<{text: string, kind: 'variable'|'input'|'function'|'word', ref?: string}>}
 */
export function suggestions(value, caret, varNames, inputs) {
  const { prefix, start } = identifierBefore(value, caret);
  if (!prefix) return [];
  const q = prefix.toLowerCase();
  // After `by=` only its words make sense.
  if (BY_BEFORE.test(value.slice(0, start)))
    return BY_WORDS.filter((w) => w.startsWith(q) && w !== prefix).map((w) => ({ text: w, kind: 'word' }));
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

/**
 * The draft a Duplicate of `metric` starts from, its id still to choose. A
 * formula metric copies its formula; a Python metric its exact formula when it
 * has one (`equivalent_expr`, ffast/metrics/builtin/equivalents.py), or else a
 * formula that takes the metric itself as its input, named after the metric.
 */
export function duplicateDraft(metric) {
  if (metric.expr != null) return toDraft(metric);
  const base = { id: metric.id, label: metric.label || '', unit: metric.unit || '' };
  if (metric.equivalent_expr)
    return toDraft({ ...base, expr: metric.equivalent_expr, vars: metric.equivalent_vars });
  let name = metric.id.split('.').pop().replace(/[^A-Za-z0-9_]/g, '_');
  if (!/^[A-Za-z_]/.test(name) || FORMULA_FUNCTIONS.includes(name) || FORMULA_CONSTANTS.includes(name))
    name = `m_${name}`;
  return toDraft({ ...base, expr: name, vars: { [name]: metric.id } });
}

/** Whether a draft differs from what was loaded (what Save and the unsaved mark follow). */
export function isDirty(draft, saved) {
  if (!saved) return !!(draft.expr || draft.vars.length || draft.label || draft.unit);
  return JSON.stringify(toPayload(draft)) !== JSON.stringify(toPayload(toDraft(saved)));
}

/**
 * The shortcut a result shape offers (rule 13): per atom colours the main view,
 * per structure plots a timeline, per element plots bars, per structure and
 * element plots a line per element (ADR 0064 amendments).
 * @param {string|null|undefined} shape
 * @returns {'colour'|'plot'|null}
 */
export function shortcutFor(shape) {
  if (shape === 'N_atoms') return 'colour';
  if (shape === 'N_frames' || shape === 'N_elements' || shape === TABLE_SHAPE) return 'plot';
  return null;
}

/** The shape of a by=(structure, element) result: a row per structure, a
 * column per element (ADR 0064 amendment 2026-10-24). */
export const TABLE_SHAPE = '(N_frames, N_elements)';

/** The grey words for a good check: where the result can go. */
export function shapeHint(shape) {
  const where = { N_atoms: 'per atom → can colour atoms', N_frames: 'per structure → can be plotted',
    N_elements: 'per element → can be plotted as bars', scalar: 'one number for the dataset',
    [TABLE_SHAPE]: 'per structure and element → can be plotted, a line per element, and colour atoms' }[shape];
  return where ? `${where}` : `result shape ${shape}`;
}

/** A number for a statistic: short, and `—` for none. */
export function formatStat(value) {
  if (value == null || !Number.isFinite(value)) return '—';
  const a = Math.abs(value);
  if (a !== 0 && (a >= 1e6 || a < 1e-3)) return value.toExponential(3);
  return String(Number(value.toPrecision(6)));
}

/** The one line a finished Try opens with; `name` is the data it ran on
 * ('aspirin · MACE'), which stays true when the focus moves to another view. */
export function tryHeadline(r, name = '') {
  if (r.cancelled) return 'Stopped';
  if (!r.ok) return r.error || 'Could not try the formula';
  const on = name ? ` — on ${name}` : '';
  if (r.bad_count) return `${r.bad_count} bad value${r.bad_count === 1 ? '' : 's'} of ${r.count} — saving would fail the metric${on}`;
  if (r.table) return `${tableSize(r.table)}, all good${on}`;
  return `${r.count} value${r.count === 1 ? '' : 's'}, all good${on}`;
}

/** A table's size in words, '100 structures × 3 elements' (ADR 0064 amendment). */
export function tableSize([rows, cols]) {
  return `${rows} structure${rows === 1 ? '' : 's'} × ${cols} element${cols === 1 ? '' : 's'}`;
}

/** The line a Try adds when some places have no value (ADR 0064): '12
 * structures have no value'; '' when every place has one. */
export function noValueLine(r) {
  const n = r.no_value_count || 0;
  if (!n) return '';
  if (r.no_value_rows === 'result' || !r.no_value_rows) return 'The result has no value';
  const rows = n === 1 ? r.no_value_rows.replace(/s$/, '') : r.no_value_rows;
  return `${n} ${rows} ${n === 1 ? 'has' : 'have'} no value`;
}

/**
 * Metrics grouped for the list: built-in, project, then user. User entries come
 * from the user list (which also holds hidden and broken ones); everything else
 * from the catalog.
 * `users` (METRIC_USERS) says who uses each metric; `used_by` is `[]` for a metric it omits.
 * @param {Array<object>} catalog @param {Array<object>} userMetrics
 * @param {Object<string, string[]>} [users]
 */
export function groupMetrics(catalog, userMetrics, users = {}) {
  const entry = (m, origin, extra = {}) => ({
    id: m.id, label: m.label || m.id, origin, shape: m.shape ?? null, expr: m.expr ?? null,
    vars: m.vars ?? null, unit: m.unit ?? '', status: 'ok', message: '',
    source: m.source ?? null, equivalent_expr: m.equivalent_expr ?? null,
    equivalent_vars: m.equivalent_vars ?? null,
    revision: null, ...extra, used_by: users[m.id] ?? [],
  });
  const byId = new Map(userMetrics.map((u) => [u.id, u]));
  const out = { 'built-in': [], project: [], user: [] };
  for (const m of catalog) {
    if (m.id.includes('__')) continue;          // made for a plot, not a metric to pick
    if (m.origin !== 'user' && parseBoxId(m.id)) continue;   // a box setting nobody saved
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
  let out = '';
  let last = 0;
  for (const m of valueMatches(expr)) {
    if (m[0] !== from) continue;
    out += expr.slice(last, m.index) + to;
    last = m.index + from.length;
  }
  return out + expr.slice(last);
}

/** Whether a name can be a variable: an identifier that is not a function or a constant. */
export function variableNameProblem(name, otherNames = []) {
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) return 'A name is letters, digits and underscores, not starting with a digit';
  if (FORMULA_CONSTANTS.includes(name)) return `${name} is built in`;
  if (otherNames.includes(name)) return `${name} is already a variable`;
  return '';
}

/**
 * The question after a session load offered metrics (rule 15). Clashes — the
 * server has that id with another formula — say how the session's is kept.
 * @param {Array<{id: string, clash_with: string|null}>} metrics
 * @returns {{title: string, message: string}}
 */
export function sessionMetricsQuestion(metrics) {
  const n = metrics.length;
  const lines = metrics.map((m) => (m.clash_with
    ? `• ${m.id} — the server has a different ${m.clash_with}; the session's is kept as ${m.id}`
    : `• ${m.id}`));
  return {
    title: 'Metrics in this session',
    message: `This session uses ${n} metric${n === 1 ? '' : 's'} this server doesn't have:\n${lines.join('\n')}\n\nAdd them?`,
  };
}

// ── keep boxes (ADR 0065) ───────────────────────────────────────────────────
// A setting is {keep: [box…], combine, statistic}; the server sends the
// settings a metric allows (ffast/metrics/boxes.py) and writes the formula.

/** The keep boxes in their order, with the words the pane shows. */
export const KEEP_BOXES = [['atom', 'atom'], ['structure', 'structure'], ['element', 'element'], ['xyz', 'x/y/z']];
export const COMBINE_CHOICES = [['component', 'Per component'], ['length', 'Length']];
export const STATISTIC_CHOICES = [['mae', 'MAE'], ['rmse', 'RMSE'], ['max', 'Max'], ['mean', 'Mean (signed)']];

const sameKeep = (a, b) => a.length === b.length && a.every((k) => b.includes(k));

/** Whether two settings are the same. */
export function sameSetting(a, b) {
  return !!a && !!b && sameKeep(a.keep, b.keep) && a.combine === b.combine && a.statistic === b.statistic;
}

/** The allowed setting with these keep boxes that is closest to `want`:
 * its Combine and Statistic when they still apply, else per component and
 * MAE. null when no allowed setting keeps exactly these boxes. */
function closest(want, allowed) {
  const score = (s) => (want.combine && s.combine === want.combine ? 8 : 0)
    + (want.statistic && s.statistic === want.statistic ? 4 : 0)
    + (s.combine === 'component' ? 2 : 0) + (s.statistic === 'mae' ? 1 : 0);
  let best = null;
  for (const s of allowed) {
    if (sameKeep(s.keep, want.keep) && (!best || score(s) > score(best))) best = s;
  }
  return best ? { ...best, keep: [...best.keep] } : null;
}

/** The setting a click on keep box `box` gives, or null when the boxes grey it.
 * atom and element are an either-or pair (no setting keeps both): ticking one
 * while the other is ticked unticks the other, and clicking the ticked one
 * when nothing else would be left to give a value per row switches to the
 * other. */
export function toggleBox(setting, box, allowed) {
  const order = KEEP_BOXES.map(([k]) => k);
  const sorted = (keep) => keep.sort((a, b) => order.indexOf(a) - order.indexOf(b));
  const keep = setting.keep.includes(box) ? setting.keep.filter((k) => k !== box) : [...setting.keep, box];
  const plain = closest({ ...setting, keep: sorted(keep) }, allowed);
  if (plain || (box !== 'atom' && box !== 'element')) return plain;
  const other = box === 'atom' ? 'element' : 'atom';
  const swapped = setting.keep.includes(box)
    ? [...setting.keep.filter((k) => k !== box), other]    // the ticked one, with nothing else to give a value
    : [...setting.keep.filter((k) => k !== other), box];   // ticked beside the other
  return setting.keep.includes(box) && setting.keep.includes(other)
    ? null : closest({ ...setting, keep: sorted(swapped) }, allowed);
}

/** Whether keep box `box` can be clicked. */
export function boxAvailable(setting, box, allowed) {
  return toggleBox(setting, box, allowed) !== null;
}

/** Why a box is greyed, in words. */
export function boxHint(setting, box, hints) {
  if (hints[box]) return hints[box];
  if (hints.atom_only && (box === 'structure' || box === 'atom')) return hints.atom_only;
  return 'not with the boxes ticked now';
}

/** The setting choosing Combine or Statistic gives (`field` 'combine' or
 * 'statistic'); Mean (signed) on a length moves to per component. */
export function chooseSetting(setting, field, value, allowed) {
  const want = { ...setting, [field]: value };
  const same = allowed.filter((s) => sameKeep(s.keep, setting.keep) && s[field] === value);
  const exact = same.find((s) => sameSetting(s, want));
  if (exact) return { ...exact, keep: [...exact.keep] };
  return same.length ? closest(want, same) : null;
}

/** The Combine choices these keep boxes allow; [] when x/y/z is kept. */
export function combineChoices(setting, allowed) {
  return COMBINE_CHOICES.map(([c]) => c)
    .filter((c) => allowed.some((s) => sameKeep(s.keep, setting.keep) && s.combine === c));
}

/** The Statistic choices these keep boxes allow; [] when nothing is averaged. */
export function statisticChoices(setting, allowed) {
  if (!setting.statistic) return [];
  return STATISTIC_CHOICES.map(([c]) => c)
    .filter((c) => allowed.some((s) => sameKeep(s.keep, setting.keep) && s.statistic === c));
}

/** `{base, keep, rest}` of a box id such as
 * `ffast.force_difference[atom;length;mae]`, or null for any other id. */
export function parseBoxId(id) {
  const m = /^([^[\]]+)\[([a-z+;]+)\]$/.exec(id || '');
  return m ? { base: m[1], fields: m[2] } : null;
}

/** The settings that give one value per atom, the only ones Color by takes
 * (ADR 0065 section 5): atom kept, or element kept without atom; never x/y/z. */
export function colourSettings(allowed) {
  return allowed.filter((s) => !s.keep.includes('xyz') && (s.keep.includes('atom') || s.keep.includes('element')));
}

/** The first per-atom setting of a vector metric, for colouring by it:
 * each atom, per component, MAE. */
export function firstColourSetting(allowed) {
  const ok = colourSettings(allowed);
  return ok.find((s) => sameKeep(s.keep, ['atom', 'structure']) && s.combine === 'component' && s.statistic === 'mae')
    || ok[0] || null;
}

// ── Colour by: whose numbers (to_fix 2a/b) ─────────────────────────────────

/** The two pass-through metrics behind Colour by's one "Forces" entry; the
 * Prediction chooses between them: Ground Truth reads the dataset's forces,
 * a model its own. */
export const FORCES_BASES = Object.freeze({ reference: 'ffast.force_reference', prediction: 'ffast.force_prediction' });
export const FORCES_LABEL = 'Forces';

const baseOf = (id) => parseBoxId(id)?.base ?? id;

/** 'reference' or 'prediction' when `id` (or its box setting) is one of the
 * Forces pass-throughs; null otherwise. */
export function forcesSide(id) {
  const base = baseOf(id);
  if (base === FORCES_BASES.reference) return 'reference';
  if (base === FORCES_BASES.prediction) return 'prediction';
  return null;
}

/** The Forces metric id for `prediction` (null: Ground Truth), keeping the
 * box setting of `id`. */
export function forcesId(id, prediction) {
  const want = prediction ? FORCES_BASES.prediction : FORCES_BASES.reference;
  const box = parseBoxId(id);
  return box ? `${want}[${box.fields}]` : want;
}

/** Whether metric `id` reads a prediction, directly or through the metrics
 * it is made of, from the catalog's input lists. A box setting reads what its
 * base reads; a metric the catalog does not list reads none. */
export function readsPrediction(id, catalogById, seen = new Set()) {
  const base = baseOf(id);
  if (seen.has(base)) return false;
  seen.add(base);
  const entry = catalogById.get(base);
  return !!entry && (entry.inputs || []).some((ref) =>
    ref.startsWith('prediction.') || readsPrediction(ref, catalogById, seen));
}

/** What Colour by's Prediction offers for `source`: 'none' (hidden: element,
 * displacement, or a metric of reference data only), 'models' (an error
 * needs a prediction, so no Ground Truth) or 'forces' (Ground Truth or a
 * model: whose forces). */
export function predictionChoice(source, catalogById) {
  if (!source?.startsWith('metric:')) return 'none';
  const id = source.slice(7);
  if (forcesSide(id)) return 'forces';
  return readsPrediction(id, catalogById) ? 'models' : 'none';
}

/** The colour bar's name for a Forces box setting: "Forces · Magnitude" for
 * each atom's length, "Forces · MAE per component", and the statistic and
 * grouping when atoms are grouped ("Forces · Max magnitude · per element"). */
export function forcesLabel(id) {
  const [keep = '', ...rest] = (parseBoxId(id)?.fields || '').split(';');
  const statistic = STATISTIC_CHOICES.find(([c]) => rest.includes(c))?.[1];
  const parts = [FORCES_LABEL, rest.includes('length') ? (statistic ? `${statistic} magnitude` : 'Magnitude')
    : statistic ? `${statistic} per component` : null];
  const grouping = { atom: 'per atom, over frames', element: 'per element' }[keep];
  if (grouping) parts.push(grouping);
  return parts.filter(Boolean).join(' · ');
}

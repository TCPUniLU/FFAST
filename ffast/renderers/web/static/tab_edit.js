/**
 * Editing a tab (ADR 0056 rules 11, 12 and 14): pure functions over a tab
 * draft, the tab as TAB_LAYOUT describes it. Each returns a new draft and
 * leaves its input alone, so Cancel is simply dropping the draft. Edit mode
 * (app.js) calls these from the gestures; Save sends `toSaved(draft)`.
 *
 * The grid: a panel sits at (row, col) and spans rowspan x colspan cells.
 * Panels sharing a `scroll_group` form one strip that moves and resizes as a
 * unit. A tab has as many columns as its panels reach or its `column_widths`
 * list, whichever is more (a column may be empty); rows likewise.
 */

import { KIND_3D } from './tab_rules.js';

/**
 * The roles each panel kind binds and the metric shapes each role takes, as
 * the desktop's panel kinds declare them (UI/panels.py; a test keeps the two
 * in step). `list` roles take several metrics.
 */
export const KIND_ROLES = Object.freeze({
  timeline: { label: 'Timeline', roles: { y: ['N_frames'] } },
  density: { label: 'Density', roles: { value: ['(curve_xy, grid)'] } },
  scatter: { label: 'Scatter', roles: { x: ['N_frames'], y: ['N_frames'] } },
  table: { label: 'Table', roles: { value: ['scalar'] } },
  overlay_timeline: { label: 'Overlay timeline', roles: { series: ['N_frames'] }, list: ['series'] },
  grouped_density: { label: 'Density per element', roles: { value: ['(N_elements, curve_xy, grid)'] } },
  grouped_table: { label: 'Table per element', roles: { mae: ['N_elements'], rmse: ['N_elements'] } },
});

/** Catalog entries a role of a kind can take, by shape. */
export function metricsFor(kind, role, catalog) {
  const shapes = KIND_ROLES[kind]?.roles?.[role] || [];
  return (catalog || []).filter((entry) => shapes.includes(entry.shape));
}

const clone = (x) => structuredClone(x);
const span = (p, key) => p[key] || 1;

/** Number of columns: what the panels reach, or `column_widths`, if more. */
export function columnsOf(tab) {
  const used = Math.max(0, ...(tab.panels || []).map((p) => p.col + span(p, 'colspan')));
  return Math.max(1, used, tab.column_widths?.length || 0);
}

/** Number of rows: what the panels reach, or `row_heights`, if more. */
export function rowsOf(tab) {
  const used = Math.max(0, ...(tab.panels || []).map((p) => p.row + span(p, 'rowspan')));
  return Math.max(used, tab.row_heights?.length || 0);
}

/** The panels with their place filled in (row, col, rowspan, colspan). */
export function place(tab) {
  return (tab.panels || []).map((p) => ({
    ...p, rowspan: span(p, 'rowspan'), colspan: span(p, 'colspan'),
  }));
}

/** Panels that move together with panel `index`: its scroll strip, or itself. */
function unitOf(panels, index) {
  const group = panels[index]?.scroll_group;
  if (!group) return [index];
  return panels.map((p, i) => (p.scroll_group === group ? i : -1)).filter((i) => i >= 0);
}

/** The box a unit covers; a strip sits at its first member's place. */
function boxOf(panels, unit) {
  const p = panels[unit[0]];
  return { row: p.row, col: p.col, rowspan: span(p, 'rowspan'), colspan: span(p, 'colspan') };
}

function setBox(panels, unit, box) {
  for (const i of unit) Object.assign(panels[i], box);
}

const overlaps = (a, b) => a.row < b.row + b.rowspan && b.row < a.row + a.rowspan
  && a.col < b.col + b.colspan && b.col < a.col + a.colspan;

/** Units other than `skip` that a box would overlap. */
function hits(panels, box, skip) {
  const seen = new Set(), out = [];
  panels.forEach((_, i) => {
    if (skip.includes(i) || seen.has(i)) return;
    const unit = unitOf(panels, i);
    unit.forEach((j) => seen.add(j));
    if (overlaps(box, boxOf(panels, unit))) out.push(unit);
  });
  return out;
}

/** Shrink a unit's spans until it overlaps nothing and fits the columns;
 * false when not even one cell is free. */
function fit(panels, unit, columns) {
  const box = boxOf(panels, unit);
  box.colspan = Math.max(1, Math.min(box.colspan, columns - box.col));
  while (hits(panels, box, unit).length) {
    if (box.colspan > 1) box.colspan--;
    else if (box.rowspan > 1) box.rowspan--;
    else return false;
  }
  setBox(panels, unit, box);
  return true;
}

/** The first free cell, row by row; a new row when the grid is full. */
function freeCell(panels, columns, skip = []) {
  for (let row = 0; ; row++) {
    for (let col = 0; col < columns; col++) {
      if (!hits(panels, { row, col, rowspan: 1, colspan: 1 }, skip).length) return { row, col };
    }
  }
}

/** Keep `row_heights` one per row when it is set. */
function syncRows(tab) {
  if (!tab.row_heights) return tab;
  const used = Math.max(1, ...tab.panels.map((p) => p.row + span(p, 'rowspan')));
  const heights = tab.row_heights.slice(0, used);
  while (heights.length < used) heights.push(1);
  tab.row_heights = heights;
  return tab;
}

/**
 * Move panel `index` (with its strip) so its top-left cell is (row, col).
 * A panel already there swaps places with it; each keeps its size where it
 * fits and shrinks where it does not. A move that cannot fit is ignored.
 */
export function movePanel(tab, index, row, col) {
  const next = clone(tab);
  const panels = next.panels = place(next);
  const columns = columnsOf(next);
  const unit = unitOf(panels, index);
  const from = boxOf(panels, unit);
  const to = { ...from, row: Math.max(0, row), col: Math.max(0, Math.min(col, columns - 1)) };
  const [other] = hits(panels, { row: to.row, col: to.col, rowspan: 1, colspan: 1 }, unit);
  setBox(panels, unit, to);
  if (other) setBox(panels, other, { ...boxOf(panels, other), row: from.row, col: from.col });
  const ok = fit(panels, unit, columns) && (!other || fit(panels, other, columns));
  return ok ? syncRows(next) : tab;
}

/** Set the span of panel `index`, as far as it goes without overlapping a
 * neighbour or leaving the columns. */
export function resizePanel(tab, index, rowspan, colspan) {
  const next = clone(tab);
  const panels = next.panels = place(next);
  const columns = columnsOf(next);
  const unit = unitOf(panels, index);
  const box = boxOf(panels, unit);
  const want = {
    ...box,
    rowspan: Math.max(1, rowspan),
    colspan: Math.max(1, Math.min(colspan, columns - box.col)),
  };
  // Grow one step at a time, keeping the last size that fitted.
  const best = { ...box, rowspan: 1, colspan: 1 };
  for (let r = 1; r <= want.rowspan; r++) {
    for (let c = 1; c <= want.colspan; c++) {
      const candidate = { ...box, rowspan: r, colspan: c };
      if (!hits(panels, candidate, unit).length && r * c >= best.rowspan * best.colspan) {
        best.rowspan = r;
        best.colspan = c;
      }
    }
  }
  setBox(panels, unit, best);
  return syncRows(next);
}

/** Add a panel at the first free cell (1 x 1). */
export function addPanel(tab, panel) {
  const next = clone(tab);
  next.panels = place(next);
  const cell = freeCell(next.panels, columnsOf(next));
  next.panels.push({ ...clone(panel), ...cell, rowspan: 1, colspan: 1, scroll_group: null });
  return syncRows(next);
}

/** Replace panel `index` with `panel`, keeping its place. */
export function replacePanel(tab, index, panel) {
  const next = clone(tab);
  const { row, col, rowspan, colspan, scroll_group } = next.panels[index];
  next.panels[index] = { ...clone(panel), row, col, rowspan, colspan, scroll_group };
  return next;
}

export function removePanel(tab, index) {
  const next = clone(tab);
  next.panels.splice(index, 1);
  return syncRows(next);
}

/** Set the number of columns. Panels in columns that go move down to the
 * first free cells; spans are clipped. */
export function setColumns(tab, columns) {
  const n = Math.max(1, Math.floor(columns));
  const next = clone(tab);
  const panels = next.panels = place(next);
  const moving = [];
  const seen = new Set();
  panels.forEach((_, i) => {
    if (seen.has(i)) return;
    const unit = unitOf(panels, i);
    unit.forEach((j) => seen.add(j));
    const box = boxOf(panels, unit);
    if (box.col >= n) moving.push(unit);
    else setBox(panels, unit, { ...box, colspan: Math.min(box.colspan, n - box.col) });
  });
  // Panels still waiting to move do not block a cell.
  const pending = new Set(moving.flat());
  for (const unit of moving) {
    const cell = freeCell(panels, n, [...pending]);
    setBox(panels, unit, { ...boxOf(panels, unit), ...cell, colspan: 1, rowspan: 1 });
    unit.forEach((i) => pending.delete(i));
  }
  const used = Math.max(0, ...panels.map((p) => p.col + p.colspan));
  if (next.column_widths) {
    const widths = next.column_widths.slice(0, n);
    while (widths.length < n) widths.push(1);
    next.column_widths = widths;
  } else if (n !== used) {
    next.column_widths = Array(n).fill(1);   // keeps empty columns when saved
  }
  return syncRows(next);
}

/** Rows with set heights share the window; without, the tab scrolls. */
export function setRowsShareWindow(tab, share) {
  const next = clone(tab);
  next.row_heights = share ? (next.row_heights || Array(Math.max(1, rowsOf(next))).fill(1)) : null;
  return next;
}

/** A new tab with nothing in it. */
export function emptyTab(name, columns = 2) {
  return {
    name, has_data_selector: true, selector: null, controls: [], panels: [],
    column_widths: Array(columns).fill(1), row_heights: null,
  };
}

/** A copy of `tab` under a new name, with nothing that says where it came from. */
export function copyOf(tab, name) {
  const { source, replaces, original_changed, hidden, revision, ...rest } = clone(tab);
  return { ...rest, name };
}

/** A 3D panel to add (linked, ADR 0056 rule 1). */
export function new3DPanel() {
  return { kind: KIND_3D, title: null, metrics: {}, metric_refs: {} };
}

/** The draft as SAVE_TAB takes it: the authoring form of a tab file. */
export function toSaved(tab) {
  return {
    name: tab.name,
    has_data_selector: tab.has_data_selector ?? true,
    selector: tab.selector ?? null,
    controls: [...(tab.controls || [])],
    column_widths: tab.column_widths ?? null,
    row_heights: tab.row_heights ?? null,
    panels: (tab.panels || []).map(({ metric_refs, metrics, ...panel }) => ({
      ...panel,
      metrics: metric_refs ?? Object.fromEntries(Object.entries(metrics || {}).map(
        ([role, id]) => [role, Array.isArray(id) ? id.map((m) => ({ metric: m })) : { metric: id }])),
    })),
  };
}

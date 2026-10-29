/**
 * Editing a tab (ADR 0056 rules 11 and 14, ADR 0069): pure functions over a
 * tab draft, the tab as TAB_LAYOUT describes it. Each returns a new draft and
 * leaves its input alone, so Cancel is simply dropping the draft. Edit mode
 * (edit_mode.js) calls these from the gestures; Save sends `toSaved(draft)`.
 *
 * The grid: a tab is laid out on `grid` columns (24) that always fill the
 * window, and rows of 50 px. A panel sits at (row, col) and covers rowspan
 * rows by colspan columns; you size it by its own edges. Panels sharing a
 * `scroll_group` form one strip that moves and resizes as a unit. A panel put
 * on another pushes it down, and panels float up, so no row stays empty.
 * `toFineGrid` turns a tab of the old form (column_widths, row_heights) into
 * this one.
 */

import { KIND_3D } from './tab_rules.js';
import { TABLE_SHAPE } from './metric_logic.js';

/**
 * The roles each panel kind binds and the metric shapes each role takes, as
 * the desktop's panel kinds declare them (UI/panels.py; a test keeps the two
 * in step). `list` roles take several metrics.
 */
export const KIND_ROLES = Object.freeze({
  timeline: { label: 'Timeline', roles: { y: ['N_frames', '(N_frames, N_elements)'] } },
  density: { label: 'Density', roles: { value: ['(curve_xy, grid)'] } },
  scatter: { label: 'Scatter', roles: { x: ['N_frames'], y: ['N_frames'] } },
  table: { label: 'Table', roles: { value: ['scalar'] } },
  overlay_timeline: { label: 'Overlay timeline', roles: { series: ['N_frames'] }, list: ['series'] },
  grouped_density: { label: 'Density per element', roles: { value: ['(N_elements, curve_xy, grid)'] } },
  grouped_table: { label: 'Table per element', roles: { mae: ['N_elements'], rmse: ['N_elements'] } },
  bars: { label: 'Bars per element', roles: { value: ['N_elements'] } },
});

/** Catalog entries a role of a kind can take, by shape. */
export function metricsFor(kind, role, catalog) {
  const shapes = KIND_ROLES[kind]?.roles?.[role] || [];
  return (catalog || []).filter((entry) => shapes.includes(entry.shape));
}

const clone = (x) => structuredClone(x);
const span = (p, key) => p[key] || 1;

/** Columns of the fine grid a tab is laid out on (ADR 0069). */
export const GRID_COLUMNS = 24;
/** Height of one row unit, in px (analysis.js sizes the grid's rows with it). */
export const ROW_UNIT_PX = 50;
/** Pixels an old row height of 1 stands for (ADR 0056, before the fine grid). */
const OLD_ROW_PX = 300;
/** Size a new panel starts with, in cells: half the width, 400 px high. */
const NEW_PANEL = Object.freeze({ colspan: GRID_COLUMNS / 2, rowspan: 8 });

/** The columns of a tab's grid. */
export const gridOf = (tab) => tab.grid || GRID_COLUMNS;

/** Number of columns of an old-form tab: what the panels reach, or
 * `column_widths`, if more. */
export function columnsOf(tab) {
  const used = Math.max(0, ...(tab.panels || []).map((p) => p.col + span(p, 'colspan')));
  return Math.max(1, used, tab.column_widths?.length || 0);
}

/** Number of rows of an old-form tab: what the panels reach, or
 * `row_heights`, if more. */
export function rowsOf(tab) {
  const used = Math.max(0, ...(tab.panels || []).map((p) => p.row + span(p, 'rowspan')));
  return Math.max(used, tab.row_heights?.length || 0);
}

/** Where `n` old columns start on a grid of `grid` columns, plus the end:
 * equal shares, or shares in proportion to `widths`, at least one column each. */
function columnEdges(n, grid, widths) {
  if (!widths) return Array.from({ length: n + 1 }, (_, i) => Math.ceil((i * grid) / n));
  const total = widths.slice(0, n).reduce((a, b) => a + b, 0);
  const edges = [0];
  let sum = 0;
  for (let i = 1; i <= n; i++) {
    sum += widths[i - 1];
    const at = Math.round((sum / total) * grid);
    edges.push(i === n ? grid : Math.min(grid - (n - i), Math.max(edges[i - 1] + 1, at)));
  }
  return edges;
}

/**
 * An old-form tab (column_widths, row_heights, cells) as a tab on the fine
 * grid (ADR 0069). Each old column becomes an equal share of the grid's
 * columns, or a share in proportion to its width; each old row becomes
 * height / 50 px units (300 px when not set). Panels keep their order and
 * scroll groups. A tab already on the grid comes back as it is.
 */
export function toFineGrid(tab, grid = GRID_COLUMNS) {
  if (tab.grid) return tab;
  const n = Math.min(columnsOf(tab), grid);
  const cols = columnEdges(n, grid, tab.column_widths);
  const rows = [0];
  for (let r = 0; r < rowsOf(tab); r++) {
    const px = (tab.row_heights?.[r] ?? 1) * OLD_ROW_PX;
    rows.push(rows[r] + Math.max(1, Math.round(px / ROW_UNIT_PX)));
  }
  const { column_widths, row_heights, ...rest } = clone(tab);
  const box = (p, strip) => {
    const col = Math.min(p.col, n - 1), colspan = Math.min(span(p, 'colspan'), n - col);
    const last = strip ? p.row + 1 : p.row + span(p, 'rowspan');
    return { row: rows[p.row], col: cols[col], rowspan: rows[last] - rows[p.row],
      colspan: cols[col + colspan] - cols[col] };
  };
  // A scroll strip sits at its first member's place, one old row high.
  const first = new Map();
  for (const p of rest.panels || []) {
    if (p.scroll_group && !first.has(p.scroll_group)) first.set(p.scroll_group, p);
  }
  return {
    ...rest, grid,
    panels: (rest.panels || []).map((p) => (p.scroll_group
      ? { ...p, ...box(first.get(p.scroll_group), true) }
      : { ...p, ...box(p, false) })),
  };
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

/** Every unit (a panel, or a scroll strip as one), by its first member. */
function allUnits(panels) {
  const seen = new Set(), out = [];
  panels.forEach((_, i) => {
    if (seen.has(i)) return;
    const unit = unitOf(panels, i);
    unit.forEach((j) => seen.add(j));
    out.push(unit);
  });
  return out;
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
const reading = (a, b) => a.row - b.row || a.col - b.col;

/** The smallest a panel gets, in cells: 3 of every 12 columns by 4 rows (a
 * 3D panel 4 of 12 by 6), so a tab always fits three panels in a row. */
export function minSize(tab, index) {
  const grid = gridOf(tab);
  const is3d = tab.panels[index]?.kind === KIND_3D;
  return { w: Math.min(grid, Math.ceil(((is3d ? 4 : 3) * grid) / 12)), h: is3d ? 6 : 4 };
}

/** `{w, h}` for panel `index`, held between its minimum and the grid's edge. */
export function clampSize(tab, index, { w, h }) {
  const min = minSize(tab, index);
  const col = tab.panels[index].col;
  return { w: Math.max(min.w, Math.min(Math.round(w), gridOf(tab) - col)), h: Math.max(min.h, Math.round(h)) };
}

/** The (row, col) panel `index` lands on when asked for (row, col): inside
 * the grid on the left and right. */
export function clampMove(tab, index, row, col) {
  const { colspan } = place(tab)[index];
  return { row: Math.max(0, Math.round(row)), col: Math.max(0, Math.min(Math.round(col), gridOf(tab) - colspan)) };
}

/**
 * Settle the units after a change: while two overlap, the one the user did
 * not touch (`active`) moves down to just below the other; then every unit
 * floats up as far as it goes, in reading order, so no row stays empty.
 */
function settle(panels, active) {
  const units = allUnits(panels);
  const boxes = () => units.map((u) => boxOf(panels, u));
  for (let guard = 0; guard < 1000; guard++) {
    const b = boxes();
    let moved = false;
    for (let i = 0; i < units.length && !moved; i++) {
      for (let j = i + 1; j < units.length && !moved; j++) {
        if (!overlaps(b[i], b[j])) continue;
        const [stay, go] = units[j].includes(active) ? [j, i]
          : units[i].includes(active) ? [i, j]
            : (reading(b[i], b[j]) <= 0 ? [i, j] : [j, i]);
        setBox(panels, units[go], { ...b[go], row: b[stay].row + b[stay].rowspan });
        moved = true;
      }
    }
    if (!moved) break;
  }
  const placed = [];
  [...units].sort((x, y) => reading(boxOf(panels, x), boxOf(panels, y))).forEach((unit) => {
    const box = boxOf(panels, unit);
    while (box.row > 0 && !placed.some((q) => overlaps({ ...box, row: box.row - 1 }, q))) box.row--;
    setBox(panels, unit, box);
    placed.push(box);
  });
}

/**
 * Move panel `index` (with its strip) so its top-left cell is (row, col),
 * kept inside the grid. A panel in the way moves down, just below it; then
 * the panels float up (see `settle`).
 */
export function movePanel(tab, index, row, col) {
  const next = clone(tab);
  const panels = next.panels = place(next);
  const unit = unitOf(panels, index);
  setBox(panels, unit, { ...boxOf(panels, unit), ...clampMove(next, index, row, col) });
  settle(panels, index);
  return next;
}

/** Set the size of panel `index` to `w` columns by `h` rows, between its
 * minimum and the grid's edge. Panels in the way move down (see `settle`). */
export function resizePanel(tab, index, { w, h }) {
  const next = clone(tab);
  const panels = next.panels = place(next);
  const size = clampSize(next, index, { w, h });
  setBox(panels, unitOf(panels, index), { ...boxOf(panels, unitOf(panels, index)), colspan: size.w, rowspan: size.h });
  settle(panels, index);
  return next;
}

/** Float every panel up as far as it goes without overlapping, as after any edit. */
export function compact(tab) {
  const next = clone(tab);
  next.panels = place(next);
  settle(next.panels, null);
  return next;
}

/** Add a panel at the first free place that fits it (half the width, 400 px
 * high), row by row; below every panel when none is free. */
export function addPanel(tab, panel) {
  const next = clone(toFineGrid(tab));   // a tab of the old form is put on the grid first
  next.panels = place(next);
  const grid = gridOf(next);
  const size = { rowspan: NEW_PANEL.rowspan, colspan: Math.min(NEW_PANEL.colspan, grid) };
  const others = allUnits(next.panels).map((u) => boxOf(next.panels, u));
  let spot = null;
  for (let row = 0; !spot; row++) {
    for (let col = 0; col + size.colspan <= grid && !spot; col++) {
      const box = { row, col, ...size };
      if (!others.some((o) => overlaps(box, o))) spot = box;
    }
  }
  next.panels.push({ ...clone(panel), ...spot, scroll_group: null });
  return next;
}

/** Metric ids a panel draws, whether it spells them as ids or as refs. */
function metricIdsOf(panel) {
  const ids = [];
  const walk = (v) => {
    if (typeof v === 'string') ids.push(v);
    else if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === 'object' && v.metric) ids.push(v.metric);
  };
  Object.values(panel.metric_refs || {}).forEach(walk);
  Object.values(panel.metrics || {}).forEach(walk);
  return ids;
}

/**
 * Add a panel to a tab; a panel that draws a table per structure and element
 * (one line per element) turns the tab's Element picker on, unless the tab
 * already has another selector.
 * @param {object} tab
 * @param {object} panel
 * @param {{id: string, shape?: string}[]} catalog METRIC_CATALOG entries
 */
export function addPanelWithPicker(tab, panel, catalog) {
  const next = addPanel(tab, panel);
  const perElement = metricIdsOf(panel).some(
    (id) => (catalog || []).find((m) => m.id === id)?.shape === TABLE_SHAPE);
  return perElement && !next.selector ? { ...next, selector: 'atomic' } : next;
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
  next.panels = place(next);
  settle(next.panels, null);
  return next;
}

/** JSON with object keys sorted, so equal drafts compare equal however
 * they were built. */
function canonical(x) {
  return JSON.stringify(x, (_, v) => (v && typeof v === 'object' && !Array.isArray(v)
    ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
    : v));
}

/** True when `after` differs from `before` only in where panels sit and how
 * big panels, columns and rows are, every 3D panel keeping its top-left
 * cell (an independent panel is known by its cell). Edit mode then moves the
 * cells in place, and the plots keep drawing. */
export function onlyPlacesChanged(before, after) {
  if (!before || !after || (before.panels || []).length !== (after.panels || []).length) return false;
  const content = ({ column_widths, row_heights, grid, panels, ...tab }) => ({
    ...tab, panels: (panels || []).map(({ row, col, rowspan, colspan, ...panel }) => panel),
  });
  if (canonical(content(before)) !== canonical(content(after))) return false;
  return before.panels.every((p, i) => p.kind !== KIND_3D
    || (p.row === after.panels[i].row && p.col === after.panels[i].col));
}

/** A new tab with nothing in it, on the fine grid. */
export function emptyTab(name) {
  return {
    name, has_data_selector: true, selector: null, controls: [], panels: [],
    grid: GRID_COLUMNS,
  };
}

/** A copy of `tab` under a new name, with nothing that says where it came from. */
export function copyOf(tab, name) {
  const { source, replaces, original_changed, hidden, revision, ...rest } = clone(tab);
  return { ...rest, name };
}

/** Make 3D panel `index` linked or independent (ADR 0056 rule 1). A linked
 * panel shows the main view and stores only its place, so the link ticks
 * and the starting look go. */
export function setPanelView(tab, index, { view, link_frame = true, link_camera = true }) {
  const next = clone(tab);
  const panel = next.panels[index];
  if (view === 'independent') Object.assign(panel, { view, link_frame, link_camera });
  else {
    panel.view = 'linked';
    delete panel.link_frame;
    delete panel.link_camera;
    delete panel.start;
  }
  return next;
}

/** Set an independent panel's starting look (rule 13); null clears it. */
export function setPanelStart(tab, index, start) {
  const next = clone(tab);
  if (start && Object.keys(start).length) next.panels[index].start = clone(start);
  else delete next.panels[index].start;
  return next;
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
    grid: tab.grid ?? null,
    column_widths: tab.grid ? null : tab.column_widths ?? null,
    row_heights: tab.grid ? null : tab.row_heights ?? null,
    panels: (tab.panels || []).map(({ metric_refs, metrics, unresolved, ...panel }) => ({
      ...panel,
      metrics: metric_refs ?? Object.fromEntries(Object.entries(metrics || {}).map(
        ([role, id]) => [role, Array.isArray(id) ? id.map((m) => ({ metric: m })) : { metric: id }])),
    })),
  };
}

/** The tab "Plot it" falls back to (ADR 0057 rule 13). */
export const MY_METRICS_TAB = 'My metrics';

/** The panel "Plot it" adds: the default kind for the metric's shape, a
 * timeline for a per-structure metric or a table per structure and element
 * (one line per element), and bars for a per-element one (ADR 0064
 * amendments). An ordinary panel, edited like any other. */
export function metricPanel(metric) {
  if (metric.shape === 'N_elements') {
    return {
      kind: 'bars', title: metric.label || metric.id,
      metrics: { value: metric.id },
      metric_refs: { value: { metric: metric.id, transform: null, params: {} } },
    };
  }
  return {
    kind: 'timeline', title: metric.label || metric.id,
    metrics: { y: metric.id },
    metric_refs: { y: { metric: metric.id, transform: null, params: {} } },
  };
}

/**
 * Where "Plot it" puts a panel (ADR 0057 rule 13): always the "My metrics" user
 * tab, made when missing, so the shortcut is predictable and never edits a
 * built-in or project tab. A new "My metrics" tab also holds the main view,
 * because the Metrics pane lives in the 3D controls and they only show in a tab
 * with a 3D panel: the plot and the pane that edits its metric stay side by side.
 * Returns the tab to save and the tab it was edited from (null: a new tab).
 * @param {object[]} tabs every tab the server sent
 * @param {object} panel what to add
 * @param {{elementPicker?: boolean}} [opts] give the tab the element picker
 */
export function plotTarget(tabs, panel, { elementPicker = false } = {}) {
  const mine = (tabs || []).find((t) => t.name === MY_METRICS_TAB);
  // A line per element wants the element picker (ADR 0064 amendment); a tab
  // that has another selector keeps it.
  const pick = (tab) => (elementPicker && !tab.selector ? { ...tab, selector: 'atomic' } : tab);
  if (mine) return { tab: pick(addPanel(mine, panel)), previous: mine.name };
  const fresh = addPanel(emptyTab(MY_METRICS_TAB), new3DPanel());
  return { tab: pick(addPanel(fresh, panel)), previous: null };
}

/**
 * Edit mode (ADR 0056 rules 11, 12, 14, 15, 16): the one place a tab
 * changes. ✎ starts editing the tab on screen; "+" starts a new tab. The
 * edits go to a draft (tab_edit.js), drawn live in place of the tab; Save
 * sends it to the server, Cancel drops it. Outside Edit mode nothing you do
 * is written, so exploring never rewrites a tab.
 *
 * While a tab is edited its controls row gives way to an edit bar, and each
 * panel gets a handle strip laid over it: drag ⠿ to move it (onto another
 * panel to swap the two), drag the corner ◢ to change its span (panels in
 * the way move down), ⚙ opens it in the builder, ✕ removes it. Dragging the thin dividers between columns and
 * rows sets their sizes. The handles are grid items of their own, covering
 * the panels' cells, so redrawing a plot never wipes them.
 *
 * If another window saves the same tab meanwhile, Save asks whether to
 * overwrite it or discard these edits (rule 16).
 */

import { askDialog } from './dialogs.js';
import {
  KIND_ROLES, addPanel, copyOf, emptyTab, metricsFor, movePanel, new3DPanel,
  removePanel, replacePanel, resizePanel, rowsOf, columnsOf, setColumns,
  setRowsShareWindow, toSaved,
} from './tab_edit.js';
import { KIND_3D, whyPanelStays } from './tab_rules.js';

/** A column's minimum width, as index.html's --col-min. */
const COL_MIN_PX = 400;

export class TabEditor {
  /**
   * @param {{
   *   tabOps: import('./tab_ops.js').TabOps,
   *   serverTabs: () => object[],          // the server's layout, hidden tabs too
   *   catalog: () => object[],             // METRIC_CATALOG entries
   *   refresh: (o: {show?: string}) => void,   // draw the layout (with the draft)
   *   setStatus: (text: string, kind: string) => void,
   * }} deps
   */
  constructor(deps) {
    this._deps = deps;
    this._draft = null;
    this._previous = null;   // the tab being edited, or null for a new tab
    this._revision = null;   // that tab's revision when editing began
    this._conflict = null;   // 'changed' | 'deleted' by another window
    this._error = '';
  }

  get editing() { return !!this._draft; }
  get draft() { return this._draft; }

  /** The layout with the draft in place of the tab being edited. */
  withDraft(tabs) {
    if (!this._draft) return tabs;
    if (this._previous == null) return [...tabs, this._draft];
    return tabs.map((t) => (t.name === this._previous ? this._draft : t));
  }

  /** Edit the tab named `name` as the server has it. */
  editTab(name) {
    const tab = this._deps.serverTabs().find((t) => t.name === name);
    if (!tab) return;
    this._begin(structuredClone(tab), name, tab.revision);
  }

  /** "+" ▸ Empty tab / Copy of…: Tab settings first, then Edit mode on a tab
   * that exists only after Save. */
  async newTab(from = null) {
    const names = this._deps.serverTabs().map((t) => t.name);
    let name = from ? `Copy of ${from.name}` : 'New tab';
    for (let n = 2; names.includes(name); n++) name = from ? `Copy of ${from.name} (${n})` : `New tab ${n}`;
    const draft = from ? copyOf(from, name) : emptyTab(name);
    const settled = await this._tabSettings(draft, { title: 'New tab', ok: 'Create' });
    if (settled) this._begin(settled, null, null);
  }

  _begin(draft, previous, revision) {
    this._draft = draft;
    this._previous = previous;
    this._revision = revision;
    this._conflict = null;
    this._error = '';
    this._deps.refresh({ show: draft.name });
  }

  /** Replace the draft and redraw it. */
  change(next) {
    if (!next || next === this._draft) return;
    this._draft = next;
    this._error = '';
    this._deps.refresh({ show: next.name });
  }

  /** Drop the draft; with `redraw`, show the tab as it was. */
  end(redraw = true) {
    const show = this._previous;
    this._draft = null;
    this._previous = null;
    if (redraw) this._deps.refresh({ show: show ?? undefined });
  }

  /** A layout arrived: did another window change the tab being edited? */
  noteLayout(tabs) {
    if (!this._draft || this._previous == null) return;
    const tab = tabs.find((t) => t.name === this._previous);
    if (!tab) this._conflict = 'deleted';
    else if (tab.revision !== this._revision) this._conflict = 'changed';
  }

  async save() {
    const name = this._draft.name;
    if (this._conflict === 'changed') {
      const answer = await askDialog({
        title: 'Changed in another window',
        message: `${this._previous} was changed in another window — overwrite or discard your edits?`,
        buttons: ['Discard my edits', 'Overwrite'],
      });
      if (answer === 'Discard my edits') { this.end(); return; }
      if (answer !== 'Overwrite') return;
    } else if (this._conflict === 'deleted') {
      const answer = await askDialog({
        title: 'Deleted in another window',
        message: `${this._previous} was deleted in another window — save your edits as a new tab, or discard them?`,
        buttons: ['Discard my edits', 'Save as a new tab'],
      });
      if (answer === 'Discard my edits') { this.end(); return; }
      if (answer !== 'Save as a new tab') return;
      this._previous = null;
    }
    const r = await this._deps.tabOps.save(toSaved(this._draft), this._previous);
    if (!r.ok) {
      this._error = r.error || 'The tab could not be saved';
      this._deps.setStatus(this._error, 'error');
      this._deps.refresh({ show: name });
      return;
    }
    this._draft = null;
    this._previous = null;
    this._deps.setStatus(`Saved ${r.name}`, 'connected');
    this._deps.refresh({ show: r.name });
  }

  // ── drawing the edit chrome ─────────────────────────────────────────────

  /**
   * Lay the edit bar and the panel handles over the drawn draft tab.
   * @param {ReturnType<import('./analysis.js').AnalysisManager['tab']>} parts
   * @param {object[]} visibleTabs the tabs in the bar, the draft among them
   */
  decorate(parts, visibleTabs) {
    if (!parts || !this._draft) return;
    parts.panelEl.classList.add('editing');
    parts.panelEl.querySelector('.edit-bar')?.remove();
    parts.panelEl.prepend(this._editBar());
    const grid = parts.gridEl;
    grid.classList.add('editing-grid');
    grid.hidden = false;
    grid.querySelectorAll('.edit-chrome, .edit-divider, .edit-ghost, .edit-empty').forEach((el) => el.remove());

    const tabIndex = visibleTabs.indexOf(this._draft);
    const units = [
      ...parts.slots.map((slot) => ({ indices: slot.indices, el: slot.el })),
      ...parts.cells3d.map((cell) => ({ indices: [cell.index], el: cell.el })),
    ];
    for (const unit of units) grid.appendChild(this._chrome(grid, unit, visibleTabs, tabIndex));
    if (!this._draft.panels.length) {
      const empty = document.createElement('div');
      empty.className = 'edit-empty panel-msg';
      empty.textContent = 'An empty tab. Add a panel with + Add panel.';
      empty.style.gridColumn = '1 / -1';
      grid.appendChild(empty);
    }
    requestAnimationFrame(() => this._dividers(grid));
  }

  _editBar() {
    const bar = document.createElement('div');
    bar.className = 'edit-bar';
    const title = document.createElement('span');
    title.className = 'edit-title';
    title.append('Editing ', Object.assign(document.createElement('b'), { textContent: this._draft.name }));
    const add = button('+ Add panel', () => this._addPanelDialog());
    add.dataset.edit = 'add-panel';
    const settings = button('Tab settings…', async () => {
      const next = await this._tabSettings(this._draft, { title: 'Tab settings', ok: 'Apply' });
      if (next) this.change(next);
    });
    settings.dataset.edit = 'tab-settings';
    const note = document.createElement('span');
    note.className = 'edit-note';
    note.textContent = this._error
      || (this._conflict === 'changed' ? 'Changed in another window since you began.' : '')
      || (this._conflict === 'deleted' ? 'Deleted in another window since you began.' : '');
    const spacer = document.createElement('span');
    spacer.className = 'spacer';
    const cancel = button('Cancel', () => this.end());
    cancel.dataset.edit = 'cancel';
    const save = button('Save', () => this.save());
    save.dataset.edit = 'save';
    save.className = 'primary';
    bar.append(title, add, settings, note, spacer, cancel, save);
    return bar;
  }

  /** The handle strip over one panel (or one scroll strip). */
  _chrome(grid, unit, visibleTabs, tabIndex) {
    const first = this._draft.panels[unit.indices[0]];
    const el = document.createElement('div');
    el.className = 'edit-chrome';
    el.dataset.panel = String(unit.indices[0]);
    el.style.gridColumn = unit.el.style.gridColumn;
    el.style.gridRow = unit.el.style.gridRow;

    const head = document.createElement('div');
    head.className = 'edit-head';
    const handle = document.createElement('span');
    handle.className = 'edit-handle';
    handle.title = 'Drag to move; drop on another panel to swap the two';
    const label = unit.indices.length > 1
      ? `${unit.indices.length} panels side by side`
      : first.kind === KIND_3D ? (first.title || '3D panel') : (first.title || KIND_ROLES[first.kind]?.label || first.kind);
    handle.textContent = `⠿ ${label}`;
    head.appendChild(handle);
    if (unit.indices.length === 1 && first.kind !== KIND_3D) {
      const gear = button('⚙', async () => {
        const panel = await this._builder(first);
        if (panel) this.change(replacePanel(this._draft, unit.indices[0], panel));
      });
      gear.title = 'Open in the builder';
      gear.dataset.edit = 'builder';
      head.appendChild(gear);
    }
    const why = unit.indices.map((i) => whyPanelStays(visibleTabs, tabIndex, i)).find(Boolean) || '';
    const remove = button('✕', () => {
      let next = this._draft;
      for (const i of [...unit.indices].sort((a, b) => b - a)) next = removePanel(next, i);
      this.change(next);
    });
    remove.dataset.edit = 'remove';
    remove.disabled = !!why;
    remove.title = why || 'Remove';
    head.appendChild(remove);

    const corner = document.createElement('span');
    corner.className = 'edit-resize';
    corner.textContent = '◢';
    corner.title = 'Drag to change how many cells it spans; panels in the way move down';
    el.append(head, corner);

    this._dragOnto(grid, handle, (cell) => movePanel(this._draft, unit.indices[0], cell.row, cell.col),
      (cell) => ({ row: cell.row, col: cell.col, rowspan: first.rowspan || 1, colspan: first.colspan || 1 }));
    this._dragOnto(grid, corner,
      (cell) => resizePanel(this._draft, unit.indices[0], cell.row - first.row + 1, cell.col - first.col + 1),
      (cell) => ({ row: first.row, col: first.col,
        rowspan: Math.max(1, cell.row - first.row + 1),
        colspan: Math.max(1, Math.min(cell.col, columnsOf(this._draft) - 1) - first.col + 1) }));
    return el;
  }

  /** Drag from `handle` to a grid cell: a ghost shows where `box(cell)`
   * lands; letting go applies `apply(cell)`. */
  _dragOnto(grid, handle, apply, box) {
    handle.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      handle.setPointerCapture(e.pointerId);
      const ghost = document.createElement('div');
      ghost.className = 'edit-ghost';
      grid.appendChild(ghost);
      let cell = null;
      const move = (ev) => {
        cell = cellAt(grid, ev.clientX, ev.clientY);
        const b = box(cell);
        ghost.style.gridColumn = `${b.col + 1} / span ${b.colspan}`;
        ghost.style.gridRow = `${b.row + 1} / span ${b.rowspan}`;
      };
      const up = () => {
        handle.removeEventListener('pointermove', move);
        handle.removeEventListener('pointerup', up);
        ghost.remove();
        if (cell) this.change(apply(cell));
      };
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', up);
      move(e);
    });
  }

  /** Dividers between columns and between rows; dragging one sets sizes. */
  _dividers(grid) {
    if (!this._draft || !grid.isConnected) return;
    const t = tracks(grid);
    const columns = columnsOf(this._draft), rows = rowsOf(this._draft);
    const make = (axis, k) => {
      const d = document.createElement('div');
      d.className = `edit-divider ${axis}`;
      d.dataset.index = String(k);
      d.title = axis === 'col' ? 'Drag to change the column widths' : 'Drag to change the row heights';
      const at = (sizes, gap, pad) => pad + sizes.slice(0, k).reduce((a, b) => a + b, 0) + gap * (k - 0.5);
      if (axis === 'col') {
        d.style.left = `${at(t.cols, t.gapC, t.padL) - 4}px`;
        d.style.top = '0';
        d.style.height = `${grid.scrollHeight}px`;
      } else {
        d.style.top = `${at(t.rows, t.gapR, t.padT) - 4}px`;
        d.style.left = '0';
        d.style.width = `${grid.scrollWidth}px`;
      }
      this._dragDivider(grid, d, axis, k);
      grid.appendChild(d);
    };
    for (let k = 1; k < Math.min(columns, t.cols.length); k++) make('col', k);
    for (let k = 1; k < Math.min(rows, t.rows.length); k++) make('row', k);
  }

  _dragDivider(grid, divider, axis, k) {
    divider.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      divider.setPointerCapture(e.pointerId);
      divider.classList.add('dragging');
      const t = tracks(grid);
      const sizes = (axis === 'col' ? t.cols : t.rows).slice();
      const start = axis === 'col' ? e.clientX : e.clientY;
      const pair = sizes[k - 1] + sizes[k];
      // A column stops at its minimum width (index.html --col-min).
      const min = axis === 'col' ? Math.min(COL_MIN_PX, pair / 2) : Math.min(80, pair / 3);
      let next = sizes;
      const move = (ev) => {
        const delta = (axis === 'col' ? ev.clientX : ev.clientY) - start;
        const a = Math.max(min, Math.min(pair - min, sizes[k - 1] + delta));
        next = sizes.slice();
        next[k - 1] = a;
        next[k] = pair - a;
        const floor = axis === 'col' ? 'var(--col-min)' : '0';
        const template = next.map((px) => `minmax(${floor}, ${px}fr)`).join(' ');
        if (axis === 'col') grid.style.gridTemplateColumns = template;
        else grid.style.gridTemplateRows = template;
      };
      const up = () => {
        divider.removeEventListener('pointermove', move);
        divider.removeEventListener('pointerup', up);
        divider.classList.remove('dragging');
        const smallest = Math.min(...next);
        const relative = next.map((px) => Math.round((px / smallest) * 100) / 100);
        if (axis === 'col') {
          this.change({ ...this._draft, column_widths: relative.slice(0, columnsOf(this._draft)) });
        } else {
          // Rows with set heights share the window (rule 12).
          this.change({ ...this._draft, row_heights: relative.slice(0, Math.max(1, rowsOf(this._draft))) });
        }
      };
      divider.addEventListener('pointermove', move);
      divider.addEventListener('pointerup', up);
    });
  }

  // ── dialogs ─────────────────────────────────────────────────────────────

  /** Ready-made panels first (rule 11): a 3D panel, then every panel of the
   * built-in and project tabs; then "Custom panel…", the builder. */
  async _addPanelDialog() {
    const body = document.createElement('div');
    body.className = 'panel-list';
    let chosen = null;
    const choice = (label, panel, detail = '') => {
      const btn = button(label, () => { chosen = panel; close('Add'); });
      btn.className = 'panel-choice';
      if (detail) btn.title = detail;
      return btn;
    };
    body.appendChild(choice('3D panel', new3DPanel(), 'Shows the main view, like the 3D tab'));
    for (const tab of this._deps.serverTabs()) {
      if (tab.source === 'user') continue;
      const panels = (tab.panels || []).filter((p) => p.kind !== KIND_3D);
      if (!panels.length) continue;
      const head = document.createElement('div');
      head.className = 'panel-list-head';
      head.textContent = tab.name;
      body.appendChild(head);
      for (const p of panels) {
        body.appendChild(choice(p.title || KIND_ROLES[p.kind]?.label || p.kind, p,
          Object.values(p.metrics || {}).flat().join(', ')));
      }
    }
    const { answer, close } = modal('Add panel', body, ['Cancel', 'Custom panel…']);
    const result = await answer;
    if (result === 'Add' && chosen) this.change(addPanel(this._draft, chosen));
    if (result === 'Custom panel…') {
      const panel = await this._builder(null);
      if (panel) this.change(addPanel(this._draft, panel));
    }
  }

  /** The builder (rule 11): a panel kind, then a metric per role from the
   * metric catalog, filtered by the shape the role takes. For now it offers
   * only metrics the server already has. */
  async _builder(panel) {
    const catalog = this._deps.catalog();
    const body = document.createElement('div');
    body.className = 'builder';
    const kind = document.createElement('select');
    kind.dataset.field = 'kind';
    for (const [name, k] of Object.entries(KIND_ROLES)) kind.append(new Option(k.label, name));
    kind.value = panel?.kind && KIND_ROLES[panel.kind] ? panel.kind : 'timeline';
    const title = Object.assign(document.createElement('input'), { type: 'text', value: panel?.title || '' });
    title.dataset.field = 'title';
    title.placeholder = 'Title';
    const roles = document.createElement('div');
    roles.className = 'builder-roles';
    body.append(field('Kind', kind), field('Title', title), roles);

    const metricSelect = (role, value) => {
      const select = document.createElement('select');
      select.dataset.role = role;
      select.append(new Option('— choose a metric —', ''));
      const options = metricsFor(kind.value, role, catalog);
      for (const m of options) select.append(new Option(m.label && m.label !== m.id ? `${m.label} (${m.id})` : m.id, m.id));
      if (value && !options.some((m) => m.id === value)) select.append(new Option(value, value));
      select.value = value || '';
      select.addEventListener('change', sync);
      return select;
    };
    const drawRoles = () => {
      roles.replaceChildren();
      const spec = KIND_ROLES[kind.value];
      const same = panel?.kind === kind.value;
      for (const role of Object.keys(spec.roles)) {
        const current = same ? panel.metrics?.[role] : null;
        if (spec.list?.includes(role)) {
          const list = document.createElement('div');
          list.className = 'builder-list';
          list.dataset.role = role;
          const addRow = (value) => {
            const row = document.createElement('div');
            row.className = 'builder-row';
            row.append(metricSelect(role, value), button('✕', () => { row.remove(); sync(); }));
            list.insertBefore(row, more);
          };
          const more = button('+ Add a series', () => { addRow(''); sync(); });
          list.appendChild(more);
          for (const value of (Array.isArray(current) && current.length ? current : [''])) addRow(value);
          roles.appendChild(field(role, list));
        } else {
          roles.appendChild(field(role, metricSelect(role, typeof current === 'string' ? current : '')));
        }
      }
      sync();
    };
    let ok = null;
    const sync = () => {
      const missing = [...roles.querySelectorAll('select')].some((s) => !s.value);
      if (ok) ok.disabled = missing;
    };
    kind.addEventListener('change', drawRoles);
    const { answer, buttons } = modal(panel ? 'Edit panel' : 'Custom panel', body, ['Cancel', panel ? 'Apply' : 'Add']);
    ok = buttons[1];
    drawRoles();
    if ((await answer) !== (panel ? 'Apply' : 'Add')) return null;

    const same = panel?.kind === kind.value;
    const out = same ? structuredClone(panel) : { kind: kind.value };
    out.kind = kind.value;
    out.title = title.value.trim() || null;
    out.metrics = {};
    out.metric_refs = {};
    for (const role of Object.keys(KIND_ROLES[kind.value].roles)) {
      const ids = [...roles.querySelectorAll(`select[data-role="${role}"]`)].map((s) => s.value);
      const keep = (id, k) => {
        const old = same ? panel.metrics?.[role] : null;
        const oldId = Array.isArray(old) ? old[k] : old;
        const oldRef = same ? panel.metric_refs?.[role] : null;
        if (oldId === id && oldRef) return Array.isArray(oldRef) ? oldRef[k] : oldRef;
        return { metric: id, transform: null, params: {} };
      };
      if (KIND_ROLES[kind.value].list?.includes(role)) {
        out.metrics[role] = ids;
        out.metric_refs[role] = ids.map((id, k) => keep(id, k));
      } else {
        out.metrics[role] = ids[0];
        out.metric_refs[role] = keep(ids[0], 0);
      }
    }
    return out;
  }

  /** Tab settings (rule 14): name, column count, the tab controls, and
   * whether the rows share the window's height. */
  async _tabSettings(draft, { title, ok }) {
    const body = document.createElement('div');
    body.className = 'tab-settings';
    const name = Object.assign(document.createElement('input'), { type: 'text', value: draft.name });
    name.dataset.field = 'name';
    const columns = Object.assign(document.createElement('input'), {
      type: 'number', min: 1, max: 8, value: columnsOf(draft) });
    columns.dataset.field = 'columns';
    const check = (label, checked, key) => {
      const box = Object.assign(document.createElement('input'), { type: 'checkbox', checked });
      box.dataset.field = key;
      const lbl = document.createElement('label');
      lbl.className = 'conn-check';
      lbl.append(box, ` ${label}`);
      return [lbl, box];
    };
    const [shiftRow, shift] = check('Energy shift', (draft.controls || []).includes('energy_shift'), 'energy_shift');
    const [elemRow, elems] = check('Element picker', draft.selector === 'atomic', 'element_picker');
    const [fitRow, fit] = check('Rows share the window height', !!draft.row_heights, 'rows_share');
    const why = document.createElement('div');
    why.className = 'ctl-hint';
    body.append(field('Name', name), field('Columns', columns), shiftRow, elemRow, fitRow, why);
    // The tab being edited may keep its own name; every other name is taken.
    const own = draft === this._draft ? this._previous : null;
    const taken = this._deps.serverTabs().map((t) => t.name).filter((n) => n !== own);
    const { answer, buttons } = modal(title, body, ['Cancel', ok]);
    const validate = () => {
      const n = name.value.trim();
      const msg = !n ? 'A tab needs a name'
        : taken.includes(n) ? `A tab named ${n} already exists` : '';
      why.textContent = msg;
      buttons[1].disabled = !!msg;
    };
    name.addEventListener('input', validate);
    validate();
    if ((await answer) !== ok) return null;
    let next = setColumns({ ...draft, name: name.value.trim() }, Number(columns.value) || 1);
    const controls = (next.controls || []).filter((c) => c !== 'energy_shift');
    next = { ...next, controls: shift.checked ? [...controls, 'energy_shift'] : controls,
      selector: elems.checked ? 'atomic' : (next.selector === 'atomic' ? null : next.selector) };
    return setRowsShareWindow(next, fit.checked);
  }
}

// ── helpers ─────────────────────────────────────────────────────────────

function button(text, onClick) {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = text;
  b.addEventListener('click', onClick);
  return b;
}

function field(label, control) {
  const wrap = document.createElement('label');
  wrap.className = 'edit-field';
  const span = document.createElement('span');
  span.textContent = label;
  wrap.append(span, control);
  return wrap;
}

/** A dialog with `body` and footer `labels`; `answer` resolves with the label
 * clicked (or one passed to `close`), null on Escape. */
function modal(title, body, labels) {
  const el = document.createElement('div');
  el.className = 'warning-modal edit-modal';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  const panel = document.createElement('div');
  panel.className = 'warning-modal-panel';
  const head = document.createElement('div');
  head.className = 'warning-modal-title';
  head.textContent = title;
  const content = document.createElement('div');
  content.className = 'edit-modal-body';
  content.appendChild(body);
  const foot = document.createElement('div');
  foot.className = 'warning-modal-footer';
  let close;
  const answer = new Promise((resolve) => {
    close = (label) => { el.remove(); resolve(label); };
  });
  const buttons = labels.map((label) => {
    const b = button(label, () => close(label));
    foot.appendChild(b);
    return b;
  });
  el.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(null); });
  panel.append(head, content, foot);
  el.appendChild(panel);
  document.body.appendChild(el);
  (body.querySelector('input, select, button') || buttons[buttons.length - 1])?.focus();
  return { answer, close, buttons };
}

/** The grid's resolved track sizes, gaps and padding, in pixels. */
function tracks(grid) {
  const cs = getComputedStyle(grid);
  const px = (list) => (list && list !== 'none' ? list.split(/\s+/).map(parseFloat).filter((v) => !Number.isNaN(v)) : []);
  return {
    cols: px(cs.gridTemplateColumns), rows: px(cs.gridTemplateRows),
    gapC: parseFloat(cs.columnGap) || 0, gapR: parseFloat(cs.rowGap) || 0,
    padL: parseFloat(cs.paddingLeft) || 0, padT: parseFloat(cs.paddingTop) || 0,
  };
}

/** The grid cell under a page point; below the last row is a new row. */
function cellAt(grid, x, y) {
  const t = tracks(grid);
  const r = grid.getBoundingClientRect();
  const index = (sizes, gap, pos) => {
    let edge = 0;
    for (let i = 0; i < sizes.length; i++) {
      edge += sizes[i];
      if (pos < edge + gap / 2) return i;
      edge += gap;
    }
    return sizes.length;
  };
  const col = index(t.cols, t.gapC, x - r.left - t.padL + grid.scrollLeft);
  const row = index(t.rows, t.gapR, y - r.top - t.padT + grid.scrollTop);
  return { row: Math.max(0, row), col: Math.max(0, Math.min(col, Math.max(0, t.cols.length - 1))) };
}

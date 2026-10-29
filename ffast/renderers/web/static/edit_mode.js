/**
 * Edit mode (ADR 0056 rules 11, 14, 15, 16; ADR 0069): the one place a tab
 * changes. ✎ starts editing the tab on screen; "+" starts a new tab. The
 * edits go to a draft (tab_edit.js), drawn live in place of the tab; Save
 * sends it to the server, Cancel drops it. Outside Edit mode nothing you do
 * is written, so exploring never rewrites a tab.
 *
 * While a tab is edited its controls row gives way to an edit bar, and each
 * panel gets a handle strip laid over it: drag ⠿ to move it (a panel in the
 * way moves down, and panels float up), drag its right edge, bottom edge or
 * the corner ◢ to size it, snapping to the grid's 24 columns and 50 px
 * rows; ⚙ opens it in the builder, ✕ removes it. While one is dragged the
 * grid's cells show. The handles are grid items of their own, covering the
 * panels' cells, so redrawing a plot never wipes them.
 *
 * If another window saves the same tab meanwhile, Save asks whether to
 * overwrite it or discard these edits (rule 16).
 */

import { askDialog, labelBy, trapFocus } from './dialogs.js';
import {
  KIND_ROLES, addPanelWithPicker, clampMove, clampSize, copyOf, emptyTab, metricsFor, movePanel,
  new3DPanel, onlyPlacesChanged, place, removePanel, replacePanel, resizePanel, setPanelStart,
  setPanelView, toFineGrid, toSaved,
} from './tab_edit.js';
import { KIND_3D, whyPanelStays } from './tab_rules.js';
import { cleanStart } from './start_settings.js';
import { icon } from './icons.js';

/** The height of one row unit and the gap between cells, in px (index.html). */
const ROW_UNIT_PX = 50;

export class TabEditor {
  /**
   * @param {{
   *   tabOps: import('./tab_ops.js').TabOps,
   *   serverTabs: () => object[],          // the server's layout, hidden tabs too
   *   catalog: () => object[],             // METRIC_CATALOG entries
   *   refresh: (o: {show?: string}) => void,   // draw the layout (with the draft)
   *   place: (draft: object) => boolean,   // move the draft's cells in place; false: refresh
   *   setStatus: (text: string, kind: string) => void,
   *   currentStart: (tabName: string, panel: object) => object,  // a 3D panel's look on screen
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
    this._begin(toFineGrid(structuredClone(tab)), name, tab.revision);
  }

  /** "+" ▸ Empty tab / Copy of…: Tab settings first, then Edit mode on a tab
   * that exists only after Save. */
  async newTab(from = null) {
    const names = this._deps.serverTabs().map((t) => t.name);
    let name = from ? `Copy of ${from.name}` : 'New tab';
    for (let n = 2; names.includes(name); n++) name = from ? `Copy of ${from.name} (${n})` : `New tab ${n}`;
    const draft = from ? toFineGrid(copyOf(from, name)) : emptyTab(name);
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

  /** Replace the draft and redraw it. Moving or resizing keeps the plots:
   * their cells move in place. */
  change(next) {
    if (!next || next === this._draft) return;
    const before = this._draft;
    this._draft = next;
    this._error = '';
    if (onlyPlacesChanged(before, next) && this._deps.place(next)) return;
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
        // Cancel (or Esc) keeps editing; focus starts on it, not on Overwrite.
        buttons: ['Cancel', 'Discard my edits', 'Overwrite'],
        danger: 'Overwrite',
      });
      if (answer === 'Discard my edits') { this.end(); return; }
      if (answer !== 'Overwrite') return;
    } else if (this._conflict === 'deleted') {
      const answer = await askDialog({
        title: 'Deleted in another window',
        message: `${this._previous} was deleted in another window — save your edits as a new tab, or discard them?`,
        buttons: ['Cancel', 'Discard my edits', 'Save as a new tab'],
        danger: 'Discard my edits',
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
    grid.querySelectorAll('.edit-chrome, .edit-ghost, .edit-empty').forEach((el) => el.remove());

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
    requestAnimationFrame(() => cornersInView(grid));
    if (!grid._cornerWatch) {
      grid._cornerWatch = new ResizeObserver(() => cornersInView(grid));
      grid._cornerWatch.observe(grid);
      grid.addEventListener('scroll', () => cornersInView(grid), { passive: true });
    }
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
      const gear = iconButton('gear', 'Open in the builder', async () => {
        const panel = await this._builder(first);
        if (panel) this.change(replacePanel(this._draft, unit.indices[0], panel));
      });
      gear.title = 'Open in the builder';
      gear.dataset.edit = 'builder';
      head.appendChild(gear);
    }
    if (unit.indices.length === 1 && first.kind === KIND_3D) {
      const gear = iconButton('gear', 'Main view or own view, links and starting look', async () => {
        const next = await this._panel3d(unit.indices[0], visibleTabs, tabIndex);
        if (next) this.change(next);
      });
      gear.title = 'Main view or own view, links and starting look';
      gear.dataset.edit = 'panel3d';
      head.appendChild(gear);
    }
    const why = unit.indices.map((i) => whyPanelStays(visibleTabs, tabIndex, i)).find(Boolean) || '';
    const remove = iconButton('close', 'Remove', () => {
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
    corner.title = 'Drag to change the width and height';
    const edgeE = document.createElement('span');
    edgeE.className = 'edit-edge e';
    edgeE.title = 'Drag to change the width';
    const edgeS = document.createElement('span');
    edgeS.className = 'edit-edge s';
    edgeS.title = 'Drag to change the height';
    el.append(head, edgeE, edgeS, corner);

    const index = unit.indices[0];
    const at = () => place(this._draft)[index];
    // Moving: the panel keeps the spot you grabbed it by, so the cell under
    // the pointer, less where it was grabbed, is its top-left cell.
    this._dragOnto(grid, handle, {
      grab: () => ({ row: at().row, col: at().col }),
      box: (cell) => { const m = clampMove(this._draft, index, cell.row, cell.col);
        return { ...m, rowspan: at().rowspan, colspan: at().colspan }; },
      apply: (cell) => movePanel(this._draft, index, cell.row, cell.col),
    });
    // Sizing: the cell under the pointer becomes the panel's last column and row.
    const sizing = (cols, rows) => ({
      box: (cell) => { const p = at();
        const s = clampSize(this._draft, index, { w: cols ? cell.col - p.col + 1 : p.colspan,
          h: rows ? cell.row - p.row + 1 : p.rowspan });
        return { row: p.row, col: p.col, rowspan: s.h, colspan: s.w }; },
      apply: (cell) => { const p = at();
        return resizePanel(this._draft, index, { w: cols ? cell.col - p.col + 1 : p.colspan,
          h: rows ? cell.row - p.row + 1 : p.rowspan }); },
    });
    this._dragOnto(grid, corner, sizing(true, true));
    this._dragOnto(grid, edgeE, sizing(true, false));
    this._dragOnto(grid, edgeS, sizing(false, true));
    return el;
  }

  /** Drag from `handle` across the grid: a ghost shows where `box(cell)`
   * lands; letting go applies `apply(cell)`. The grid's cells show while it
   * is dragged. `grab` (moving) gives the panel's top-left cell, so the panel
   * moves by the distance dragged instead of jumping under the pointer. */
  _dragOnto(grid, handle, { box, apply, grab = null }) {
    handle.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      handle.setPointerCapture(e.pointerId);
      handle.classList.add('dragging');
      grid.classList.add('dragging');
      const ghost = document.createElement('div');
      ghost.className = 'edit-ghost';
      grid.appendChild(ghost);
      const from = grab ? cellAt(grid, e.clientX, e.clientY) : null;
      const origin = grab ? grab() : null;
      let cell = null;
      const target = (ev) => {
        const c = cellAt(grid, ev.clientX, ev.clientY);
        return grab ? { row: origin.row + c.row - from.row, col: origin.col + c.col - from.col } : c;
      };
      const move = (ev) => {
        cell = target(ev);
        const b = box(cell);
        ghost.style.gridColumn = `${b.col + 1} / span ${b.colspan}`;
        ghost.style.gridRow = `${b.row + 1} / span ${b.rowspan}`;
      };
      const up = () => {
        handle.removeEventListener('pointermove', move);
        handle.removeEventListener('pointerup', up);
        handle.classList.remove('dragging');
        grid.classList.remove('dragging');
        ghost.remove();
        if (cell) this.change(apply(cell));
      };
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', up);
      move(e);
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
    const catalog = this._deps.catalog();
    if (result === 'Add' && chosen) this.change(addPanelWithPicker(this._draft, chosen, catalog));
    if (result === 'Custom panel…') {
      const panel = await this._builder(null);
      if (panel) this.change(addPanelWithPicker(this._draft, panel, catalog));
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
            row.append(metricSelect(role, value), iconButton('close', 'Remove this series', () => { row.remove(); sync(); }));
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

  /** A 3D panel's settings (ADR 0056 rules 1, 2, 13 and 15): the main view
   * or its own view (independent; the screen says "Own view"); for an own one, the two link ticks and its starting
   * look, which "Use current 3D settings as start" takes from the screen. */
  async _panel3d(index, visibleTabs, tabIndex) {
    const panel = this._draft.panels[index];
    const body = document.createElement('div');
    body.className = 'panel3d-settings';
    const view = document.createElement('select');
    view.dataset.field = 'view';
    view.append(new Option('Main view', 'linked'), new Option('Own view', 'independent'));
    view.value = panel.view === 'independent' ? 'independent' : 'linked';
    // The last linked panel stays linked (rule 6).
    const stays = view.value === 'linked' ? whyPanelStays(visibleTabs, tabIndex, index) : '';
    view.options[1].disabled = !!stays;
    const [frameRow, frame] = checkField("Follow the main view's frame", panel.link_frame !== false, 'link_frame');
    const [cameraRow, camera] = checkField("Follow the main view's camera", panel.link_camera !== false, 'link_camera');
    let start = cleanStart(panel.start);
    const summary = document.createElement('div');
    summary.className = 'ctl-hint';
    summary.dataset.field = 'start';
    const describe = () => {
      const keys = Object.entries(start || {});
      summary.textContent = keys.length
        ? `Starts with ${keys.map(([k, v]) => `${k} = ${v}`).join(', ')}`
        : 'Starts with the default look';
    };
    const useCurrent = button('Use current 3D settings as start', () => {
      start = cleanStart(this._deps.currentStart?.(this._draft.name, panel));
      describe();
    });
    useCurrent.dataset.edit = 'use-current';
    const reset = button('Default look', () => { start = null; describe(); });
    const startButtons = document.createElement('div');
    startButtons.className = 'edit-row';
    startButtons.append(useCurrent, reset);
    const hint = document.createElement('div');
    hint.className = 'ctl-hint';
    hint.textContent = stays ? `${stays}, so this panel stays on it.` : '';
    const sync = () => {
      const own = view.value === 'independent';
      for (const control of [frame, camera, useCurrent, reset]) control.disabled = !own;
    };
    view.addEventListener('change', sync);
    sync();
    describe();
    body.append(field('Shows', view), frameRow, cameraRow, summary, startButtons, hint);
    const { answer } = modal('3D panel', body, ['Cancel', 'Apply']);
    if ((await answer) !== 'Apply') return null;
    const next = setPanelView(this._draft, index,
      { view: view.value, link_frame: frame.checked, link_camera: camera.checked });
    return view.value === 'independent' ? setPanelStart(next, index, start) : next;
  }

  /** Tab settings (rule 14): name and the tab controls. */
  async _tabSettings(draft, { title, ok }) {
    const body = document.createElement('div');
    body.className = 'tab-settings';
    const name = Object.assign(document.createElement('input'), { type: 'text', value: draft.name });
    name.dataset.field = 'name';
    const [shiftRow, shift] = checkField('Energy shift', (draft.controls || []).includes('energy_shift'), 'energy_shift');
    const [elemRow, elems] = checkField('Element picker', draft.selector === 'atomic', 'element_picker');
    const why = document.createElement('div');
    why.className = 'ctl-hint';
    body.append(field('Name', name), shiftRow, elemRow, why);
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
    const next = { ...draft, name: name.value.trim() };
    const controls = (next.controls || []).filter((c) => c !== 'energy_shift');
    return { ...next, controls: shift.checked ? [...controls, 'energy_shift'] : controls,
      selector: elems.checked ? 'atomic' : (next.selector === 'atomic' ? null : next.selector) };
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

/** A button drawn as an icon, named for screen readers by `label`. */
function iconButton(name, label, onClick) {
  const b = button('', onClick);
  b.innerHTML = icon(name);
  b.setAttribute('aria-label', label);
  return b;
}

/** A labelled checkbox for a dialog: [its row, the box]. */
function checkField(label, checked, key) {
  const box = Object.assign(document.createElement('input'), { type: 'checkbox', checked });
  box.dataset.field = key;
  const lbl = document.createElement('label');
  lbl.className = 'conn-check';
  lbl.append(box, ` ${label}`);
  return [lbl, box];
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
  labelBy(el, head);
  const content = document.createElement('div');
  content.className = 'edit-modal-body';
  content.appendChild(body);
  const foot = document.createElement('div');
  foot.className = 'warning-modal-footer';
  let close;
  let untrap = () => {};
  const answer = new Promise((resolve) => {
    close = (label) => { el.remove(); untrap(); resolve(label); };
  });
  const buttons = labels.map((label) => {
    const b = button(label, () => close(label));
    foot.appendChild(b);
    return b;
  });
  el.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(null); });
  untrap = trapFocus(el);
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

/** Keep each resize corner ◢ and bottom edge where they can be grabbed: a
 * panel taller than the grid's visible part has them at the visible edge,
 * not out of sight below it. */
function cornersInView(grid) {
  if (!grid.isConnected) return;
  const g = grid.getBoundingClientRect();
  const right = Math.min(g.left + grid.clientLeft + grid.clientWidth, window.innerWidth);
  const bottom = Math.min(g.top + grid.clientTop + grid.clientHeight, window.innerHeight);
  for (const handle of grid.querySelectorAll('.edit-resize, .edit-edge.s')) {
    const c = handle.parentElement.getBoundingClientRect();
    // Never past the chrome's own top-left, so it stays on its panel.
    const past = (end, edge, size) => Math.max(0, Math.min(end - edge, size - 24));
    const corner = handle.classList.contains('edit-resize');
    if (corner) handle.style.right = `${3 + past(c.right, right, c.width)}px`;
    handle.style.bottom = `${(corner ? 1 : -5) + past(c.bottom, bottom, c.height)}px`;
  }
}

/** The grid cell under a page point. Columns stay inside the grid; rows go
 * on below the last row, a unit high each, so a panel can be dragged down. */
function cellAt(grid, x, y) {
  const t = tracks(grid);
  const r = grid.getBoundingClientRect();
  const index = (sizes, gap, pos, unit = 0) => {
    let edge = 0;
    for (let i = 0; i < sizes.length; i++) {
      edge += sizes[i];
      if (pos < edge + gap / 2) return i;
      edge += gap;
    }
    return unit ? sizes.length + Math.floor((pos - edge + gap / 2) / (unit + gap)) : sizes.length - 1;
  };
  const col = index(t.cols, t.gapC, x - r.left - t.padL + grid.scrollLeft);
  const row = index(t.rows, t.gapR, y - r.top - t.padT + grid.scrollTop, ROW_UNIT_PX);
  return { row: Math.max(0, row), col: Math.max(0, col) };
}

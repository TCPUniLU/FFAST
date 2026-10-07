/**
 * Small DOM helpers for the Loupe settings sidebar (ADR 0045 Phase 1).
 *
 * A "pane" is a titled section; `oneSectionOpen` makes the sidebar's panes a
 * list where one is open at a time (ADR 0055). A "row" is a label + control
 * line inside a pane. No framework — plain DOM,
 * consistent with the zero-build stance.
 */

/** @typedef {{min?: number, max?: number, step?: number}} RangeOpts */

/**
 * @param {string} title
 * @returns {{el: HTMLElement, body: HTMLElement}} el is the pane to append
 *   to the sidebar; body is where row helpers should append controls.
 */
export function createPane(title) {
  const el = document.createElement('div');
  el.className = 'pane';
  el.setAttribute('data-pane', title);   // stable hook for tests (Playwright locators)

  const header = document.createElement('div');
  header.className = 'pane-header';
  header.innerHTML = `<span class="pane-title">${title}</span><span class="pane-chevron">▾</span>`;

  const body = document.createElement('div');
  body.className = 'pane-body';

  el.append(header, body);
  return { el, body };
}

/**
 * Make the panes in `sidebarEl` a list where only one is open at a time
 * (ADR 0055). Clicking the open section's header closes it, leaving none
 * open. `onChange` hears every change, for remembering it.
 * @param {HTMLElement} sidebarEl
 * @param {{initial?: string|null, onChange?: (title: string|null) => void}} [opts]
 * @returns {{open: (title: string|null) => void, readonly openTitle: string|null}}
 */
export function oneSectionOpen(sidebarEl, { initial = null, onChange = () => {} } = {}) {
  let current = null;
  const show = (title) => {
    current = title;
    for (const pane of sidebarEl.querySelectorAll(':scope > .pane'))
      pane.classList.toggle('collapsed', pane.dataset.pane !== title);
  };
  const open = (title) => {
    if (title === current) return;
    show(title);
    onChange(title);
  };
  sidebarEl.addEventListener('click', (e) => {
    const header = e.target.closest('.pane-header');
    if (!header || header.parentElement?.parentElement !== sidebarEl) return;
    const title = header.parentElement.dataset.pane;
    open(title === current ? null : title);
  });
  show(initial);
  return {
    open,
    get openTitle() { return current; },
    /** Put the panes back as the list has them (after a search). */
    restore: () => show(current),
  };
}

/**
 * Hide a row until something else is true; `reason` says what ("Load a
 * prediction first"). An empty reason shows the row. The sidebar search still
 * finds a hidden row and shows it greyed out with its reason (ADR 0055).
 * @param {HTMLElement} rowEl @param {string} reason
 */
export function setRowNeeds(rowEl, reason) {
  rowEl.classList.toggle('needs', !!reason);
  if (reason) rowEl.dataset.needs = reason;
  else delete rowEl.dataset.needs;
}

/**
 * Sidebar search (ADR 0055): typing opens every section whose title or rows
 * match and highlights the matching rows, including rows behind "Exact
 * angles" and rows waiting for something (shown greyed out with the reason).
 * Clearing it, or Escape, restores the section that was open.
 * @param {HTMLInputElement} input @param {HTMLElement} sidebarEl
 * @param {{restore: () => void}} sections @param {HTMLElement} emptyEl
 */
export function bindSidebarSearch(input, sidebarEl, sections, emptyEl) {
  const rowText = (row) => [
    row.querySelector('label')?.textContent || '',
    ...[...row.querySelectorAll('button')].map((b) => b.textContent),
  ].join(' ').toLowerCase();

  const run = () => {
    const q = input.value.trim().toLowerCase();
    for (const el of sidebarEl.querySelectorAll('.search-hit, .search-reveal'))
      el.classList.remove('search-hit', 'search-reveal');
    sidebarEl.classList.toggle('searching', !!q);
    if (!q) {
      sections.restore();
      emptyEl.hidden = true;
      return;
    }
    let any = false;
    for (const pane of sidebarEl.querySelectorAll(':scope > .pane')) {
      if (pane.style.display === 'none') continue;   // a section that does not apply
      let hit = pane.dataset.pane.toLowerCase().includes(q);
      for (const row of pane.querySelectorAll('.ctl-row')) {
        if (!rowText(row).includes(q)) continue;
        row.classList.add('search-hit');
        row.closest('.exact-angles')?.classList.add('search-reveal');
        hit = true;
      }
      pane.classList.toggle('collapsed', !hit);
      any ||= hit;
    }
    emptyEl.hidden = any;
  };
  input.addEventListener('input', run);
  input.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    input.value = '';
    run();
    input.blur();
  });
  return { run };
}

/** Maps a control element to the `.ctl-row` div it was placed in — lets
 * callers toggle a row's visibility without DOM-property-tagging the
 * control itself (and without `.closest()`'s loose `Element` return type). */
const _rowByControl = new WeakMap();

/**
 * A label + arbitrary control element, appended as one row.
 * @param {HTMLElement} parent @param {string} label @param {HTMLElement} controlEl
 * @returns {HTMLElement} the row div
 */
export function row(parent, label, controlEl) {
  const r = document.createElement('div');
  r.className = 'ctl-row';
  r.setAttribute('data-label', label);   // stable hook for tests (Playwright locators)
  const lbl = document.createElement('label');
  lbl.textContent = label;
  r.append(lbl, controlEl);
  parent.appendChild(r);
  _rowByControl.set(controlEl, r);
  return r;
}

/** The `.ctl-row` a control was placed in by one of the row helpers below. */
export function rowElement(control) {
  return _rowByControl.get(control);
}

/** @returns {HTMLInputElement} */
export function checkboxRow(parent, label, checked, onChange) {
  const input = document.createElement('input');
  input.type = 'checkbox';
  input.checked = !!checked;
  input.addEventListener('change', () => onChange(input.checked));
  row(parent, label, input);
  return input;
}

/** @returns {HTMLSelectElement} */
export function selectRow(parent, label, options, value, onChange) {
  const sel = document.createElement('select');
  for (const opt of options) {
    const o = document.createElement('option');
    o.value = typeof opt === 'string' ? opt : opt.value;
    o.textContent = typeof opt === 'string' ? opt : opt.label;
    sel.appendChild(o);
  }
  sel.value = value;
  sel.addEventListener('change', () => onChange(sel.value));
  row(parent, label, sel);
  return sel;
}

/** @param {RangeOpts} opts @returns {HTMLInputElement} */
export function numberRow(parent, label, value, opts, onChange) {
  const { min, max, step = 1 } = opts || {};
  const input = document.createElement('input');
  input.type = 'number';
  input.className = 'ctl-number';
  if (min !== undefined) input.min = String(min);
  if (max !== undefined) input.max = String(max);
  input.step = String(step);
  input.value = String(value);
  input.addEventListener('change', () => onChange(parseFloat(input.value)));
  row(parent, label, input);
  return input;
}

/** @param {RangeOpts} opts @returns {HTMLInputElement} the range input */
export function sliderRow(parent, label, value, opts, onChange) {
  const { min = 0, max = 100, step = 1 } = opts || {};
  const wrap = document.createElement('div');
  wrap.className = 'ctl-slider-wrap';
  const input = document.createElement('input');
  input.type = 'range';
  input.min = String(min);
  input.max = String(max);
  input.step = String(step);
  input.value = String(value);
  const out = document.createElement('span');
  out.className = 'ctl-slider-value';
  out.textContent = String(value);
  input.addEventListener('input', () => {
    out.textContent = input.value;
    onChange(parseFloat(input.value));
  });
  wrap.append(input, out);
  row(parent, label, wrap);
  _rowByControl.set(input, _rowByControl.get(wrap));
  return input;
}

/** @returns {HTMLInputElement} */
export function textRow(parent, label, value, onChange) {
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'ctl-text';
  input.value = value || '';
  input.addEventListener('change', () => onChange(input.value));
  row(parent, label, input);
  return input;
}

/** @returns {HTMLInputElement} */
export function colorRow(parent, label, hex, onChange) {
  const input = document.createElement('input');
  input.type = 'color';
  input.className = 'ctl-color';
  input.value = hex || '#000000';
  input.addEventListener('input', () => onChange(input.value));
  row(parent, label, input);
  return input;
}

/** @returns {HTMLButtonElement} */
export function buttonRow(parent, label, text, onClick) {
  const btn = document.createElement('button');
  btn.textContent = text;
  btn.addEventListener('click', onClick);
  row(parent, label, btn);
  return btn;
}

/** A row of buttons with no label column (e.g. camera presets). */
export function buttonGroup(parent, buttons) {
  const wrap = document.createElement('div');
  wrap.className = 'ctl-btn-group';
  for (const { text, title, onClick } of buttons) {
    const btn = document.createElement('button');
    btn.textContent = text;
    if (title) btn.title = title;
    btn.addEventListener('click', onClick);
    wrap.appendChild(btn);
  }
  parent.appendChild(wrap);
  return wrap;
}

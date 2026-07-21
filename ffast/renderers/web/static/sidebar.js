/**
 * Small DOM helpers for the Loupe settings sidebar (ADR 0045 Phase 1).
 *
 * A "pane" is a collapsible titled section (mirrors Qt's SettingsPane); a
 * "row" is a label + control line inside one. No framework — plain DOM,
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
  header.addEventListener('click', () => el.classList.toggle('collapsed'));

  const body = document.createElement('div');
  body.className = 'pane-body';

  el.append(header, body);
  return { el, body };
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

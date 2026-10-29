/**
 * Small in-page dialogs, in the style of the connection dialog (ADR 0055):
 * the browser's own confirm() blocks the page and cannot be styled.
 */

let dialogCount = 0;

/** Name a dialog by its title element (screen readers read it on open). */
export function labelBy(modal, head) {
  head.id = `dialog-title-${++dialogCount}`;
  modal.setAttribute('aria-labelledby', head.id);
}

const FOCUSABLE = 'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), '
  + 'textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

/**
 * Keep Tab and Shift+Tab inside `modal` (ADR 0063). Returns a function that
 * removes the trap and puts focus back on what had it when the trap was set,
 * if that is still in the page and focus is in the modal or lost (on body):
 * call it as the modal closes, also one that is taken out of the page.
 * @param {HTMLElement} modal
 * @returns {() => void}
 */
export function trapFocus(modal) {
  const opener = document.activeElement;
  const onKey = (e) => {
    if (e.key !== 'Tab') return;
    const items = [...modal.querySelectorAll(FOCUSABLE)].filter((el) => el.getClientRects().length);
    if (!items.length) { e.preventDefault(); return; }
    const first = items[0], last = items[items.length - 1];
    const at = document.activeElement;
    if (!modal.contains(at) || (e.shiftKey && at === first) || (!e.shiftKey && at === last)) {
      e.preventDefault();
      (e.shiftKey ? last : first).focus();
    }
  };
  modal.addEventListener('keydown', onKey);
  return () => {
    modal.removeEventListener('keydown', onKey);
    const at = document.activeElement;
    const lost = !at || at === document.body || modal.contains(at);
    if (lost && opener && opener !== document.body && opener.isConnected) opener.focus();
  };
}

/**
 * Ask a question; resolves with the label of the chosen button, or null when
 * the dialog is dismissed (Escape).
 * @param {{title: string, message: string, buttons: string[], danger?: string}} o
 *   the last button is the default, focused one, unless `danger` names a
 *   button (one that deletes something): that one is outlined in red and focus
 *   goes to the first button that is not it.
 * @returns {Promise<string|null>}
 */
export function askDialog({ title, message, buttons, danger = '' }) {
  return new Promise((resolve) => {
    const modal = document.createElement('div');
    modal.className = 'warning-modal ask-dialog';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    const panel = document.createElement('div');
    panel.className = 'warning-modal-panel';
    const head = document.createElement('div');
    head.className = 'warning-modal-title';
    head.textContent = title;
    labelBy(modal, head);
    const text = document.createElement('p');
    text.className = 'warning-modal-message';
    text.textContent = message;
    const foot = document.createElement('div');
    foot.className = 'warning-modal-footer';
    let untrap = () => {};
    const done = (answer) => { modal.remove(); untrap(); resolve(answer); };
    for (const label of buttons) {
      const btn = document.createElement('button');
      btn.textContent = label;
      if (label === danger) btn.classList.add('danger');
      btn.addEventListener('click', () => done(label));
      foot.appendChild(btn);
    }
    modal.addEventListener('keydown', (e) => { if (e.key === 'Escape') done(null); });
    untrap = trapFocus(modal);
    panel.append(head, text, foot);
    modal.appendChild(panel);
    document.body.appendChild(modal);
    (danger ? [...foot.children].find((b) => !b.classList.contains('danger')) : foot.lastElementChild)?.focus();
  });
}

/**
 * Ask for one line of text. `problem(text)` names what is wrong with it ('' when
 * fine); OK waits while there is one. Resolves with the trimmed text, or null on
 * Cancel or Escape.
 * @param {{title: string, label: string, value?: string,
 *   problem?: (text: string) => string, okLabel?: string}} o
 * @returns {Promise<string|null>}
 */
export function askText({ title, label, value = '', problem = () => '', okLabel = 'OK' }) {
  return new Promise((resolve) => {
    const modal = document.createElement('div');
    modal.id = 'text-ask';
    modal.className = 'warning-modal ask-dialog';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    const panel = document.createElement('div');
    panel.className = 'warning-modal-panel';
    const head = document.createElement('div');
    head.className = 'warning-modal-title';
    head.textContent = title;
    labelBy(modal, head);
    const field = document.createElement('label');
    field.append(label);
    const input = document.createElement('input');
    input.type = 'text';
    input.value = value;
    input.spellcheck = false;
    field.appendChild(input);
    const help = document.createElement('p');
    help.className = 'help-text';
    const foot = document.createElement('div');
    foot.className = 'warning-modal-footer';
    let untrap = () => {};
    const done = (answer) => { modal.remove(); untrap(); resolve(answer); };
    const cancel = document.createElement('button');
    cancel.textContent = 'Cancel';
    cancel.addEventListener('click', () => done(null));
    const ok = document.createElement('button');
    ok.textContent = okLabel;
    const accept = () => { if (!ok.disabled) done(input.value.trim()); };
    ok.addEventListener('click', accept);
    const check = () => {
      const why = problem(input.value);
      help.textContent = why;
      ok.disabled = !!why;
    };
    input.addEventListener('input', check);
    modal.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') done(null);
      else if (e.key === 'Enter' && e.target === input) { e.preventDefault(); accept(); }
    });
    foot.append(cancel, ok);
    panel.append(head, field, help, foot);
    modal.appendChild(panel);
    check();
    untrap = trapFocus(modal);
    document.body.appendChild(modal);
    input.focus();
    input.select();
  });
}

/**
 * A small form: one box per field. Resolves with the boxes' text in field
 * order when the last button is chosen (or Enter is pressed in a box), and
 * null otherwise (another button, Escape).
 * @param {{title: string, note?: string, buttons?: string[],
 *   fields: Array<{label: string, value: string, placeholder?: string,
 *   type?: 'text'|'number', min?: number, max?: number}>}} o
 * @returns {Promise<string[]|null>}
 */
export function fieldsDialog({ title, note = '', fields, buttons = ['Cancel', 'Save'] }) {
  return new Promise((resolve) => {
    const modal = document.createElement('div');
    modal.className = 'warning-modal ask-dialog fields-dialog';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    const panel = document.createElement('div');
    panel.className = 'warning-modal-panel';
    const head = document.createElement('div');
    head.className = 'warning-modal-title';
    head.textContent = title;
    labelBy(modal, head);
    panel.appendChild(head);
    if (note) {
      const text = document.createElement('p');
      text.className = 'warning-modal-message';
      text.textContent = note;
      panel.appendChild(text);
    }
    const inputs = fields.map((f) => {
      const label = document.createElement('label');
      label.append(f.label);
      const input = document.createElement('input');
      input.type = f.type || 'text';
      input.value = f.value ?? '';
      if (f.placeholder) input.placeholder = f.placeholder;
      if (f.min != null) input.min = String(f.min);
      if (f.max != null) input.max = String(f.max);
      label.appendChild(input);
      panel.appendChild(label);
      return input;
    });
    const foot = document.createElement('div');
    foot.className = 'warning-modal-footer';
    let untrap = () => {};
    const done = (answer) => { modal.remove(); untrap(); resolve(answer); };
    const accept = () => done(inputs.map((i) => i.value));
    buttons.forEach((label, i) => {
      const btn = document.createElement('button');
      btn.textContent = label;
      btn.addEventListener('click', () => (i === buttons.length - 1 ? accept() : done(null)));
      foot.appendChild(btn);
    });
    modal.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') done(null);
      else if (e.key === 'Enter' && e.target.tagName === 'INPUT') { e.preventDefault(); accept(); }
    });
    untrap = trapFocus(modal);
    panel.appendChild(foot);
    modal.appendChild(panel);
    document.body.appendChild(modal);
    inputs[0]?.focus();
  });
}

/**
 * Ask for the unit of a dataset's energies or forces, in a small box at the
 * click. Enter keeps what is typed (empty: no unit), Escape or a click
 * elsewhere keeps the old one.
 * @param {{x: number, y: number, dimension: string, value: string}} o
 * @returns {Promise<string|null>} the unit, or null when cancelled
 */
export function unitPopover({ x, y, dimension, value }) {
  document.getElementById('unit-popover')?.remove();
  return new Promise((resolve) => {
    const box = document.createElement('div');
    box.id = 'unit-popover';
    box.setAttribute('role', 'dialog');
    box.style.left = `${Math.max(8, Math.min(x, window.innerWidth - 220))}px`;
    box.style.top = `${Math.max(8, Math.min(y + 8, window.innerHeight - 80))}px`;
    const label = document.createElement('label');
    label.textContent = `Unit of ${dimension}`;
    const input = document.createElement('input');
    input.type = 'text';
    input.value = value || '';
    input.placeholder = dimension === 'force' ? 'eV/Å' : 'eV';
    input.spellcheck = false;
    label.appendChild(input);
    box.appendChild(label);
    let done = false;
    const finish = (answer) => {
      if (done) return;
      done = true;
      document.removeEventListener('pointerdown', outside, true);
      box.remove();
      resolve(answer);
    };
    const outside = (e) => { if (!box.contains(e.target)) finish(null); };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); finish(input.value.trim()); }
      else if (e.key === 'Escape') { e.preventDefault(); finish(null); }
    });
    document.addEventListener('pointerdown', outside, true);
    document.body.appendChild(box);
    input.focus();
    input.select();
  });
}

/**
 * Show text to copy or save as a file (Export, ADR 0056 rule 9).
 * @param {{title: string, text: string, filename: string, note?: string}} o
 */
export function textDialog({ title, text, filename, note = '' }) {
  const modal = document.createElement('div');
  modal.className = 'warning-modal';
  modal.id = 'text-dialog';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  const panel = document.createElement('div');
  panel.className = 'warning-modal-panel text-dialog-panel';
  const head = document.createElement('div');
  head.className = 'warning-modal-title';
  head.textContent = title;
  labelBy(modal, head);
  const body = document.createElement('div');
  body.className = 'text-dialog-body';
  if (note) {
    const p = document.createElement('p');
    p.className = 'help-text';
    p.textContent = note;
    body.appendChild(p);
  }
  const area = document.createElement('textarea');
  area.readOnly = true;
  area.value = text;
  area.spellcheck = false;
  body.appendChild(area);
  const foot = document.createElement('div');
  foot.className = 'warning-modal-footer';
  let untrap = () => {};
  const close = () => { modal.remove(); untrap(); };
  const copy = document.createElement('button');
  copy.textContent = 'Copy';
  copy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      area.select();
      document.execCommand('copy');
    }
    copy.textContent = 'Copied';
  });
  const save = document.createElement('button');
  save.textContent = 'Download';
  save.addEventListener('click', () => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: 'application/toml' }));
    a.download = filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });
  const spacer = document.createElement('span');
  spacer.className = 'spacer';
  const done = document.createElement('button');
  done.textContent = 'Close';
  done.addEventListener('click', close);
  foot.append(copy, save, spacer, done);
  modal.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  untrap = trapFocus(modal);
  panel.append(head, body, foot);
  modal.appendChild(panel);
  document.body.appendChild(modal);
  done.focus();
  return modal;
}

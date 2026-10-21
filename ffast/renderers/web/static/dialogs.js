/**
 * Small in-page dialogs, in the style of the connection dialog (ADR 0055):
 * the browser's own confirm() blocks the page and cannot be styled.
 */

/**
 * Ask a question; resolves with the label of the chosen button, or null when
 * the dialog is dismissed (Escape).
 * @param {{title: string, message: string, buttons: string[]}} o the last
 *   button is the default, focused one
 * @returns {Promise<string|null>}
 */
export function askDialog({ title, message, buttons }) {
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
    const text = document.createElement('p');
    text.className = 'warning-modal-message';
    text.textContent = message;
    const foot = document.createElement('div');
    foot.className = 'warning-modal-footer';
    const done = (answer) => { modal.remove(); resolve(answer); };
    for (const label of buttons) {
      const btn = document.createElement('button');
      btn.textContent = label;
      btn.addEventListener('click', () => done(label));
      foot.appendChild(btn);
    }
    modal.addEventListener('keydown', (e) => { if (e.key === 'Escape') done(null); });
    panel.append(head, text, foot);
    modal.appendChild(panel);
    document.body.appendChild(modal);
    foot.lastElementChild?.focus();
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
  const close = () => modal.remove();
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
  panel.append(head, body, foot);
  modal.appendChild(panel);
  document.body.appendChild(modal);
  done.focus();
  return modal;
}

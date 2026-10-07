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

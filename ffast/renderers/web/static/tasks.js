/**
 * The running tasks of the server, behind the top bar's Tasks button (web
 * parity with the desktop's Tasks list). The server names a task with
 * `TASK_INFO` and cancels one on `CANCEL_TASK`; progress comes as
 * `TASK_PROGRESS`.
 */

import { icon } from './icons.js';
import { spinner } from './spinner.js';

/** "3/10", or "25%" when the job reports a percent; '' without numbers. Pure. */
export function progressText({ prog, progMax, percent } = {}) {
  if (!Number.isFinite(prog) || !Number.isFinite(progMax) || !progMax) return '';
  return percent ? `${Math.round(prog / progMax * 100)}%` : `${prog}/${progMax}`;
}

export class TaskList {
  /**
   * @param {{menu: HTMLElement, button: HTMLElement, panel: HTMLElement,
   *   onCancel: (id: number|string) => void, whyNoCancel: () => string}} o
   */
  constructor({ menu, button, panel, onCancel, whyNoCancel }) {
    this._menu = menu;
    this._button = button;
    this._panel = panel;
    this._onCancel = onCancel;
    this._whyNoCancel = whyNoCancel;
    /** @type {Map<string, {id: any, row: HTMLElement}>} */
    this._rows = new Map();
    // One spinner for the button's life, so a row coming or going does not
    // restart it (the button is hidden while nothing runs).
    this._label = document.createElement('span');
    button.replaceChildren(spinner(), this._label);
    button.addEventListener('click', () => this.toggle());
    panel.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { this.close(); button.focus(); }
    });
    document.addEventListener('pointerdown', (e) => {
      if (!menu.contains(e.target)) this.close();
    });
  }

  /** A task started: add its row when the job is one to show. A task that
   *  cannot be cancelled (a computing metric) says so instead of a Cancel. */
  info(id, { name, visual, cancellable = true }) {
    const key = String(id);
    if (!visual || this._rows.has(key)) return;
    const row = document.createElement('div');
    row.className = 'task-row';
    row.setAttribute('role', 'listitem');
    row.dataset.taskId = key;
    const title = document.createElement('span');
    title.className = 'task-name';
    title.textContent = name;
    const count = document.createElement('span');
    count.className = 'task-count';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'task-cancel';
    cancel.innerHTML = icon('close');
    const label = `Cancel ${name}`;
    cancel.setAttribute('aria-label', label);
    const why = this._whyNoCancel();
    cancel.title = why || label;
    cancel.disabled = !!why;
    cancel.addEventListener('click', () => {
      this._onCancel(id);
      cancel.disabled = true;   // the row goes when TASK_DONE arrives
    });
    const message = document.createElement('span');
    message.className = 'task-message';
    message.textContent = 'Working…';
    if (!cancellable) {
      cancel.hidden = true;
      message.textContent = 'Working… (cannot be stopped once started)';
    }
    const bar = document.createElement('progress');
    row.append(spinner(), title, count, cancel, message, bar);
    this._panel.appendChild(row);
    this._rows.set(key, { id, row });
    this._refresh();
  }

  /** Progress of a listed task; ids that are not listed (a metric Try) are ignored. */
  progress(id, kw = {}) {
    const entry = this._rows.get(String(id));
    if (!entry) return;
    const { row } = entry;
    const message = row.querySelector('.task-message');
    message.textContent = kw.message || 'Working…';
    message.classList.toggle('error', !!kw.error);
    row.querySelector('.task-count').textContent = progressText(kw);
    const bar = row.querySelector('progress');
    if (Number.isFinite(kw.prog) && Number.isFinite(kw.progMax) && kw.progMax) {
      bar.max = kw.progMax;
      bar.value = kw.prog;
    } else {
      bar.removeAttribute('value');
    }
    if (kw.title) {
      row.querySelector('.task-name').textContent = kw.title;
      row.querySelector('.task-cancel').setAttribute('aria-label', `Cancel ${kw.title}`);
    }
  }

  done(id) {
    const entry = this._rows.get(String(id));
    if (!entry) return;
    entry.row.remove();
    this._rows.delete(String(id));
    this._refresh();
  }

  reset() {
    for (const { row } of this._rows.values()) row.remove();
    this._rows.clear();
    this._refresh();
  }

  toggle() { if (this._panel.classList.contains('hidden')) this.open(); else this.close(); }

  open() {
    this._panel.classList.remove('hidden');
    this._button.setAttribute('aria-expanded', 'true');
  }

  close() {
    this._panel.classList.add('hidden');
    this._button.setAttribute('aria-expanded', 'false');
  }

  _refresh() {
    const n = this._rows.size;
    this._label.textContent = `Tasks (${n})`;
    this._menu.hidden = n === 0;
    if (n === 0) this.close();
  }
}

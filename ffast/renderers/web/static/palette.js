/**
 * The command palette: a search box over the one action list (ADR 0055).
 * Alt+Shift+K opens it, typing narrows the list, Enter runs the top choice,
 * Escape closes it. An action that cannot run now stays in the list, greyed
 * out with its reason.
 */
import { runAction, whyUnavailable } from './actions.js';

/** The actions whose label contains every word typed, in list order.
 * @param {Array<{label: string}>} actions @param {string} query */
export function filterActions(actions, query) {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  return actions.filter((a) => words.every((w) => a.label.toLowerCase().includes(w)));
}

/** @param {() => Array<import('./actions.js').Action>} getActions
 *  @param {(why: string) => void} onBlocked called when Enter hits an action that cannot run */
export function createPalette(getActions, onBlocked) {
  const box = document.createElement('div');
  box.id = 'palette';
  box.className = 'hidden';
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-label', 'Command palette');
  const input = document.createElement('input');
  input.type = 'text';
  input.placeholder = 'Type a command…';
  input.setAttribute('aria-label', 'Command');
  const list = document.createElement('div');
  list.setAttribute('role', 'listbox');
  box.append(input, list);
  document.body.appendChild(box);

  let shown = [];
  let at = 0;
  let before = null;   // what had focus, so Escape gives it back

  const draw = () => {
    shown = filterActions(getActions(), input.value);
    at = Math.min(at, Math.max(shown.length - 1, 0));
    list.replaceChildren(...shown.map((a, i) => {
      const row = document.createElement('div');
      row.setAttribute('role', 'option');
      row.className = 'palette-row' + (i === at ? ' active' : '');
      row.setAttribute('aria-selected', String(i === at));
      const why = whyUnavailable(a);
      if (why) { row.classList.add('blocked'); row.title = why; }
      const name = document.createElement('span');
      name.textContent = a.label;
      row.appendChild(name);
      if (a.keys) {
        const keys = document.createElement('span');
        keys.className = 'palette-keys';
        keys.textContent = a.keys;
        row.appendChild(keys);
      }
      row.addEventListener('click', () => choose(a));
      return row;
    }));
    if (!shown.length) {
      const none = document.createElement('div');
      none.className = 'palette-none';
      none.textContent = 'No command matches';
      list.appendChild(none);
    }
    list.querySelector('.active')?.scrollIntoView({ block: 'nearest' });
  };

  const close = () => {
    if (box.classList.contains('hidden')) return;
    box.classList.add('hidden');
    before?.focus?.();
    before = null;
  };
  const choose = (action) => {
    const why = whyUnavailable(action);
    close();
    if (why) onBlocked(why); else runAction(action);
  };
  const open = () => {
    if (box.classList.contains('hidden')) before = document.activeElement;
    input.value = '';
    at = 0;
    box.classList.remove('hidden');
    draw();
    input.focus();
  };

  input.addEventListener('input', () => { at = 0; draw(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); close(); }
    else if (e.key === 'Enter') { e.preventDefault(); if (shown[at]) choose(shown[at]); }
    else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!shown.length) return;
      at = (at + (e.key === 'ArrowDown' ? 1 : -1) + shown.length) % shown.length;
      draw();
    }
  });
  document.addEventListener('pointerdown', (e) => { if (!box.contains(e.target)) close(); });
  return { open, close, get isOpen() { return !box.classList.contains('hidden'); } };
}

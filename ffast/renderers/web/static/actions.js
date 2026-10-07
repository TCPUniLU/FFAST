/**
 * One action list (ADR 0055) feeds the File menu, the keyboard shortcuts and
 * the command palette, so a label or a "why not now" reason is written once.
 *
 * An action is `{id, label, run, unavailable?}`. `unavailable()` returns ''
 * when the action can run, or the reason it cannot ("Connect to a server
 * first"), which the menu shows on the greyed-out item.
 */

/** @typedef {{id: string, label: string, run: () => void, unavailable?: () => string}} Action */

/** @param {Action} action @returns {string} '' when the action can run */
export function whyUnavailable(action) {
  return action.unavailable ? action.unavailable() || '' : '';
}

/** Run `action` if it can run now; returns whether it ran. */
export function runAction(action) {
  if (!action || whyUnavailable(action)) return false;
  action.run();
  return true;
}

/**
 * A drop-down menu over `actions`. Items are rebuilt each time it opens, so
 * their enabled state is always current. Closes on Escape, on a click outside
 * and after running an item.
 * @param {HTMLButtonElement} button @param {HTMLElement} list
 * @param {Action[]} actions
 */
export function bindMenu(button, list, actions) {
  const close = () => {
    list.classList.add('hidden');
    button.setAttribute('aria-expanded', 'false');
  };
  const open = () => {
    list.replaceChildren(...actions.map((action) => {
      const item = document.createElement('button');
      item.setAttribute('role', 'menuitem');
      item.dataset.action = action.id;
      item.textContent = action.label;
      const why = whyUnavailable(action);
      item.disabled = !!why;
      if (why) item.title = why;
      item.addEventListener('click', () => { close(); runAction(action); });
      return item;
    }));
    list.classList.remove('hidden');
    button.setAttribute('aria-expanded', 'true');
    list.querySelector('[role=menuitem]:not(:disabled)')?.focus();
  };
  button.addEventListener('click', () => (list.classList.contains('hidden') ? open() : close()));
  list.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { close(); button.focus(); }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const items = [...list.querySelectorAll('[role=menuitem]:not(:disabled)')];
      const i = items.indexOf(document.activeElement);
      const next = e.key === 'ArrowDown' ? i + 1 : i - 1;
      items[(next + items.length) % items.length]?.focus();
    }
  });
  document.addEventListener('pointerdown', (e) => {
    if (!list.contains(e.target) && !button.contains(e.target)) close();
  });
  return { open, close };
}

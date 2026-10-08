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
 * their enabled state is always current; `actions` may be a function, for a
 * menu whose items change (the tab menu's hidden tabs), and an entry
 * `{separator: true}` draws a line. Closes on Escape, on a click outside and
 * after running an item.
 * @param {HTMLButtonElement} button @param {HTMLElement} list
 * @param {Action[] | (() => Array<Action|{separator: true}>)} actions
 */
export function bindMenu(button, list, actions) {
  const close = () => {
    list.classList.add('hidden');
    button.setAttribute('aria-expanded', 'false');
  };
  const open = () => {
    fillMenu(list, typeof actions === 'function' ? actions() : actions, close);
    list.classList.remove('hidden');
    button.setAttribute('aria-expanded', 'true');
    list.querySelector('[role=menuitem]:not(:disabled)')?.focus();
  };
  button.addEventListener('click', () => (list.classList.contains('hidden') ? open() : close()));
  bindMenuKeys(list, () => { close(); button.focus(); });
  document.addEventListener('pointerdown', (e) => {
    if (!list.contains(e.target) && !button.contains(e.target)) close();
  });
  return { open, close };
}

/**
 * One drop-down `list` shared by many buttons, for the rows of a list that is
 * redrawn (binding a menu to each row's button would add a document listener
 * per row on every redraw). `open(button, actions)` shows it under `button`;
 * opening it again from the same button closes it.
 * @param {HTMLElement} list
 */
export function sharedMenu(list) {
  let owner = null;
  const close = () => {
    list.classList.add('hidden');
    owner?.setAttribute('aria-expanded', 'false');
    owner = null;
  };
  /** @param {HTMLElement} button @param {Array<Action|{separator: true}>} actions */
  const open = (button, actions) => {
    const again = owner === button;
    close();
    if (again) return;
    owner = button;
    fillMenu(list, actions, close);
    const at = button.getBoundingClientRect();
    list.style.left = `${at.left}px`;
    list.style.top = `${at.bottom + 4}px`;
    list.classList.remove('hidden');
    button.setAttribute('aria-expanded', 'true');
    list.querySelector('[role=menuitem]:not(:disabled)')?.focus();
  };
  bindMenuKeys(list, () => { const back = owner; close(); back?.focus(); });
  document.addEventListener('pointerdown', (e) => {
    if (owner && !list.contains(e.target) && !owner.contains(e.target)) close();
  });
  return { open, close };
}

/** Fill a menu `list` with `entries`; choosing an item runs `close` first. */
function fillMenu(list, entries, close) {
  list.replaceChildren(...entries.map((action) => {
    if (action.separator) {
      const line = document.createElement('div');
      line.setAttribute('role', 'separator');
      line.className = 'menu-sep';
      return line;
    }
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
}

/** Escape closes a menu (`escape`), the arrow keys move between its items. */
function bindMenuKeys(list, escape) {
  list.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') escape();
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const items = [...list.querySelectorAll('[role=menuitem]:not(:disabled)')];
      const i = items.indexOf(document.activeElement);
      const next = e.key === 'ArrowDown' ? i + 1 : i - 1;
      items[(next + items.length) % items.length]?.focus();
    }
  });
}

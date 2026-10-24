/**
 * Warnings as pop-ups in the bottom-right corner (SERVER_WARNING).
 *
 * A pop-up does not block the page: it closes by its × or by itself after
 * HIDE_AFTER_MS, unless the pointer is on it. The same text while its pop-up
 * is still open adds "×2" to it instead of a second pop-up. At most MAX_SHOWN
 * are open; a new one closes the oldest.
 *
 * `showOnce` is for messages a redraw repeats (a panel's result warning): it
 * shows each key once per page load, open or closed since (ADR 0066 rule 4).
 */

const HIDE_AFTER_MS = 10000;
const MAX_SHOWN = 4;

function stack() {
  let box = document.getElementById('toasts');
  if (!box) {
    box = document.createElement('div');
    box.id = 'toasts';
    box.setAttribute('role', 'status');
    box.setAttribute('aria-live', 'polite');
    document.body.appendChild(box);
  }
  return box;
}

/**
 * Show `message` as a pop-up. `level` is the server's: "warning", "error" or
 * "critical"; the last two are drawn as errors.
 * @param {string} message
 * @param {string} [level]
 * @returns {HTMLElement} the pop-up
 */
export function showWarning(message, level = 'warning') {
  const box = stack();
  const text = String(message);
  const open = [...box.children].find((t) => t.dataset.text === text);
  if (open) {
    const n = Number(open.dataset.count) + 1;
    open.dataset.count = String(n);
    open.querySelector('.toast-count').textContent = `×${n}`;
    open._restart();
    return open;
  }

  const toast = document.createElement('div');
  toast.className = `toast toast-${level === 'warning' ? 'warning' : 'error'}`;
  toast.dataset.text = text;
  toast.dataset.count = '1';
  const mark = document.createElement('span');
  mark.className = 'toast-mark';
  mark.textContent = '⚠';
  mark.setAttribute('aria-hidden', 'true');
  const body = document.createElement('span');
  body.className = 'toast-text';
  body.textContent = text;
  const count = document.createElement('span');
  count.className = 'toast-count';
  const close = document.createElement('button');
  close.className = 'toast-close';
  close.type = 'button';
  close.textContent = '×';
  close.title = 'Close';
  close.setAttribute('aria-label', 'Close warning');
  toast.append(mark, body, count, close);

  let timer = null;
  const remove = () => { clearTimeout(timer); toast.remove(); };
  toast._restart = () => { clearTimeout(timer); timer = setTimeout(remove, HIDE_AFTER_MS); };
  toast.addEventListener('pointerenter', () => clearTimeout(timer));
  toast.addEventListener('pointerleave', toast._restart);
  close.addEventListener('click', remove);

  box.appendChild(toast);
  while (box.children.length > MAX_SHOWN) box.firstElementChild.remove();
  toast._restart();
  return toast;
}

const shownKeys = new Set();

/**
 * `showWarning(message, level)` the first time `key` is seen in this window;
 * null after that.
 * @param {string} key @param {string} message @param {string} [level]
 * @returns {HTMLElement|null}
 */
export function showOnce(key, message, level = 'warning') {
  if (shownKeys.has(key)) return null;
  shownKeys.add(key);
  return showWarning(message, level);
}

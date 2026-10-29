/**
 * A hover box that shows at once, for text the browser's own tooltip shows
 * too late to be noticed (a panel's ⚠). One box for the page, drawn above
 * everything, so a panel that scrolls its contents does not cut it off.
 */

function tipBox() {
  let box = document.getElementById('hover-tip');
  if (!box) {
    box = document.createElement('div');
    box.id = 'hover-tip';
    box.setAttribute('role', 'tooltip');
    box.hidden = true;
    document.body.appendChild(box);
  }
  return box;
}

function show(el) {
  const text = el.dataset.tip;
  if (!text) return;
  const box = tipBox();
  box.textContent = text;
  box.hidden = false;
  const r = el.getBoundingClientRect();
  const w = box.offsetWidth;
  const h = box.offsetHeight;
  const left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8));
  const below = r.bottom + 6;
  const top = below + h > window.innerHeight - 8 ? Math.max(8, r.top - h - 6) : below;
  box.style.left = `${left}px`;
  box.style.top = `${top}px`;
}

function hide() {
  const box = document.getElementById('hover-tip');
  if (box) box.hidden = true;
}

/**
 * Show `el.dataset.tip` in the hover box while the pointer is on `el` or it
 * has keyboard focus. Set or change the text through `el.dataset.tip`.
 * @param {HTMLElement} el
 */
export function attachTip(el) {
  if (el._tip) return;
  el._tip = true;
  el.addEventListener('pointerenter', () => show(el));
  el.addEventListener('pointerleave', hide);
  el.addEventListener('focus', () => show(el));
  el.addEventListener('blur', hide);
  el.addEventListener('keydown', (e) => { if (e.key === 'Escape') hide(); });
}

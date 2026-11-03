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

/** True when `el` hides part of its one-line text (ellipsis or clipped). */
function isCut(el) {
  if (el.scrollWidth <= el.clientWidth + 1) return false;
  if (el.matches('input, select')) return true;
  const st = getComputedStyle(el);
  return st.textOverflow === 'ellipsis' || st.overflowX === 'hidden' || st.overflowX === 'clip';
}

function fullText(el) {
  if (el instanceof HTMLSelectElement) return el.selectedOptions[0]?.textContent ?? '';
  if (el instanceof HTMLInputElement) return el.value;
  return el.textContent;
}

/**
 * Text the page cuts short ("…", or just clipped) shows in full as the
 * browser tooltip on hover. One listener for the page, so every list, row
 * and input gets it without its own code. An element's own `title` wins.
 */
export function initFullTextTitles(root = document) {
  root.addEventListener('pointerover', (e) => {
    let el = e.target instanceof Element ? e.target : null;
    // the hovered element or a near parent (the row's name span)
    for (let up = 0; el && up < 3 && el !== document.body; up++, el = el.parentElement) {
      if (el.dataset.autoTitle) { el.removeAttribute('title'); delete el.dataset.autoTitle; }
      if (el.title) return;                              // its own tooltip
      if (isCut(el)) break;
    }
    if (!el || el === document.body || !isCut(el)) return;
    const full = fullText(el).trim();
    if (!full) return;
    el.title = full;
    el.dataset.autoTitle = '1';                          // redone each hover
  });
}

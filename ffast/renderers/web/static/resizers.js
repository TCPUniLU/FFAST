/**
 * Draggable panel edges: drag to set a panel's width, double-click for the
 * default, arrow keys when the edge has focus. The width is browser layout
 * state (ADR 0055 rule 3), remembered in localStorage, never in a session.
 */

import { loadLayout, saveLayout } from './layout_state.js';

const KEY_STEP_PX = 10;

/**
 * @param {HTMLElement} handle the edge to drag
 * @param {HTMLElement} panel the panel whose width it sets
 * @param {{
 *   name: string,            // key under `widths` in the stored layout
 *   side: 'left'|'right',    // which side of the window the panel sits on
 *   width: number,           // default width, also what double-click restores
 *   min: number,
 *   max: () => number,       // recomputed per drag, from the window size
 * }} opts
 */
export function makeResizable(handle, panel, { name, side, width: defaultWidth, min, max }) {
  const clamp = (w) => Math.round(Math.max(min, Math.min(max(), w)));
  const apply = (w, remember = true) => {
    panel.style.width = `${clamp(w)}px`;
    handle.setAttribute('aria-valuenow', String(clamp(w)));
    if (remember) saveLayout({ widths: { ...(loadLayout().widths || {}), [name]: clamp(w) } });
  };
  // A panel on the right grows when its edge moves left.
  const sign = side === 'right' ? -1 : 1;

  handle.setAttribute('role', 'separator');
  handle.setAttribute('aria-orientation', 'vertical');
  handle.tabIndex = 0;
  handle.title = 'Drag to resize; double-click to reset';

  const stored = loadLayout().widths?.[name];
  if (typeof stored === 'number') apply(stored, false);

  handle.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    const startX = e.clientX, startW = panel.getBoundingClientRect().width;
    document.body.classList.add('resizing');
    const move = (ev) => apply(startW + sign * (ev.clientX - startX), false);
    const up = (ev) => {
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
      document.body.classList.remove('resizing');
      apply(startW + sign * (ev.clientX - startX));
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
  });
  handle.addEventListener('dblclick', () => apply(defaultWidth));
  handle.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const dir = e.key === 'ArrowRight' ? 1 : -1;
    apply(panel.getBoundingClientRect().width + sign * dir * KEY_STEP_PX);
  });
}

// How this browser shows the figure area (ADR 0063): Screen or Journal page,
// and the size of everything (Density). Both are browser layout state in
// `ffast.layout` (layout_state.js), never in a session file.

import { loadLayout, saveLayout } from './layout_state.js';

/** `--scale` for each Density (Large is the size before ADR 0063, for talks). */
export const DENSITIES = { compact: 1, comfortable: 1.15, large: 1.4 };

/** The Density named in the stored layout, or Compact when it names none. */
export function densityName(value) {
  return Object.hasOwn(DENSITIES, value) ? value : 'compact';
}

/** Put `journal` (a boolean) and `density` (a name) on the page. */
export function applyAppearance({ journal, density }) {
  const name = densityName(density);
  document.body.classList.toggle('journal', !!journal);
  document.documentElement.style.setProperty('--scale', String(DENSITIES[name]));
  for (const button of document.querySelectorAll('#view-mode [role=radio]')) {
    button.setAttribute('aria-checked', String((button.dataset.mode === 'journal') === !!journal));
  }
  const select = document.getElementById('density');
  if (select) select.value = name;
}

/**
 * Restore the stored choices and wire the top bar's switch and selector.
 * @param {() => void} onChange called after a choice, to draw the plots again
 */
export function initAppearance(onChange) {
  const stored = loadLayout();
  const state = { journal: stored.journal === true, density: densityName(stored.density) };
  applyAppearance(state);
  const change = (patch) => {
    Object.assign(state, patch);
    applyAppearance(state);
    saveLayout({ journal: state.journal, density: state.density });
    onChange();
  };
  for (const button of document.querySelectorAll('#view-mode [role=radio]')) {
    button.addEventListener('click', () => change({ journal: button.dataset.mode === 'journal' }));
  }
  document.getElementById('density')?.addEventListener('change', (e) => change({ density: e.target.value }));
}

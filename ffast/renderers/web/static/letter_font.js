// The font of the panel letters (a, b, c…) on the Journal page. On screen the
// letters use the program font; on the Journal page they use this one, serif
// unless the user picks another by double-clicking or right-clicking a letter.
// One choice for every letter, kept in this browser's layout state
// (layout_state.js), never in a session file.

import { sharedMenu } from './actions.js';
import { loadLayout, saveLayout } from './layout_state.js';

/** The fonts a panel letter can take on the Journal page; the first is the default. */
export const LETTER_FONTS = [
  { id: 'serif', label: 'Georgia (serif)', stack: 'Georgia, "Times New Roman", serif' },
  { id: 'times', label: 'Times', stack: '"Times New Roman", Times, serif' },
  { id: 'helvetica', label: 'Helvetica / Arial', stack: '"Helvetica Neue", Helvetica, Arial, sans-serif' },
  { id: 'program', label: 'Program font', stack: 'var(--font)' },
];

/** The letter font named by `value`, or the default when it names none. */
export function letterFontId(value) {
  return LETTER_FONTS.some((f) => f.id === value) ? value : LETTER_FONTS[0].id;
}

/** Give the Journal page's panel letters the font `id`. */
export function applyLetterFont(id) {
  const font = LETTER_FONTS.find((f) => f.id === letterFontId(id));
  document.documentElement.style.setProperty('--journal-letter-font', font.stack);
}

/**
 * Restore the stored letter font, and on the Journal page let a double-click
 * or a right-click on any panel letter open a menu of fonts.
 */
export function initLetterFont() {
  let current = letterFontId(loadLayout().letterFont);
  applyLetterFont(current);

  const list = document.createElement('div');
  list.className = 'menu-list letter-font-menu hidden';
  list.setAttribute('role', 'menu');
  list.setAttribute('aria-label', 'Panel letter font');
  document.body.appendChild(list);
  const menu = sharedMenu(list);

  const actions = () => LETTER_FONTS.map((font) => ({
    id: `letter-font-${font.id}`,
    label: `${font.id === current ? '✓ ' : ' '}${font.label}`,
    run: () => {
      current = font.id;
      applyLetterFont(current);
      saveLayout({ letterFont: current });
    },
  }));

  // Capture phase: the letter's own panel (a plot, the 3D view) must not see it.
  const openOnLetter = (e) => {
    if (!document.body.classList.contains('journal')) return;
    const letter = e.target.closest?.('.panel-letter');
    if (!letter) return;
    e.preventDefault();
    e.stopPropagation();
    menu.open(letter, actions());
  };
  document.addEventListener('dblclick', openOnLetter, true);
  document.addEventListener('contextmenu', openOnLetter, true);
}

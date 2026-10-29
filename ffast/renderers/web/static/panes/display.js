/**
 * Display + Unit Cell pane (ADR 0045 issue 05). Mirrors the DISPLAY schema in
 * modules/loupe/loupeViewSettings.py. Hide-atoms/highlight token specs are
 * resolved server-side (ffast.atom_filter / SET_SELECTION); the client just
 * tokenizes text into ints or element-symbol strings. The pane shows one
 * view's `display` settings (`show`) and writes each change into them.
 */

import { createPane, numberRow, textRow, checkboxRow } from '../sidebar.js';
import { defaultSettings } from '../view_settings.js';

/** Tokenize "0 1 2", "C", "-H" into ints/strings — integers (incl. "-3") stay
 * ints; everything else (incl. "-H") stays a string. Server resolves both. */
function parseFilterTokens(text) {
  return String(text || '').replace(/,/g, ' ').split(/\s+/).filter(Boolean).map((tok) => {
    const n = Number(tok);
    return Number.isInteger(n) ? n : tok;
  });
}

/** Tokenize a plain index list ("0 1 2") into ints only, ignoring anything else. */
function parseIndexList(text) {
  const out = [];
  for (const tok of String(text || '').replace(/,/g, ' ').split(/\s+/).filter(Boolean)) {
    const n = parseInt(tok, 10);
    if (Number.isFinite(n)) out.push(n);
  }
  return out;
}

/**
 * @param {HTMLElement} sidebarEl
 * @param {{
 *   onAtomSize: (scale: number) => void,
 *   onHideAtoms: (tokens: (number|string)[]) => void,
 *   onHighlight: (indices: number[]) => void,
 *   onUnitCell: (visible: boolean) => void,
 * }} callbacks
 */
export function createDisplayPane(sidebarEl, callbacks) {
  const { el, body } = createPane('Display');
  sidebarEl.appendChild(el);

  let state = defaultSettings().display;   // the shown view's settings

  const atomSizeRow = numberRow(body, 'Atom size', 1.0, { min: 0.1, max: 10, step: 0.1 }, (v) => {
    state.atomSize = v || 1;
    callbacks.onAtomSize(v);
  });
  const atomHideRow = textRow(body, 'Hide atoms', '', (text) => {
    state.hide = text;
    callbacks.onHideAtoms(parseFilterTokens(text));
  });
  const highlightAtomRow = textRow(body, 'Highlight atoms', '', (text) => {
    state.highlight = text;
    callbacks.onHighlight(parseIndexList(text));
  });
  const showUnitRow = checkboxRow(body, 'Show unit cell', true, (v) => {
    state.unitCell = v;
    callbacks.onUnitCell(v);
  });

  return {
    /** The Atom size setting, as the server scales sizes by it. */
    atomScale() {
      return state.atomSize || 1;
    },

    /** Show a view's `display` settings; later changes are written into them.
     * Nothing is sent: the view's own server state already holds its look. */
    show(settings) {
      state = settings;
      atomSizeRow.value = String(settings.atomSize);
      atomHideRow.value = settings.hide;
      highlightAtomRow.value = settings.highlight;
      showUnitRow.checked = settings.unitCell;
    },
  };
}

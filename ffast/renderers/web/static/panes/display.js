/**
 * Display + Unit Cell pane (ADR 0045 issue 05). Mirrors the DISPLAY schema in
 * modules/loupe/loupeViewSettings.py. Hide-atoms/highlight token specs are
 * resolved server-side (ffast.atom_filter / SET_SELECTION); the client just
 * tokenizes text into ints or element-symbol strings.
 */

import { createPane, numberRow, textRow, checkboxRow } from '../sidebar.js';

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
 *   onPickRadius: (px: number) => void,
 *   onUnitCell: (visible: boolean) => void,
 * }} callbacks
 */
export function createDisplayPane(sidebarEl, callbacks) {
  const { el, body } = createPane('Display');
  sidebarEl.appendChild(el);

  numberRow(body, 'Atom size', 1.0, { min: 0.1, max: 10, step: 0.1 }, callbacks.onAtomSize);
  textRow(body, 'Hide atoms', '', (text) => callbacks.onHideAtoms(parseFilterTokens(text)));
  textRow(body, 'Highlight atoms', '', (text) => callbacks.onHighlight(parseIndexList(text)));
  numberRow(body, 'Pick radius (px)', 12, { min: 4, max: 40, step: 1 }, callbacks.onPickRadius);
  checkboxRow(body, 'Show unit cell', true, callbacks.onUnitCell);

  return {};
}

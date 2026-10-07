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
 *   onUnitCell: (visible: boolean) => void,
 * }} callbacks
 */
export function createDisplayPane(sidebarEl, callbacks) {
  const { el, body } = createPane('Display');
  sidebarEl.appendChild(el);

  const atomSizeRow= numberRow(body, 'Atom size', 1.0, { min: 0.1, max: 10, step: 0.1 }, callbacks.onAtomSize);
  const atomHideRow=textRow(body, 'Hide atoms', '', (text) => callbacks.onHideAtoms(parseFilterTokens(text)));
  const highlightAtomRow = textRow(body, 'Highlight atoms', '', (text) => callbacks.onHighlight(parseIndexList(text)));
  const showUnitRow = checkboxRow(body, 'Show unit cell', true, callbacks.onUnitCell);

  return {
    /** The Atom size setting, as the server scales sizes by it. */
    atomScale() {
      return parseFloat(atomSizeRow.value) || 1;
    },
    atomSizeStatus: new Map(), // fp -> number
    atomHidStatus: new Map(),
    highlightAtomStatus: new Map(),
    showUnitStatus: new Map(),

    saveState(fp) {
      this.atomSizeStatus.set(fp, atomSizeRow.value);
      this.atomHidStatus.set(fp, atomHideRow.value);
      this.highlightAtomStatus.set(fp, highlightAtomRow.value);
      this.showUnitStatus.set(fp, showUnitRow.checked);
    },

    /** Store an Atom size for `key` without showing it (ADR 0056 start). */
    presetState(key, { atomSize = 1 } = {}) {
      this.atomSizeStatus.set(key, String(atomSize));
    },

    /** The Atom size stored for `key`. */
    lookOf(key) {
      return { atomSize: parseFloat(this.atomSizeStatus.get(key)) || 1 };
    },

    loadState(fp) {
      atomSizeRow.value = this.atomSizeStatus.get(fp) || '1.0';
      atomHideRow.value = this.atomHidStatus.get(fp) || '';
      highlightAtomRow.value = this.highlightAtomStatus.get(fp) || '';
      showUnitRow.checked = this.showUnitStatus.get(fp) ?? true;
    },

  };
}

/**
 * Alignment pane (ADR 0045 issue 13). Mirrors the ALIGNMENT schema split
 * across modules/loupe/loupeAtomAlign.py + loupeViewSettings.py, unified here.
 *
 * Two mutually-exclusive modes onto the server's alignment features:
 *   • Kabsch  — TOGGLE_FEATURE("kabsch_align") + ffast.kabsch_alignment.heavy_only
 *   • 3-atom  — TOGGLE_FEATURE("atom_align")  + ffast.atom_align.atom_indices (3)
 *
 * The three reference atoms for 3-atom mode are pickable (Align pick tool);
 * the pane exposes setPickedIndices so the tool can fill them.
 */

import { createPane, checkboxRow, textRow, rowElement } from '../sidebar.js';

/** Parse a plain "0 1 2" list into integers only. */
function parseInts(text) {
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
 *   onKabsch: (enabled: boolean, heavyOnly: boolean) => void,
 *   onAtomAlign: (enabled: boolean, indices: number[]) => void,
 * }} callbacks
 */
export function createAlignPane(sidebarEl, callbacks) {
  const { el, body } = createPane('Alignment');
  sidebarEl.appendChild(el);

  let kabsch = false, heavyOnly = true, atomAlign = false, indices = [];

  const kabschInput = checkboxRow(body, 'Kabsch align', kabsch, (v) => {
    kabsch = v;
    if (kabsch && atomAlign) { atomAlign = false; atomAlignInput.checked = false; callbacks.onAtomAlign(false, indices); }
    _syncVisibility();
    callbacks.onKabsch(kabsch, heavyOnly);
  });
  const heavyInput = checkboxRow(body, 'Heavy atoms only', heavyOnly, (v) => {
    heavyOnly = v;
    if (kabsch) callbacks.onKabsch(kabsch, heavyOnly);
  });

  const atomAlignInput = checkboxRow(body, '3-atom frame align', atomAlign, (v) => {
    atomAlign = v;
    if (atomAlign && kabsch) { kabsch = false; kabschInput.checked = false; callbacks.onKabsch(false, heavyOnly); }
    _syncVisibility();
    callbacks.onAtomAlign(atomAlign, indices);
  });
  const idxInput = textRow(body, 'Reference atoms', '', (text) => {
    indices = parseInts(text).slice(0, 3);
    idxInput.value = indices.join(' ');
    _syncHint();
    if (atomAlign) callbacks.onAtomAlign(atomAlign, indices);
  });
  idxInput.placeholder = 'three atom indices';

  const hint = document.createElement('div');
  hint.className = 'ctl-hint';
  hint.style.display = 'none';
  body.appendChild(hint);

  function _syncHint() {
    if (!atomAlign) { hint.style.display = 'none'; return; }
    const ok = indices.length === 3;
    hint.textContent = ok ? '3 reference atoms set' : `pick ${3 - indices.length} more atom${3 - indices.length === 1 ? '' : 's'}`;
    hint.classList.toggle('ok', ok);
    hint.style.display = '';
  }

  function _syncVisibility() {
    rowElement(heavyInput).style.display = kabsch ? '' : 'none';
    rowElement(idxInput).style.display = atomAlign ? '' : 'none';
    _syncHint();
  }
  _syncVisibility();

  return {
    /** Fill the reference-atom box from the Align pick tool (scientific ids). */
    setPickedIndices(ids) {
      indices = (ids || []).slice(0, 3);
      idxInput.value = indices.join(' ');
      _syncHint();
    },
    /** Whether 3-atom mode is armed (so the app knows to feed picks here). */
    isAtomAlign() { return atomAlign; },
    /** Turn on 3-atom mode when the Align pick tool is armed (Qt parity). */
    enableAtomAlignMode() {
      if (atomAlign) return;
      atomAlign = true;
      atomAlignInput.checked = true;
      if (kabsch) { kabsch = false; kabschInput.checked = false; callbacks.onKabsch(false, heavyOnly); }
      _syncVisibility();
    },
    /** Commit the current 3-atom reference set (called when the tool has 3). */
    applyAtomAlign() {
      if (atomAlign) callbacks.onAtomAlign(atomAlign, indices);
    },
  };
}

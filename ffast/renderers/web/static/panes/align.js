/**
 * Alignment pane (ADR 0045 issue 13). Mirrors the ALIGNMENT schema split
 * across modules/loupe/loupeAtomAlign.py + loupeViewSettings.py, unified here.
 *
 * Two mutually-exclusive modes onto the server's alignment features:
 *   • Kabsch  — TOGGLE_FEATURE("kabsch_align") + ffast.kabsch_alignment.heavy_only
 *   • 3-atom  — TOGGLE_FEATURE("atom_align")  + ffast.atom_align.atom_indices (3)
 *
 * The three reference atoms for 3-atom mode are pickable (Align pick tool);
 * the pane exposes setPickedIndices so the tool can fill them. The pane shows
 * one view's `align` settings (`show`) and writes each change into them.
 */

import { createPane, checkboxRow, textRow, rowElement, setRowNeeds } from '../sidebar.js';
import { defaultSettings } from '../view_settings.js';

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

  let state = defaultSettings().align;   // the shown view's settings
  let variable = false;                  // the dataset's atoms change by frame

  const kabschInput = checkboxRow(body, 'Kabsch align', state.kabsch, (v) => {
    state.kabsch = v;
    if (state.kabsch && state.atomAlign) {
      state.atomAlign = false; atomAlignInput.checked = false; callbacks.onAtomAlign(false, state.indices);
    }
    _syncVisibility();
    callbacks.onKabsch(state.kabsch, state.heavyOnly);
  });
  const heavyInput = checkboxRow(body, 'Heavy atoms only', state.heavyOnly, (v) => {
    state.heavyOnly = v;
    if (state.kabsch) callbacks.onKabsch(state.kabsch, state.heavyOnly);
  });

  const atomAlignInput = checkboxRow(body, '3-atom frame align', state.atomAlign, (v) => {
    state.atomAlign = v;
    if (state.atomAlign && state.kabsch) {
      state.kabsch = false; kabschInput.checked = false; callbacks.onKabsch(false, state.heavyOnly);
    }
    _syncVisibility();
    callbacks.onAtomAlign(state.atomAlign, state.indices);
  });
  const idxInput = textRow(body, 'Reference atoms', '', (text) => {
    state.indices = parseInts(text).slice(0, 3);
    state.idxInput = idxInput.value = state.indices.join(' ');
    _syncHint();
    if (state.atomAlign) callbacks.onAtomAlign(state.atomAlign, state.indices);
  });
  idxInput.placeholder = 'three atom indices';

  const hint = document.createElement('div');
  hint.className = 'ctl-hint';
  hint.style.display = 'none';
  body.appendChild(hint);

  function _syncHint() {
    if (variable) {
      hint.textContent = 'Off: this dataset\'s atoms change between frames';
      hint.classList.remove('ok');
      hint.style.display = '';
      return;
    }
    if (!state.atomAlign) { hint.style.display = 'none'; return; }
    const n = state.indices.length;
    const ok = n === 3;
    hint.textContent = ok ? '3 reference atoms set' : `pick ${3 - n} more atom${3 - n === 1 ? '' : 's'}`;
    hint.classList.toggle('ok', ok);
    hint.style.display = '';
  }

  function _syncVisibility() {
    const off = variable ? 'A dataset with the same atoms in every frame' : '';
    setRowNeeds(rowElement(kabschInput), off);
    setRowNeeds(rowElement(atomAlignInput), off);
    setRowNeeds(rowElement(heavyInput), off || (state.kabsch ? '' : 'Turn on "Kabsch align" first'));
    setRowNeeds(rowElement(idxInput), off || (state.atomAlign ? '' : 'Turn on "3-atom frame align" first'));
    _syncHint();
  }
  _syncVisibility();

  return {
    /** Fill the reference-atom box from the Align pick tool (scientific ids). */
    setPickedIndices(ids) {
      state.indices = (ids || []).slice(0, 3);
      state.idxInput = idxInput.value = state.indices.join(' ');
      _syncHint();
    },
    /** Whether 3-atom mode is armed (so the app knows to feed picks here). */
    isAtomAlign() { return state.atomAlign; },
    /** Turn on 3-atom mode when the Align pick tool is armed (Qt parity). */
    enableAtomAlignMode() {
      if (state.atomAlign || variable) return;
      state.atomAlign = true;
      atomAlignInput.checked = true;
      if (state.kabsch) { state.kabsch = false; kabschInput.checked = false; callbacks.onKabsch(false, state.heavyOnly); }
      _syncVisibility();
    },
    /** Commit the current 3-atom reference set (called when the tool has 3). */
    applyAtomAlign() {
      if (state.atomAlign) callbacks.onAtomAlign(state.atomAlign, state.indices);
    },

    /** Alignment is off where the dataset's atoms change between frames
     * (its REMOTE_DATASET_META says `variable`): one frame's atoms cannot be
     * matched to another's. A view that had it on is turned off. */
    setDataset(meta) {
      variable = !!meta?.variable;
      if (variable && state.kabsch) {
        state.kabsch = false; kabschInput.checked = false; callbacks.onKabsch(false, state.heavyOnly);
      }
      if (variable && state.atomAlign) {
        state.atomAlign = false; atomAlignInput.checked = false; callbacks.onAtomAlign(false, state.indices);
      }
      _syncVisibility();
    },

    /** Show a view's `align` settings; later changes are written into them.
     * Picks are not kept across a view change: each new view clears them. */
    show(settings) {
      state = settings;
      if (state.indices.length !== 3) { state.indices = []; state.idxInput = ''; }
      kabschInput.checked = state.kabsch;
      heavyInput.checked = state.heavyOnly;
      atomAlignInput.checked = state.atomAlign;
      idxInput.value = state.idxInput;
      _syncVisibility();
    },
  };
}

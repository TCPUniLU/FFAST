/**
 * Extract Subset pane (ADR 0045 issue 12). Mirrors modules/loupe/loupeAtomFilter.py.
 *
 * The indices box accepts the same mixed spec as Qt: integer atom indices and
 * element-symbol tokens ("C" include, "-H" exclude). Tokens are resolved
 * server-side (CREATE_SUBSET), so the box ships the raw tokens. Elements alone
 * make an element subset, which keeps those elements' atoms in every frame;
 * where the structures differ, atom numbers are refused (ADR 0067). The
 * server's answer shows under the button. The Extract pick tool fills the box
 * by picking atoms. The pane
 * shows only where canExtractFrom allows it (parity with Qt's
 * AtomFilterPaneHiding). The pane shows one view's `extract` settings (`show`)
 * and writes into them.
 */

import { createPane, textRow, buttonRow } from '../sidebar.js';
import { defaultSettings } from '../view_settings.js';

/** Whether Extract may cut an atom subset from a dataset (its REMOTE_DATASET_META):
 * a whole dataset or a frozen subset, whose frames stay put. Not a live subset,
 * which changes with the zoom, nor an atom subset, which chose its atoms. Same
 * rule as the desktop's DatasetLoader.canExtractAtoms. */
export function canExtractFrom(meta) {
  return !meta?.is_sub || !!meta.frozen;
}

/** Split "0 1 C -H" into ints (incl. "-3") and element-symbol strings. */
export function parseExtractTokens(text) {
  return String(text || '').replace(/,/g, ' ').split(/\s+/).filter(Boolean).map((tok) => {
    const n = Number(tok);
    return Number.isInteger(n) ? n : tok;
  });
}

const ANY_PLACEHOLDER = 'e.g. 0 1 2  or  C  -H';
// Atom numbers name a different atom in each structure of such a dataset.
const ELEMENTS_PLACEHOLDER = 'Elements, e.g. C  or  -H';

/**
 * @param {HTMLElement} sidebarEl
 * @param {{ onExtract: (tokens: (number|string)[]) => void }} callbacks
 */
export function createExtractPane(sidebarEl, callbacks) {
  const { el, body } = createPane('Extract Subset');
  sidebarEl.appendChild(el);

  let state = defaultSettings().extract;   // the shown view's settings
  // Says what the pane is for without opening its "?" (to_fix_v2).
  const intro = document.createElement('div');
  intro.className = 'pane-intro';
  intro.textContent = 'A new dataset with only these atoms, in every frame.';
  body.appendChild(intro);
  const indices = textRow(body, 'Indices', '', (text) => { state.indices = text; });
  indices.placeholder = ANY_PLACEHOLDER;

  const hint = document.createElement('div');
  hint.className = 'ctl-hint ok';
  hint.style.display = 'none';
  body.appendChild(hint);

  const extractBtn = buttonRow(body, '', 'Extract as Subset Dataset', () => {
    const tokens = parseExtractTokens(indices.value);
    if (tokens.length === 0) {
      hint.textContent = 'No indices — pick atoms or type indices first';
      hint.classList.remove('ok');
      hint.style.display = '';
      return;
    }
    hint.textContent = 'Extracting…';
    hint.classList.add('ok');
    hint.style.display = '';
    callbacks.onExtract(tokens);
  });
  extractBtn.title = 'Make a new dataset that keeps only these atoms; it appears in the dataset list';

  return {
    /** Reflect the current picked atom-id set into the box (pick tool fill). */
    setPickedIndices(ids) {
      indices.value = state.indices = (ids || []).join(' ');
    },
    /** Current box tokens (integers + element symbols). */
    getTokens() {
      return parseExtractTokens(indices.value);
    },
    /** Auto-hide for datasets that are already subsets. */
    setVisible(visible) {
      el.style.display = visible ? '' : 'none';
    },

    /** Show the pane where Extract may cut from this dataset (its
     * REMOTE_DATASET_META), and say what it takes there. */
    setDataset(meta) {
      el.style.display = canExtractFrom(meta) ? '' : 'none';
      indices.placeholder = meta?.variable ? ELEMENTS_PLACEHOLDER : ANY_PLACEHOLDER;
    },

    /** The server's answer to Extract (SUBSET_EXTRACTED): what it made, or why not. */
    showResult({ ok, text, error }) {
      hint.textContent = ok ? `Extracted ${text}` : error;
      hint.classList.toggle('ok', !!ok);
      hint.style.display = '';
    },

    /** Show a view's `extract` settings; later changes are written into them. */
    show(settings) {
      state = settings;
      indices.value = settings.indices;
    },

    clearInput() {
      indices.value = state.indices = '';
    },
  };
}

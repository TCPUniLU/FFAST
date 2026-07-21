/**
 * Extract Subset pane (ADR 0045 issue 12). Mirrors modules/loupe/loupeAtomFilter.py.
 *
 * The indices box accepts the same mixed spec as Qt: integer atom indices and
 * element-symbol tokens ("C" include, "-H" exclude). Tokens are resolved
 * server-side (CREATE_SUBSET → _resolve_filter_indices), so the box ships the
 * raw tokens. The Extract pick tool fills the box by picking atoms. The pane
 * auto-hides for datasets that are already subsets (parity with Qt's
 * AtomFilterPaneHiding).
 */

import { createPane, textRow, buttonRow } from '../sidebar.js';

/** Split "0 1 C -H" into ints (incl. "-3") and element-symbol strings. */
export function parseExtractTokens(text) {
  return String(text || '').replace(/,/g, ' ').split(/\s+/).filter(Boolean).map((tok) => {
    const n = Number(tok);
    return Number.isInteger(n) ? n : tok;
  });
}

/**
 * @param {HTMLElement} sidebarEl
 * @param {{ onExtract: (tokens: (number|string)[]) => void }} callbacks
 */
export function createExtractPane(sidebarEl, callbacks) {
  const { el, body } = createPane('Extract Subset');
  sidebarEl.appendChild(el);

  const indices = textRow(body, 'Indices', '', () => {});
  indices.placeholder = 'e.g. 0 1 2  or  C  -H';

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
  extractBtn.title = 'Create a new atom-filtered dataset from these indices';

  return {
    /** Reflect the current picked atom-id set into the box (pick tool fill). */
    setPickedIndices(ids) {
      indices.value = (ids || []).join(' ');
    },
    /** Current box tokens (integers + element symbols). */
    getTokens() {
      return parseExtractTokens(indices.value);
    },
    /** Auto-hide for datasets that are already subsets. */
    setVisible(visible) {
      el.style.display = visible ? '' : 'none';
    },
  };
}

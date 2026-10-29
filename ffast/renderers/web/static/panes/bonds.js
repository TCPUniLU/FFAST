/**
 * Bonds pane (ADR 0045 issue 06). Mirrors modules/loupe/loupeBonds.py.
 * Width/colour are client-render-only (no wire parameter, matches Qt); mode
 * and fixed-indices drive the server's ffast.bonds stage. The Bonds pick tool
 * (#10) edits the fixed set via toggleBondPair — two picks toggle a canonical
 * pair, seeding from the current dynamic bonds when the set is empty (Qt's
 * BondSelect.selectCallback). The pane shows one view's `bonds` settings
 * (`show`) and writes each change into them.
 */

import { createPane, sliderRow, colorRow, selectRow, row, buttonRow, rowElement, setRowNeeds, setSliderValue } from '../sidebar.js';
import { defaultSettings } from '../view_settings.js';

/**
 * Parse "0-1, 2-5" / "0 1\n2 5" into [[0,1],[2,5]], skipping malformed pairs.
 * @returns {{pairs: number[][], rejected: number}} rejected = non-blank lines
 *   that weren't exactly two integers (issue 06: "invalid input is rejected
 *   with feedback").
 */
function parseBondPairs(text) {
  const pairs = [];
  let rejected = 0;
  for (const rawLine of String(text || '').split(/[\n,]/)) {
    const line = rawLine.trim();
    if (!line) continue;
    const nums = line.split(/[\s-]+/).filter(Boolean).map(Number);
    if (nums.length === 2 && nums.every(Number.isFinite)) pairs.push([nums[0], nums[1]]);
    else rejected++;
  }
  return { pairs, rejected };
}

function formatBondPairs(pairs) {
  return (pairs || []).map(([a, b]) => `${a}-${b}`).join('\n');
}

/**
 * @param {HTMLElement} sidebarEl
 * @param {{
 *   onStyle: (width: number, color: string, colorChosen: boolean) => void,
 *   onApply: (bondType: string, fixedIndices: number[][]) => void,
 *   getDynamicBondPairs: () => number[][],
 * }} callbacks
 */
export function createBondsPane(sidebarEl, callbacks) {
  const { el, body } = createPane('Bonds');
  sidebarEl.appendChild(el);

  const state = { current: defaultSettings().bonds };   // the shown view's settings
  const DEFAULT_COLOR = state.current.color;
  // A colour other than the default replaces the 3D view's two-tone bonds.
  const style = () => callbacks.onStyle(state.current.width, state.current.color, state.current.color !== DEFAULT_COLOR);

  const widthInput = sliderRow(body, 'Bond width', state.current.width, { min: 10, max: 100 }, (v) => {
    state.current.width = v; style();
  });
  const colorInput = colorRow(body, 'Bond colour', state.current.color, (v) => {
    state.current.color = v; style();
  });

  const typeSelect = selectRow(body, 'Bonds Type', ['Fixed', 'Dynamic'], state.current.bondType, (v) => {
    state.current.bondType = v;
    _syncVisibility();
    _apply();
  });
  const _apply = () => callbacks.onApply(state.current.bondType, state.current.fixedIndices);

  const textarea = document.createElement('textarea');
  textarea.className = 'ctl-textarea';
  textarea.placeholder = 'One "a-b" pair per line';
  textarea.addEventListener('change', () => {
    const { pairs, rejected } = parseBondPairs(textarea.value);
    state.current.fixedIndices = pairs;
    textarea.value = formatBondPairs(pairs);
    _showHint(rejected);
    _apply();
  });
  const textareaRowEl = row(body, 'Bond indices', textarea);

  const hint = document.createElement('div');
  hint.className = 'ctl-hint';
  hint.style.display = 'none';
  body.appendChild(hint);

  function _showHint(rejected) {
    const n = state.current.fixedIndices.length;
    if (rejected > 0) {
      hint.textContent = `${rejected} invalid pair${rejected === 1 ? '' : 's'} ignored (expected "a-b")`;
      hint.classList.remove('ok');
    } else {
      hint.textContent = `${n} bond pair${n === 1 ? '' : 's'}`;
      hint.classList.add('ok');
    }
    hint.style.display = '';
  }

  const fillBtn = buttonRow(body, '', 'Fill from dynamic', () => {
    state.current.fixedIndices = callbacks.getDynamicBondPairs();
    textarea.value = formatBondPairs(state.current.fixedIndices);
    _showHint(0);
    _apply();
  });
  fillBtn.title = 'Fill the bond index list from the current pairwise-distance bonds';

  function _syncVisibility() {
    const show = state.current.bondType === 'Fixed';
    const needsFixed = show ? '' : 'Set "Bonds Type" to Fixed first';
    setRowNeeds(textareaRowEl, needsFixed);
    hint.style.display = show && hint.textContent ? '' : 'none';
    setRowNeeds(rowElement(fillBtn), needsFixed);
  }
  _syncVisibility();

  return {
    /** Show a view's `bonds` settings and draw them; later changes are written
     * into them. Its bond type is the view's own, already on the server, so
     * nothing is sent. */
    show(settings) {
      state.current = settings;
      setSliderValue(widthInput, settings.width);
      colorInput.value = settings.color;
      typeSelect.value = settings.bondType;
      textarea.value = formatBondPairs(settings.fixedIndices);
      _syncVisibility();
      style();
    },

    /**
     * Toggle bond (a, b) in the fixed set from two picked atoms (Qt's
     * BondSelect): seed from the current dynamic bonds when the set is empty so
     * editing doesn't collapse the topology, canonicalise as a sorted pair,
     * toggle membership, switch to Fixed mode, and re-apply.
     */
    toggleBondPair(a, b) {
      if (a === b) return;
      const s = state.current;
      if (s.fixedIndices.length === 0) s.fixedIndices = callbacks.getDynamicBondPairs();
      const key = ([x, y]) => (x < y ? `${x}-${y}` : `${y}-${x}`);
      const target = key([a, b]);
      const idx = s.fixedIndices.findIndex((p) => key(p) === target);
      if (idx >= 0) s.fixedIndices.splice(idx, 1);
      else s.fixedIndices.push(a < b ? [a, b] : [b, a]);
      s.bondType = 'Fixed';
      typeSelect.value = 'Fixed';
      _syncVisibility();
      textarea.value = formatBondPairs(s.fixedIndices);
      _showHint(0);
      _apply();
    },
  };
}

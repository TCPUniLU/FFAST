/**
 * Bonds pane (ADR 0045 issue 06). Mirrors modules/loupe/loupeBonds.py.
 * Width/colour are client-render-only (no wire parameter, matches Qt); mode
 * and fixed-indices drive the server's ffast.bonds stage. The Bonds pick tool
 * (#10) edits the fixed set via toggleBondPair — two picks toggle a canonical
 * pair, seeding from the current dynamic bonds when the set is empty (Qt's
 * BondSelect.selectCallback).
 */

import { createPane, sliderRow, colorRow, selectRow, row, buttonRow, rowElement } from '../sidebar.js';

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
 *   onStyle: (width: number, color: string) => void,
 *   onApply: (bondType: string, fixedIndices: number[][]) => void,
 *   getDynamicBondPairs: () => number[][],
 * }} callbacks
 */
export function createBondsPane(sidebarEl, callbacks) {
  const { el, body } = createPane('Bonds');
  sidebarEl.appendChild(el);

  let width = 100, color = '#404040', bondType = 'Dynamic', fixedIndices = [];

  sliderRow(body, 'Bond width', width, { min: 10, max: 100 }, (v) => {
    width = v; callbacks.onStyle(width, color);
  });
  colorRow(body, 'Bond colour', color, (v) => {
    color = v; callbacks.onStyle(width, color);
  });

  const typeSelect = selectRow(body, 'Bonds Type', ['Fixed', 'Dynamic'], bondType, (v) => {
    bondType = v;
    _syncVisibility();
    callbacks.onApply(bondType, fixedIndices);
  });

  const textarea = document.createElement('textarea');
  textarea.className = 'ctl-textarea';
  textarea.placeholder = 'One "a-b" pair per line';
  textarea.addEventListener('change', () => {
    const { pairs, rejected } = parseBondPairs(textarea.value);
    fixedIndices = pairs;
    textarea.value = formatBondPairs(fixedIndices);
    _showHint(rejected);
    callbacks.onApply(bondType, fixedIndices);
  });
  const textareaRowEl = row(body, 'Bond indices', textarea);

  const hint = document.createElement('div');
  hint.className = 'ctl-hint';
  hint.style.display = 'none';
  body.appendChild(hint);

  function _showHint(rejected) {
    if (rejected > 0) {
      hint.textContent = `${rejected} invalid pair${rejected === 1 ? '' : 's'} ignored (expected "a-b")`;
      hint.classList.remove('ok');
    } else {
      hint.textContent = `${fixedIndices.length} bond pair${fixedIndices.length === 1 ? '' : 's'}`;
      hint.classList.add('ok');
    }
    hint.style.display = '';
  }

  const fillBtn = buttonRow(body, '', 'Fill from dynamic', () => {
    fixedIndices = callbacks.getDynamicBondPairs();
    textarea.value = formatBondPairs(fixedIndices);
    _showHint(0);
    callbacks.onApply(bondType, fixedIndices);
  });
  fillBtn.title = 'Fill the bond index list from the current pairwise-distance bonds';

  function _syncVisibility() {
    const show = bondType === 'Fixed';
    textareaRowEl.style.display = show ? '' : 'none';
    hint.style.display = show && hint.textContent ? '' : 'none';
    rowElement(fillBtn).style.display = show ? '' : 'none';
  }
  _syncVisibility();

  return {
    /**
     * Toggle bond (a, b) in the fixed set from two picked atoms (Qt's
     * BondSelect): seed from the current dynamic bonds when the set is empty so
     * editing doesn't collapse the topology, canonicalise as a sorted pair,
     * toggle membership, switch to Fixed mode, and re-apply.
     */
    toggleBondPair(a, b) {
      if (a === b) return;
      if (fixedIndices.length === 0) fixedIndices = callbacks.getDynamicBondPairs();
      const key = ([x, y]) => (x < y ? `${x}-${y}` : `${y}-${x}`);
      const target = key([a, b]);
      const idx = fixedIndices.findIndex((p) => key(p) === target);
      if (idx >= 0) fixedIndices.splice(idx, 1);
      else fixedIndices.push(a < b ? [a, b] : [b, a]);
      bondType = 'Fixed';
      typeSelect.value = 'Fixed';
      _syncVisibility();
      textarea.value = formatBondPairs(fixedIndices);
      _showHint(0);
      callbacks.onApply(bondType, fixedIndices);
    },
  };
}

/**
 * Bonds pane (ADR 0045 issue 06). Mirrors modules/loupe/loupeBonds.py.
 * Width/colour are client-render-only (no wire parameter, matches Qt); mode
 * and fixed-indices drive the server's ffast.bonds stage. Pick-to-add/remove
 * is deferred to the picking infrastructure (#10) — this pane covers the
 * non-picking controls only.
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

  return {};
}

/**
 * Bonds pane (ADR 0045 issue 06). Mirrors modules/loupe/loupeBonds.py.
 * Width/colour are client-render-only (no wire parameter, matches Qt); mode
 * and fixed-indices drive the server's ffast.bonds stage. The Bonds pick tool
 * (#10) edits the fixed set via toggleBondPair — two picks toggle a canonical
 * pair, seeding from the current dynamic bonds when the set is empty (Qt's
 * BondSelect.selectCallback).
 */

import { createPane, sliderRow, colorRow, selectRow, row, buttonRow, rowElement, setRowNeeds, setSliderValue } from '../sidebar.js';

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

  const DEFAULT_COLOR = '#404040';
  let width = 100, color = DEFAULT_COLOR, bondType = 'Dynamic', fixedIndices = [];
  // A colour other than the default replaces the 3D view's two-tone bonds.
  const style = () => callbacks.onStyle(width, color, color !== DEFAULT_COLOR);

  const widthInput = sliderRow(body, 'Bond width', width, { min: 10, max: 100 }, (v) => {
    width = v; style();
  });
  const colorInput = colorRow(body, 'Bond colour', color, (v) => {
    color = v; style();
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
    const needsFixed = show ? '' : 'Set "Bonds Type" to Fixed first';
    setRowNeeds(textareaRowEl, needsFixed);
    hint.style.display = show && hint.textContent ? '' : 'none';
    setRowNeeds(rowElement(fillBtn), needsFixed);
  }
  _syncVisibility();

  /** Bond look per dataset in the main view, and per independent 3D panel
   * (ADR 0056). */
  const saved = new Map();   // key -> {width, color, bondType, fixedIndices}
  return {
    saveState(key) {
      saved.set(key, { width, color, bondType, fixedIndices: [...fixedIndices] });
    },

    /** Show `key`'s bond look and draw it; its bond type is the view's own,
     * already on the server, so nothing is sent. */
    loadState(key) {
      const s = saved.get(key) || { width: 100, color: DEFAULT_COLOR, bondType: 'Dynamic', fixedIndices: [] };
      ({ width, color, bondType } = s);
      fixedIndices = [...s.fixedIndices];
      setSliderValue(widthInput, width);
      colorInput.value = color;
      typeSelect.value = bondType;
      textarea.value = formatBondPairs(fixedIndices);
      _syncVisibility();
      style();
    },

    /** Store a bond look for `key` without showing it (ADR 0056 start). */
    presetState(key, { width: w = 100, colour = DEFAULT_COLOR } = {}) {
      saved.set(key, { width: w, color: colour, bondType: 'Dynamic', fixedIndices: [] });
    },

    /** The bond width and colour stored for `key`. */
    lookOf(key) {
      const s = saved.get(key);
      return { width: s?.width ?? 100, colour: s?.color ?? DEFAULT_COLOR };
    },

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

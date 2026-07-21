/**
 * Force Vectors pane (ADR 0045 issue 07). Mirrors modules/loupe/loupeForceVectors.py.
 * Delivers show/source/length/normalise plus filter-to-selection: the "Filter
 * to selection" toggle gates the server's ffast.force_arrows.filter_enabled,
 * and the Force pick tool fills the atom set (setPickedIndices).
 */

import { createPane, checkboxRow, selectRow, sliderRow, rowElement } from '../sidebar.js';

/**
 * @param {HTMLElement} sidebarEl
 * @param {{
 *   onApply: (state: {show: boolean, modelKey: string|null, length: number, normalised: boolean, filterEnabled: boolean, atomIndices: number[]}) => void,
 *   getModels: () => Map<string, {name?: string}>,
 * }} callbacks
 */
export function createForcesPane(sidebarEl, callbacks) {
  const { el, body } = createPane('Force Vectors');
  sidebarEl.appendChild(el);

  const state = { show: false, modelKey: null, length: 10, normalised: true, filterEnabled: false, atomIndices: [] };
  const apply = () => callbacks.onApply({ ...state, atomIndices: [...state.atomIndices] });
  let keyByLabel = new Map();   // combo label -> model fingerprint

  const showInput = checkboxRow(body, 'Show force vectors', state.show, (v) => { state.show = v; _syncVisibility(); apply(); });

  const sourceSelect = selectRow(body, 'Source', ['Ground Truth'], 'Ground Truth', (label) => {
    state.modelKey = label === 'Ground Truth' ? null : keyByLabel.get(label) ?? null;
    apply();
  });

  const normalisedInput = checkboxRow(body, 'Normalised', state.normalised, (v) => { state.normalised = v; apply(); });
  const lengthInput = sliderRow(body, 'Length', state.length, { min: 1, max: 200 }, (v) => { state.length = v; apply(); });
  const filterInput = checkboxRow(body, 'Filter to selection', state.filterEnabled, (v) => { state.filterEnabled = v; apply(); });

  function _syncVisibility() {
    const show = state.show;
    for (const control of [sourceSelect, normalisedInput, lengthInput, filterInput]) rowElement(control).style.display = show ? '' : 'none';
  }
  _syncVisibility();

  return {
    /** Fill the filter atom set from the Force pick tool; enable filtering. */
    setPickedIndices(ids) {
      state.atomIndices = [...(ids || [])];
      if (!state.filterEnabled) { state.filterEnabled = true; filterInput.checked = true; }
      apply();
    },
    /** Set show/source without firing onApply (per-dataset restore). */
    setState(show, modelKey) {
      state.show = show;
      state.modelKey = modelKey;
      showInput.checked = show;
      let label = 'Ground Truth';
      for (const [lbl, fp] of keyByLabel) if (fp === modelKey) label = lbl;
      if ([...sourceSelect.options].some((o) => o.value === label)) sourceSelect.value = label;
      _syncVisibility();
    },
    /** Refresh the source combo from the currently loaded models. */
    refreshModels() {
      const models = callbacks.getModels();
      const prevLabel = sourceSelect.value;
      sourceSelect.innerHTML = '';
      keyByLabel = new Map();
      const gt = document.createElement('option');
      gt.value = gt.textContent = 'Ground Truth';
      sourceSelect.appendChild(gt);
      for (const [fp, meta] of models) {
        const label = meta.name || fp.slice(0, 8);
        keyByLabel.set(label, fp);
        const opt = document.createElement('option');
        opt.value = opt.textContent = label;
        sourceSelect.appendChild(opt);
      }
      if ([...sourceSelect.options].some((o) => o.value === prevLabel)) sourceSelect.value = prevLabel;
    },
  };
}

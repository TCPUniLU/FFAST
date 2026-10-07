/**
 * Force Vectors pane (ADR 0045 issue 07). Mirrors modules/loupe/loupeForceVectors.py.
 * Delivers show/source/length/normalise plus filter-to-selection: the "Filter
 * to selection" toggle gates the server's ffast.force_arrows.filter_enabled,
 * and the Force pick tool fills the atom set (setPickedIndices).
 */

import { createPane, checkboxRow, selectRow, sliderRow, rowElement, setRowNeeds } from '../sidebar.js';
const ONLY_FORCES_WARNING_KEY = 'ffast.onlyForcesWarning.dismissed';

/*This function is used to make the 'Only show force vectors' warning visible and handle user input*/
function showOnlyForcesWarning() {
  const modal = document.getElementById('only-forces-warning-modal');
  const dontShowInput = document.getElementById('only-forces-warning-dont-show');
  const okButton = document.getElementById('only-forces-warning-ok');

  // The preference has already been saved in this browser.
  if (localStorage.getItem(ONLY_FORCES_WARNING_KEY) === 'true') {
    return;
  }

  // Start unchecked every time the dialog is shown.
  dontShowInput.checked = false;

  modal.classList.remove('hidden');

  const close = () => {
    if (dontShowInput.checked) {
      localStorage.setItem(ONLY_FORCES_WARNING_KEY, 'true');
    }

    modal.classList.add('hidden');

    okButton.removeEventListener('click', close);
  };

  okButton.addEventListener('click', close, { once: true });
  okButton.focus();
}
/**
 * @param {HTMLElement} sidebarEl
 * @param {{
 *   onApply: (state: {show: boolean, onlyForces: boolean, modelKey: string|null, length: number, normalised: boolean, filterEnabled: boolean, atomIndices: number[]}) => void,
 *   getModels: () => Map<string, {name?: string}>,
 * }} callbacks
 */
export function createForcesPane(sidebarEl, callbacks) {
  const { el, body } = createPane('Force Vectors');
  sidebarEl.appendChild(el);

  const state = { show: false, onlyForces: false, modelKey: null, length: 10, normalised: true, filterEnabled: false, atomIndices: [] };
  const apply = () => callbacks.onApply({ ...state, atomIndices: [...state.atomIndices] });
  let keyByLabel = new Map();   // combo label -> model fingerprint
  let hasPredictions = false;   // Source offers only Ground Truth until one loads (ADR 0055)

  const showInput = checkboxRow(body, 'Show force vectors', state.show, (v) => { state.show = v; _syncVisibility(); apply(); });
  const onlyForces = checkboxRow(body, 'Only show force vectors', state.onlyForces,
      (v) => {state.onlyForces = v; if (v) {
      showOnlyForcesWarning();} apply();})
  const sourceSelect = selectRow(body, 'Source', ['Ground Truth'], 'Ground Truth', (label) => {
    state.modelKey = label === 'Ground Truth' ? null : keyByLabel.get(label) ?? null;
    apply();
  });

  const normalisedInput = checkboxRow(body, 'Normalised', state.normalised, (v) => { state.normalised = v; apply(); });
  const lengthInput = sliderRow(body, 'Length', state.length, { min: 1, max: 200 }, (v) => { state.length = v; apply(); });
  const filterInput = checkboxRow(body, 'Filter to selection', state.filterEnabled, (v) => { state.filterEnabled = v; apply(); });

  function _syncVisibility() {
    const show = state.show;
    const needsShow = show ? '' : 'Turn on "Show force vectors" first';
    for (const control of [onlyForces, normalisedInput, lengthInput, filterInput]) setRowNeeds(rowElement(control), needsShow);
    setRowNeeds(rowElement(sourceSelect), hasPredictions ? needsShow : 'Load a prediction first');
  }
  _syncVisibility();

  return {
    onlyForcesStatus: new Map(),
    normalisedInputStatus: new Map(),
    lengthInputStatus: new Map(),
    filterInputStatus: new Map(),
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
      hasPredictions = models.size > 0;
      _syncVisibility();
    },

    saveState(fp) {
      this.onlyForcesStatus.set(fp, onlyForces.checked);
      this.normalisedInputStatus.set(fp, normalisedInput.checked);
      this.lengthInputStatus.set(fp, lengthInput.value);
      this.filterInputStatus.set(fp, filterInput.checked);
    },

    loadState(fp) {
      onlyForces.checked = this.onlyForcesStatus.get(fp) ?? false;
      normalisedInput.checked = this.normalisedInputStatus.get(fp) ?? true;
      lengthInput.value = this.lengthInputStatus.get(fp) || '10';
      filterInput.checked = this.filterInputStatus.get(fp) ?? false;
      const sliderText = document.querySelector('.pane[data-pane="Force Vectors"] ' +
      '.ctl-row[data-label="Length"] ' +
      '.ctl-slider-value')
      sliderText.textContent = lengthInput.value;

      state.onlyForces = onlyForces.checked;
      state.normalised = normalisedInput.checked;
      state.length = parseInt(lengthInput.value);
      state.filterEnabled = filterInput.checked;
    },
  };
}

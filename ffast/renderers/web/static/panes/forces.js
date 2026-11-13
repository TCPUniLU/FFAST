/**
 * Force Vectors pane (ADR 0045 issue 07).
 * Delivers show/source/length/normalise plus filter-to-selection: the "Filter
 * to selection" toggle gates the server's ffast.force_arrows.filter_enabled,
 * and the Force pick tool fills the atom set (setPickedIndices).
 */

import { createPane, checkboxRow, selectRow, sliderRow, rowElement, setRowNeeds, setSliderValue } from '../sidebar.js';
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
 * Why the arrows asked for cannot show, or '' when they can: the reference
 * data (Ground Truth) of a dataset whose file has no forces, such as a live
 * run's dump.
 * @param {{show: boolean, modelKey: string|null}} state
 * @param {{name?: string, has_forces?: boolean}|undefined} datasetMeta the shown dataset's
 */
export function noForcesReason(state, datasetMeta) {
  if (!state.show || state.modelKey != null || datasetMeta?.has_forces !== false) return '';
  return `${datasetMeta.name || 'This dataset'} has no forces in its file, so there are no arrows `
    + 'to draw. Pick a prediction as Source to see its forces.';
}

/**
 * @param {HTMLElement} sidebarEl
 * @param {{
 *   onApply: (state: {show: boolean, onlyForces: boolean, modelKey: string|null, length: number, normalised: boolean, thickness: number, filterEnabled: boolean, atomIndices: number[]}) => void,
 *   getModels: () => Map<string, {name?: string}>,
 *   getDatasetMeta?: () => ({name?: string, has_forces?: boolean}|undefined),
 * }} callbacks getDatasetMeta: the shown dataset's, for the "no forces" note
 */
export function createForcesPane(sidebarEl, callbacks) {
  const { el, body } = createPane('Force Vectors');
  sidebarEl.appendChild(el);

  // atomIndicesByFrame: null = one shared pick set; an object = each frame's own (to_fix_v2).
  const state = { show: false, onlyForces: false, modelKey: null, length: 10, normalised: true, thickness: 100, filterEnabled: false, atomIndices: [], atomIndicesByFrame: null };
  const apply = () => callbacks.onApply({ ...state, atomIndices: [...state.atomIndices],
    atomIndicesByFrame: state.atomIndicesByFrame && { ...state.atomIndicesByFrame } });
  let keyByLabel = new Map();   // combo label -> model fingerprint
  let hasPredictions = false;   // Source offers only Ground Truth until one loads (ADR 0055)

  const showInput = checkboxRow(body, 'Show force vectors', state.show, (v) => { state.show = v; _syncVisibility(); apply(); });
  const onlyForces = checkboxRow(body, 'Only show force vectors', state.onlyForces,
      (v) => {state.onlyForces = v; if (v) {
      showOnlyForcesWarning();} apply();})
  const sourceSelect = selectRow(body, 'Source', ['Ground Truth'], 'Ground Truth', (label) => {
    state.modelKey = label === 'Ground Truth' ? null : keyByLabel.get(label) ?? null;
    _syncVisibility();
    apply();
  });

  const normalisedInput = checkboxRow(body, 'Normalised', state.normalised, (v) => { state.normalised = v; apply(); });
  const lengthInput = sliderRow(body, 'Length', state.length, { min: 1, max: 200 }, (v) => { state.length = v; apply(); });
  const thicknessInput = sliderRow(body, 'Arrow thickness', state.thickness, { min: 20, max: 300 }, (v) => { state.thickness = v; apply(); });
  const filterInput = checkboxRow(body, 'Filter to selection', state.filterEnabled, (v) => { state.filterEnabled = v; apply(); });
  // Why no arrows show, while that is so (a dataset without forces).
  const note = document.createElement('div');
  note.className = 'forces-note';
  note.hidden = true;
  body.insertBefore(note, rowElement(onlyForces));

  function _syncVisibility() {
    const show = state.show;
    const why = noForcesReason(state, callbacks.getDatasetMeta?.());
    note.textContent = why;
    note.hidden = !why;
    const needsShow = show ? '' : 'Turn on "Show force vectors" first';
    for (const control of [onlyForces, normalisedInput, lengthInput, thicknessInput, filterInput]) setRowNeeds(rowElement(control), needsShow);
    setRowNeeds(rowElement(sourceSelect), hasPredictions ? needsShow : 'Load a prediction first');
  }
  _syncVisibility();

  return {
    /** The shown dataset changed: say again whether it has forces. */
    syncNote() { _syncVisibility(); },
    /** Fill the filter atom set from the Force pick tool; enable filtering.
     * With a `frame` (a variable dataset), the picks belong to that frame only. */
    setPickedIndices(ids, frame = null) {
      if (frame == null) {
        state.atomIndices = [...(ids || [])];
      } else {
        state.atomIndicesByFrame ??= {};
        if (ids?.length) state.atomIndicesByFrame[String(frame)] = [...ids];
        else delete state.atomIndicesByFrame[String(frame)];
      }
      if (!state.filterEnabled) { state.filterEnabled = true; filterInput.checked = true; }
      apply();
    },
    /** The Force tool was armed: arrows go on, on every atom until the
     * first pick narrows them (to_fix_v2). */
    armShow() {
      if (state.show) return;
      state.show = true;
      showInput.checked = true;
      _syncVisibility();
      apply();
    },
    /** The atoms the filter keeps now, or on `frame` when one is given. */
    pickedIndices(frame = null) {
      if (frame == null) return [...state.atomIndices];
      return [...(state.atomIndicesByFrame?.[String(frame)] || [])];
    },
    /** Set show/source and, for a view of its own, the rest without firing
     * onApply (per-dataset and per-panel restore). */
    setState(show, modelKey, extras = {}) {
      const { length = state.length, normalised = state.normalised, onlyForces: only = state.onlyForces,
        filterEnabled = state.filterEnabled, atomIndices = state.atomIndices, thickness = 100,
        atomIndicesByFrame = null } = extras;
      Object.assign(state, { show, modelKey, length, normalised, onlyForces: only, filterEnabled, thickness,
        atomIndices: [...(atomIndices || [])], atomIndicesByFrame: atomIndicesByFrame && { ...atomIndicesByFrame } });
      showInput.checked = show;
      setSliderValue(lengthInput, length);
      setSliderValue(thicknessInput, thickness);
      normalisedInput.checked = normalised;
      onlyForces.checked = only;
      filterInput.checked = filterEnabled;
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
  };
}

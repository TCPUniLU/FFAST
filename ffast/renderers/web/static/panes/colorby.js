/**
 * Colour-by pane (ADR 0045 issue 03): source selector, prediction chooser,
 * colormap, per-metric compute params, and a static colourbar. Mirrors
 * modules/loupe/loupeAtoms.py.
 *
 * The Prediction selector sets the `ffast.atom_color` stage's own
 * `prediction_ref` parameter — a per-stage override on top of the view's
 * global `state.prediction_ref` (color_values.py `resolve_atom_color_values`),
 * the same "Option B" pattern `ffast.force_arrows` already uses — so colouring
 * by one model's error can coexist with a different model driving the force
 * overlay or the object rail's active prediction.
 */

import { createPane, selectRow, row, rowElement } from '../sidebar.js';
import { gradientCss } from '../colormap.js';

const COLORMAPS = ['viridis', 'inferno', 'plasma', 'coolwarm', 'hot', 'bwr', 'force_error'];
const COLORABLE_SHAPES = new Set(['N_atoms', 'N_elements']);

/**
 * @param {HTMLElement} sidebarEl
 * @param {{
 *   onSourceChange: (source: string) => void,
 *   onPredictionChange: (modelKey: string|null) => void,
 *   onColormapChange: (colormap: string) => void,
 *   onMetricParam: (key: string, value: any) => void,
 * }} callbacks
 */
export function createColorByPane(sidebarEl, callbacks) {
  const { el, body } = createPane('Colour By');
  sidebarEl.appendChild(el);

  const labelToSource = new Map([['Elements', 'element'], ['Displacement', 'displacement']]);
  const labelToMetricId = new Map();
  let metricsById = new Map();   // id -> catalog entry
  let currentLabel = 'Elements';
  let keyByLabel = new Map();    // prediction combo label -> model fingerprint

  const coloringSelect = selectRow(body, 'Coloring', ['Elements', 'Displacement'], 'Elements', (label) => {
    currentLabel = label;
    _syncVisibility();
    callbacks.onSourceChange(labelToSource.get(label) || 'element');
    _rebuildParamControls();
  });

  const predictionSelect = selectRow(body, 'Prediction', ['Ground Truth'], 'Ground Truth', (label) => {
    callbacks.onPredictionChange(label === 'Ground Truth' ? null : keyByLabel.get(label) ?? null);
  });

  const colormapSelect = selectRow(body, 'Colormap', COLORMAPS, 'viridis', (cm) => {
    callbacks.onColormapChange(cm);
  });

  const paramContainer = document.createElement('div');
  paramContainer.className = 'metric-params';
  body.appendChild(paramContainer);

  // ── colourbar: static gradient + vmin/vmax + label ──────────────────────
  const colorbar = document.createElement('div');
  colorbar.id = 'colorbar';
  colorbar.className = 'hidden';
  const cbMin = document.createElement('span');
  cbMin.className = 'cb-min';
  const cbGradient = document.createElement('div');
  cbGradient.id = 'colorbar-gradient';
  const cbMax = document.createElement('span');
  cbMax.className = 'cb-max';
  colorbar.append(cbMin, cbGradient, cbMax);
  const cbLabel = document.createElement('div');
  cbLabel.id = 'colorbar-label';
  body.append(colorbar, cbLabel);

  function _syncVisibility() {
    const isElements = currentLabel === 'Elements';
    // Qt hides Colormap/Prediction until Coloring leaves "Elements" (ADR 0040).
    rowElement(colormapSelect).style.display = isElements ? 'none' : '';
    rowElement(predictionSelect).style.display = isElements ? 'none' : '';
  }
  _syncVisibility();

  function _rebuildParamControls() {
    paramContainer.innerHTML = '';
    const mid = labelToMetricId.get(currentLabel);
    if (mid == null) return;
    const params = metricsById.get(mid)?.parameters || {};
    for (const [key, param] of Object.entries(params)) {
      if (param.type === 'choice') {
        selectRow(paramContainer, key, param.choices || [], param.default, (v) => callbacks.onMetricParam(key, v));
      } else if (param.type === 'float') {
        const input = document.createElement('input');
        input.type = 'number';
        input.step = '0.0001';
        if (param.min != null) input.min = String(param.min);
        if (param.max != null) input.max = String(param.max);
        input.value = String(param.default ?? 0);
        input.addEventListener('change', () => callbacks.onMetricParam(key, parseFloat(input.value)));
        row(paramContainer, key, input);
      } else if (param.type === 'bool') {
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.checked = !!param.default;
        input.addEventListener('change', () => callbacks.onMetricParam(key, input.checked));
        row(paramContainer, key, input);
      }
    }
  }

  return {
    /** The variables used to store the state of this whole element used for saving and loading the state when different datasets are chosen. */
    colorState: new Map(), // fp -> string value
    predictionState: new Map(), // fp -> string value
    colormapState: new Map(), // fp -> string value
    normState: new Map(), // fp -> string or Null
    /** Refresh the Prediction combo from the currently loaded models. */
    refreshModels(models) {
      const prevLabel = predictionSelect.value;
      predictionSelect.innerHTML = '';
      keyByLabel = new Map();
      const gt = document.createElement('option');
      gt.value = gt.textContent = 'Ground Truth';
      predictionSelect.appendChild(gt);
      for (const [fp, meta] of models) {
        const label = meta.name || fp.slice(0, 8);
        keyByLabel.set(label, fp);
        const opt = document.createElement('option');
        opt.value = opt.textContent = label;
        predictionSelect.appendChild(opt);
      }
      if ([...predictionSelect.options].some((o) => o.value === prevLabel)) predictionSelect.value = prevLabel;
    },

    /** @param {Array<{id:string,label?:string,shape:string,parameters?:object}>} entries */
    setMetricCatalog(entries) {
      metricsById = new Map(entries.map((e) => [e.id, e]));
      for (const entry of entries) {
        if (!COLORABLE_SHAPES.has(entry.shape)) continue;
        const label = entry.label || entry.id;
        if (labelToMetricId.has(label)) continue;
        labelToMetricId.set(label, entry.id);
        labelToSource.set(label, `metric:${entry.id}`);
        const opt = document.createElement('option');
        opt.value = label;
        opt.textContent = label;
        coloringSelect.appendChild(opt);
      }
      _rebuildParamControls();
    },

    /** @param {import('../protocol.js').AtomColorBy|null} colorBy */
    setColorBy(colorBy) {
      if (!colorBy) { colorbar.classList.add('hidden'); return; }
      colorbar.classList.remove('hidden');
      cbGradient.style.background = `linear-gradient(to right, ${gradientCss(colorBy.colormap)})`;
      cbMin.textContent = colorBy.vmin.toPrecision(3);
      cbMax.textContent = colorBy.vmax.toPrecision(3);
      cbLabel.textContent = colorBy.label + (colorBy.unit ? ` (${colorBy.unit})` : '');
    },

    /**
     *
     * @param {string} fp
     */
    saveState(fp) {
      this.colorState.set(fp, coloringSelect.value);
      this.colormapState.set(fp, colormapSelect.value);
      this.predictionState.set(fp, predictionSelect.value);
      const norm_row = [...document.getElementById('loupe-sidebar').querySelectorAll('.ctl-row')].find(
          (row) => row.querySelector('label')?.textContent.trim() === 'norm'
      ) ?? null;
      if (norm_row !== null) {
        const norm_value = norm_row.querySelector('select')?.value;
        this.normState.set(fp, norm_value ?? 'None');
        //console.log('stored: ', norm_value ?? 'None');
      } else {
        this.normState.set(fp, 'None');
        //console.log('stored: ', 'None');
      }
    },

    /**
     *
     * @param {string} fp
     */
    loadState(fp) {
      coloringSelect.value = this.colorState.get(fp) || 'Elements';
      //console.log('color select state:', coloringSelect.value);
      currentLabel = coloringSelect.value;
      predictionSelect.value = this.predictionState.get(fp) || 'Ground Truth';
      //console.log('predictions state: ', predictionSelect.value);
      colormapSelect.value = this.colormapState.get(fp) || 'viridis';
      //console.log('colormap select: ', colormapSelect.value);

      _syncVisibility();
      _rebuildParamControls();

      const normValue = this.normState.get(fp) || 'None';
      //console.log('retrieved: ', normValue);

      const normRow = [...document.getElementById('loupe-sidebar').querySelectorAll('.ctl-row')].find(
          (row) => row.querySelector('label')?.textContent.trim() === 'norm'
      )?.querySelector('select') ?? null;
      if (normValue !== 'None') { //
        if (normRow !== null) {
          normRow.value = normValue;
        }
      }
    }
  };
}

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
 *
 * The pane shows one view's `colorBy` settings (`show`) and writes each change
 * into them. The settings hold the source (`element`, `displacement`,
 * `metric:<id>`) and the prediction's fingerprint, not the labels the drop-downs
 * show: labels depend on the metric catalog and the loaded predictions.
 */

import { COLORABLE_SHAPES } from '../start_settings.js';
import { createPane, selectRow, row, rowElement, setRowNeeds } from '../sidebar.js';
import { gradientCss } from '../colormap.js';
import { defaultSettings } from '../view_settings.js';

const COLORMAPS = ['viridis', 'inferno', 'plasma', 'coolwarm', 'hot', 'bwr', 'force_error'];
// Shared with an independent panel's starting look (start_settings.js).

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
  let currentLabel = 'Elements'; // the Coloring label on show
  let keyByLabel = new Map();    // prediction combo label -> model fingerprint
  let state = defaultSettings().colorBy;   // the shown view's settings

  /** The Coloring label of a source, or undefined (a metric the catalog lacks). */
  const labelOfSource = (source) => [...labelToSource].find(([, src]) => src === source)?.[0];
  /** The prediction combo label of a fingerprint (Ground Truth for none or unknown). */
  const labelOfPrediction = (fp) => [...keyByLabel].find(([, key]) => key === fp)?.[0] || 'Ground Truth';

  const coloringSelect = selectRow(body, 'Coloring', ['Elements', 'Displacement'], 'Elements', (label) => {
    currentLabel = label;
    state.source = labelToSource.get(label) || 'element';
    missingNote.hidden = true;
    _syncVisibility();
    callbacks.onSourceChange(state.source);
    _rebuildParamControls();
  });

  const predictionSelect = selectRow(body, 'Prediction', ['Ground Truth'], 'Ground Truth', (label) => {
    state.prediction = label === 'Ground Truth' ? null : keyByLabel.get(label) ?? null;
    callbacks.onPredictionChange(state.prediction);
  });

  const colormapSelect = selectRow(body, 'Colormap', COLORMAPS, 'viridis', (cm) => {
    state.colormap = cm;
    callbacks.onColormapChange(cm);
  });

  const paramContainer = document.createElement('div');
  paramContainer.className = 'metric-params';
  body.appendChild(paramContainer);

  // Said when the metric the atoms were coloured by has gone (ADR 0057 rule 15).
  const missingNote = document.createElement('div');
  missingNote.className = 'colorby-missing';
  missingNote.hidden = true;
  body.appendChild(missingNote);

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

  let hasPredictions = false;
  function _syncVisibility() {
    const isElements = currentLabel === 'Elements';
    // Qt hides Colormap/Prediction until Coloring leaves "Elements" (ADR 0040);
    // Prediction also waits until there is a prediction to choose (ADR 0055).
    const needsColouring = isElements ? 'Choose a colouring other than Elements first' : '';
    setRowNeeds(rowElement(colormapSelect), needsColouring);
    setRowNeeds(rowElement(predictionSelect), hasPredictions ? needsColouring : 'Load a prediction first');
  }
  _syncVisibility();

  /** Build the shown metric's parameter controls. A new colouring starts from
   * the parameters' defaults; showing a view puts back its norm (`restore`). */
  function _rebuildParamControls({ restore = false } = {}) {
    paramContainer.innerHTML = '';
    const mid = labelToMetricId.get(currentLabel);
    if (mid != null) _buildParamControls(metricsById.get(mid)?.parameters || {});
    const norm = paramContainer.querySelector(".ctl-row[data-label='norm'] select");
    if (!restore) state.norm = norm?.value ?? 'None';
    else if (norm && state.norm !== 'None') norm.value = state.norm;
  }

  function _buildParamControls(params) {
    for (const [key, param] of Object.entries(params)) {
      if (param.type === 'choice') {
        selectRow(paramContainer, key, param.choices || [], param.default, (v) => {
          if (key === 'norm') state.norm = v;
          callbacks.onMetricParam(key, v);
        });
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
    /** Refresh the Prediction combo from the currently loaded models. */
    refreshModels(models) {
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
      predictionSelect.value = labelOfPrediction(state.prediction);
      hasPredictions = models.size > 0;
      _syncVisibility();
    },

    /** @param {Array<{id:string,label?:string,shape:string,parameters?:object}>} entries */
    setMetricCatalog(entries) {
      metricsById = new Map(entries.map((e) => [e.id, e]));
      const wasMetric = labelToMetricId.get(currentLabel);
      // Rebuilt from the catalog each time, so a metric that was renamed or
      // deleted (user metrics change while the app runs, ADR 0057) leaves the list.
      for (const label of labelToMetricId.keys()) labelToSource.delete(label);
      labelToMetricId.clear();
      for (const opt of [...coloringSelect.options])
        if (!['Elements', 'Displacement'].includes(opt.value)) opt.remove();
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
      // Keep the choice if it is still there; otherwise fall back to the
      // elements and say so.
      const label = labelOfSource(state.source);
      if (label) coloringSelect.value = currentLabel = label;
      else {
        coloringSelect.value = currentLabel = 'Elements';
        if (state.source.startsWith('metric:')) state.source = 'element';
        _syncVisibility();
        if (wasMetric) {
          missingNote.textContent = `${wasMetric} is not on this server; showing element colours.`;
          missingNote.hidden = false;
        }
      }
      _rebuildParamControls();
    },

    /** Colour the atoms by metric `id` (the Metrics pane's shortcut, ADR 0057
     * rule 13): the same as picking it in the Coloring list.
     * @returns {boolean} false when the metric has no value per atom here */
    chooseMetric(id) {
      const label = [...labelToMetricId].find(([, m]) => m === id)?.[0];
      if (!label) return false;
      coloringSelect.value = currentLabel = label;
      state.source = `metric:${id}`;
      missingNote.hidden = true;
      _syncVisibility();
      callbacks.onSourceChange(state.source);
      _rebuildParamControls();
      return true;
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

    /** The prediction metric colours use now, or null (ground truth). */
    predictionOf() {
      return keyByLabel.size && [...keyByLabel.values()].includes(state.prediction) ? state.prediction : null;
    },

    /** Use `fp` as the colouring prediction without sending it. */
    setPrediction(fp) {
      state.prediction = fp ?? null;
      predictionSelect.value = labelOfPrediction(state.prediction);
    },

    /** Show a view's `colorBy` settings; later changes are written into them.
     * A metric the catalog does not (yet) list shows as Elements, and the
     * settings keep it for when the catalog arrives. Nothing is sent. */
    show(settings) {
      state = settings;
      const label = labelOfSource(settings.source);
      coloringSelect.value = currentLabel = label || 'Elements';
      predictionSelect.value = labelOfPrediction(settings.prediction);
      colormapSelect.value = settings.colormap;
      _syncVisibility();
      _rebuildParamControls({ restore: true });
    },
  };
}

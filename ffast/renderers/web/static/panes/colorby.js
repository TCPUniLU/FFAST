/**
 * Colour-by pane (ADR 0045 issue 03): source selector, prediction chooser,
 * colormap, per-metric compute params, and the colour bar. Mirrors
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
 *
 * Keep boxes (ADR 0065 section 5): a metric's setting is chosen with the boxes
 * under the Coloring list, and a change recolours at once. The setting is the
 * source's box id (`metric:ffast.force_difference[atom;length;mae]`), saved
 * with the view; the list shows it under its base. Save as metric keeps it as
 * a user metric. Only settings with one value per atom are offered.
 *
 * Whose numbers: one "Forces" entry stands for the two force pass-throughs
 * (ffast.force_reference / ffast.force_prediction, which the Forces scatter
 * also reads), and the Prediction picks which: Ground Truth or a model. An
 * error offers only models; a metric of reference data alone hides the
 * Prediction. Which is which comes from the catalog's input lists
 * (metric_logic.js `predictionChoice`).
 */

import { createKeepBoxes } from '../keep_boxes.js';
import {
  FORCES_BASES, FORCES_LABEL, colourSettings, firstColourSetting, forcesId, forcesLabel, forcesSide,
  parseBoxId, predictionChoice, sameSetting,
} from '../metric_logic.js';
import { COLORABLE_SHAPES, DEFAULT_LOOK, colouringLabel } from '../start_settings.js';
import { createPane, selectRow, row, rowElement, setRowNeeds } from '../sidebar.js';
import { defaultSettings } from '../view_settings.js';
import { editableDimension, resolveUnit, withUnit } from '../units.js';
import { unitPopover } from '../dialogs.js';
import { formatScientific } from '../panels.js';
import { createColourBar } from '../colorbar_overlay.js';

const COLORMAPS = ['force_error', 'cividis', 'viridis', 'inferno', 'plasma', 'coolwarm', 'hot', 'bwr'];
// Shared with an independent panel's starting look (start_settings.js).

// A base with x/y/z per atom (Force Difference): coloured through its boxes.
const VECTOR_PER_ATOM = '(N_atoms, xyz)';

/**
 * @param {HTMLElement} sidebarEl
 * @param {{
 *   onSourceChange: (source: string) => void,
 *   onPredictionChange: (modelKey: string|null) => void,
 *   onColormapChange: (colormap: string) => void,
 *   onMetricParam: (key: string, value: any) => void,
 *   onLimit?: (key: 'vmin'|'vmax', value: number|null) => void,
 *   unitsOf?: () => Object<string, string>,
 *   onSetUnit?: (dimension: string, unit: string) => void,
 *   boxes?: (request: {metricId?: string, base?: string, setting?: object}) => Promise<object>,
 *   saveMetric?: (metric: object) => Promise<{ok: boolean, error: string|null}>,
 *   defaultPrediction?: () => string|null,
 * }} callbacks unitsOf: the units of the shown view's dataset; onSetUnit: name one;
 *   boxes: REQUEST_METRIC_BOXES; saveMetric: Save as metric; defaultPrediction:
 *   the shown view's own prediction, which an error uses until one is chosen
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

  /** The Coloring label of a source, or undefined (a metric the catalog lacks).
   * A box setting shows as its base; either force pass-through as Forces. */
  const labelOfSource = (source) => {
    const find = (src) => [...labelToSource].find(([, s]) => s === src)?.[0];
    const id = source?.startsWith('metric:') ? source.slice(7) : null;
    if (id && forcesSide(id)) return labelToSource.has(FORCES_LABEL) ? FORCES_LABEL : undefined;
    const box = id ? parseBoxId(id) : null;
    return find(source) ?? (box ? find(`metric:${box.base}`) : undefined);
  };
  /** The prediction combo label of a fingerprint (Ground Truth for none or unknown). */
  const labelOfPrediction = (fp) => [...keyByLabel].find(([, key]) => key === fp)?.[0] || 'Ground Truth';
  const loaded = (fp) => !!fp && [...keyByLabel.values()].includes(fp);
  /** The view's own prediction when loaded, else the first loaded one. */
  const fallbackPrediction = () => {
    const own = callbacks.defaultPrediction?.();
    return loaded(own) ? own : [...keyByLabel.values()][0] ?? null;
  };
  /** The prediction the colouring on show uses: null for Ground Truth (or
   * when the Prediction is hidden). An error with none chosen uses the view's. */
  const shownPrediction = () => {
    const mode = predictionChoice(state.source, metricsById);
    if (mode === 'none' || (mode === 'forces' && forcesSide(state.source.slice(7)) === 'reference')) return null;
    return loaded(state.prediction) ? state.prediction : fallbackPrediction();
  };

  const coloringSelect = selectRow(body, 'Coloring', ['Elements', 'Displacement'], 'Elements', (label) => {
    currentLabel = label;
    if (label === FORCES_LABEL) {
      // whose forces: the prediction chosen last, else the dataset's own
      firstSetting(forcesId(FORCES_BASES.reference, loaded(state.prediction) ? state.prediction : null));
      return;
    }
    const mid = labelToMetricId.get(label);
    if (mid && metricsById.get(mid)?.shape === VECTOR_PER_ATOM) {
      // A vector base colours through its first per-atom setting.
      firstSetting(mid);
      return;
    }
    applySource(labelToSource.get(label) || 'element');
  });

  // ── keep boxes ──────────────────────────────────────────────────────────
  const boxesEl = document.createElement('div');
  boxesEl.className = 'colorby-boxes';
  const keepBoxes = createKeepBoxes((setting) => chooseBoxes(setting));
  const saveLink = document.createElement('button');
  saveLink.className = 'mx-link';
  saveLink.textContent = 'Save as metric';
  const boxesNote = document.createElement('div');
  boxesNote.className = 'mx-boxes-note';
  boxesEl.append(keepBoxes.el, saveLink, boxesNote);
  body.appendChild(boxesEl);
  boxesEl.hidden = true;
  let boxes = null;         // METRIC_BOXES of the colouring metric, or null
  let boxesSeq = 0;
  saveLink.addEventListener('click', async () => {
    if (!boxes?.id || !callbacks.saveMetric) return;
    const r = await callbacks.saveMetric({ id: boxes.id, label: boxes.label, unit: boxes.unit,
      expr: boxes.expr, vars: boxes.vars });
    boxesNote.textContent = r.ok ? 'Saved: the Metrics pane lists it.' : (r.error || 'Could not save');
  });

  /** Colour by `source` (element, displacement or metric:<id>) now. An error
   * gets a prediction when it has none; Forces of a model with no model
   * loaded become the dataset's own. The prediction goes first, so the new
   * colouring is never computed without it. */
  function applySource(source) {
    if (source.startsWith('metric:') && forcesSide(source.slice(7)) === 'prediction' && !shownPredictionFor(source))
      source = `metric:${forcesId(source.slice(7), null)}`;
    state.source = source;
    boxesNote.textContent = '';
    missingNote.hidden = true;
    const want = shownPrediction();
    if (predictionChoice(source, metricsById) !== 'none' && want !== state.prediction) {
      state.prediction = want;
      callbacks.onPredictionChange(want);
    }
    _syncVisibility();
    clearLimits();
    callbacks.onSourceChange(state.source);
    _rebuildParamControls();
    showBoxes();
  }

  /** The prediction `source` would use, without choosing it. */
  function shownPredictionFor(source) {
    const was = state.source;
    state.source = source;
    try { return shownPrediction(); } finally { state.source = was; }
  }

  /** Ask the server; only the answer to the latest question counts. */
  async function askBoxes(request) {
    if (!callbacks.boxes) return null;
    const seq = ++boxesSeq;
    const kw = await callbacks.boxes(request);
    return seq === boxesSeq && kw && !kw.error ? kw : null;
  }

  async function firstSetting(base) {
    const kw = await askBoxes({ metricId: base });
    const setting = kw && firstColourSetting(kw.allowed);
    if (setting) chooseBoxes(setting, base);
  }

  /** A box change recolours at once (no approval). */
  async function chooseBoxes(setting, base = boxes?.base) {
    const kw = await askBoxes({ base, setting });
    if (!kw?.id) return;
    boxes = kw;
    applySource(`metric:${kw.id}`);
  }

  /** The boxes of the colouring metric, when it has more than one per-atom setting. */
  async function showBoxes() {
    const id = state.source?.startsWith('metric:') ? state.source.slice(7) : null;
    if (!id) { boxes = null; renderBoxes(); return; }
    if (boxes?.id !== id) {
      boxes = null;
      renderBoxes();
      boxes = await askBoxes({ metricId: id });
    }
    renderBoxes();
  }

  function renderBoxes() {
    const allowed = boxes ? colourSettings(boxes.allowed) : [];
    boxesEl.hidden = !boxes || allowed.length <= 1 || !allowed.some((s) => sameSetting(s, boxes.setting));
    if (boxesEl.hidden) return;
    keepBoxes.render(boxes.setting, allowed, {
      ...boxes.hints, xyz: 'a colour is one value per atom',
    });
    // a box setting nobody saved yet can be kept as a metric
    saveLink.hidden = !parseBoxId(boxes.id) || metricsById.get(boxes.id)?.origin === 'user';
  }

  const predictionSelect = selectRow(body, 'Prediction', ['Ground Truth'], 'Ground Truth', (label) => {
    state.prediction = label === 'Ground Truth' ? null : keyByLabel.get(label) ?? null;
    const id = state.source.startsWith('metric:') ? state.source.slice(7) : null;
    const forces = id && forcesSide(id) ? `metric:${forcesId(id, state.prediction)}` : null;
    if (!forces || forces === state.source) { callbacks.onPredictionChange(state.prediction); return; }
    // Forces: the metric follows whose forces. A model's forces need the
    // model first; the dataset's own need none, so they go first.
    state.source = forces;
    if (state.prediction) callbacks.onPredictionChange(state.prediction);
    callbacks.onSourceChange(forces);
    if (!state.prediction) callbacks.onPredictionChange(null);
    _renderLabel();
    showBoxes();
  });

  const colormapSelect = selectRow(body, 'Colormap', COLORMAPS, DEFAULT_LOOK.colormap, (cm) => {
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

  // ── colour bar: four movable pieces, the bar, the name and the two limits
  // (colorbar_overlay.js, to_fix 17) ──────────────────────────────────────
  const cbMin = document.createElement('span');
  cbMin.className = 'cb-min';
  const cbMax = document.createElement('span');
  cbMax.className = 'cb-max';
  const cbLabel = document.createElement('div');
  cbLabel.id = 'colorbar-label';
  const colourBar = createColourBar({ name: cbLabel, max: cbMax, min: cbMin });
  // The legend lives on the 3D view, not in this section: app.js puts it
  // into the focused panel's viewport (ADR 0063).
  const legend = document.createElement('div');
  legend.id = 'legend3d';
  legend.className = 'legend3d';
  legend.append(colourBar.el);
  editableLimit(cbMin, 'vmin');
  editableLimit(cbMax, 'vmax');

  /** Click a limit to type it; an empty box makes it automatic again. */
  function editableLimit(span, key) {
    span.tabIndex = 0;
    span.setAttribute('role', 'button');
    span.title = 'Click to set; leave empty for automatic';
    const edit = () => {
      if (span.querySelector('input')) return;
      const shown = span.textContent;
      const input = document.createElement('input');
      input.type = 'text';
      input.className = 'cb-limit-input';
      input.value = state[key] ?? '';
      input.placeholder = shown;
      input.setAttribute('aria-label', key === 'vmin' ? 'Lowest colour value' : 'Highest colour value');
      span.replaceChildren(input);
      input.focus();
      input.select();
      let done = false;
      const finish = (commit) => {
        if (done) return;
        done = true;
        span.textContent = shown;
        if (!commit) return;
        const text = input.value.trim();
        const value = text === '' ? null : Number(text);
        if (value !== null && !Number.isFinite(value)) return;
        if (value === (state[key] ?? null)) return;
        state[key] = value;
        callbacks.onLimit?.(key, value);
      };
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') finish(true);
        else if (e.key === 'Escape') finish(false);
        e.stopPropagation();
      });
      input.addEventListener('blur', () => finish(true));
    };
    span.addEventListener('click', edit);
    span.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); edit(); } });
  }

  /** A new colouring starts from the automatic range. */
  function clearLimits() {
    for (const key of ['vmin', 'vmax']) {
      if (state[key] == null) continue;
      state[key] = null;
      callbacks.onLimit?.(key, null);
    }
  }
  let shownColorBy = null;   // the colouring the colourbar shows, for its label
  const _renderLabel = () => {
    if (!shownColorBy) return;
    const units = callbacks.unitsOf?.() || {};
    const id = state.source?.startsWith('metric:') ? state.source.slice(7) : '';
    const name = forcesSide(id) ? forcesLabel(id) : shownColorBy.label;
    cbLabel.textContent = withUnit(name, resolveUnit(shownColorBy.unit, units));
    cbLabel.classList.toggle('unit-editable', !!editableDimension(shownColorBy.unit));
  };
  // Clicking the label names the unit of an energy or force colouring.
  cbLabel.addEventListener('click', async (e) => {
    const dimension = shownColorBy && editableDimension(shownColorBy.unit);
    if (!dimension || !callbacks.onSetUnit) return;
    const unit = await unitPopover({
      x: e.clientX, y: e.clientY, dimension,
      value: (callbacks.unitsOf?.() || {})[dimension] || '',
    });
    if (unit !== null) callbacks.onSetUnit(dimension, unit);
  });

  function _syncVisibility() {
    const isElements = currentLabel === 'Elements';
    // Qt hides Colormap until Coloring leaves "Elements" (ADR 0040).
    setRowNeeds(rowElement(colormapSelect), isElements ? 'Choose a colouring other than Elements first' : '');
    // Prediction: whose numbers. Ground Truth only for Forces, hidden for
    // reference data alone; an error waits for a prediction (ADR 0055).
    const mode = predictionChoice(state.source, metricsById);
    predictionSelect.replaceChildren();
    const labels = [...(mode === 'forces' ? ['Ground Truth'] : []), ...keyByLabel.keys()];
    for (const label of labels) {
      const opt = document.createElement('option');
      opt.value = opt.textContent = label;
      predictionSelect.appendChild(opt);
    }
    predictionSelect.value = labelOfPrediction(shownPrediction());
    const metric = state.source?.startsWith('metric:');
    setRowNeeds(rowElement(predictionSelect), mode === 'none'
      ? (metric ? 'This colouring reads the dataset alone' : 'Choose a metric colouring first')
      : mode === 'models' && !keyByLabel.size ? 'Load a prediction first' : '');
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
    /** The colour legend (the colour bar's pieces), for the 3D view. */
    legend,

    /** The colour bar, which a PNG export draws on (colorbar_overlay.js). */
    colourBar,

    /** Refresh the Prediction combo from the currently loaded models. */
    refreshModels(models) {
      keyByLabel = new Map();
      for (const [fp, meta] of models) keyByLabel.set(meta.name || fp.slice(0, 8), fp);
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
      if (metricsById.has(FORCES_BASES.reference)) {
        // one entry for both force pass-throughs; the Prediction says whose
        labelToMetricId.set(FORCES_LABEL, FORCES_BASES.reference);
        labelToSource.set(FORCES_LABEL, `metric:${FORCES_BASES.reference}`);
        const opt = document.createElement('option');
        opt.value = opt.textContent = FORCES_LABEL;
        coloringSelect.appendChild(opt);
      }
      for (const entry of entries) {
        // a box setting is listed as its base, and chosen with the boxes
        if (parseBoxId(entry.id) || forcesSide(entry.id)) continue;
        if (!COLORABLE_SHAPES.has(entry.shape) && entry.shape !== VECTOR_PER_ATOM) continue;
        const label = colouringLabel(entry);
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
      if (label) {
        coloringSelect.value = currentLabel = label;
        _syncVisibility();
      } else {
        coloringSelect.value = currentLabel = 'Elements';
        if (state.source.startsWith('metric:')) state.source = 'element';
        _syncVisibility();
        if (wasMetric) {
          missingNote.textContent = `${wasMetric} is not on this server; showing element colours.`;
          missingNote.hidden = false;
        }
      }
      _rebuildParamControls();
      renderBoxes();
    },

    /** Colour the atoms by metric `id` (the Metrics pane's shortcut, ADR 0057
     * rule 13): the same as picking it in the Coloring list.
     * @returns {boolean} false when the metric has no value per atom here */
    chooseMetric(id) {
      const label = labelOfSource(`metric:${id}`);
      if (!label) return false;
      coloringSelect.value = currentLabel = label;
      applySource(`metric:${id}`);
      return true;
    },

    /** @param {import('../protocol.js').AtomColorBy|null} colorBy */
    setColorBy(colorBy) {
      if (!colorBy) { colourBar.setShown(false); return; }
      colourBar.setShown(true);
      colourBar.setColormap(colorBy.colormap);
      if (!cbMin.querySelector('input')) cbMin.textContent = formatScientific(colorBy.vmin);
      if (!cbMax.querySelector('input')) cbMax.textContent = formatScientific(colorBy.vmax);
      shownColorBy = colorBy;
      _renderLabel();
    },

    /** The shown dataset's units changed (or another view is shown). */
    refreshUnits() { _renderLabel(); },

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
      colormapSelect.value = settings.colormap;
      _syncVisibility();
      _rebuildParamControls({ restore: true });
      showBoxes();
    },
  };
}

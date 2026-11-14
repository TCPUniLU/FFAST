/**
 * Tab manager (ADR 0045 Phase 3, ADR 0056) — the config-driven
 * Analysis Tabs (ADR 0021). It builds every tab header and
 * panel grid from the server's `TAB_LAYOUT`, fetches each 2D Panel's metric
 * arrays over the metric channel for the current dataset/prediction, and
 * renders them with `panels.js`.
 *
 * A tab may mix 2D panels with 3D panels (ADR 0056). The grid cells are built
 * once per layout; a 3D cell is left empty here for app.js to put a 3D panel
 * in, and stays put while the 2D cards around it are redrawn. The built-in
 * "3D" tab is one of these tabs. Tabs are appended to `#tabbar` /
 * `#tabpanels`; activation is delegated back to app's `_selectTab`, which
 * toggles `.active` by `panel-<id>`.
 *
 * 2D panels render lazily: a tab's panels are (re)fetched when it becomes the
 * active tab or when the selection context changes while it is active. A
 * monotonic render token discards results that a newer refresh has superseded.
 *
 * Tab-level analysis controls (PRD 59-60):
 *   energy_shift → a shared `shifted` compute-param across the tab's panels
 *   smoothing    → a shared `window` compute-param
 *   element-picker (selector === 'atomic') → which element groups the
 *     grouped_* kinds draw (their row order = the dataset's sorted-unique-Z).
 */

import { helpToggle, PICKER_HELP } from './help.js';
import { renderPanel, PLOT_KINDS, PLOT_CONFIG, elementSymbol, contrastInk, unitRoleAt, refreshPeaks, panelWarnings, panelPopups, panelFailures, panelNotes, neededPredictions, clearPlot, tickAt, nextSort, rethin, fullIndex, fullTraces, buildPanel, replaceTraceData } from './panels.js';
import { showOnce } from './toasts.js';
import { attachTip } from './hover_tip.js';
import { spinner } from './spinner.js';
import { elementColourOn } from './element_colours.js';
import { plotTheme } from './theme.js';
import { visitedKey } from './peaks.js';
import { panelLetters, readingOrder, caption, captionSegments } from './figure.js';
import { datasetUnits, editableDimension, resolveUnit } from './units.js';
import { unitPopover, fieldsDialog, axesDialog } from './dialogs.js';
import { sharedMenu } from './actions.js';
import { icon } from './icons.js';
import {
  plotCsv, tableCsv, panelFileName, downloadText, downloadPlot, backgroundBehind,
  pictureChoice, pictureProblem, loadPictureChoice, savePictureChoice, SHARPNESS,
} from './panel_export.js';
import { KIND_3D } from './tab_rules.js';
import { GRID_COLUMNS, ROW_UNIT_PX, LABEL_FIELDS, AXIS_FIELDS, toFineGrid } from './tab_edit.js';
import { predictionApplies } from './frame_links.js';
import { loadTabState, saveTabState, loadLayout, saveLayout } from './layout_state.js';
import {
  liveEventLine, liveNoScoreMessage, liveRunRow, liveRunProblem, liveRunRuleText, liveRunSeries,
  liveRunThreshold, liveRunsEvents, liveRunsOverlays, liveScoredRow, sameRunUpdate, tonedText,
} from './live_run.js';

/** Two columns of the smallest plot, and the gap between cells, in px (the grid gap in index.html). */
const COLUMN_MIN = 360;
const GRID_GAP = 10;
/** A 3D panel's row in a narrow grid, in px: at 300 px the colour legend covers the molecule. */
const NARROW_3D_PX = 460;
/** A row of a narrow grid is at least this high, in px: one panel of the stack. */
const NARROW_ROW_PX = 300;

/** What a live run's score plot shows (its "?"): one line per legend entry. */
const SCORES_LEGEND_HELP = [
  'Score ÷ threshold: for each saved frame, the worst test\'s score divided by that test\'s threshold. '
    + 'Above the dashed line at 1, the frame looks unlike the reference.',
  'Marked (ticks along the bottom): a frame above 1. One mark alone is not an alarm.',
  'Chemistry flag (ticks just above them): a bond broke, or two atoms came too close.',
  'With several runs, each run has its own two rows of ticks in its curve\'s colour, the first run '
    + 'lowest: marked below, chemistry flag above. The legend names them.',
  'Estimated onset (dotted line) and confirmed (solid line): an alarm. It is confirmed when enough '
    + 'frames are marked within a time window (the alarm rule, behind the "?" in the Run card), or '
    + 'after 3 chemistry-flagged frames in a row. The shaded band runs from the estimated start to '
    + 'the confirmation.',
  'Stop observed: the step where LAMMPS stopped.',
  'Click a point to show that frame in the 3D panel.',
].join('\n');

/** control name → the shared compute-param it drives. */
const CONTROL_PARAM = { energy_shift: 'shifted', smoothing: 'window' };

/** Plot kinds whose view picks out structures, so SUB can make a subset. */
const COMPARE_HINT = "Compare runs: tick several runs in this tab's selector.";
const COMPARE_HINT_KEY = 'ffast.hint.compareRuns';
const SUB_KINDS = new Set(['timeline', 'overlay_timeline', 'scatter', 'parity', 'density']);
/** Plots whose X axis is the frame: "Link X axes" zooms them together. */
const X_LINKED_KINDS = new Set(['timeline', 'overlay_timeline']);
// Roles a kind draws, when it names others too. A parity panel's x and y are
// every force component (>4 GB on a large dataset): only the server reads
// them, to make a subset from a box (ADR 0078).
const DRAWN_ROLES = { parity: new Set(['density', 'outliers']) };

/** A panel's name in a tab's kept state (a reload): what it is and where,
 * so an edit that changes or moves it does not hand it another's zoom. */
function panelKey(spec) {
  return `${spec.kind}|${spec.title || ''}|${spec.row ?? ''},${spec.col ?? ''}`;
}

/** The data a plot draws; a kept zoom applies only to the same data. */
function seriesKey(series) {
  return (series || []).map((s) => `${s.datasetFp}|${s.modelFp || ''}`).join(' ');
}

/** Wait this long after the last zoom or pan before asking for its points (ADR 0099). */
const VIEW_DELAY_MS = 120;

/** A plot's drawing area in pixels: as drawn, or guessed from its box. */
function viewSize(el) {
  const s = el?._fullLayout?._size;
  if (s?.w && s?.h) return { width: s.w, height: s.h };
  return { width: Math.max(100, (el?.clientWidth || 800) - 70),
           height: Math.max(100, (el?.clientHeight || 300) - 50) };
}

/** Wait this long after the last zoom or pan before moving the subset. */
const SUB_DELAY_MS = 250;

/** The visible x and y ranges of a Plotly plot, or null before it is drawn. */
export function plotRange(el) {
  const layout = el && el._fullLayout;
  if (!layout?.xaxis?.range) return null;
  return { x: axisValues(layout.xaxis), y: layout.yaxis?.range ? axisValues(layout.yaxis) : null };
}

/** An axis's shown range in data values, low to high: a log axis keeps its
 * range as log10, a flipped one runs high to low. */
export function axisValues(axis) {
  const r = axis.range.map(Number).map((v) => (axis.type === 'log' ? 10 ** v : v));
  return r[0] <= r[1] ? r : [r[1], r[0]];
}

/**
 * The series the other plots of a tab draw while one plot has SUB ticked:
 * each dataset × prediction pair swapped for the subset that plot made of
 * it, once that subset is listed. Legend names stay as they were.
 * @param {Array<{datasetFp: string, modelFp: string|null, name: string, datasetName: string}>} refs
 * @param {Map<string, string>} subsets `${parentFp}|${modelFp}|${plot}` → subset fingerprint
 * @param {string} plot the name of the plot with SUB ticked
 * @param {Map<string, {name?: string}>} datasets the listed datasets
 */
export function subsetRefs(refs, subsets, plot, datasets) {
  return refs.map((r) => {
    const fp = subsets.get(`${r.datasetFp}|${r.modelFp || ''}|${plot}`);
    const meta = fp && datasets.get(fp);
    return meta ? { ...r, datasetFp: fp, datasetName: meta.name || r.datasetName } : r;
  });
}

/** A short string that changes when a dataset's frames change. */
export function framesKey(meta) {
  const pf = meta?.parent_frames;
  if (!pf) return String(meta?.n ?? '');
  let sum = 0;
  for (const f of pf) sum += f;
  return `${pf.length}:${pf[0]}:${pf[pf.length - 1]}:${sum}`;
}

/**
 * Pair the selected datasets with the selected predictions — one series per
 * drawable combination, in dataset-major order.
 *
 * A prediction applies only to the dataset it was computed for
 * (`datasetFps`); an empty list means "unknown, allow it", matching the object
 * rail's own applicability test. `models` may contain a single `null` to mean
 * reference-only (no prediction selected).
 *
 * Names are kept short: the prediction alone identifies a
 * series while one dataset is in play, and the dataset joins the name once
 * several are — so four models against one dataset do not produce four copies
 * of its name in the legend.
 *
 * @param {Array<{fp: string, name: string}>} datasets
 * @param {Array<{fp: string, name: string, datasetFps?: string[]}|null>} models
 * @returns {Array<{datasetFp: string, modelFp: string|null, name: string,
 *   datasetName: string, modelName: string}>}
 */
export function pairSeries(datasets, models) {
  const ds = datasets || [];
  const ms = (models && models.length) ? models : [null];
  const manyDatasets = ds.length > 1;
  const out = [];
  for (const d of ds) {
    for (const m of ms) {
      if (m) {
        const fps = m.datasetFps || [];
        if (fps.length && !fps.includes(d.fp)) continue;
      }
      const modelName = m ? m.name : '';
      let name;
      if (!m) name = d.name;
      else if (manyDatasets) name = `${d.name} & ${modelName}`;
      else name = modelName;
      out.push({
        datasetFp: d.fp,
        modelFp: m ? m.fp : null,
        name,
        datasetName: d.name,
        modelName,
      });
    }
  }
  return out;
}

/** Put a grid cell where its panel sits. */
function placeCell(el, spec) {
  el.style.gridColumn = `${spec.col + 1} / span ${spec.colspan || 1}`;
  el.style.gridRow = `${spec.row + 1} / span ${spec.rowspan || 1}`;
}

export class AnalysisManager {
  /**
   * @param {{
   *   tabbar: HTMLElement, tabpanels: HTMLElement,
   *   metricClient?: import('./metrics.js').MetricClient|null,
   *   onSelectTab: (id: string) => void,
   *   onMoveTab?: (name: string, before: string|null) => void,
   *   onTabContext?: (id: string, el: HTMLElement) => void,
   *   onStepTab?: (name: string, dir: number, opts?: {refocus: boolean}) => void,
   *   whyNoMove?: () => string,
   *   onPanelLabels?: (tabName: string, spec: object, patch: object) => unknown,
   *   onSub: (o: {name: string, series: Array<{parentFp: string, modelFp: string|null}>,
   *     view?: object, active?: boolean, tick?: boolean}) => void,
   *   onFreeze?: (fp: string) => void,
   *   seriesLooks?: (series: Array<{datasetFp: string, modelFp: string|null}>)
   *     => Array<{colour: string, style: number}>,
   *   onPointFrame: (o: {datasetFp: string, modelFp: string|null, frame: number}) => void,
   *   onSetUnit?: (o: {datasetFp: string, dimension: string, unit: string}) => void,
   *   onLive?: (cmd: object) => void,
   *   explainsChoices?: (t: object, spec: object) => unknown,
   *   onExplains?: (tabName: string, spec: object, index: number) => unknown,
   *   onSameStep?: () => void,
   *   onPauseChange?: () => void,
   *   onLoadPrediction?: (datasetFp: string) => unknown,
   *   onSeriesChanged?: (detail: object) => void,
   * }} deps
   */
  constructor(deps) {
    this._tabbar = deps.tabbar;
    new ResizeObserver(() => this._syncTabbarFade()).observe(this._tabbar);
    this._tabpanels = deps.tabpanels;
    this._metrics = deps.metricClient;
    this._onSelectTab = deps.onSelectTab;
    this._onMoveTab = deps.onMoveTab;
    this._onStepTab = deps.onStepTab;
    this._onTabContext = deps.onTabContext;
    this._whyNoMove = deps.whyNoMove;
    this._onPanelLabels = deps.onPanelLabels;
    /** The name of the tab being dragged in the bar, or null (ADR 0070). */
    this._dragging = null;
    this._onSub = deps.onSub;
    // A live run's stop controls and attribution ask the app (live_ui.js).
    this._onLive = deps.onLive;
    // The 3D panel an attribution card explains: its caption picker (app.js).
    this._explainsChoices = deps.explainsChoices;
    this._onExplains = deps.onExplains;
    this._onSameStep = deps.onSameStep;
    this._onPauseChange = deps.onPauseChange;
    this._onFreeze = deps.onFreeze;
    this._seriesLooks = deps.seriesLooks;
    this._openFrame = deps.onPointFrame;
    /** Frames the user opened from a plot, "dataset|model|frame"; kept for the page's life. */
    this._visited = new Set();
    this._onSetUnit = deps.onSetUnit;
    this._onLoadPrediction = deps.onLoadPrediction;
    /** Told what a tab plots after a click on its data choice: {tab, datasets, models}. */
    this._onSeriesChanged = deps.onSeriesChanged;

    /** @type {Map<string, object>} id → catalog entry */
    this._catalog = new Map();
    /** @type {Array<object>} per-tab state */
    this._tabs = [];
    this._activeId = null;
    this._ctx = { datasetFp: null, modelFp: null, datasetMeta: null };
    /** Everything loaded, for the per-tab selectors: fp → meta. */
    this._available = { datasets: new Map(), models: new Map() };
    this._renderToken = 0;
    /** Subsets SUB made: `${parentFp}|${modelFp}|${plot}` → fingerprint. */
    this._subsets = new Map();
    /** Live runs (ADR 0087): run id → the last LIVE_RUN_UPDATE. */
    this._live = new Map();
    /** Paused tabs (ADR 0091): tab name → what the tab held when it was paused.
     * Never saved: a reload ends every pause, and nothing is sent to the server. */
    this._pauses = new Map();
    /** run id → {result, mode, pending}: the clicked frame's attribution (live_ui.js). */
    this._liveAtt = new Map();

    // One ⋯ menu list for every panel (web parity: plot and table export).
    const panelMenuList = document.createElement('div');
    panelMenuList.className = 'menu-list panel-menu hidden';
    panelMenuList.id = 'panel-menu-list';
    panelMenuList.setAttribute('role', 'menu');
    document.body.appendChild(panelMenuList);
    this._panelMenu = sharedMenu(panelMenuList);
  }

  /**
   * Publish the loaded datasets/predictions the per-tab selectors offer.
   * Separate from `setContext`, which carries the *rail's* single selection —
   * the rail drives the 3D view, a tab's own selection drives its panels.
   * @param {{datasets: Map<string, object>, models: Map<string, object>}} avail
   */
  setAvailable({ datasets, models }) {
    const active = this._activeTab();
    const before = active && this._drawnKeys(active);
    this._available = {
      datasets: datasets || new Map(),
      models: models || new Map(),
    };
    for (const t of this._tabs) {
      // Drop anything that has since been deleted; an empty list falls back to
      // following the rail rather than showing nothing.
      const before = `${t.selectedDatasets?.length}|${t.selectedModels?.length}`;
      // A pinned run's dataset may arrive after its tab: keep the pin until it does.
      if (t.pinWaiting && t.spec.pinned.every((fp) => this._available.datasets.has(fp))) t.pinWaiting = false;
      if (t.selectedDatasets && !t.pinWaiting)
        t.selectedDatasets = t.selectedDatasets.filter((fp) => this._available.datasets.has(fp));
      if (t.selectedModels)
        t.selectedModels = t.selectedModels.filter((fp) => this._available.models.has(fp));
      if (`${t.selectedDatasets?.length}|${t.selectedModels?.length}` !== before) this._keep(t);
      if (t.seriesSelectorEl) this._renderSeriesSelector(t);
    }
    // A subset following a plot's zoom is announced again on every zoom;
    // redrawing a tab that does not draw it would reset that plot's zoom.
    this._redrawIfChanged(active, before);
  }

  /** The server made a subset for a plot with SUB ticked (SUBSET_DECLARED). */
  noteSubset({ fingerprint, parent_fingerprint, model_fp, name }) {
    const active = this._activeTab();
    const before = active && this._drawnKeys(active);
    this._subsets.set(`${parent_fingerprint}|${model_fp || ''}|${name}`, fingerprint);
    this._redrawIfChanged(active, before);
  }

  /** The live connection's metric channel; null while disconnected. */
  setMetricClient(client) {
    this._metrics = client || null;
    if (!client) this._catalog = new Map();
  }

  /** @param {Array<object>} entries METRIC_CATALOG entries */
  setMetricCatalog(entries) {
    this._catalog = new Map((entries || []).map((e) => [e.id, e]));
    if (this._activeTab()) this._renderTab(this._activeTab());
  }

  /** A live run's new state and scores: redraw the live panels of the tab on
   * screen when the run is among the runs ticked in it (ADR 0087, 0089). */
  setLiveRun(kw) {
    const before = this._live.get(kw.run_id);
    this._live.set(kw.run_id, kw);
    this._syncCompareHint();
    for (const x of this._tabs) this._syncPause(x);
    const t = this._activeTab();
    if (!t || !t.slots.every((slot) => slot.cards)) return;
    if (!this._tabRuns(t).some((r) => r.fp === kw.fingerprint)) return;
    if (t.pause) {      // the Run card and the events go on; the plots wait (ADR 0091)
      this._redrawLiveParts(t, 'status');
      this._redrawLiveParts(t, 'events');
      return;
    }
    // The same state sent again (every 10 s): only the status is drawn again,
    // for its "heartbeat N s ago". Redrawing the scores of a long run froze
    // the page for seconds each time.
    const same = sameRunUpdate(before, kw);
    this._redrawLiveParts(t, same ? 'status' : null);
    if (!same) this._redrawRunPanels(t, kw.fingerprint);
  }

  /** Redraw the tab's metric panels that show run `fp` (ADR 0091): the run has
   * new frames or scores, so what they read has changed. A zoom is kept. */
  _redrawRunPanels(t, fp) {
    t.slots.forEach((slot) => {
      slot.cards.forEach((card, i) => {
        const spec = slot.specs[i];
        if (spec.kind === 'live' || !Object.keys(spec.metrics || {}).length) return;
        if (this._cardRefs(t, spec).some((r) => r.datasetFp === fp))
          this._fetchAndRenderPanel(t, spec, card, this._renderToken);
      });
    });
  }

  /** A live run's attribution state changed (live_ui.js): it is the run the
   * attribution panels explain now; redraw them. */
  setLiveAttribution(runId, state) {
    this._liveAtt.set(runId, state);
    this._attRun = runId;
    const t = this._activeTab();
    if (!t || !t.slots.every((slot) => slot.cards)) return;
    for (const slot of t.slots) {
      slot.cards.forEach((card, i) => {
        const spec = slot.specs[i];
        if (spec.kind === 'live' && spec.options?.part === 'attribution') this._drawLivePart(t, spec, card);
      });
    }
  }

  /** Redraw the tab's live panels; with `only`, just the panels of that part. */
  _redrawLiveParts(t, only = null) {
    for (const slot of t.slots) {
      slot.cards.forEach((card, i) => {
        const spec = slot.specs[i];
        if (spec.kind === 'live' && (!only || spec.options?.part === only))
          this._fetchAndRenderPanel(t, spec, card, this._renderToken);
      });
    }
  }

  /** The runs a tab draws: its ticked datasets that are live runs. `kw` is the
   * run's last LIVE_RUN_UPDATE, null until it arrives. */
  _tabRuns(t) {
    return this._tabDatasets(t).map((fp) => ({ fp, kw: this.liveRunOf(fp) })).filter((r) => r.kw
      || String(r.fp).startsWith('live:'));
  }

  /** Whether tab `id` has Same step in all 3D panels switched on (ADR 0089). */
  sameStepOn(id) {
    return !!this._tabs.find((x) => x.id === id)?.sameStep;
  }

  /** The live run whose frames are dataset `fp` (its last LIVE_RUN_UPDATE), or null. */
  liveRunOf(fp) {
    if (!fp) return null;
    for (const kw of this._live.values()) if (kw.fingerprint === fp) return kw;
    return null;
  }

  /** The name of a live run's tab, once the server lists it. */
  liveTabId(name) {
    return this._tabs.find((t) => t.spec.name === name)?.id ?? null;
  }

  /** A `live` panel (ADR 0089): its status rows, events, the attribution of the
   * last frame explained, or the scores of every run ticked in the tab. Plain
   * HTML (the scores are a timeline), redrawn from the runs' last LIVE_RUN_UPDATE. */
  _drawLivePart(t, spec, card, token) {
    const part = spec.options?.part;
    card.el.dataset.part = part;
    const runs = this._tabRuns(t);
    const ready = runs.filter((r) => r.kw);
    const plain = this._tabDatasets(t).filter((fp) => !String(fp).startsWith('live:'));
    if (part === 'scores') return this._drawLiveScores(t, spec, card, token, ready, runs, plain);
    card.captionEl.hidden = true;
    if (part === 'attribution') this._drawExplains(t, spec, card);
    card.body.className = `live-part live-${part}`;
    if (!ready.length) {
      const own = plain[0] ? liveNoScoreMessage(this._nameOf('datasets', plain[0])) : '';
      card.body.textContent = runs.length ? 'Waiting for the run…' : own || 'Tick a live run in the selector.';
      return;
    }
    if (part === 'status') this._drawLiveStatus(spec, card, ready);
    else if (part === 'events') this._drawLiveEvents(card, ready);
    else if (part === 'attribution') this._drawLiveAttribution(spec, card, ready);
  }

  /** A "?" in the score card's title; its note, under the plot, says what
   * the curve, the marks and the lines mean. Made once per card. */
  _scoresHelp(t, card) {
    if (card.scoresHelp) return;
    const { button, note } = this._liveHelp(`scores:${t.spec.name}`, SCORES_LEGEND_HELP,
      'this plot', 'live-scores-explain');
    card.title.insertBefore(button, card.more);
    card.el.insertBefore(note, card.params);
    card.scoresHelp = true;
  }

  /** The scores panel: one curve per ticked run against step, on one threshold line. */
  async _drawLiveScores(t, spec, card, token, ready, runs, plain) {
    this._scoresHelp(t, card);
    const seq = card.seq = (card.seq || 0) + 1;
    const nameOf = (kw) => kw.name || kw.tab || kw.run_id;
    const scored = ready.filter((r) => r.kw.series?.step?.length);
    const notes = plain.map((fp) => liveNoScoreMessage(this._nameOf('datasets', fp)));
    if (!scored.length) {
      // Why there is no curve; the run's state is in the Run card above.
      const problem = ready.map((r) => liveRunProblem(r.kw)).filter(Boolean).join('\n');
      card.body.className = '';
      const msg = document.createElement('div');
      msg.className = 'panel-msg';
      msg.textContent = [runs.length && !ready.length ? 'Waiting for the run…' : '', problem,
        ...notes, ready.length ? 'No scores yet.' : ''].filter(Boolean).join('\n');
      clearPlot(card.body);
      card.body.replaceChildren(msg);
      card.captionEl.hidden = true;
      card.series = null;
      return;
    }
    const named = scored.map((r) => ({ name: nameOf(r.kw), kw: r.kw }));
    // One run keeps its curve's own name; several are told apart by their runs' names.
    const series = scored.map((r) => (scored.length > 1
      ? { ...liveRunSeries(r.kw)[0], name: nameOf(r.kw) } : liveRunSeries(r.kw)[0]));
    const plotSpec = { ...spec, kind: 'timeline', metrics: {}, liveScores: true,
      y_line: liveRunThreshold(), overlays: liveRunsOverlays(named) };
    card.plotSpec = plotSpec;
    card.body.className = 'panel-plot';
    await this._drawPanel(t, plotSpec, card, series, token, seq);
    if (token !== this._renderToken || seq !== card.seq) return;
    // No caption: each run's state is in the Run card, its warnings in Events.
    // Only a plain dataset ticked here says why it has no curve.
    // A frame the reference could not score is a gap in the curve; say why, in its words.
    for (const { kw } of scored) {
      const gaps = Object.entries(kw.refused_frames || {});
      if (!gaps.length) continue;
      const [frame, why] = gaps[0];
      notes.push(`${scored.length > 1 ? `${nameOf(kw)}: ` : ''}${gaps.length} ${gaps.length === 1 ? 'frame' : 'frames'} `
        + `not scored (gaps in the curve). Frame ${frame}: ${why}`);
    }
    card.captionEl.hidden = !notes.length;
    card.captionText.textContent = notes.join('\n');
  }

  /** The Run panel: a table with a row per ticked run, in the error table's
   * look (one shape for one run or many, so runs compare by column). Its
   * buttons sit in the title row like every panel's: the alarm rule's "?" and
   * "Manage runs…". A problem that stops scores is a line under the table. */
  _drawLiveStatus(spec, card, ready) {
    const named = ready.length > 1;
    const now = Date.now();
    const all = ready.map((r) => r.kw);
    const kws = all.filter((kw) => kw.kind !== 'scored');
    const items = all.filter((kw) => kw.kind === 'scored');
    this._liveStatusTitle(card, all);

    const table = document.createElement('table');
    table.className = 'panel-table live-status-table';
    const head = table.createTHead().insertRow();
    const cols = [['Run', ''], ['LAMMPS step', 'num'], ['Scored to', 'num'], ['Scorer', ''],
      ['Atom order', ''], ['State', ''], ['Stop on alarm', 'tick']];
    for (const [text, cls] of cols) {
      const th = document.createElement('th');
      th.textContent = text;
      if (cls) th.className = cls;
      head.appendChild(th);
    }
    const body = table.createTBody();
    for (const kw of kws) {
      const row = liveRunRow(kw, now);
      const tr = body.insertRow();
      tr.dataset.runId = kw.run_id;
      const name = this._liveCell(tr, { text: row.name, tone: '' }, 'cell-name');
      if (kw.practice) {
        const chip = document.createElement('span');
        chip.className = 'practice-chip'; chip.textContent = 'practice';
        name.append(chip);
      }
      const lammps = this._liveCell(tr, row.lammps, 'num');
      if (row.lammps.fraction != null) {
        const bar = document.createElement('span');
        bar.className = 'live-bar';
        bar.setAttribute('aria-hidden', 'true');
        const fill = document.createElement('i');
        fill.style.width = `${(row.lammps.fraction * 100).toFixed(1)}%`;
        bar.appendChild(fill);
        lammps.appendChild(bar);
      }
      this._liveCell(tr, row.scored, 'num');
      this._liveCell(tr, row.scorer);
      this._liveCell(tr, row.atoms);
      this._liveCell(tr, row.state, 'live-state');
      const tick = tr.insertCell();
      tick.className = 'tick';
      const box = this._liveStopOnAlarm(kw);
      if (box) tick.appendChild(box);
      else { tick.textContent = '—'; tick.classList.add('muted'); }
    }
    const parts = kws.length ? [table] : [];
    if (items.length) parts.push(this._scoredStatusTable(items));
    for (const kw of all) {
      const problem = liveRunProblem(kw);
      if (!problem) continue;
      const p = document.createElement('div');
      p.className = 'live-problem';
      p.innerHTML = icon('warning');
      p.append(named ? `${kw.name || kw.tab || kw.run_id}: ${problem}` : problem);
      parts.push(p);
    }
    if (card.ruleNote) parts.push(card.ruleNote);
    card.body.replaceChildren(...parts);
  }

  /** The scored items' rows (ADR 0093): where each was scored from, how far, its state.
   * No stop controls and no atom-order or scorer columns: nothing runs. */
  _scoredStatusTable(items) {
    const table = document.createElement('table');
    table.className = 'panel-table live-status-table live-scored-table';
    const head = table.createTHead().insertRow();
    for (const text of ['Scored item', 'Scored', 'State']) {
      const th = document.createElement('th');
      th.textContent = text;
      head.appendChild(th);
    }
    const body = table.createTBody();
    for (const kw of items) {
      const row = liveScoredRow(kw, this._nameOf('datasets', kw.parent));
      const tr = body.insertRow();
      tr.dataset.runId = kw.run_id;
      this._liveCell(tr, { text: row.name, tone: '' }, 'cell-name');
      this._liveCell(tr, row.scored);
      this._liveCell(tr, row.state, 'live-state');
    }
    return table;
  }

  /** A table cell; trouble gets its colour and the warning icon, never colour alone. */
  _liveCell(tr, cellValue, cls = '') {
    const td = tr.insertCell();
    if (cls) td.className = cls;
    return tonedText(td, cellValue);
  }

  /** The Run panel's title-row buttons, made once per card: the alarm rule's
   * "?" (its note, under the table, stays open across the redraws) and
   * "Manage runs…", which opens the Live Runs dialog on the first running run. */
  _liveStatusTitle(card, kws) {
    const rules = kws.map((kw) => [kw, liveRunRuleText(kw)]).filter(([, text]) => text);
    const text = rules.map(([kw, rule]) => (kws.length > 1
      ? `${kw.name || kw.tab || kw.run_id}: ${rule}` : rule)).join('\n');
    if (text && !card.ruleBtn) {
      const help = this._liveHelp(`rule:${kws[0]?.run_id}`, text, 'the alarm rule', 'live-rule');
      card.ruleBtn = help.button;
      card.ruleNote = help.note;
      card.title.insertBefore(help.button, card.more);
    }
    if (card.ruleBtn) {
      card.ruleBtn.hidden = !text;
      card.ruleNote.textContent = text;
    }
    if (this._onLive && !card.manageBtn) {
      const manage = document.createElement('button');
      manage.type = 'button';
      manage.className = 'live-manage';
      manage.textContent = 'Manage runs…';
      manage.addEventListener('click', () => this._onLive({ action: 'manage', run_id: card.manageRun }));
      card.manageBtn = manage;
      card.title.insertBefore(manage, card.ruleBtn || card.more);
    }
    card.manageRun = (kws.find((kw) => !kw.end) || kws[0])?.run_id;
  }

  /** A "?" and its note for a live card; the note stays open across redraws. */
  _liveHelp(key, text, subject, cls) {
    this._liveHelpOpen ??= new Set();
    const help = helpToggle(text, { subject, open: this._liveHelpOpen.has(key),
      onToggle: (open) => (open ? this._liveHelpOpen.add(key) : this._liveHelpOpen.delete(key)) });
    help.note.classList.add(cls);
    return help;
  }

  /** A run's "Stop on alarm" tick box; null once the run ended. "Stop now…"
   * is in the Live Runs dialog only (ADR 0089). */
  _liveStopOnAlarm(kw) {
    if (kw.end || kw.kind === 'scored' || !this._onLive) return null;
    const tick = document.createElement('input');
    tick.type = 'checkbox';
    tick.className = 'live-stop-on-alarm';
    tick.checked = !!kw.stop_on_alarm;
    tick.setAttribute('aria-label', `Stop ${kw.name || kw.tab || kw.run_id} on alarm`);
    tick.addEventListener('change', () => this._onLive({ action: 'stop_on_alarm', run_id: kw.run_id, on: tick.checked }));
    return tick;
  }

  /** The clicked frame's strongest descriptor groups, as strength ÷ threshold
   * (never shares), with the test explained and the colour switch. */
  /** A 3D panel of the tab on screen changed what it shows: the attribution
   * cards' "Explains" pickers name it anew. */
  syncExplains() {
    const t = this._activeTab();
    if (!t || !t.slots.every((slot) => slot.cards)) return;
    for (const slot of t.slots) {
      slot.cards.forEach((card, i) => {
        const spec = slot.specs[i];
        if (spec.kind === 'live' && spec.options?.part === 'attribution') this._drawExplains(t, spec, card);
      });
    }
  }

  /** The attribution card's caption line: "Explains:" and a drop-down of the
   * tab's 3D panels, like a 3D panel's own caption picker. Saved in the tab. */
  _drawExplains(t, spec, card) {
    const choices = this._explainsChoices?.(t, spec);
    if (!choices?.options.length) return;
    let pick = card.captionText.querySelector('select.live-att-explains');
    if (!pick) {
      pick = document.createElement('select');
      pick.className = 'live-att-explains';
      pick.setAttribute('aria-label', 'The 3D panel this attribution explains');
      pick.addEventListener('change', () => this._onExplains?.(t.spec.name, spec, Number(pick.value)));
      card.captionText.replaceChildren('Explains ', pick);
    }
    pick.replaceChildren(...choices.options.map((o) => {
      const option = document.createElement('option');
      option.value = String(o.value);
      option.textContent = o.label;
      return option;
    }));
    pick.value = String(choices.value);
    pick.disabled = !!choices.why;
    pick.title = choices.why || '';
    card.captionEl.hidden = false;
  }

  _drawLiveAttribution(spec, card, ready) {
    // The run clicked or shown last, when it is ticked in this tab.
    const kw = ready.find((x) => x.kw.run_id === this._attRun)?.kw;
    const state = kw && this._liveAtt.get(kw.run_id);
    const r = state?.result;
    const el = (tag, cls, text) => {
      const e = document.createElement(tag);
      if (cls) e.className = cls;
      if (text != null) e.textContent = text;
      return e;
    };
    if (!kw) {
      const first = card.title.querySelector('.panel-letter')?.nextElementSibling;
      if (first) first.textContent = spec.title || 'Attribution';
      card.body.replaceChildren(el('div', 'live-att-note',
        'Click a point on a ticked run\'s curve to see what drove its score.'));
      return;
    }
    if (!state) {
      card.body.replaceChildren(el('div', 'live-att-note',
        'Show one of this run\'s frames (frame slider, or a point on the score plot) to see what drove its score.'));
      return;
    }
    // The title names the run and step explained, whichever run was shown last.
    const titleEl = card.title.querySelector('.panel-letter')?.nextElementSibling;
    const setTitle = (text) => { if (titleEl) titleEl.textContent = text; };
    setTitle(spec.title || 'Attribution');
    // A line of its own, kept empty when nothing is asked, so the rest does
    // not move each time the next frame is asked for.
    const status = el('div', 'live-att-status', state.pending ? `Asking DetectAna about frame ${state.frame}…` : '');
    if (state.pending && (!r || r.error)) {
      card.body.replaceChildren(status);
      return;
    }
    if (r?.error) {
      card.body.replaceChildren(el('div', 'live-problem', r.error));
      return;
    }
    setTitle(`${spec.title || 'Attribution'} · ${kw.name || kw.tab || kw.run_id}, step ${Number(r.step).toLocaleString('en-US')}`);
    // While the next frame is asked for, the last answer stays: its head names its step.
    const head = el('div', 'live-att-head',
      `Step ${Number(r.step).toLocaleString('en-US')} · ${r.test.join(' ')} at ${r.ratio.toFixed(2)}× its threshold`);
    const modes = el('div', 'live-att-modes');
    for (const m of ['element', 'attribution']) {
      const b = el('button', state.mode === m ? 'active' : '', m === 'element' ? 'Element colours' : 'Attribution');
      b.type = 'button';
      b.dataset.mode = m;
      b.setAttribute('aria-pressed', String(state.mode === m));
      b.addEventListener('click', () => this._onLive?.({ action: 'attribution_mode', run_id: kw.run_id, mode: m }));
      modes.appendChild(b);
    }
    const pick = el('select', 'live-att-test');
    pick.setAttribute('aria-label', 'Test to explain');
    for (const t of r.tests || []) {
      const o = el('option', '', `${t.track} ${t.score} (${t.ratio.toFixed(2)}×)`);
      o.value = `${t.track}|${t.score}`;
      o.selected = t.track === r.test[0] && t.score === r.test[1];
      pick.appendChild(o);
    }
    pick.addEventListener('change', () => {
      const [track, score] = pick.value.split('|');
      this._onLive?.({ action: 'attribute', run_id: kw.run_id, frame: r.frame, track, score });
    });
    // What a strength means: a "?" beside the Strength column, its note just above the table.
    const { button: noteBtn, note } = this._liveHelp(`att:${kw.run_id}`, 'Strength: how much the score '
      + 'would drop if only this group looked normal again, in thresholds (1.00 = one whole threshold). '
      + 'Groups share atoms, so strengths do not add up to the score. A bigger group (Size: how many '
      + 'numbers it has) can explain more: compare groups of similar size.', 'a strength', 'live-att-explain');
    const table = el('table', 'live-att-groups');
    const headRow = el('tr', 'live-att-cols');
    const strengthTh = el('th', 'num', 'Strength');
    strengthTh.appendChild(noteBtn);
    const sizeTh = el('th', 'num', 'Size');
    sizeTh.title = 'How many numbers (bond lengths, angles, dihedrals) the group has';
    headRow.append(el('th', '', 'Group'), strengthTh, sizeTh);
    table.appendChild(headRow);
    for (const g of r.groups || []) {
      const tr = el('tr');
      // "dihedral C3-C1-C6-C5": a narrow card breaks the line at the space, not inside the atoms.
      const name = el('td');
      const cut = g.label.indexOf(' ');
      if (cut > 0) name.append(`${g.label.slice(0, cut)} `, el('span', 'live-att-atoms', g.label.slice(cut + 1)));
      else name.textContent = g.label;
      tr.append(name, el('td', `num${g.strength >= 1 ? ' strong' : ''}`, g.strength.toFixed(2)),
        el('td', 'num', String(g.size)));
      table.appendChild(tr);
    }
    const scale = el('div', 'live-att-scale');
    const ticks = el('div', 'live-att-ticks');
    ticks.append(el('span', '', '0'), el('span', '', '1'), el('span', '', '≥ 2'));
    card.body.replaceChildren(status, head, modes, pick, note, table, scale, ticks);
  }

  _drawLiveEvents(card, ready) {
    const runs = ready.map((r) => ({ name: r.kw.name || r.kw.tab || r.kw.run_id, kw: r.kw }));
    const many = runs.length > 1;
    const list = card.body.querySelector('table.live-event-list') || document.createElement('table');
    list.className = 'live-event-list';
    const stick = !list.isConnected || list.parentElement.scrollTop + list.parentElement.clientHeight
      >= list.parentElement.scrollHeight - 4;
    const rows = liveRunsEvents(runs).map(({ run, event }) => {
      const line = liveEventLine(event);
      const tr = document.createElement('tr');
      if (line.tone) tr.className = line.tone;
      const time = document.createElement('td');
      time.textContent = line.time;
      const text = document.createElement('td');
      text.textContent = line.text;
      if (line.report) {
        const a = document.createElement('span');
        a.className = 'live-report';
        a.textContent = ` · report: ${line.report}`;
        text.appendChild(a);
      }
      if (many) {
        const who = document.createElement('td');
        who.className = 'live-event-run';
        who.textContent = run;
        tr.append(time, who, text);
      } else tr.append(time, text);
      return tr;
    });
    if (many) {
      const head = document.createElement('tr');
      for (const h of ['Time', 'Run', 'Event']) {
        const th = document.createElement('th');
        th.textContent = h;
        head.appendChild(th);
      }
      list.replaceChildren(head, ...rows);
    } else list.replaceChildren(...rows);
    if (!list.isConnected) card.body.replaceChildren(list);
    if (stick) card.body.scrollTop = card.body.scrollHeight;   // follow new events, unless scrolled back
  }

  /** Build (or rebuild) every tab from a TAB_LAYOUT payload. */
  setLayout(tabs) {
    this.clear();
    (tabs || []).forEach((spec, i) => this._buildTab(spec, i));
    this._syncTabbarFade();
    this._syncCompareHint();
  }

  /** Show the compare hint in every live tab while two or more runs are open,
   * unless it was dismissed or some live tab already compares runs. */
  _syncCompareHint() {
    let show = false;
    if (!this._compareHintDismissed()) {
      const open = [...this._live.values()].filter((kw) => kw.fingerprint).length;
      show = open >= 2 && !this._tabs.some((t) => t.spec.layout_of && this._tabRuns(t).length >= 2);
    }
    for (const t of this._tabs) t.hintEl.hidden = !(show && t.spec.layout_of);
  }

  _compareHintDismissed() {
    if (this._hintGone) return true;
    try { return localStorage.getItem(COMPARE_HINT_KEY) === '1'; } catch { return false; }
  }

  _dismissCompareHint() {
    this._hintGone = true;
    try { localStorage.setItem(COMPARE_HINT_KEY, '1'); } catch { /* the hint stays gone for this page */ }
    this._syncCompareHint();
  }

  /** @returns {Array<{id: string, name: string}>} the tabs, in bar order */
  get tabList() {
    return this._tabs.map((t) => ({ id: t.id, name: t.spec.name }));
  }

  /**
   * A tab's parts that app.js lays the 3D controls around, and its 3D cells.
   * @returns {null|{id: string, name: string, spec: object, panelEl: HTMLElement,
   *   bodyEl: HTMLElement, mainEl: HTMLElement, gridEl: HTMLElement,
   *   cells3d: Array<{spec: object, index: number, el: HTMLElement}>,
   *   slots: Array<{el: HTMLElement, indices: number[]}>}}
   */
  tab(id) {
    const t = this._tabs.find((x) => x.id === id);
    if (!t) return null;
    const { panelEl, bodyEl, mainEl, gridEl, cells3d, slots } = t;
    return { id, name: t.spec.name, spec: t.spec, panelEl, bodyEl, mainEl, gridEl, cells3d, slots };
  }

  /** Update the current selection context and refresh the active tab. */
  setContext({ datasetFp, modelFp, datasetMeta }) {
    const active = this._activeTab();
    const before = active && this._drawnKeys(active);
    this._ctx = { datasetFp, modelFp, datasetMeta };
    // Element order for the picker/grouped kinds: sorted unique atomic numbers.
    // A frame subset offers its parent's, so the picker (and the plots) stay
    // put as the zoom moves the subset over structures of other make-up.
    let source = datasetMeta;
    while (source?.parent && source.parent_frames && this._available.datasets.has(source.parent))
      source = this._available.datasets.get(source.parent);
    // A variable dataset sends its distinct elements, not every atom's.
    const zs = (source && (source.element_set || source.elements)) || [];
    this._elementOrder = [...new Set(zs.map(Number))].sort((a, b) => a - b);
    for (const t of this._tabs) {
      // Prune element selection to the new dataset's elements.
      t.selectedElements = t.selectedElements.filter(
        (z) => z === 'All' || this._elementOrder.includes(z));
      if (t.selectorEl) this._renderElementPicker(t);
      // The rail moved, so a tab still following it shows a different default.
      if (t.seriesSelectorEl) this._renderSeriesSelector(t);
    }
    // A tab with its own datasets and predictions draws the same plots as
    // before; redrawing would only lose the zoom (a plot click can move the
    // rail, ADR 0056 rule 4).
    this._redrawIfChanged(active, before);
  }

  /** What a tab's plots depend on: its series, the frames of each (a subset
   * changes them as it follows a zoom), and the elements. */
  _drawnKey(t) {
    return this._drawnKeys(t).all;
  }

  /** What a tab draws: `all` of it, and `subbed`, what its plots other than
   * the one with SUB ticked draw (the subsets, which move with the zoom). */
  _drawnKeys(t) {
    const key = (refs) => JSON.stringify(
      refs.map((r) => [r.datasetFp,
        t.pause?.keys.get(r.datasetFp) ?? framesKey(this._available.datasets.get(r.datasetFp))]));
    const refs = this.seriesRefs(t);
    const units = refs.map((r) => datasetUnits(r.datasetFp, this._available.datasets));
    const all = JSON.stringify([refs, key(refs), this._elementOrder || [], t.selectedElements, units]);
    const subbed = this._cardRefs(t, null);
    const swapped = subbed.some((r, i) => r.datasetFp !== refs[i].datasetFp);
    return { all, subbed: swapped ? key(subbed) : '' };
  }

  /** Redraw the active tab if what it draws changed: all of it, or only the
   * plots other than the one with SUB ticked, so that plot keeps its zoom. */
  _redrawIfChanged(t, before) {
    if (!t) return;
    const now = this._drawnKeys(t);
    if (now.all !== before.all) this._refreshTab(t);
    else if (now.subbed !== before.subbed) this._refreshSubbed(t);
  }

  /** The panel index of the plot with SUB ticked in this tab, or null. */
  _subDriver(t) {
    const [key] = t.subbing.keys();
    return key ?? null;
  }

  /** The series card `spec` draws: the tab's, or, while another plot has SUB
   * ticked, each series' subset from that plot (null: any other plot). */
  _cardRefs(t, spec) {
    const refs = this.seriesRefs(t);
    const driver = this._subDriver(t);
    if (driver == null || (spec && t.spec.panels.indexOf(spec) === driver)) return refs;
    return subsetRefs(refs, this._subsets, this._subName(t, t.spec.panels[driver]),
      this._available.datasets);
  }

  /** Redraw in place every card but the one with SUB ticked. */
  _refreshSubbed(t) {
    const driver = this._subDriver(t);
    for (const slot of t.slots) {
      (slot.cards || []).forEach((card, i) => {
        if (t.spec.panels.indexOf(slot.specs[i]) !== driver)
          this._fetchAndRenderPanel(t, slot.specs[i], card, this._renderToken);
      });
    }
  }

  /** Draw the plots again, after a series colour, Journal page or Density
   * changed what they read. Each plot is redrawn in place from the results its
   * card holds: nothing is asked of the server and a zoom is kept. A card still
   * waiting for its results draws with the new look when they come. Only the
   * tab on screen is drawn; the others are drawn when `activate` shows them. */
  rerenderAll() {
    const t = this._tabs.find((x) => x.id === this._activeId);
    if (!t) return;
    if (!t.slots.length || !t.slots.every((slot) => slot.cards) || t.gridEl.hidden) {
      this._renderTab(t);
      return;
    }
    for (const slot of t.slots) {
      slot.cards.forEach((card, i) => {
        if (card.series && !card.waiting)
          this._drawPanel(t, card.plotSpec || slot.specs[i], card, card.series, this._renderToken, card.seq);
      });
    }
  }

  /** Results of dataset or prediction `fp` may have changed (a load replaced
   * it under the same fingerprint): the tab on screen fetches its plots again
   * if a series shows it. Another tab fetches when it is shown (activate). */
  refreshFor(fp) {
    const t = this._activeTab();
    if (t && this.seriesRefs(t).some((r) => r.datasetFp === fp || r.modelFp === fp)) this._refreshTab(t);
  }

  /** Called by app when a tab is activated (renders analysis tabs lazily). */
  activate(id) {
    this._activeId = id;
    const t = this._tabs.find((x) => x.id === id);
    if (t) this._syncPause(t);
    if (t) this._refreshTab(t);
  }

  /** Remove every tab (on relayout). */
  clear() {
    // Let go of the old cards' requests once the new layout has asked for its
    // own (it is drawn right after this), so a metric both want carries on.
    const old = this._tabs.flatMap((t) => t.slots.flatMap((slot) => slot.cards || []));
    setTimeout(() => old.forEach((card) => card.abort?.abort()), 0);
    for (const t of this._tabs) {
      t.resizeObserver?.disconnect();   // or each rebuild leaves one watching
      t.tabEl.remove();
      t.panelEl.remove();
    }
    this._tabs = [];
  }

  _activeTab() {
    return this._tabs.find((t) => t.id === this._activeId) || null;
  }

  // ── tab construction ────────────────────────────────────────────────────
  _buildTab(spec, index) {
    spec = toFineGrid(spec);   // an old-form tab is drawn on the fine grid too (ADR 0069)
    const id = `tab-${index}`;
    const tabEl = document.createElement('div');
    tabEl.className = 'tab';
    tabEl.textContent = spec.name;
    tabEl.dataset.tab = id;
    tabEl.dataset.name = spec.name;
    tabEl.setAttribute('role', 'tab');
    tabEl.setAttribute('aria-selected', 'false');
    tabEl.tabIndex = -1;   // app.js makes the tab on screen the one tab stop
    // No mark on a tab that replaces a built-in or project one (to_fix_v3 #9):
    // Edit mode says so, and has the Reset to original button.
    tabEl.addEventListener('click', () => this._onSelectTab(id));
    tabEl.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      this._onTabContext?.(id, tabEl);
    });
    // Drag a tab onto another: left half drops before it, right half after
    // it (ADR 0070). Nothing moves while the tab cannot change.
    tabEl.draggable = true;
    const clearDrop = () => tabEl.classList.remove('drop-before', 'drop-after');
    tabEl.addEventListener('dragstart', (e) => {
      if (this._whyNoMove?.()) { e.preventDefault(); return; }
      e.dataTransfer.setData('text/plain', spec.name);
      e.dataTransfer.effectAllowed = 'move';
      this._dragging = spec.name;
    });
    tabEl.addEventListener('dragover', (e) => {
      if (!this._dragging || this._dragging === spec.name) return;
      e.preventDefault();
      const before = e.offsetX < tabEl.clientWidth / 2;
      tabEl.classList.toggle('drop-before', before);
      tabEl.classList.toggle('drop-after', !before);
    });
    tabEl.addEventListener('dragleave', clearDrop);
    tabEl.addEventListener('drop', (e) => {
      e.preventDefault();
      const dragging = this._dragging;
      const isBefore = tabEl.classList.contains('drop-before');
      clearDrop();
      if (!dragging || dragging === spec.name) return;
      let before = spec.name;
      if (!isBefore) {
        const tabs = [...this._tabbar.querySelectorAll('.tab')];
        before = tabs[tabs.indexOf(tabEl) + 1]?.dataset.name ?? null;
      }
      this._onMoveTab?.(dragging, before);
    });
    tabEl.addEventListener('dragend', () => {
      this._dragging = null;
      for (const t of this._tabbar.querySelectorAll('.tab')) t.classList.remove('drop-before', 'drop-after');
    });
    // Left and Right move to the next tab and open it; Home and End go to the ends.
    tabEl.addEventListener('keydown', (e) => {
      if (e.ctrlKey && e.shiftKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
        e.preventDefault();
        this._onStepTab?.(spec.name, e.key === 'ArrowLeft' ? -1 : 1, { refocus: true });
        return;
      }
      const tabs = [...this._tabbar.querySelectorAll('.tab')];
      const here = tabs.indexOf(tabEl);
      const to = { ArrowRight: here + 1, ArrowLeft: here - 1, Home: 0, End: tabs.length - 1 }[e.key];
      if (to === undefined) return;
      e.preventDefault();
      const next = tabs[(to + tabs.length) % tabs.length];
      this._onSelectTab(next.dataset.tab);
      next.focus();
    });
    this._tabbar.appendChild(tabEl);

    // .tabpanel > controls row + body; the body holds the main column (pick
    // bar, grid, playback strip) and, beside it, the 3D settings sidebar —
    // app.js moves those 3D controls into a tab that has a 3D panel.
    const panelEl = document.createElement('div');
    panelEl.className = 'tabpanel analysis-tab';
    panelEl.id = `panel-${id}`;

    const controlsEl = document.createElement('div');
    controlsEl.className = 'analysis-controls';
    controlsEl.inert = document.body.classList.contains('edit-locked');   // a tab made during an edit
    const bodyEl = document.createElement('div');
    bodyEl.className = 'tab-body';
    const mainEl = document.createElement('div');
    mainEl.className = 'tab-main';
    const msgEl = document.createElement('div');
    msgEl.className = 'panel-msg tab-msg';
    msgEl.hidden = true;
    const gridEl = document.createElement('div');
    gridEl.className = 'analysis-grid';
    // Live tabs: one line on how to compare runs (ADR 0089), shown while it helps.
    const hintEl = document.createElement('div');
    hintEl.className = 'live-hint';
    hintEl.hidden = true;
    if (spec.layout_of) {
      const text = document.createElement('span');
      text.textContent = COMPARE_HINT;
      const close = document.createElement('button');
      close.type = 'button';
      close.className = 'live-hint-close';
      close.textContent = '×';
      close.setAttribute('aria-label', 'Dismiss this hint');
      close.addEventListener('click', () => { this._dismissCompareHint(); });
      hintEl.append(text, close);
    }
    mainEl.append(hintEl, msgEl, gridEl);
    bodyEl.append(mainEl);
    panelEl.append(controlsEl, bodyEl);
    this._tabpanels.appendChild(panelEl);

    const t = {
      id, spec, tabEl, panelEl, controlsEl, bodyEl, mainEl, msgEl, hintEl, gridEl,
      slots: [],                        // 2D grid cells: {el, specs}
      cells3d: [],                      // 3D grid cells, for app.js
      sharedParams: {},                 // shifted / window
      selectedElements: ['All'],        // element picker state
      selectorEl: null,
      // null = follow the object rail's single selection (the behaviour before
      // per-tab comparison existed); a list = this tab's own choice.
      selectedDatasets: null,
      selectedModels: null,
      seriesSelectorEl: null,
      // Plots with SUB ticked: panel index → the series it made subsets of.
      subbing: new Map(),
      // Zoomed plots: panelKey → {x, y, series}, kept for a reload.
      zoom: {},
      peaksOn: new Set(),   // timelines (panelKey) whose peak labels are switched on (off by default)
    };
    t.resizeObserver = new ResizeObserver(() => {
      const before = t.narrow;
      this._sizeGrid(t);
      if (t.narrow !== before) this._applyOrder(t);
    });
    t.resizeObserver.observe(gridEl);
    this._restoreTab(t);
    // A live tab opens with its own run ticked (ADR 0089); the rail does not move it.
    if (spec.pinned?.length && !t.selectedDatasets) {
      t.selectedDatasets = [...spec.pinned];
      t.pinWaiting = !spec.pinned.every((fp) => this._available.datasets.has(fp));
    }
    t.pause = this._pauses.get(spec.name) || null;      // a layout rebuild keeps a pause
    this._tabs.push(t);
    this._layoutGrid(t);
    this._buildControls(t);
  }

  /** Bring a tab back as it was being explored before a reload (or a layout
   * rebuild): its own datasets and predictions, the plot with SUB ticked —
   * which goes on following the zoom, without moving the main view — and
   * each plot's zoom (applied as the plots are drawn, `_applyZoom`). */
  _restoreTab(t) {
    const kept = loadTabState(t.spec.name);
    if (!kept) return;
    const known = (list, have) => {
      const left = (list || []).filter((fp) => have.has(fp));
      return left.length ? left : null;
    };
    t.selectedDatasets = known(kept.datasets, this._available.datasets);
    t.selectedModels = known(kept.models, this._available.models);
    t.zoom = { ...(kept.zoom || {}) };
    t.peaksOn = new Set(kept.peaksOn || []);
    t.sameStep = !!kept.sameStep;
    const sub = t.spec.panels.findIndex((sp) => panelKey(sp) === kept.sub);
    if (sub >= 0 && t.selectedDatasets) t.subbing.set(sub, []);
  }

  /** Keep how a tab is being explored, for a reload (`_restoreTab`). */
  _keep(t) {
    const driver = this._subDriver(t);
    saveTabState(t.spec.name, {
      datasets: t.selectedDatasets, models: t.selectedModels,
      sub: driver == null ? null : panelKey(t.spec.panels[driver]),
      zoom: t.zoom,
      peaksOn: [...t.peaksOn],
      sameStep: !!t.sameStep,
    });
  }

  /** Note a plot's zoom, or that it shows everything, for a reload. */
  _noteZoom(t, spec, card) {
    const layout = card.body._fullLayout;
    if (!layout?.xaxis) return;
    const key = panelKey(spec);
    const before = JSON.stringify(t.zoom[key] ?? null);
    // A range the plot was drawn with (a labelled timeline sets its y range)
    // is no more a zoom than autorange is.
    const drawn = card.body._drawnRange || {};
    const range = (axis, initial) => {
      if (!axis || axis.autorange) return null;
      const r = axis.range.map(Number);
      return initial && r[0] === initial[0] && r[1] === initial[1] ? null : r;
    };
    const x = range(layout.xaxis, drawn.x), y = range(layout.yaxis, drawn.y);
    if (!x && !y) delete t.zoom[key];
    else t.zoom[key] = { x, y, series: seriesKey(card.series) };
    if (JSON.stringify(t.zoom[key] ?? null) !== before) this._keep(t);
  }

  /** "Link X axes" (the tab's link_x): a timeline's new X range, or its
   * return to the full range, goes to the tab's other timelines. Each keeps
   * its own flip. */
  async _linkX(t, spec, ev) {
    if (!t.spec.link_x || !X_LINKED_KINDS.has(spec.kind) || this._linkingX || !ev) return;
    let lo = ev['xaxis.range[0]'], hi = ev['xaxis.range[1]'];
    if (lo == null && Array.isArray(ev['xaxis.range'])) [lo, hi] = ev['xaxis.range'];
    const auto = ev['xaxis.autorange'] != null && lo == null;
    if (!auto && (lo == null || hi == null)) return;
    if (!auto && Number(lo) > Number(hi)) [lo, hi] = [hi, lo];
    const others = [];
    for (const slot of t.slots) {
      (slot.cards || []).forEach((card, i) => {
        const other = slot.specs[i];
        if (other !== spec && X_LINKED_KINDS.has(other.kind) && card.body?.layout) others.push([other, card.body]);
      });
    }
    this._linkingX = true;   // their own relayout events must not pass the range on again
    try {
      await Promise.all(others.map(([other, el]) => globalThis.Plotly.relayout(el,
        auto ? { 'xaxis.autorange': other.x_flip ? 'reversed' : true }
          : { 'xaxis.range': other.x_flip ? [hi, lo] : [lo, hi] })));
    } finally {
      this._linkingX = false;
    }
  }

  /** A plot just drawn takes the zoom it had on the same data; a zoom kept
   * for other data is dropped. */
  async _applyZoom(t, spec, card) {
    const key = panelKey(spec);
    const zoom = t.zoom[key];
    if (!zoom) return;
    if (zoom.series !== seriesKey(card.series)) {
      delete t.zoom[key];
      this._keep(t);
      return;
    }
    const update = {};
    if (zoom.x) update['xaxis.range'] = zoom.x;
    if (zoom.y) update['yaxis.range'] = zoom.y;
    await globalThis.Plotly.relayout(card.body, update);
    await rethin(card.body);
  }

  /**
   * Make the grid cells once: honour row/col/span, fold scroll_group members
   * into one horizontal strip at the first member's cell, and leave each 3D
   * panel an empty cell. The grid has `grid` columns (24) that fill the width
   * and rows of 50 px (ADR 0069); a tab with a single cell gives it the whole
   * height (the built-in "3D" tab). The grid scrolls what does not fit.
   */
  _layoutGrid(t) {
    const grid = t.gridEl;
    const panels = t.spec.panels || [];
    this._sizeGrid(t);
    t.letters = this._lettersOf(panels);

    const strips = new Map();   // scroll_group → slot
    panels.forEach((spec, index) => {
      const place = (el) => {
        placeCell(el, spec);
        grid.appendChild(el);
      };
      if (spec.kind === KIND_3D) {
        const el = document.createElement('div');
        el.className = 'analysis-panel panel-3d';
        el.dataset.kind = KIND_3D;
        if (spec.title) {
          el.dataset.title = spec.title;
          const title = document.createElement('div');
          title.className = 'panel-title';
          title.textContent = spec.title;
          el.appendChild(title);
        }
        // Its letter sits in the viewport, top left; Panel3D.mount moves it there.
        const letterEl = document.createElement('span');
        letterEl.className = 'panel-letter';
        letterEl.textContent = t.letters.get(index) || '';
        el.appendChild(letterEl);
        place(el);
        t.cells3d.push({ spec, index, el, letterEl });
      } else if (spec.scroll_group) {
        let slot = strips.get(spec.scroll_group);
        if (!slot) {
          const el = document.createElement('div');
          el.className = 'analysis-scrollstrip';
          place(el);
          slot = { el, specs: [], indices: [] };
          strips.set(spec.scroll_group, slot);
          t.slots.push(slot);
        }
        slot.specs.push(spec);
        slot.indices.push(index);
      } else {
        const el = document.createElement('div');
        el.className = 'grid-slot';
        place(el);
        t.slots.push({ el, specs: [spec], indices: [index] });
      }
    });
    t.has2d = t.slots.length > 0;
    this._applyOrder(t);
    grid.classList.toggle('fill', grid.childElementCount === 1);
    // With several 3D panels, the focused one is outlined (ADR 0056 rule 8).
    grid.classList.toggle('multi3d', t.cells3d.length > 1);
  }

  /** Letters for a tab's panels, by screen position (figure.js). */
  _lettersOf(panels) {
    return panelLetters(panels.map((p, index) => ({ index, row: p.row, col: p.col })));
  }

  /** Give every panel the letter its place now calls for. */
  _applyLetters(t) {
    t.letters = this._lettersOf(t.spec.panels || []);
    for (const slot of t.slots) {
      (slot.cards || []).forEach((card, i) => {
        card.letter = t.letters.get(slot.indices[i]) || '';
        card.letterEl.textContent = card.letter;
        card.captionLetter.textContent = card.letter;
      });
    }
    for (const cell of t.cells3d) cell.letterEl.textContent = t.letters.get(cell.index) || '';
    this._applyOrder(t);
  }

  /** The grid's columns and rows (ADR 0069): `grid` equal columns that fill
   * the width, rows of 50 px. A grid too narrow for two columns of the
   * smallest plot stacks its panels in one column, in reading order, instead
   * of squeezing them; a tab whose panels are all full width stays as it is.
   * An unmeasured (hidden) grid keeps its state. */
  _sizeGrid(t) {
    const panels = t.spec.panels || [];
    const grid = t.spec.grid || GRID_COLUMNS;
    const multi = panels.some((p) => (p.colspan || 1) < grid);
    const width = t.gridEl.clientWidth;
    if (width) t.narrow = multi && width < 2 * COLUMN_MIN + GRID_GAP;
    t.gridEl.classList.toggle('narrow', !!t.narrow);
    t.gridEl.style.setProperty('--grid-columns', String(grid));
    t.gridEl.style.gridTemplateColumns = t.narrow ? 'minmax(0, 1fr)' : `repeat(${grid}, minmax(0, 1fr))`;
    this._sizeRows(t);
  }

  /** Each row's height: 50 px, plus the room the peak labels of a timeline
   * covering it take, so its plot keeps its size; the room goes to the last
   * row the timeline covers. Written per row because the grid has a set
   * height and scrolls. A narrow grid's rows are its panels in reading
   * order, at least 300 px, a 3D panel's 460 px. */
  _sizeRows(t) {
    const rank = readingOrder((t.spec.panels || []).map((p, index) => ({ index, row: p.row, col: p.col })));
    const items = [
      ...(t.slots || []).map((slot) => ({ spec: slot.specs[0], index: slot.indices[0],
        extra: Math.max(0, ...(slot.cards || []).map((c) => c.body._extraHeight || 0)) })),
      ...(t.cells3d || []).map((cell) => ({ spec: cell.spec, index: cell.index, extra: 0,
        min: t.narrow ? NARROW_3D_PX : 0 })),
    ];
    if (t.narrow) items.sort((a, b) => rank.get(a.index) - rank.get(b.index));
    const rowOf = (item, i) => (t.narrow ? i : item.spec.row + (item.spec.rowspan || 1) - 1);
    const extra = [], min = [];
    items.forEach((item, i) => {
      const r = rowOf(item, i);
      extra[r] = Math.max(extra[r] || 0, item.extra);
      min[r] = Math.max(min[r] || 0, item.min || 0);
    });
    if (t.gridEl.classList.contains('fill') || !items.length) {
      t.gridEl.style.gridTemplateRows = '';   // index.html's rows: one whole-height cell, or none
      return;
    }
    if (t.narrow) {
      t.gridEl.style.gridTemplateRows = Array.from({ length: extra.length }, (_, i) =>
        `minmax(${Math.max(NARROW_ROW_PX, min[i] || 0) + (extra[i] || 0)}px, auto)`).join(' ');
      return;
    }
    t.gridEl.style.gridTemplateRows = Array.from({ length: extra.length }, (_, i) =>
      `${ROW_UNIT_PX + (extra[i] || 0)}px`).join(' ');
  }

  /** Stack order for a narrow grid: reading order, whatever the file order is. */
  _applyOrder(t) {
    const rank = readingOrder((t.spec.panels || []).map((p, index) => ({ index, row: p.row, col: p.col })));
    for (const slot of t.slots) slot.el.style.order = String(rank.get(slot.indices[0]) ?? 0);
    for (const cell of t.cells3d) cell.el.style.order = String(rank.get(cell.index) ?? 0);
  }

  /** Fade the tab strip's right edge while it holds more tabs than fit. */
  _syncTabbarFade() {
    this._tabbar.classList.toggle('overflowing', this._tabbar.scrollWidth > this._tabbar.clientWidth + 1);
  }

  /**
   * Edit mode moved or resized panels of tab `spec.name` and changed nothing
   * else (tab_edit.onlyPlacesChanged): move its cells in place, so the plots
   * keep drawing and follow their cells instead of being drawn again. The
   * cards and their handlers know the panels by object, so the new places
   * are copied onto the panels the tab has.
   * @returns {ReturnType<AnalysisManager['tab']>} the tab's parts, or null
   */
  placeCells(spec) {
    const t = this._tabs.find((x) => x.spec.name === spec.name);
    if (!t || t.spec.panels.length !== spec.panels.length) return null;
    t.spec.panels.forEach((panel, i) => {
      const { row, col, rowspan, colspan } = spec.panels[i];
      Object.assign(panel, { row, col, rowspan, colspan });
    });
    t.spec.grid = spec.grid;
    this._sizeGrid(t);
    for (const slot of t.slots) placeCell(slot.el, slot.specs[0]);
    for (const cell of t.cells3d) placeCell(cell.el, cell.spec);
    this._applyLetters(t);
    return this.tab(t.id);
  }

  _buildControls(t) {
    const el = t.controlsEl;
    el.innerHTML = '';
    const names = new Set([
      ...(t.spec.controls || []),
      ...t.spec.panels.flatMap((p) => p.controls || []),
    ]);
    // Params driven by a tab-rendered control are hidden from EVERY panel's
    // per-panel controls (a shared smoothing slider must not also appear as a
    // redundant per-panel `window` input on a panel that didn't declare it).
    t.controlParams = new Set(
      [...names].map((c) => CONTROL_PARAM[c]).filter(Boolean));

    if (names.has('energy_shift')) {
      const item = document.createElement('div');
      item.className = 'ac-item';
      item.dataset.control = 'energy_shift';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.addEventListener('change', () => {
        if (cb.checked) t.sharedParams.shifted = true;
        else delete t.sharedParams.shifted;
        this._refreshTab(t);
      });
      const lbl = document.createElement('label');
      lbl.textContent = 'Energy shift';
      item.append(lbl, cb);
      el.appendChild(item);
    }

    if (names.has('same_step')) {
      const item = document.createElement('div');
      item.className = 'ac-item';
      item.dataset.control = 'same_step';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = !!t.sameStep;
      cb.addEventListener('change', () => {
        t.sameStep = cb.checked;
        this._keep(t);
        if (t.sameStep) this._onSameStep?.(t.id);   // the panels catch up with the frame shown
      });
      const lbl = document.createElement('label');
      lbl.textContent = 'Same step in all 3D panels';
      item.append(lbl, cb);
      el.appendChild(item);
    }

    if (names.has('smoothing')) {
      const item = document.createElement('div');
      item.className = 'ac-item';
      item.dataset.control = 'smoothing';
      const lbl = document.createElement('label');
      lbl.textContent = 'Smoothing';
      const range = document.createElement('input');
      range.type = 'range';
      range.min = '1'; range.max = '100'; range.step = '1'; range.value = '1';
      const out = document.createElement('span');
      out.className = 'ac-empty';
      out.textContent = '1';
      range.addEventListener('input', () => { out.textContent = range.value; });
      range.addEventListener('change', () => {
        const w = parseInt(range.value, 10);
        if (w > 1) t.sharedParams.window = w;
        else delete t.sharedParams.window;
        this._refreshTab(t);
      });
      item.append(lbl, range, out);
      el.appendChild(item);
    }

    if (t.spec.selector === 'atomic') {
      const item = document.createElement('div');
      item.className = 'ac-item';
      item.dataset.control = 'element-picker';
      const lbl = document.createElement('label');
      lbl.textContent = 'Elements';
      const holder = document.createElement('span');
      holder.className = 'ac-item';
      item.append(lbl, holder);
      el.appendChild(item);
      t.selectorEl = holder;
      this._renderElementPicker(t);
    }

    // Every tab with 2D panels gets the comparison selector — it is not a
    // configured control but the tab's own data scope. A tab of 3D panels showing the main view has no
    // use for it.
    if (t.has2d) {
      const series = document.createElement('div');
      series.className = 'ac-item ac-series';
      series.dataset.control = 'series-selector';
      el.appendChild(series);
      t.seriesSelectorEl = series;
      this._renderSeriesSelector(t);
    }

    // Pause (ADR 0091): shown while a live run is ticked in the tab.
    const pause = document.createElement('div');
    pause.className = 'ac-item';
    pause.dataset.control = 'pause';
    t.pauseBtn = document.createElement('button');
    t.pauseBtn.type = 'button';
    t.pauseBtn.className = 'ac-pause';
    t.pauseBtn.addEventListener('click', () => this._togglePause(t));
    pause.appendChild(t.pauseBtn);
    el.appendChild(pause);
    t.pauseEl = pause;
    this._syncPause(t);
  }

  /** Whether a run ticked in the tab has raised an alarm since the pause began. */
  _pauseAlarm(t) {
    if (!t.pause) return false;
    for (const [fp, was] of t.pause.alarms) {
      if ((this.liveRunOf(fp)?.progress?.alarms?.length || 0) > was) return true;
    }
    return false;
  }

  /** The Pause button of tab `t`: shown with a live run ticked (or while paused); reads
   * Resume while paused, and warns when an alarm came meanwhile. */
  _syncPause(t) {
    if (!t.pauseEl) return;
    const live = this._tabDatasets(t).some((fp) => this.liveRunOf(fp));
    // A paused tab keeps its button even if the run is unticked: it must be resumable.
    t.pauseEl.hidden = !live && !t.pause;
    const alarm = this._pauseAlarm(t);
    t.pauseBtn.textContent = !t.pause ? 'Pause' : alarm ? 'Resume · new alarm' : 'Resume';
    t.pauseBtn.classList.toggle('warn', alarm);
    t.controlsEl.hidden = ![...t.controlsEl.children].some((c) => !c.hidden);
  }

  /** Pause the tab: its plots and the frame range of its 3D panels stay as
   * they are while the Run card and the events keep going. Resume draws all once. */
  _togglePause(t) {
    if (t.pause) {
      t.pause = null;
      this._pauses.delete(t.spec.name);
      this._syncPause(t);
      this._refreshTab(t);
      this._onPauseChange?.();
      return;
    }
    const pause = { alarms: new Map(), keys: new Map(), n: new Map() };
    for (const fp of this._tabDatasets(t)) {
      const kw = this.liveRunOf(fp);
      if (!kw) continue;
      const meta = this._available.datasets.get(fp);
      pause.alarms.set(fp, kw.progress?.alarms?.length || 0);
      pause.keys.set(fp, framesKey(meta));
      pause.n.set(fp, meta?.n ?? kw.n_frames ?? 0);
    }
    t.pause = pause;
    this._pauses.set(t.spec.name, pause);
    this._syncPause(t);
  }

  /** The frame count a paused tab on screen holds for dataset `fp`, else null. */
  pausedFrameCount(fp) {
    const t = this._tabs.find((x) => x.id === this._activeId);
    return t?.pause?.n.get(fp) ?? null;
  }

  // ── series resolution (dataset × prediction) ────────────────────────────
  //
  // A tab's selector holds *lists* of datasets and predictions, and its panels
  // draw one entry per (prediction, dataset) pair. This resolves those pairs per tab.

  _nameOf(which, fp) {
    const meta = this._available[which].get(fp);
    return (meta && meta.name) || (fp ? fp.slice(0, 8) : '');
  }

  /** Datasets this tab draws — its own selection, else the rail's. */
  _tabDatasets(t) {
    if (t.selectedDatasets && t.selectedDatasets.length) return t.selectedDatasets;
    return this._ctx.datasetFp ? [this._ctx.datasetFp] : [];
  }

  /** Predictions this tab draws; `[null]` means reference-only. */
  _tabModels(t) {
    if (t.selectedModels && t.selectedModels.length) return t.selectedModels;
    return this._ctx.modelFp ? [this._ctx.modelFp] : [null];
  }

  /** The datasets a prediction draws on: those it was made for and their
   * subsets; empty when unknown (see `pairSeries`). */
  _datasetsOf(modelFp) {
    const meta = this._available.models.get(modelFp) || {};
    if (!(meta.dataset_fingerprints || []).length) return [];
    const all = this._available.datasets;
    return [...all.keys()].filter((fp) => predictionApplies(meta, fp, all));
  }

  /** The dataset × prediction pairs tab `id`'s picker has selected (or the
   * rail's, while it follows the rail): what a new independent 3D panel in
   * it chooses from (ADR 0056 rule 7). */
  tabPairs(id) {
    const t = this._tabs.find((x) => x.id === id);
    return t ? this.seriesRefs(t).map((r) => ({ datasetFp: r.datasetFp, modelFp: r.modelFp })) : [];
  }

  /** The (dataset × prediction) pairs this tab draws (see `pairSeries`). */
  seriesRefs(t) {
    return pairSeries(
      this._tabDatasets(t).map((fp) => ({ fp, name: this._nameOf('datasets', fp) })),
      this._tabModels(t).map((fp) => fp && {
        fp,
        name: this._nameOf('models', fp),
        datasetFps: this._datasetsOf(fp),
      }),
    );
  }

  _renderSeriesSelector(t) {
    const holder = t.seriesSelectorEl;
    if (!holder) return;
    holder.innerHTML = '';

    const group = (which, label, selected, follow) => {
      const entries = [...this._available[which].entries()];
      if (!entries.length) return;
      const wrap = document.createElement('span');
      wrap.className = 'ac-item';
      wrap.dataset.series = which;
      const lbl = document.createElement('label');
      lbl.textContent = label;
      wrap.appendChild(lbl);
      for (const [fp, meta] of entries) {
        const btn = document.createElement('button');
        const isOn = selected ? selected.includes(fp) : follow.includes(fp);
        btn.className = 'elem-btn' + (isOn ? ' active' : '')
          + (selected ? '' : ' following');
        btn.textContent = meta.name || fp.slice(0, 8);
        btn.dataset.fp = fp;
        btn.title = selected ? '' : 'Following the object rail — click to pin';
        btn.addEventListener('click', () => this._toggleSeries(t, which, fp));
        wrap.appendChild(btn);
      }
      holder.appendChild(wrap);
    };

    group('datasets', 'Datasets', t.selectedDatasets, this._tabDatasets(t));
    // A tab of live panels and 3D panels has no metric to compute: no prediction row.
    if (t.spec.panels.some((p) => p.kind !== KIND_3D && p.kind !== 'live'))
      group('models', 'Predictions', t.selectedModels, this._tabModels(t).filter(Boolean));
    if (!holder.childElementCount) return;
    const { button, note } = helpToggle(PICKER_HELP, {
      subject: "this tab's choice of data",
      open: !!t.pickerHelpOpen,
      onToggle: (open) => { t.pickerHelpOpen = open; },
    });
    holder.append(button, note);
  }

  _toggleSeries(t, which, fp) {
    const key = which === 'datasets' ? 'selectedDatasets' : 'selectedModels';
    // First click on a following tab pins the rail's current choice, then
    // applies the toggle to it — so clicking a second prediction *adds* it
    // rather than silently discarding the one already on screen.
    let list = t[key];
    if (!list) {
      list = which === 'datasets'
        ? [...this._tabDatasets(t)]
        : this._tabModels(t).filter(Boolean);
    }
    list = list.includes(fp) ? list.filter((x) => x !== fp) : [...list, fp];
    // Datasets cannot all be off — a panel with no dataset has nothing to say.
    if (which === 'datasets' && !list.length) list = [...this._tabDatasets(t)];
    t[key] = list;
    this._keep(t);
    this._syncCompareHint();
    this._renderSeriesSelector(t);
    this._refreshTab(t);
    this._onSeriesChanged?.({
      tab: t.spec.name, datasets: [...this._tabDatasets(t)], models: this._tabModels(t).filter(Boolean),
    });
  }

  _renderElementPicker(t) {
    const holder = t.selectorEl;
    if (!holder) return;
    holder.innerHTML = '';
    const options = ['All', ...(this._elementOrder || [])];
    for (const z of options) {
      const btn = document.createElement('button');
      btn.className = 'elem-btn' + (t.selectedElements.includes(z) ? ' active' : '');
      btn.textContent = z === 'All' ? 'All' : elementSymbol(z);
      btn.dataset.element = String(z);
      // An element button wears the element's colour in the 3D view (CPK),
      // shaded as its plot lines are where it would not show.
      if (z !== 'All') {
        const colour = elementColourOn(z, plotTheme().surface);
        btn.style.color = colour;
        btn.style.boxShadow = `inset 0 0 0 2px ${colour}`;
        if (btn.classList.contains('active')) {
          btn.style.background = colour;
          btn.style.color = contrastInk(colour);
        }
      }
      btn.addEventListener('click', () => {
        const has = t.selectedElements.includes(z);
        if (has) t.selectedElements = t.selectedElements.filter((x) => x !== z);
        else t.selectedElements = [...t.selectedElements, z];
        if (!t.selectedElements.length) t.selectedElements = ['All'];
        this._renderElementPicker(t);
        this._refreshTab(t);
      });
      holder.appendChild(btn);
    }
  }

  // ── rendering ─────────────────────────────────────────────────────────────
  /** Redraw the tab's 2D panels; its 3D cells are left alone. */
  _renderTab(t) {
    const token = ++this._renderToken;
    if (!t.has2d) return;
    let msg = '';
    // A live run's tab plots its scores, which need no metric or selection.
    const liveOnly = t.spec.panels.every((p) => p.kind === KIND_3D || p.kind === 'live');
    if (liveOnly) msg = '';
    else if (!this._metrics || !this._catalog.size) msg = 'Waiting for metric catalog…';
    else if (!this._tabDatasets(t).length) msg = 'Select a dataset to view this analysis.';
    // A tab of plots only says it once; around a 3D panel each card says it.
    const whole = !!msg && !t.cells3d.length;
    t.msgEl.hidden = !whole;
    t.msgEl.textContent = whole ? msg : '';
    if (whole && !this._catalog.size) t.msgEl.prepend(spinner(), ' ');
    t.gridEl.hidden = whole;
    if (whole) return;

    for (const slot of t.slots) {
      const old = slot.cards || [];
      const cards = slot.specs.map((spec, i) =>
        this._buildPanelCard(t, spec, token, t.letters.get(slot.indices[i]) || ''));
      slot.el.replaceChildren(...cards.map((c) => c.el));
      slot.cards = cards;
      cards.forEach((card, i) => {
        if (msg) card.body.innerHTML = `<div class="panel-msg">${msg}</div>`;
        else this._fetchAndRenderPanel(t, slot.specs[i], card, token);
      });
      // After the new cards asked: a metric both want is not cancelled.
      old.forEach((card) => card.abort?.abort());
    }
  }

  /** New numbers for the same panels (energy shift, smoothing): each plot
   * stays on screen and is redrawn in place when its values arrive, instead
   * of every card being rebuilt empty. Before the first draw: `_renderTab`. */
  _refreshTab(t) {
    const drawn = t.has2d && this._metrics && this._catalog.size && this._tabDatasets(t).length
      && !t.gridEl.hidden && t.slots.length && t.slots.every((slot) => slot.cards);
    if (!drawn) {
      this._renderTab(t);
      return;
    }
    const token = ++this._renderToken;
    for (const slot of t.slots) {
      slot.cards.forEach((card, i) => this._fetchAndRenderPanel(t, slot.specs[i], card, token));
    }
  }

  _buildPanelCard(t, spec, token, letter = '') {
    const el = document.createElement('div');
    el.className = 'analysis-panel';
    el.dataset.kind = spec.kind;
    if (spec.title) el.dataset.title = spec.title;
    if (spec.kind === 'live') el.dataset.part = spec.options?.part || '';

    const title = document.createElement('div');
    title.className = 'panel-title';
    const letterEl = document.createElement('span');
    letterEl.className = 'panel-letter';
    letterEl.textContent = letter;
    const name = document.createElement('span');
    name.innerHTML = spec.title || '';
    title.append(letterEl, name);
    if (spec.tooltip) title.title = spec.tooltip;
    const more = document.createElement('button');
    more.type = 'button';
    more.className = 'panel-more';
    more.innerHTML = icon('more');
    more.setAttribute('aria-label', `More for ${spec.title || 'this panel'}`);
    more.setAttribute('aria-haspopup', 'menu');
    more.setAttribute('aria-expanded', 'false');
    title.appendChild(more);
    el.appendChild(title);

    const body = document.createElement('div');
    const isPlot = PLOT_KINDS.has(spec.kind);
    body.className = isPlot ? 'panel-plot' : '';
    el.appendChild(body);

    const params = document.createElement('div');
    params.className = 'panel-params';
    el.appendChild(params);

    // The caption under the figure: its letter, then one line (figure.js).
    const captionEl = document.createElement('div');
    captionEl.className = 'panel-caption-line';
    captionEl.hidden = true;
    const captionLetter = document.createElement('b');
    captionLetter.textContent = letter;
    const captionText = document.createElement('span');
    captionEl.append(captionLetter, ' | ', captionText);
    el.appendChild(captionEl);

    const card = { el, title, more, body, params, letter, letterEl, captionEl, captionLetter, captionText };
    more.addEventListener('click', () => this._panelMenu.open(more, this._panelActions(t, spec, card)));
    body.addEventListener('click', (e) => this._onUnitClick(card, e));
    body.addEventListener('click', (e) => this._onSortClick(t, spec, card, e));
    body.addEventListener('keydown', (e) => {
      if ((e.key === 'Enter' || e.key === ' ') && e.target.matches?.('th[data-sort]')) {
        e.preventDefault();
        this._onSortClick(t, spec, card, e);
      }
    });
    captionEl.addEventListener('click', (e) => this._onUnitClick(card, e));
    return card;
  }

  /** The ⋯ menu of a panel: downloads (web parity plan 2026-10-29). */
  _panelActions(t, spec, card) {
    const el = card.body;
    const stem = panelFileName(spec);
    const notDrawn = () => (el.querySelector('.main-svg') ? '' : 'Nothing is drawn yet');
    const pictures = [
      { id: 'panel-png', label: 'Download PNG…', unavailable: notDrawn,
        run: () => this._downloadPicture(el, card, stem, 'png') },
      { id: 'panel-svg', label: 'Download SVG…', unavailable: notDrawn,
        run: () => this._downloadPicture(el, card, stem, 'svg') },
      { id: 'panel-csv', label: 'Download data (CSV)', unavailable: notDrawn,
        run: () => this._downloadData(t, spec, card, stem) },
    ];
    // "Hide legend" / "Show legend": saved in the tab as `legend = false`; the
    // default is the automatic rule (a legend once there is more than one thing to name).
    const legend = {
      id: 'panel-legend', label: spec.legend === false ? 'Show legend' : 'Hide legend',
      unavailable: () => notDrawn() || this._whyNoMove?.()
        || (el._labelDefaults?.legend ? '' : 'This plot has nothing to name'),
      run: () => this._onPanelLabels?.(t.spec.name, spec, { legend: spec.legend === false ? null : false }),
    };
    // A live card: its score plot downloads as a plot does; the run's state,
    // events and attribution are not figures.
    if (spec.kind === 'live') {
      return (spec.options?.part || 'scores') === 'scores' ? [...pictures, { separator: true }, legend]
        : [{ id: 'panel-none', label: 'Nothing to download', unavailable: () => 'Only the score plot downloads' }];
    }
    if (!PLOT_KINDS.has(spec.kind)) {
      const table = () => el.querySelector('table.panel-table');
      return [{ id: 'panel-table-csv', label: 'Download table (CSV)',
        unavailable: () => (table() ? '' : 'Nothing is drawn yet'),
        run: () => downloadText(tableCsv(table()), `${stem}.csv`) }];
    }
    return [
      ...pictures,
      { separator: true },
      legend,
      { id: 'panel-labels', label: 'Edit labels…',
        unavailable: () => notDrawn() || this._whyNoMove?.() || '',
        run: () => this._editLabels(t, spec, card) },
      { id: 'panel-axes', label: 'Axes…',
        unavailable: () => notDrawn() || this._whyNoMove?.() || '',
        run: () => this._editAxes(t, spec) },
      { id: 'panel-axes-reset', label: 'Reset axes',
        unavailable: () => this._whyNoMove?.()
          || (AXIS_FIELDS.some((k) => spec[k] != null) ? '' : 'No axes changed'),
        run: () => this._onPanelLabels?.(t.spec.name, spec,
          Object.fromEntries(AXIS_FIELDS.map((k) => [k, null]))) },
      { id: 'panel-labels-reset', label: 'Reset labels',
        unavailable: () => this._whyNoMove?.()
          || (LABEL_FIELDS.some((k) => spec[k] != null) ? '' : 'No labels changed'),
        run: () => this._onPanelLabels?.(t.spec.name, spec,
          Object.fromEntries(LABEL_FIELDS.map((k) => [k, null]))) },
    ];
  }

  /** Edit a plot's axis titles and text sizes (web parity plan
   * 2026-10-29, decisions 4-6); saved in the tab. */
  async _editLabels(t, spec, card) {
    const d = card.body._labelDefaults || { x: '', y: '', labelSize: null, legendSize: null, legend: false };
    const strip = (s) => String(s ?? '').replace(/<[^>]*>/g, '');
    const sizeBox = (label, value, fallback) => ({
      label, type: 'number', min: 6, max: 40, value: String(value ?? ''), placeholder: strip(fallback) });
    const fields = [
      { label: 'X axis title', value: spec.x_title || '', placeholder: strip(d.x) },
      { label: 'Y axis title', value: spec.y_title || '', placeholder: strip(d.y) },
      sizeBox('Axis title size', spec.label_size, d.labelSize),
    ];
    if (d.legend) fields.push(sizeBox('Legend text size', spec.legend_size, d.legendSize));
    const values = await fieldsDialog({
      title: `Labels of ${strip(spec.title) || 'this plot'}`,
      note: 'Leave a box empty for the default. A typed axis title replaces the whole title, unit included.'
        + (d.legend ? ' Drag the legend to move it.' : ''),
      fields,
    });
    if (!values) return;
    const text = (v) => v.trim() || null;
    const size = (v) => { const n = Number(v); return v.trim() && Number.isFinite(n) ? Math.round(n) : null; };
    const patch = { x_title: text(values[0]), y_title: text(values[1]), label_size: size(values[2]) };
    if (d.legend) patch.legend_size = size(values[3]);
    await this._onPanelLabels?.(t.spec.name, spec, patch);
  }

  /** Ask the picture's size (and, for a PNG, its sharpness), then save it
   * (gap 5 of the Qt-removal audit). The choice is kept in this browser. */
  async _downloadPicture(el, card, stem, format) {
    const c = pictureChoice(loadPictureChoice(), { width: el.clientWidth, height: el.clientHeight });
    const png = format === 'png';
    const fields = [
      { label: 'Width (px)', type: 'number', min: 50, max: 10000, value: String(c.width) },
      { label: 'Height (px)', type: 'number', min: 50, max: 10000, value: String(c.height) },
      { label: 'Transparent background', type: 'checkbox', value: c.transparent },
    ];
    if (png) fields.push({ label: 'Sharpness', type: 'select', value: String(c.sharpness),
      options: SHARPNESS.map((k) => [String(k), k === 1 ? '×1 (screen)' : `×${k}${k === 3 ? ' (print, about 300 dpi)' : ''}`]) });
    let values = null;
    for (let note = `On screen it is ${el.clientWidth} × ${el.clientHeight} px.`; ; ) {
      values = await fieldsDialog({ title: `Download ${png ? 'PNG' : 'SVG'}`, note, fields, buttons: ['Cancel', 'Download'] });
      if (!values) return;
      const why = pictureProblem(Number(values[0]), Number(values[1]));
      if (!why) break;
      note = why;
      fields[0].value = values[0]; fields[1].value = values[1]; fields[2].value = values[2];
      if (png) fields[3].value = values[3];
    }
    const choice = { width: Number(values[0]), height: Number(values[1]), transparent: values[2],
      sharpness: png ? Number(values[3]) : c.sharpness };
    savePictureChoice(choice);
    await downloadPlot(el, format, stem, {
      width: choice.width, height: choice.height, scale: png ? choice.sharpness : 1,
      background: choice.transparent ? null : backgroundBehind(card.el) });
  }

  /** Scale, range, flip and grid of a plot's axes (gaps 1-3 of the
   * Qt-removal audit); saved in the tab like the labels. */
  async _editAxes(t, spec) {
    const strip = (s) => String(s ?? '').replace(/<[^>]*>/g, '');
    const axis = (a) => ({
      log: spec[`${a}_scale`] === 'log', min: spec[`${a}_min`] ?? null,
      max: spec[`${a}_max`] ?? null, flip: !!spec[`${a}_flip`], grid: spec[`${a}_grid`] !== false });
    const out = await axesDialog({ title: `Axes of ${strip(spec.title) || 'this plot'}`, x: axis('x'), y: axis('y') });
    if (!out) return;
    const patch = {};
    for (const a of ['x', 'y']) {
      patch[`${a}_scale`] = out[a].log ? 'log' : null;
      patch[`${a}_min`] = out[a].min;
      patch[`${a}_max`] = out[a].max;
      patch[`${a}_flip`] = out[a].flip ? true : null;
      patch[`${a}_grid`] = out[a].grid ? null : false;
    }
    await this._onPanelLabels?.(t.spec.name, spec, patch);
  }

  /** Write the caption line of a drawn panel; a "[unit]" in it that names an
   * energy or force unit is clickable, like the axis title it repeats. */
  _showCaption(card, spec) {
    const info = card.body._figureInfo;
    const text = info ? caption(spec.kind, info) : '';
    card.captionEl.hidden = !text;
    if (!text) { card.captionText.replaceChildren(); return; }
    const axes = card.body._unitAxes || {};
    const nodes = captionSegments(text, info).map((seg) => {
      const role = seg.axis && axes[seg.axis];
      if (!role || !(card.unitDims || {})[role]) return document.createTextNode(seg.text);
      const span = document.createElement('span');
      span.className = 'unit-editable';
      span.dataset.unitRole = role;
      span.textContent = seg.text;
      return span;
    });
    card.captionText.replaceChildren(...nodes);
  }

  /** The sort a table panel shows: the user's own (kept in this browser, never
   * in a tab file), else the one its tab file starts with. */
  _tableSort(t, spec) {
    const mine = loadLayout().tableSort?.[`${t.spec.name}|${panelKey(spec)}`];
    return mine === undefined ? spec.sort || null : mine;
  }

  /** A click on a table header sorts by it: ascending, descending, original order. */
  _onSortClick(t, spec, card, e) {
    const th = e.target.closest?.('th[data-sort]');
    if (!th || e.target.closest('[data-unit-role]') || !card.series) return;
    const next = nextSort(this._tableSort(t, spec), th.dataset.sort);
    const all = { ...(loadLayout().tableSort || {}), [`${t.spec.name}|${panelKey(spec)}`]: next };
    saveLayout({ tableSort: all });
    this._drawPanel(t, spec, card, card.series, this._renderToken, card.seq);
  }

  /** A click on a "[unit]" (or a unit) in an axis title or a table header
   * names the unit of that dimension for the datasets the panel shows. */
  async _onUnitClick(card, e) {
    const role = unitRoleAt(e.target, card.body._unitAxes);
    const dimension = role && card.unitDims && card.unitDims[role];
    if (!dimension || !this._onSetUnit) return;
    const fps = [...new Set((card.series || []).map((s) => s.datasetFp).filter(Boolean))];
    if (!fps.length) return;
    e.stopPropagation();
    const unit = await unitPopover({
      x: e.clientX, y: e.clientY, dimension,
      value: datasetUnits(fps[0], this._available.datasets)[dimension] || '',
    });
    if (unit === null) return;
    for (const datasetFp of fps) this._onSetUnit({ datasetFp, dimension, unit });
  }

  /** A panel waiting for its metrics turns a spinner: in the middle of an
   *  empty panel, or in the title bar beside a plot it still shows. */
  _busy(card, step) {
    card.waiting = (card.waiting || 0) + step;
    if (card.waiting > 0 && !card.spinnerEl) {
      const empty = ![...card.body.children].some((c) => !c.classList.contains('panel-busy'));
      if (empty) {
        card.spinnerEl = document.createElement('div');
        card.spinnerEl.className = 'panel-busy';
        card.spinnerEl.appendChild(spinner(true));
        card.body.appendChild(card.spinnerEl);
      } else {
        card.spinnerEl = spinner();
        card.title.insertBefore(card.spinnerEl, card.more);
      }
    } else if (card.waiting <= 0 && card.spinnerEl) {
      card.spinnerEl.remove();
      card.spinnerEl = null;
    }
  }

  async _fetchAndRenderPanel(t, spec, card, token) {
    if (spec.kind === 'live') return this._drawLivePart(t, spec, card, token);
    const refs = this._cardRefs(t, spec);
    const seq = card.seq = (card.seq || 0) + 1;

    // Assemble the fetch jobs (a role is one id, except `series` = list of ids)
    // and issue them for every series. Requests are keyed per
    // (metric, params, model, dataset), so two series sharing a reference-only
    // metric hit one cache slot rather than computing it twice.
    const jobs = [];
    const drawn = DRAWN_ROLES[spec.kind];
    for (const [role, val] of Object.entries(spec.metrics || {})) {
      if (drawn && !drawn.has(role)) continue;
      if (Array.isArray(val)) val.forEach((id, k) => jobs.push({ role, id, k }));
      else jobs.push({ role, id: val });
    }
    // A metric this server does not have: say so here, where it is used
    // (ADR 0057 rule 15), instead of leaving an empty plot.
    const missing = [...new Set([
      ...(spec.unresolved || []),
      ...jobs.flatMap((j) => (this._catalog.has(j.id) ? [] : [j.id])),
    ])];
    if (missing.length) {
      card.body.className = '';
      card.body._extraHeight = 0;
      card.body.style.minHeight = '';
      this._sizeRows(t);
      const msg = document.createElement('div');
      msg.className = 'panel-msg';
      msg.textContent = `${missing.length === 1 ? 'Metric' : 'Metrics'} ${missing.join(', ')} ` +
        `${missing.length === 1 ? 'is' : 'are'} not on this server.`;
      clearPlot(card.body);
      card.body.replaceChildren(msg);
      card.captionEl.hidden = true;
      card.params.innerHTML = '';
      card.series = null;
      card.abort?.abort();
      card.abort = null;
      return;
    }
    // This fetch replaces any the card still waits for. The old one lets go
    // only after the new requests are made, so a metric both ask for is not
    // cancelled and computed again (ADR 0085).
    const previous = card.abort;
    const abort = card.abort = new AbortController();
    let perSeries;
    // A timeline or scatter asks for the points it can show (ADR 0099): a
    // large result comes thinned, with the frame of each point.
    const viewKind = this._viewKind(spec);
    const thins = refs.map(() => null);
    this._busy(card, +1);
    try {
      const asked = viewKind
        ? Promise.all(refs.map((ref, si) => this._metrics.view({
          ...this._viewAsk(t, spec, viewKind, ref, card, this._keptZoom(t, spec, refs)),
          signal: abort.signal,
        }).then((v) => {
          if (v?.idx) thins[si] = { idx: v.idx, n: v.n };
          return jobs.map((j) => (v ? v.results[j.role] ?? null : null));
        })))
        : Promise.all(refs.map((ref) =>
          Promise.all(jobs.map((j) => this._metrics.request(j.id, {
            datasetFp: ref.datasetFp,
            modelFp: ref.modelFp,
            params: this._metricParams(t, spec, j.id),
            signal: abort.signal,
          })))));
      previous?.abort();
      perSeries = await asked;
    } finally {
      this._busy(card, -1);
    }
    if (token !== this._renderToken || seq !== card.seq) return;   // superseded

    // A metric that ran and failed says why, once, as a pop-up (ADR 0066);
    // below it counts as no result.
    const failures = panelFailures(refs, perSeries, (id) => this._catalog.get(id)?.label || id);
    for (const f of failures) showOnce(f.key, f.text, 'error');
    // A prediction nobody has loaded for a chosen model: one pop-up per
    // (dataset, prediction) with a way to load it (ADR 0085).
    const needed = neededPredictions(refs, perSeries);
    for (const n of needed) {
      const text = `${this._nameOf('datasets', n.datasetFp)} has no prediction by ` +
        `${this._nameOf('models', n.modelFp)}, so some panels are empty.`;
      showOnce(`needs_prediction|${n.datasetFp}|${n.modelFp}`, text, 'warning',
        this._onLoadPrediction ? { label: 'Load prediction…', run: () => this._onLoadPrediction(n.datasetFp) } : null);
    }
    const notes = panelNotes(perSeries);
    for (const results of perSeries) results.forEach((r, i) => { if (r?.failed) results[i] = null; });

    // A series whose every metric came back empty is dropped rather than drawn
    // as a gap: a prediction that cannot compute this panel should not cost the
    // panel its other predictions.
    const series = [];
    refs.forEach((ref, si) => {
      const results = perSeries[si];
      const data = {};
      jobs.forEach((j, i) => {
        if (j.k !== undefined) { (data[j.role] ||= [])[j.k] = results[i]; }
        else data[j.role] = results[i];
      });
      const thin = thins[si];
      // A thinned line's x is the frame of each point.
      if (thin && viewKind === 'line' && !data.x) data.x = { nd: { values: thin.idx, shape: [thin.idx.length] } };
      if (results.some((r) => r && r.nd)) series.push({ ...ref, data, ...(thin ? { thin } : {}) });
    });

    if (!series.length) {
      card.body.className = '';
      card.body._extraHeight = 0;
      card.body.style.minHeight = '';
      this._sizeRows(t);
      const anyModel = refs.some((r) => r.modelFp);
      const msg = document.createElement('div');
      msg.className = 'panel-msg';
      msg.textContent = failures.length ? failures.map((f) => f.text).join('\n')
        : notes.length ? notes.join('\n')
        : needed.length ? needed.map((n) => `Needs a prediction of this dataset by ${this._nameOf('models', n.modelFp)}.`).join('\n')
        : anyModel ? 'No data for this selection.' : 'Select a prediction to compute this panel.';
      clearPlot(card.body);
      card.body.replaceChildren(msg);
      card.captionEl.hidden = true;
      card.params.innerHTML = '';
      return;
    }
    card.body.className = PLOT_KINDS.has(spec.kind) ? 'panel-plot' : '';
    await this._drawPanel(t, spec, card, series, token, seq);
  }

  /** Draw `series` (results the card already holds) into the card, with the
   * look and the zoom the tab has now. `token`/`seq` as in
   * `_fetchAndRenderPanel`: a newer draw or fetch supersedes this one. */
  async _drawPanel(t, spec, card, series, token, seq) {
    // Colour and style of each series, chosen across the whole panel.
    const looks = new Map();
    (this._seriesLooks?.(series) || []).forEach((look, i) => looks.set(series[i], look));
    const ctx = {
      units: this._panelUnits(spec, series),
      unitDims: this._panelUnitDims(spec),
      sort: spec.kind === 'table' ? this._tableSort(t, spec) : null,
      perFrame: this._isPerFrameScatter(spec),
      elementOrder: this._elementOrder || [],
      selectedElements: t.selectedElements,
      seriesColour: (s) => looks.get(s)?.colour || null,
      seriesStyle: (s) => looks.get(s)?.style || 0,
      visited: this._visited,
      peaks: t.peaksOn.has(panelKey(spec)),
    };
    // A ÷ threshold value draws a dashed line at 1 (ADR 0091).
    const level = spec.kind === 'timeline' && !Number.isFinite(spec.y_line)
      ? this._catalog.get(spec.metrics?.y)?.level : null;
    const drawn = Number.isFinite(level) ? { ...spec, y_line: level } : spec;
    renderPanel(card.body, drawn, series, ctx);
    card.drawn = { spec: drawn, ctx };     // a zoom's new points are built alike
    this._showWarnings(card, series);
    card.series = series;
    card.unitDims = ctx.unitDims;
    // Which axis titles name a unit the user can set (index.html makes those clickable).
    const axes = card.body._unitAxes || {};
    card.body.toggleAttribute('data-unit-x', !!ctx.unitDims[axes.x]);
    card.body.toggleAttribute('data-unit-y', !!ctx.unitDims[axes.y]);
    card.el.dataset.datasets = series.map((x) => x.datasetFp).join(' ');
    if (PLOT_KINDS.has(spec.kind)) {
      // Before SUB is wired: it sends the zoom this plot shows.
      await this._applyZoom(t, spec, card);
      if (token !== this._renderToken || seq !== card.seq) return;   // superseded
    }
    this._wirePanelInteractions(t, spec, card);
    this._wirePeaksToggle(t, spec, card);
    this._sizeRows(t);
    this._buildPanelParams(t, spec, card);
    this._showCaption(card, spec);
  }

  /** ⚠ beside the title when a result behind the panel carries a warning;
   * the sentences show on hover (ADR 0064 amendment), and each pops up once,
   * naming its metric (ADR 0066). */
  _showWarnings(card, series) {
    const labelOf = (id) => this._catalog.get(id)?.label || id;
    for (const p of panelPopups(series, labelOf)) showOnce(p.key, p.text);
    const warnings = panelWarnings(series);
    let mark = card.title.querySelector('.panel-warning');
    if (!warnings.length) { if (mark) mark.remove(); return; }
    if (!mark) {
      mark = document.createElement('span');
      mark.className = 'panel-warning';
      mark.textContent = '⚠';
      mark.tabIndex = 0;
      attachTip(mark);
      // In the title's own span, so it sits right after the words.
      card.title.querySelector('.panel-letter').nextElementSibling.append(mark);
    }
    mark.dataset.tip = warnings.join('\n');
    mark.setAttribute('aria-label', `Warning: ${warnings.join('; ')}`);
  }

  /** The "Peaks" checkbox of a one-series timeline: its peak labels on or off. */
  _wirePeaksToggle(t, spec, card) {
    // A live run's x is the step; peak labels name frame indices.
    if (spec.kind !== 'timeline' || spec.liveScores) return;
    const key = panelKey(spec);
    if (!card.peaksToggle) {
      const toggle = document.createElement('label');
      toggle.className = 'peaks-toggle';
      toggle.title = 'Label the five highest peaks';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.addEventListener('change', () => {
        if (cb.checked) t.peaksOn.add(key); else t.peaksOn.delete(key);
        this._keep(t);
        this._fetchAndRenderPanel(t, spec, card, this._renderToken);
      });
      cb.setAttribute('aria-label', `Peaks on ${spec.title || 'this timeline'}`);   // one per panel
      toggle.append(cb, document.createTextNode('Peaks'));
      card.title.insertBefore(toggle, card.title.querySelector('.sub-toggle') || card.more);
      card.peaksToggle = cb;
      card.peaksLabel = toggle;
    }
    // Several series are never labelled, so there is nothing to switch.
    card.peaksLabel.hidden = !card.body._peaksEligible;
    card.peaksToggle.checked = t.peaksOn.has(key);
  }

  /** A plot point or a peak label opened a frame: remember it and dim its
   * label in every timeline of that dataset and model on the tab on screen. */
  _onPointFrame(o) {
    this._visited.add(visitedKey(o.datasetFp, o.modelFp, o.frame));
    // The tab the click was made on, before opening the frame moves to the 3D view.
    const t = this._tabs.find((x) => x.id === this._activeId);
    for (const slot of (t && t.slots) || []) {
      for (const card of slot.cards || []) {
        const s = (card.series || [])[(card.body._subInfo?.curveSeries || [])[0]];
        if (!s || s.datasetFp !== o.datasetFp || s.modelFp !== o.modelFp) continue;
        refreshPeaks(card.body, (frame) => this._visited.has(visitedKey(s.datasetFp, s.modelFp, frame)));
      }
    }
    this._openFrame?.(o);
  }

  // ── thinned views (ADR 0099) ───────────────────────────────────────────────

  /** "Download data (CSV)": every point of the plot. A thinned view holds
   * only the points drawn, so its whole results are fetched first, with a
   * spinner in the title meanwhile (ADR 0099 decision 5). */
  async _downloadData(t, spec, card, stem) {
    const el = card.body;
    const series = card.series || [];
    if (!card.drawn || !series.some((s) => s.thin)) {
      downloadText(plotCsv(fullTraces(el), el._figureInfo || {}), `${stem}.csv`);
      return;
    }
    const busy = spinner(true);
    card.title.insertBefore(busy, card.more);
    try {
      const whole = await Promise.all(series.map(async (s) => {
        if (!s.thin) return s;
        const data = {};
        await Promise.all(Object.entries(spec.metrics).map(async ([role, id]) => {
          data[role] = await this._metrics.request(id, {
            datasetFp: s.datasetFp, modelFp: s.modelFp, params: this._metricParams(t, spec, id) });
        }));
        if (Object.values(data).some((r) => !r?.nd)) return null;
        const { thin, ...rest } = s;      // and its x of frames goes: the index is x again
        return { ...rest, data };
      }));
      if (whole.some((s) => !s)) {
        showOnce(`csv|${stem}`, `The data of ${spec.title || 'this panel'} could not be fetched for the file.`, 'error');
        return;
      }
      const built = buildPanel(card.drawn.spec, whole, card.drawn.ctx);
      downloadText(plotCsv(built.traces, el._figureInfo || {}), `${stem}.csv`);
    } finally {
      busy.remove();
    }
  }

  /** 'line' or 'points' for a panel that asks for thinned views, else null:
   * a timeline (not a live run's) or a scatter of single metrics. */
  _viewKind(spec) {
    const m = spec.metrics || {};
    const roles = Object.keys(m);
    if (!roles.every((r) => typeof m[r] === 'string')) return null;
    if (spec.kind === 'timeline' && !spec.liveScores && m.y && roles.every((r) => r === 'y' || r === 'x')) return 'line';
    if (spec.kind === 'scatter' && m.x && m.y && roles.length === 2) return 'points';
    return null;
  }

  /** The zoom kept for this panel, when it is of the same data. */
  _keptZoom(t, spec, refs) {
    const zoom = t.zoom?.[panelKey(spec)];
    return zoom && zoom.series === seriesKey(refs) ? zoom : null;
  }

  /** A view request for one series of a panel, for `range` ({x, y} or null). */
  _viewAsk(t, spec, kind, ref, card, range) {
    const ids = Object.values(spec.metrics);
    return {
      kind, metrics: spec.metrics,
      params: Object.fromEntries(ids.map((id) => [id, this._metricParams(t, spec, id)])),
      datasetFp: ref.datasetFp, modelFp: ref.modelFp,
      xRange: range?.x || null, yRange: range?.y || null,
      ...viewSize(card.body),
    };
  }

  /** After a zoom or pan settles, ask for the points of the new view. */
  _scheduleView(t, spec, card, ev) {
    if (ev && !Object.keys(ev).some((k) => k.startsWith('xaxis') || k.startsWith('yaxis'))) return;
    clearTimeout(card.viewTimer);
    card.viewTimer = setTimeout(() => this._refreshView(t, spec, card), VIEW_DELAY_MS);
  }

  /** The thinned series of a drawn plot, again for the range it shows: its
   * data is replaced in place, so the zoom stays. The plot keeps showing the
   * points it has until the new ones come. */
  async _refreshView(t, spec, card) {
    const el = card.body;
    const range = plotRange(el);
    const series = card.series;
    const kind = this._viewKind(spec);
    if (!range || !series || !kind || !card.drawn) return;
    const seq = card.seq;
    card.viewAbort?.abort();
    const abort = card.viewAbort = new AbortController();
    const want = series.map((s, i) => (s.thin ? i : -1)).filter((i) => i >= 0);
    const replies = await Promise.all(want.map((i) => this._metrics.view({
      ...this._viewAsk(t, spec, kind, series[i], card, range), signal: abort.signal })));
    if (seq !== card.seq || card.series !== series || abort.signal.aborted) return;   // redrawn meanwhile
    replies.forEach((v, k) => {
      if (!v || !v.idx) return;
      const s = series[want[k]];
      for (const [role, r] of Object.entries(v.results)) if (r?.nd) s.data[role] = r;
      s.thin = { idx: v.idx, n: v.n };
      if (kind === 'line' && !spec.metrics.x) s.data.x = { nd: { values: v.idx, shape: [v.idx.length] } };
    });
    const built = buildPanel(card.drawn.spec, series, card.drawn.ctx);
    const curves = el._subInfo?.curveSeries || [];
    const rows = [];
    curves.forEach((si, c) => {
      if (want.includes(si) && built?.traces?.[c]) rows.push([c, built.traces[c].x, built.traces[c].y]);
    });
    if (rows.length) await replaceTraceData(el, rows);
  }

  /** Compute-param values to send for one metric of a panel. */
  _metricParams(t, spec, metricId) {
    const entry = this._catalog.get(metricId);
    const out = {};
    if (!entry || !entry.parameters) return out;
    const overrides = (t.overrides && t.overrides[metricId]) || {};
    for (const [name, p] of Object.entries(entry.parameters)) {
      if (p.role && p.role !== 'compute') continue;
      if (name in t.sharedParams) out[name] = t.sharedParams[name];
      else if (name in overrides) out[name] = overrides[name];
      // else omit → server applies the schema default (better cache reuse).
    }
    return out;
  }

  /** The catalog entry of the (first) metric bound to each role. */
  _roleEntries(spec) {
    return Object.entries(spec.metrics || {}).map(([role, id]) => {
      const first = Array.isArray(id) ? id[0] : id;
      return [role, first && this._catalog.get(first)];
    });
  }

  /** Per-role units to print (x, y, value, mae, …): an energy or force in the
   * unit the first series' dataset has, "[unit]" while it has none. */
  _panelUnits(spec, series) {
    const dsUnits = datasetUnits(series?.[0]?.datasetFp, this._available.datasets);
    const units = {};
    for (const [role, entry] of this._roleEntries(spec))
      units[role] = entry ? resolveUnit(entry.unit, dsUnits) : '';
    // a list of metrics (a table's columns): one unit each, as `role:k`
    for (const [role, val] of Object.entries(spec.metrics || {})) {
      if (Array.isArray(val)) val.forEach((id, k) => {
        const entry = this._catalog.get(id);
        units[`${role}:${k}`] = entry ? resolveUnit(entry.unit, dsUnits) : '';
      });
    }
    return units;
  }

  /** Per-role dimension whose unit the user can name, for the roles that have one. */
  _panelUnitDims(spec) {
    const dims = {};
    for (const [role, entry] of this._roleEntries(spec)) {
      const dim = entry && editableDimension(entry.unit);
      if (dim) dims[role] = dim;
    }
    for (const [role, val] of Object.entries(spec.metrics || {})) {
      if (Array.isArray(val)) val.forEach((id, k) => {
        const dim = this._catalog.get(id) && editableDimension(this._catalog.get(id).unit);
        if (dim) dims[`${role}:${k}`] = dim;
      });
    }
    return dims;
  }

  /** A scatter panel is subbable only when its x metric is per-frame. */
  _isPerFrameScatter(spec) {
    if (spec.kind !== 'scatter') return false;
    const xid = spec.metrics && spec.metrics.x;
    const entry = xid && this._catalog.get(Array.isArray(xid) ? xid[0] : xid);
    return !!entry && entry.shape === 'N_frames';
  }

  // ── per-panel compute controls (retune inputs) ──────────────────────────────
  _buildPanelParams(t, spec, card) {
    const holder = card.params;
    holder.innerHTML = '';
    const hidden = new Set(spec.hidden_params || []);
    // Params driven by any tab-rendered control are hidden from the panel too.
    for (const pn of t.controlParams || []) hidden.add(pn);
    const seen = new Set();
    const ids = Object.values(spec.metrics || {}).flat();
    for (const id of ids) {
      const entry = this._catalog.get(id);
      if (!entry || !entry.parameters) continue;
      for (const [name, p] of Object.entries(entry.parameters)) {
        if (p.role && p.role !== 'compute') continue;
        if (hidden.has(name) || seen.has(`${id}:${name}`)) continue;
        seen.add(`${id}:${name}`);
        this._makeParamControl(t, id, name, p, holder, card);
      }
    }
  }

  _makeParamControl(t, metricId, name, p, holder, card) {
    const wrap = document.createElement('div');
    wrap.className = 'pp-item';
    wrap.dataset.param = name;
    const lbl = document.createElement('label');
    lbl.textContent = p.label || name;
    wrap.appendChild(lbl);

    const ensureOverrides = () => {
      t.overrides = t.overrides || {};
      t.overrides[metricId] = t.overrides[metricId] || {};
    };
    const commit = (v) => {
      ensureOverrides();
      t.overrides[metricId][name] = v;
      this._refreshCard(t, card);
    };

    let input;
    if (p.type === 'choice') {
      input = document.createElement('select');
      for (const c of p.choices || []) {
        const o = document.createElement('option');
        o.value = o.textContent = c;
        input.appendChild(o);
      }
      input.value = p.default;
      input.addEventListener('change', () => commit(input.value));
    } else if (p.type === 'bool') {
      input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = !!p.default;
      input.addEventListener('change', () => commit(input.checked));
    } else {
      input = document.createElement('input');
      input.type = 'number';
      input.value = String(p.default ?? 0);
      if (p.min != null) input.min = String(p.min);
      if (p.max != null) input.max = String(p.max);
      input.step = p.type === 'int' ? '1' : 'any';
      input.addEventListener('change', () => {
        const value = p.type === 'int' ? parseInt(input.value, 10) : parseFloat(input.value);
        commit(value);
        p.default = value;
      })
    }
    wrap.appendChild(input);
    holder.appendChild(wrap);
  }

  /** Re-fetch + redraw one panel card in place (a param control changed). */
  _refreshCard(t, card) {
    const spec = t.spec.panels.find((p) =>
      (p.title || '') === (card.el.dataset.title || '') && p.kind === card.el.dataset.kind);
    if (spec) this._fetchAndRenderPanel(t, spec, card, this._renderToken);
  }

  // ── SUB: a subset that follows the plot's zoom ─────────────────────────────
  //
  // Ticking SUB on a plot makes a subset of each series it
  // draws: the frames inside the plot's visible range. It follows every zoom
  // and pan; unticking hides it. The tab's other plots and tables draw the
  // subset instead of the full data and redraw as it moves; the plot with SUB
  // keeps the full data and its zoom. One plot per tab has SUB at a time. The server works out the frames from the
  // view (ffast/session/subbing.py), so a density plot subs by value and a
  // force scatter by structure.

  _wireSub(t, spec, card) {
    const key = t.spec.panels.indexOf(spec);
    if (!card.subToggle) {
      const toggle = document.createElement('label');
      toggle.className = 'sub-toggle';
      toggle.title = 'Make a subset of the structures on screen; it follows the zoom';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.addEventListener('change', () => {
        const before = this._drawnKeys(t);
        // One plot per tab has SUB: the tab's other plots draw its subset,
        // and two plots would keep subsetting each other's.
        this._untickSub(t);
        if (cb.checked) {
          // The main view moves to the subset; pinning keeps this tab on
          // the data it shows, rather than following the rail to the subset.
          this._pinSeries(t);
          t.subbing.set(key, []);
          t.subTicked = key;
          // A plot that was drawing another plot's subset first redraws on
          // the full data; drawn, it sends its view (below).
          const made = new Set(this._subsets.values());
          if ((card.series || []).some((x) => made.has(x.datasetFp)))
            this._fetchAndRenderPanel(t, spec, card, this._renderToken);
          else this._sendSubViews(t, spec, card);
        }
        this._keep(t);
        if (this._activeTab() === t) this._redrawIfChanged(t, before);
      });
      toggle.append(cb, document.createTextNode('Sub'));
      card.title.insertBefore(toggle, card.more);
      card.subToggle = cb;
      const freeze = document.createElement('button');
      freeze.type = 'button';
      freeze.className = 'sub-freeze';
      freeze.textContent = 'Freeze';
      freeze.title = 'Keep these frames; the subset stops following the zoom';
      freeze.addEventListener('click', () => this._freezeSubsets(t, spec));
      card.title.insertBefore(freeze, card.more);
      card.subFreeze = freeze;
      cb.addEventListener('change', () => { freeze.hidden = !cb.checked; });
    }
    card.subToggle.checked = t.subbing.has(key);
    card.subFreeze.hidden = !card.subToggle.checked;

    const el = card.body;   // its relayout listeners were cleared by the caller
    el.on('plotly_relayout', () => {
      if (!t.subbing.has(key)) return;
      clearTimeout(card.subTimer);
      card.subTimer = setTimeout(() => this._sendSubViews(t, spec, card), SUB_DELAY_MS);
    });
    // A redrawn plot shows its full range again, so the subsets follow.
    if (t.subbing.has(key)) this._sendSubViews(t, spec, card);
  }

  /** Untick SUB on the tab's plot that has it, hiding its subsets. */
  _untickSub(t) {
    const key = this._subDriver(t);
    if (key == null) return;
    this._hideSubsets(t.subbing.get(key) || [], this._subName(t, t.spec.panels[key]));
    t.subbing.delete(key);
    for (const slot of t.slots) {
      const i = slot.specs.findIndex((sp) => t.spec.panels.indexOf(sp) === key);
      const toggle = i >= 0 ? slot.cards?.[i]?.subToggle : null;
      if (toggle) toggle.checked = false;
      if (i >= 0 && slot.cards?.[i]?.subFreeze) slot.cards[i].subFreeze.hidden = true;
    }
  }

  /** Freeze the subsets this plot's SUB made (like the rail ⋯ ▸ Freeze). */
  _freezeSubsets(t, spec) {
    const key = t.spec.panels.indexOf(spec);
    const name = this._subName(t, spec);
    for (const s of t.subbing.get(key) || []) {
      const fp = this._subsets.get(`${s.parentFp}|${s.modelFp || ''}|${name}`);
      if (fp) this._onFreeze?.(fp);
    }
  }

  /** A subset is named after the plot it was made in. */
  _subName(t, spec) {
    return spec.title || t.spec.name;
  }

  /** Fix the tab's datasets and predictions to what it draws now. */
  _pinSeries(t) {
    if (!t.selectedDatasets) t.selectedDatasets = [...this._tabDatasets(t)];
    const models = this._tabModels(t).filter(Boolean);
    if (!t.selectedModels && models.length) t.selectedModels = models;
    this._renderSeriesSelector(t);
  }

  /** Send the plot's view for each series it draws; hide the subsets of
   * series it no longer draws. The first view after SUB is ticked says so,
   * so the main view moves to the subset. */
  _sendSubViews(t, spec, card) {
    const key = t.spec.panels.indexOf(spec);
    const range = plotRange(card.body);
    if (!this._onSub || !range || !t.subbing.has(key)) return;
    const tick = t.subTicked === key;
    if (tick) t.subTicked = null;
    const name = this._subName(t, spec);
    const ids = Object.values(spec.metrics || {}).flat();
    const view = {
      kind: spec.kind,
      metrics: spec.metrics || {},
      params: Object.fromEntries(ids.map((id) => [id, this._metricParams(t, spec, id)])),
      ...range,
    };
    const series = (card.series || []).map((s) => ({ parentFp: s.datasetFp, modelFp: s.modelFp }));
    const same = (a, b) => a.parentFp === b.parentFp && a.modelFp === b.modelFp;
    this._hideSubsets((t.subbing.get(key) || []).filter((old) => !series.some((s) => same(s, old))), name);
    t.subbing.set(key, series);
    if (series.length) this._onSub({ name, series, view, tick });
  }

  _hideSubsets(series, name) {
    if (series.length) this._onSub?.({ name, series, active: false });
  }

  /** The frame of the live-run mark under a mouse click on a score plot, or null. */
  _tickFrame(card, spec, ev) {
    const el = card.body, fl = el._fullLayout;
    if (!fl?.xaxis || !fl._size) return null;
    const shown = new Set((el.layout?.shapes || [])
      .filter((s) => s.visible !== false && String(s.name || '').startsWith('tick:'))
      .map((s) => Number(s.name.slice(5))));
    const r = el.getBoundingClientRect();
    const ticks = spec.overlays.ticks;
    const hit = tickAt(ticks, shown, ev.clientX - r.left - fl._size.l, ev.clientY - r.top - fl._size.t,
      { h: fl._size.h, xToPx: (x) => fl.xaxis.l2p(x) });
    if (hit) return this._liveFrameAt(card, ticks[hit.tick].curve ?? 0, hit.x);
    // The translucent onset-to-confirmation band is clickable too. Ignore
    // hidden bands and clicks outside the plot (including its legend).
    const px = ev.clientX - r.left - fl._size.l, py = ev.clientY - r.top - fl._size.t;
    if (px < 0 || px > fl._size.w || py < 0 || py > fl._size.h) return null;
    const shapes = el.layout?.shapes || [];
    const band = (spec.overlays.bands || []).find((b, k) =>
      shapes.some((shape) => shape.name === `alarm-band:${k}` && shape.visible !== false)
      && px >= fl.xaxis.l2p(b.x0) && px <= fl.xaxis.l2p(b.x1));
    return band ? this._liveFrameAt(card, band.curve ?? 0, fl.xaxis.p2d(px)) : null;
  }

  /** The frame nearest a step in one live series. */
  _liveFrameAt(card, curve, step) {
    const src = (card.series || [])[curve];
    const steps = src?.data?.x?.nd?.values;
    if (!steps?.length) return null;
    let i = 0;
    for (let j = 1; j < steps.length; j++) {
      if (Math.abs(steps[j] - step) < Math.abs(steps[i] - step)) i = j;
    }
    if (src.frames?.[i] == null) return null;
    return { datasetFp: src.datasetFp, modelFp: src.modelFp, frame: src.frames[i] };
  }

  // ── subbing + point→frame (PRD 61-63) ──────────────────────────────────────
  _wirePanelInteractions(t, spec, card) {
    const el = card.body;
    if (!PLOT_KINDS.has(spec.kind) || typeof el.on !== 'function') return;
    if (el.removeAllListeners) el.removeAllListeners('plotly_relayout');
    el.on('plotly_relayout', (ev) => {
      // A dragged legend is saved in the tab (web parity plan 2026-10-29).
      if (ev && 'legend.x' in ev && 'legend.y' in ev) {
        const r3 = (v) => Math.round(Number(v) * 1000) / 1000;
        this._onPanelLabels?.(t.spec.name, spec, { legend_x: r3(ev['legend.x']), legend_y: r3(ev['legend.y']) });
      }
      rethin(el, ev);   // the points the new view shows
      if (card.series?.some((s) => s.thin)) this._scheduleView(t, spec, card, ev);
      this._noteZoom(t, spec, card);
      this._linkX(t, spec, ev);
    });
    // A live run's scores are not a metric of a dataset: no subset from them.
    if (SUB_KINDS.has(spec.kind) && !spec.liveScores) this._wireSub(t, spec, card);
    clearTimeout(card.frameTimer);   // a click on the plot this one replaces
    card.frameTimer = null;
    if (!el._subInfo || !el._subInfo.perFrame) return;   // only per-frame kinds

    // Open a clicked frame, after Plotly's double-click delay.
    const openFrame = (frame, mouse) => {
      clearTimeout(card.frameTimer);
      card.frameTimer = null;
      // Plotly reports a double-click's second click too, after plotly_doubleclick.
      if (mouse?.detail > 1) return;
      card.frameTimer = setTimeout(() => {
        card.frameTimer = null;
        this._onPointFrame(frame);
        // A live run's frame is also explained (ADR 0087): its attribution is asked for.
        const run = this.liveRunOf(frame.datasetFp);
        if (run) this._onLive?.({ action: 'attribute', run_id: run.run_id, frame: frame.frame });
      }, PLOT_CONFIG.doubleClickDelay ?? 300);
    };

    // Point → frame: click a per-frame point to show that structure (PRD 63).
    // The click names the clicked curve's data, so the structure is looked up
    // in the 3D view that holds it (ADR 0056 rule 4). It waits out Plotly's
    // double-click delay: a double-click (back to the first view) opens nothing.
    if (el.removeAllListeners) el.removeAllListeners('plotly_click');
    el.on('plotly_click', (ev) => {
      const info = el._subInfo;
      const pt = ev && ev.points && ev.points[0];
      if (!pt || !info) return;
      if (info.dataCurveCount != null && pt.curveNumber >= info.dataCurveCount) {
        // Alarm lines are overlay series on y2, not scientific curve points.
        const line = spec.liveScores && (spec.overlays?.lines || []).find((l) => l.label === pt.data?.name && l.x === pt.x);
        const frame = line && this._liveFrameAt(card, line.curve ?? 0, line.x);
        if (frame) openFrame(frame, ev.event);
        return;
      }
      const src = (card.series || [])[(info.curveSeries || [])[pt.curveNumber] ?? 0];
      // A point that is not a frame carries its frame in its data (a parity dot).
      // A live run's points are its scored frames, in order (ADR 0087).
      // A thinned trace draws some of its points (thin.js): back to the full data.
      const drawnAt = fullIndex(el, pt.curveNumber, pt.pointIndex);
      // A thinned view's points name their frames (ADR 0099).
      const point = src?.thin ? src.thin.idx[drawnAt] : drawnAt;
      const index = info.frameField != null ? pt.customdata?.[info.frameField]
        : src?.frames ? src.frames[point] : point;
      if (index == null || !src) return;
      openFrame({ datasetFp: src.datasetFp, modelFp: src.modelFp, frame: index }, ev.event);
    });
    // A live run's marks (ticks) are paths, not points: Plotly reports no
    // click on them, but passes the plain click on, after plotly_click. A
    // click on a mark opens the mark's frame, as a click on the curve does
    // (and wins over a curve point picked by the same click).
    if (card.tickClick) el.removeEventListener('click', card.tickClick);
    card.tickClick = null;
    if (spec.liveScores && spec.overlays?.ticks?.length) {
      card.tickClick = (ev) => {
        const frame = this._tickFrame(card, spec, ev);
        if (frame) openFrame(frame, ev);
      };
      el.addEventListener('click', card.tickClick);
    }
    if (el.removeAllListeners) el.removeAllListeners('plotly_doubleclick');
    el.on('plotly_doubleclick', () => {
      clearTimeout(card.frameTimer);
      card.frameTimer = null;
    });
    // A peak label opens its frame, as a click on the curve does.
    if (el._peaks && el._peaks.length) {
      if (el.removeAllListeners) el.removeAllListeners('plotly_clickannotation');
      el.on('plotly_clickannotation', (ev) => {
        const peak = el._peaks[ev.index];
        const src = (card.series || [])[(el._subInfo.curveSeries || [])[0] ?? 0];
        if (peak && src) this._onPointFrame({ datasetFp: src.datasetFp, modelFp: src.modelFp, frame: peak.frame });
      });
    }
  }
}

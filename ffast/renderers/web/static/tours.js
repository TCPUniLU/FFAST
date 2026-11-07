// The guided tours (ADR 0088). Each tour is data for tour_runner.js: steps
// that name what to light up, what to say, and which app event ends them.
// The functions here only read the app, except the undo of First steps'
// step 1, which deletes the example pair it loaded, and the loading tour,
// which opens and closes the file browser.

import { loadLayout } from './layout_state.js';
import { joinPath } from './remote_browser.js';

/** The example files the loading tour loads, one round per pair, each in its
 * example's folder under examples/data. Both pairs use eV and eV/Å, so Basic
 * Errors never plots them in different units. Change the round texts below
 * (`ROUND_TEXT`) with them. */
export const TOUR_FILES = [
  { folder: 'variable-sized molecular', dataset: 'dataset.xyz', prediction: 'prediction.xyz' },
  { folder: 'variable-sized periodic', dataset: 'mptrj_sampled_over_200.extxyz',
    prediction: 'predictions_mptrj_sampled.xyz' },
];

/** What the loading tour says about each round's files. */
const ROUND_TEXT = [
  { dataset: '100 small molecules from the SPICE dataset, with their reference energies and forces.' },
  { dataset: '202 frames of crystals from the Materials Project, with 1 to 96 atoms each.',
    folder: 'It holds crystals: atoms in a box that repeats in every direction.',
    prediction: ' Leave reference_plus_noise.xyz: it is the reference with noise added, a check rather than a model.' },
];

/** The guided tours (ADR 0088): data for tour_runner.js, reading `app`. */
export function buildTours(app) {
  /** The pair this run of the tour loaded, from its example-loaded event. */
  let loadedHere = null;
  app.tourEvents.addEventListener('example-loaded', (e) => { loadedHere = e.detail; });

  /** Show the tab with the main 3D view, if another tab is on screen. */
  const toMainView = () => {
    if (!app._activePanel?.linked) app._selectTab(app._recentMainViewTabId());
  };
  /** The main 3D view with the settings sidebar open. */
  const withSidebar = () => {
    toMainView();
    if (document.getElementById('content').classList.contains('sidebar-hidden')) app._setSidebarHidden(false, false);
  };
  const view = () => app._activePanel?.viewport ?? null;

  // Basic Errors (a built-in tab; a user's own tabs can hide or replace it).
  const BASIC = 'Basic Errors';
  const TIMELINE = 'Forces MAE timeline';
  const tabNamed = (name) => app._analysis.tabList.find((t) => t.name === name);
  const onBasic = () => tabNamed(BASIC)?.id === app._activeTab;
  const hasTimeline = () => {
    const tab = tabNamed(BASIC);
    return !!tab && app._analysis.tab(tab.id).spec.panels.some((p) => p.title === TIMELINE);
  };
  const toBasic = () => { if (!onBasic()) app._selectTab(tabNamed(BASIC).id); };
  const activeTab = () => document.querySelector('#tabpanels .tabpanel.active');
  const timeline = () => document.querySelector(
    `#tabpanels .tabpanel.active .analysis-panel[data-title="${TIMELINE}"]`);
  /** The Load Example button; once clicked, the 3D view opens with the
   * dataset and hides it before the prediction is in, so the view is lit. */
  const exampleButton = () => {
    const btn = document.getElementById('empty-example-btn');
    return btn?.getClientRects().length || !app._exampleWatch ? btn : view();
  };

  const steps = {
    loadExample: {
      targets: [exampleButton], side: 'bottom', title: 'Load the example',
      body: 'Start with a small example: a dataset of 100 small molecules and one model’s predictions for them.',
      until: 'example-loaded', failOn: 'example-load-failed', wait: 'Click Load Example',
      skip: () => app._datasets.size > 0,
      undo: async () => {
        if (!loadedHere) return;
        await app._removeLoaded(loadedHere.datasetFp, loadedHere.modelFp);
        loadedHere = null;
      },
    },
    oneFrame: {
      targets: [view], side: 'inside', title: 'One frame of the dataset',
      body: 'These are the atoms of one geometry. Drag to rotate. Scroll to zoom.',
      prepare: toMainView,
    },
    slider: {
      targets: ['#frame-slider'], side: 'top', title: 'Move through frames',
      body: 'Drag the slider. Each frame is one geometry from the file, with its reference energy and forces.',
      until: 'frame-changed', wait: 'Drag the slider', stay: true, prepare: toMainView,
    },
    loaded: {
      targets: ['#dataset-list', '#model-list'], side: 'right', title: 'What is loaded',
      body: 'Datasets hold the reference geometries, energies and forces. Predictions are one model’s answers for the same frames.',
    },
    forceError: {
      targets: ['#quick-styles [data-action="style-force"]'], side: 'left', title: 'Show where the model is wrong',
      body: 'Click Force error. It colours each atom by how far the model’s force is from the reference force.',
      until: 'quick-style-applied', wait: 'Click Force error', prepare: withSidebar,
    },
    readColours: {
      targets: [view, '#frame-slider'], side: 'inside', title: 'Read the colours',
      body: 'Blue atoms are close to the reference. The colours run through green and yellow to red, where the force error is largest. The colour bar gives the scale. Drag the slider to see it change between frames.',
      prepare: toMainView,
    },
    openBasic: {
      targets: [`#tabbar .tab[data-name="${BASIC}"]`], side: 'bottom', title: 'Open the error plots',
      body: 'The tabs above the view hold plots of the whole dataset. Click Basic Errors.',
      until: 'tab-selected', accept: (d) => d.name === BASIC, wait: 'Click Basic Errors',
      done: onBasic, skip: () => !hasTimeline(),
    },
    basicPlots: {
      targets: [activeTab], side: 'inside', title: 'Errors over the whole dataset',
      body: 'Each panel measures the model’s error over all frames. The distribution shows how many frames have each size of force error. The timeline shows the error frame by frame, so its peaks are the frames where the model does worst.',
      prepare: toBasic, skip: () => !hasTimeline(),
    },
    clickPoint: {
      targets: [timeline], side: 'left', title: 'Open a bad frame',
      body: 'Click a high point on the Forces MAE timeline. FFAST opens that frame in the 3D view.',
      until: 'point-picked', wait: 'Click a point',
      prepare: () => { toBasic(); timeline()?.scrollIntoView({ block: 'nearest' }); },
      skip: () => !hasTimeline(),
    },
    pickedFrame: {
      targets: [view, '#frame-slider'], side: 'inside', title: 'The frame you clicked',
      body: 'The 3D view now shows the frame you clicked, coloured by force error, so you can see which atoms the model gets wrong. Later, to look at another frame, go back to Basic Errors and click another point. Click Next to go on.',
      prepare: toMainView, skip: () => !hasTimeline(),
    },
    hints: {
      targets: ['#hint-bar'], side: 'top', title: 'Next steps',
      body: 'This bar suggests what to try next. To learn how to load your own files, take File ▸ Tour: Load a dataset and a prediction. Every tour is in the File menu.',
      skip: () => (loadLayout().hintsDismissed || []).includes('with-prediction'),
    },
  };

  const firstSteps = {
    id: 'first-steps', title: 'First steps', about: '11 steps', isDefault: true,
    unavailable: () => app._needsControl()
      || (app._datasets.size && !(app._currentDatasetFp && app._currentModelFp)
        ? 'Open a dataset with a prediction first, or start with nothing loaded' : ''),
    onStart: () => { loadedHere = null; },
    steps: [steps.loadExample, steps.oneFrame, steps.slider, steps.loaded,
      steps.forceError, steps.readColours,
      steps.openBasic, steps.basicPlots, steps.clickPoint, steps.pickedFrame, steps.hints],
  };
  return [firstSteps, loadingTour(app, { tabNamed, BASIC })];
}

/** The tour "Load a dataset and a prediction": two rounds of loading through
 * the file browser (TOUR_FILES), then both pairs on Basic Errors. */
function loadingTour(app, { tabNamed, BASIC }) {
  /** What this run loaded, in order: fingerprints. */
  const own = { datasets: [], predictions: [] };
  const running = () => app._tourId === 'load-data';
  app.tourEvents.addEventListener('dataset-loaded', (e) => { if (running()) own.datasets.push(e.detail.fp); });
  app.tourEvents.addEventListener('prediction-loaded', (e) => { if (running()) own.predictions.push(e.detail.fp); });

  const browser = app._browser;
  const root = () => app._examples?.folder ?? null;
  const exampleDir = (k) => joinPath(root(), TOUR_FILES[k].folder);
  const examplePath = (k, name) => joinPath(exampleDir(k), name);
  const dsName = (fp) => app._datasets.get(fp)?.name || String(fp).slice(0, 8);
  const modelName = (fp) => app._models.get(fp)?.name || String(fp).slice(0, 8);
  const closeBrowser = () => { if (browser.mode() !== null) browser.close(); };
  /** The dialog open in `mode`; opened in the examples folder when it is not. */
  const ensureBrowser = (mode) => { if (browser.mode() !== mode) browser.open(mode, { folder: root() }); };
  /** The dialog open in `mode`, showing round k's folder. */
  const ensureIn = (mode, k) => {
    if (browser.mode() !== mode) browser.open(mode, { folder: exampleDir(k) });
    else if (browser.folder() !== exampleDir(k)) browser.goTo(exampleDir(k));
  };
  const loadedYet = (mode, k) => (mode === 'dataset' ? own.datasets : own.predictions).length > k;
  const $ = (id) => document.getElementById(id);
  const cancelled = (title) => (d) => (d.name
    ? `${d.name} is loaded already. Delete it, then take the tour again.`
    : `Tour stopped: “${title}” was cancelled. Take the tour again from the File menu.`);

  /** "Click +": the dialog opens in the examples folder. */
  const openStep = (k, mode) => (mode === 'dataset' ? {
    targets: ['#add-dataset-btn'], side: 'right', title: k ? 'Add a second dataset' : 'Open the file browser',
    body: k ? 'FFAST can hold several datasets at once, here molecules and crystals. Click + beside Datasets again.'
      : 'The + beside Datasets opens the file browser. File ▸ Load Dataset… does the same. Click +.',
    until: 'browser-opened', accept: (d) => d.mode === 'dataset', wait: 'Click +',
    prepare: () => { closeBrowser(); browser.startIn(root()); },
  } : {
    targets: ['#add-prediction-btn'], side: 'right', title: k ? 'Load the second prediction' : 'Load its prediction',
    body: () => `${k ? '' : 'A prediction is one model’s energies and forces for the frames of a dataset. '}Now load the one for ${dsName(own.datasets[k])}. Click + beside Predictions.`,
    until: 'browser-opened', accept: (d) => d.mode === 'prediction', wait: 'Click +',
    prepare: () => { closeBrowser(); browser.startIn(root()); },
  });

  /** Open round k's folder in the examples folder. */
  const folderStep = (k, mode) => {
    const name = TOUR_FILES[k].folder;
    return {
      targets: ['#fb-header', '#fb-list'], side: 'right',
      title: mode === 'dataset' ? (k ? 'Open the crystals folder' : 'Open the example’s folder') : 'Open the same folder',
      body: mode === 'dataset'
        ? (k ? `Click ${name}. ${ROUND_TEXT[k].folder}`
          : `The browser shows the folders of the computer that runs FFAST. For this tour it opens in FFAST’s examples folder, with one folder per example. Click ${name}. With your own data, you would click folders like this, or type a path at the top.`)
        : `A prediction file sits next to its dataset. Click ${name} again.`,
      until: 'folder-listed', accept: (d) => d.mode === mode && d.path === exampleDir(k),
      refused: (d) => (d.path === root() ? `Click ${name}` : `Click ↑, then ${name}`),
      wait: `Click ${name}`,
      done: () => browser.mode() === mode && browser.folder() === exampleDir(k),
      prepare: () => ensureBrowser(mode),
    };
  };

  /** Pick round k's file in its folder. */
  const pickStep = (k, mode) => {
    const file = TOUR_FILES[k][mode];
    return {
      targets: ['#fb-header', '#fb-list'], side: 'right',
      title: mode === 'dataset' ? (k ? 'Find the second dataset file' : 'Find the dataset file') : 'Find the prediction file',
      body: mode === 'dataset' ? `Click ${file}: ${ROUND_TEXT[k].dataset}`
        : () => `Click ${file}: one model’s predictions for the frames of ${dsName(own.datasets[k])}. FFAST reads the energy and force columns in it.${ROUND_TEXT[k].prediction || ''}`,
      until: 'file-selected', accept: (d) => d.mode === mode && d.path === examplePath(k, file),
      refused: () => `This tour uses ${file}. Click it.`, wait: `Click ${file}`,
      prepare: () => ensureIn(mode, k),
    };
  };

  /** Load: Cancel ends the tour. Once Load is clicked the dialog closes, so the
   * list the file appears in is lit instead (also on Back from a later step). */
  const loadStep = (k, mode) => {
    const title = mode === 'dataset' ? (k ? 'Load the second dataset' : 'Load the dataset')
      : (k ? 'Load the second prediction' : 'Load the prediction');
    const list = mode === 'dataset' ? 'dataset-list' : 'model-list';
    return {
      targets: [() => (browser.mode() === mode ? $('fb-cancel') : $(list)),
        () => (browser.mode() === mode ? $('fb-load') : null)],
      side: 'top', title,
      body: mode === 'dataset' ? 'Click Load. The dataset appears under Datasets. Cancel ends the tour.'
        : 'Click Load. The prediction appears under Predictions. Cancel ends the tour.',
      until: mode === 'dataset' ? 'dataset-loaded' : 'prediction-loaded', wait: 'Click Load',
      failOn: ['browser-cancelled', 'load-failed', 'load-duplicate'], failText: cancelled(title),
      prepare: () => { if (loadedYet(mode, k)) closeBrowser(); else ensureIn(mode, k); },
    };
  };

  const datasetSteps = (k) => [
    openStep(k, 'dataset'), folderStep(k, 'dataset'), pickStep(k, 'dataset'),
    {
      targets: ['#fb-dataset-fields', '#fb-keys'], side: 'top', title: 'Units and frames',
      body: 'Energy unit and Force unit say which units your file uses. Every Nth frame loads only some of the frames of a long file. If the file holds more than one energy or force column, FFAST asks which ones to use.',
      prepare: () => ensureIn('dataset', k),
    },
    loadStep(k, 'dataset'),
  ];
  const predictionSteps = (k) => [
    openStep(k, 'prediction'), folderStep(k, 'prediction'), pickStep(k, 'prediction'),
    {
      targets: ['#fb-target-ds'], side: 'top', title: 'Choose its dataset',
      body: () => `For dataset says which dataset the prediction belongs to. It must be ${dsName(own.datasets[k])}${$('fb-target-ds').value === own.datasets[k] ? ', and it already is.' : '. Choose it.'}`,
      until: 'target-changed', accept: (d) => d.datasetFp === own.datasets[k],
      refused: () => `Choose ${dsName(own.datasets[k])}`, wait: () => `Choose ${dsName(own.datasets[k])}`,
      done: () => $('fb-target-ds').value === own.datasets[k],
      prepare: () => ensureIn('prediction', k),
    },
    {
      targets: ['#fb-energy-key', '#fb-force-key'], side: 'top', title: 'Energy and force columns',
      body: 'Energy and Force say which columns of the file hold the model’s energies and forces. FFAST picks the likeliest ones; change them if your file names them differently.',
      prepare: () => ensureIn('prediction', k),
    },
    loadStep(k, 'prediction'),
  ];

  // Basic Errors with both pairs.
  const basicTab = () => tabNamed(BASIC);
  const onBasic = () => basicTab()?.id === app._activeTab;
  const shown = () => {
    const t = app._analysis.tab(basicTab().id);
    return { datasets: [...app._analysis._tabDatasets(t)], models: app._analysis._tabModels(t).filter(Boolean) };
  };
  const sameSet = (a, b) => a.length === b.length && a.every((x) => b.includes(x));
  const seriesRight = (d) => sameSet(d.datasets, own.datasets) && sameSet(d.models, own.predictions);
  const seriesTodo = (d) => {
    const on = [...own.datasets.filter((x) => !d.datasets.includes(x)).map(dsName),
      ...own.predictions.filter((x) => !d.models.includes(x)).map(modelName)];
    const off = [...d.datasets.filter((x) => !own.datasets.includes(x)).map(dsName),
      ...d.models.filter((x) => !own.predictions.includes(x)).map(modelName)];
    return [on.length ? `Turn on ${on.join(', ')}` : '', off.length ? `Turn off ${off.join(', ')}` : '']
      .filter(Boolean).join('. ');
  };
  const toBasic = () => { closeBrowser(); if (!onBasic()) app._selectTab(basicTab().id); };
  const activeTab = () => document.querySelector('#tabpanels .tabpanel.active');

  const basicSteps = [
    {
      targets: [`#tabbar .tab[data-name="${BASIC}"]`], side: 'bottom', title: 'Compare them in the plots',
      body: 'Basic Errors plots the errors of the whole dataset. Click Basic Errors.',
      until: 'tab-selected', accept: (d) => d.name === BASIC, wait: 'Click Basic Errors',
      done: onBasic, prepare: closeBrowser,
    },
    {
      targets: [() => activeTab()?.querySelector('.ac-series')], side: 'bottom', title: 'Choose what the plots show',
      body: () => `These buttons choose what this tab plots. It starts with the dataset selected in the list and its predictions. Turn on ${own.datasets.map(dsName).join(' and ')}, and ${own.predictions.map(modelName).join(' and ')}; turn off anything else.`,
      until: 'series-changed', accept: (d) => d.tab === BASIC && seriesRight(d), refused: (d) => seriesTodo(d),
      wait: () => seriesTodo(shown()), done: () => seriesRight(shown()), prepare: toBasic,
    },
    {
      targets: [activeTab], side: 'inside', title: 'One series per pair',
      body: () => `Each plot now shows two series: ${dsName(own.datasets[0])} with ${modelName(own.predictions[0])}, and ${dsName(own.datasets[1])} with ${modelName(own.predictions[1])}. A prediction is only plotted with its own dataset, so the molecules and the crystals never mix. The legend names each pair.`,
      prepare: toBasic,
    },
  ];

  return {
    id: 'load-data', title: 'Load a dataset and a prediction', about: '25 steps',
    unavailable: () => {
      const control = app._needsControl();
      if (control) return control;
      if (!app._examples) return 'This server has no example files';
      for (const round of TOUR_FILES) {
        for (const file of [round.dataset, round.prediction]) {
          const rel = `${round.folder}/${file}`;
          if (!app._examples.files.includes(rel)) return `Needs ${rel} in the examples folder`;
        }
      }
      if (app._exampleFps.size) return 'The example is loaded: delete it first';
      if (!basicTab()) return `Needs the ${BASIC} tab`;
      return '';
    },
    onStart: () => { own.datasets = []; own.predictions = []; },
    steps: [...datasetSteps(0), ...predictionSteps(0), ...datasetSteps(1), ...predictionSteps(1), ...basicSteps],
  };
}

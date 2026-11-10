// The guided tours (ADR 0088). Each tour is data for tour_runner.js: steps
// that name what to light up, what to say, and which app event ends them.
// Steps read app state; actions load examples, control a practice run, or
// undo an earlier action. The loading tour uses the real file browser.

import { loadLayout } from './layout_state.js';
import { joinPath } from './remote_browser.js';
import { OUT } from './events.js';

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
  const watch = lammpsTour(app);
  return [firstSteps, loadingTour(app, { tabNamed, BASIC }), watch, ownLammpsTour(app, watch)];
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


/** Watch a real wrapper/scorer run, with recorded frames instead of LAMMPS. */
function lammpsTour(app) {
  const run = () => app._practiceRun;
  const tab = () => document.querySelector('#tabpanels .tabpanel.active');
  const panel = (title) => tab()?.querySelector(`.analysis-panel[data-title="${title}"]`) ?? null;
  const status = () => panel('Run');
  const score = () => panel('Worst test ÷ its threshold, against step');
  const view = () => app._activePanel?.viewport ?? panel('3D');
  const events = () => panel('Events');
  const send = (event, kw) => app._conn?.send(event, kw, []);
  const sameRun = (d) => d.run_id === app._practiceId;
  const gateWait = () => {
    if (run()?.paused) return 'Click Heat it up';
    const gate = run()?.practice?.gate_step;
    const step = run()?.producer?.last_step ?? 0;
    // The shared tutorial settings arrive with the run; every 100 steps is one frame.
    const seconds = Math.ceil(Math.max(0, (gate - step) / 100) * (run()?.practice?.sleep_s ?? 0));
    return `Calm part: step ${step.toLocaleString('en-US')} of ${gate?.toLocaleString('en-US') ?? '?'} · about ${seconds} s to the pause`;
  };
  const steps = [
    { title: 'Start a practice run', targets: ['#tabbar'], side: 'bottom',
      body: 'This tutorial replays a toy aspirin run recorded on an HPC cluster. The real FFAST wrapper and scorer watch it, as they would watch a LAMMPS run. Therefore, no LAMMPS is needed.',
      actions: [{ label: 'Start practice run', primary: true, run: () => send(OUT.START_PRACTICE_RUN, { kind: 'replay' }) }],
      until: 'live-first-frame', accept: sameRun, wait: 'Click Start practice run' },
    { title: 'The run, one row', targets: [status], side: 'bottom',
      links: [{ label: 'Atom-order warning: meaning and fix (README, opens in a new tab)', href: 'https://github.com/TCPUniLU/FFAST/blob/main/README.md' }],
      body: 'LAMMPS step: how far LAMMPS got, with a bar towards the planned end. Scored to: how far the scorer got. Atom order: this recording is unverified; its first frame passes the bond check, but it has no saved data-file proof. State: alarms and how the run ended. “practice” marks runs made by a tour.' },
    { title: 'What LAMMPS writes', targets: [view], side: 'right',
      body: 'The run’s own 3D view follows the newest frame. Here it is the toy aspirin at 300 K, the temperature the reference was made from. Selecting an older frame pauses following; return to the newest frame to resume.' },
    { title: 'The score', targets: [score], side: 'left',
      body: 'Each point is one frame: the worst test’s score divided by its threshold. Under the dashed line at 1, the frame looks like the reference. An orange tick marks a frame over 1; one stray tick is normal.' },
    { title: 'Events', targets: [events], side: 'top',
      body: 'Everything that happens to the run, in order: start, the atom-order check, alarms, stop requests. The same list is kept in the run folder.' },
    { title: 'Turn on Stop on alarm', targets: [() => status()?.querySelector('td.tick'), () => status()?.querySelector('th.tick')], side: 'bottom',
      body: 'With this on, FFAST stops LAMMPS when an alarm fires. It is off by default. On a cluster it saves the hours a broken run would waste.',
      until: 'stop-on-alarm-changed', accept: (d) => sameRun(d) && d.on,
      done: () => !!run()?.stop_on_alarm, wait: 'Tick Stop on alarm' },
    { title: 'Heat it up', targets: [status, score], side: 'bottom',
      body: () => (run()?.paused ? 'The run is paused before the heat. ' : 'The run first plays its calm part, then pauses. ')
        + 'Heating takes the molecule from 300 K to 3000 K, far outside what the reference has seen.',
      actions: [{ label: 'Heat it up', primary: true, enabled: () => !!run()?.paused,
        run: () => send(OUT.HEAT_PRACTICE_RUN, { run_id: app._practiceId }) }],
      until: 'practice-heated', accept: sameRun, wait: gateWait },
    { title: 'An alarm', targets: [score, status], side: 'left',
      body: () => run()?.progress?.alarms?.length
        ? 'A chemistry alarm: a bond broke and stayed broken over several frames. One odd frame never raises an alarm; it has to last.'
        : 'Watch the score climb over 1. An alarm needs the trouble to last over several frames, not one stray frame.',
      until: 'live-alarm', accept: sameRun, wait: 'Waiting for the alarm',
      done: () => !!run()?.progress?.alarms?.length, stay: true },
    { title: 'It stopped itself', targets: [status, events], side: 'bottom',
      body: () => run()?.end ? `Stop on alarm wrote STOP; the replay saw it at its next check and stopped at step ${run().end.stop_observed_step?.toLocaleString('en-US')}. In the Events panel, “Stop requested” means the alarm asked for a stop, “STOP written” means the wrapper created the stop file, and “Stop observed” means the log confirmed that the replay had stopped.`
        : 'Stop on alarm writes a STOP file. The replay checks it before each frame, as one-process LAMMPS checks it through fix halt.',
      until: 'live-stopped', accept: sameRun, wait: 'Waiting for the run to stop', done: () => !!run()?.end, stay: true },
    { title: 'Go to the onset', targets: [score, view], side: 'left',
      body: 'Look for the vertical violet dotted line labeled “chemistry onset”. It marks the start of the three consecutive flagged frames that confirmed the chemistry alarm. The score may start changing earlier. The solid violet line marks confirmation; the horizontal dashed line at 1 is the score threshold. Hover a line for its label and step, then click the onset line or a point between it and confirmation. The 3D view jumps to that frame.',
      until: 'point-picked', accept: (d) => d.datasetFp === run()?.fingerprint
        && run()?.progress?.alarms?.some((a) => d.frame >= a.onset?.frame && d.frame <= a.confirmation?.frame),
      refused: () => 'Click the dotted line, or a point between it and the solid line', wait: 'Click the dotted onset line', stay: true },
    { title: 'What explains it', targets: [() => panel('Attribution'), view], side: 'right',
      body: 'Attribution lists the coordinates that push the score up at the picked frame. Strength says how much the score would drop if a group looked normal again, in thresholds. The test picker chooses the test; Explains chooses the 3D panel.' },
    { title: 'Two kinds of alarm', targets: [score, events], side: 'left',
      body: 'Violet ticks: chemistry flags (a bond broke or formed). Orange ticks: the score was over 1. Each has its own alarm. Here the chemistry alarm came first and stopped the run.' },
    { title: 'Keep the practice run?', targets: ['#tabbar', status], side: 'bottom',
      body: 'It stays in Live Runs, marked “practice”, until you delete it. You can take this tour again from the LAMMPS menu.',
      actions: [{ label: 'Keep it', run: () => app._tourSignal('practice-kept', { run_id: app._practiceId }) },
        { label: 'Delete practice run', run: () => send(OUT.DELETE_PRACTICE_RUN, { run_id: app._practiceId }) }],
      until: 'practice-finished', noNext: true, wait: 'Choose Keep it or Delete practice run' },
  ];
  for (const step of steps) step.failOn = 'practice-failed';
  for (const name of ['practice-kept', 'practice-deleted']) app.tourEvents.addEventListener(name, (e) => {
    if (sameRun(e.detail)) app._tourSignal('practice-finished', e.detail);
  });
  return { id: 'watch-lammps', title: 'Watch a LAMMPS run', about: '13 steps', lammps: true,
    unavailable: () => app._needsControl(),
    onStart: () => { app._practiceId = null; app._practiceRun = null; }, steps };
}

/** Run the copied input through Run with FFAST and a real local LAMMPS. */
function ownLammpsTour(app, watch) {
  const run = () => app._practiceRun;
  const dialog = () => app._live()._rwf;
  const $ = (q) => document.querySelector(q);
  const modal = () => $('#run-with-ffast-dialog');
  const tab = () => $('#tabpanels .tabpanel.active');
  const panel = (title) => tab()?.querySelector(`.analysis-panel[data-title="${title}"]`) ?? null;
  const next = () => modal()?.querySelector('.rwf-go');
  const send = (event, kw = {}) => app._conn?.send(event, kw, []);
  const sameRun = (d) => d.run_id === app._practiceId;
  const chooseDone = () => !!app._practiceInput && dialog()?.folder === app._practiceInput.folder;
  const steps = [
    { title: 'Open Run with FFAST', targets: ['#lammps-menu-btn', () => $('#lammps-menu-list:not([hidden])')], side: 'bottom',
      body: () => app._practiceInput
        ? `The tour copied the tutorial input to ${app._practiceInput.folder}. Open LAMMPS → Run with FFAST….`
        : 'Preparing a working copy of the toy aspirin input. Then open LAMMPS → Run with FFAST….',
      until: 'rwf-open', wait: 'Open LAMMPS → Run with FFAST…', done: () => !!modal() },
    { title: 'Choose the run folder', targets: ['#rwf-folder'], side: 'bottom',
      body: 'Pick the folder that holds the input. For this tour, the working copy is picked for you. Its input, data file and reference will be found when you choose This computer.',
      until: 'rwf-chosen', accept: (d) => d.folder === app._practiceInput?.folder,
      wait: 'Waiting for the copied folder', done: chooseDone, stay: true },
    { title: 'Run it here', targets: ['#rwf-where'], side: 'bottom',
      body: 'On the cluster, FFAST submits a Slurm job. This computer runs LAMMPS here, as one process, without a job script.',
      until: 'rwf-target', accept: (d) => d.target === 'local',
      wait: 'Choose This computer', done: () => dialog()?.target === 'local' },
    { title: 'Check', targets: [() => modal()?.querySelector('.rwf-body'), next], side: 'left',
      body: () => dialog()?.prepared
        ? dialog().prepared.ok
          ? 'The passing build can run FFAST’s snapshot, dump and STOP check. Other builds, if found, show why they failed. You can choose another passing build or give an executable path.'
          : 'No usable LAMMPS was found. Give its executable path and check again, or take “Watch a LAMMPS run”, which needs no LAMMPS.'
        : 'Press Check ›. FFAST looks for LAMMPS on this computer and tries the commands it adds.',
      actions: [{ label: 'Take “Watch a LAMMPS run” instead', enabled: () => !!dialog()?.prepared && !dialog().prepared.ok,
        run: () => {
          app._tourHandle?.stop();
          dialog()?.close();
          app._startTour(watch);
        } }],
      until: 'rwf-checked', accept: (d) => d.ok, wait: 'Press Check ›, or check a LAMMPS path',
      done: () => !!dialog()?.prepared?.ok, stay: true },
    { title: 'The include line', targets: [() => dialog()?.step < 2 ? next() : $('[data-rwf="include"]')], side: 'left',
      body: () => dialog()?.step < 2 ? 'Press Review › to see the copies as diffs.'
        : 'The green include line before the first run loads FFAST’s monitoring: frames for the scorer and a STOP-file check. It comes after the atoms exist and before the dynamics start.',
      until: 'rwf-review', wait: 'Press Review ›', done: () => dialog()?.step === 2, stay: true },
    { title: 'Snapshot, and two tutorial lines', targets: [() => $('[data-rwf="snapshot"]'), () => $('[data-rwf="tutorial"]')], side: 'left',
      body: 'Green, after read_data: a snapshot of the atoms as loaded, used to prove atom order. The shell sleep and GO/STOP wait belong to the tutorial input: they slow the run and pause it before heating. FFAST never adds those lines to your own inputs.' },
    { title: 'The data copy', targets: [() => modal()?.querySelector('.rwf-tabs')], side: 'bottom',
      body: 'FFAST also writes a data file from the reference geometry with atom types in the reference’s order. Open the aspirin.ffast.data tab to read where it came from.',
      until: 'rwf-data-tab', wait: 'Open aspirin.ffast.data', stay: true },
    { title: 'Start it', targets: [next], side: 'top',
      body: 'Write copies and start runs the checked LAMMPS through ffast-lammps. The input you reviewed is its input; the originals stay unchanged. Its live tab opens on the first frame.',
      until: 'live-first-frame', accept: sameRun, wait: 'Click Write copies and start' },
    { ...watch.steps[6],
      body: () => run()?.paused ? 'Your LAMMPS is paused before heating, in the tutorial’s shell wait. Heat it to 3000 K.'
        : 'Your LAMMPS runs the calm part first, then pauses. Heat it up turns on at that pause.' },
    { title: 'An alarm', targets: [() => panel('Worst test ÷ its threshold, against step'), () => panel('Run')], side: 'left',
      body: () => run()?.progress?.alarms?.length
        ? 'An alarm has fired. Stop on alarm is off, so LAMMPS keeps going. The alarm’s kind and step are shown in the run and Events panels; they may differ from the replay on your build.'
        : 'Stop on alarm is off this time. Wait for a lasting anomaly to raise an alarm; LAMMPS will keep going so you can stop it yourself.',
      until: 'live-alarm', accept: sameRun, wait: 'Waiting for an alarm', done: () => !!run()?.progress?.alarms?.length, stay: true },
    { title: 'Stop it yourself', targets: [() => $('.ask-dialog')
        ?? $(`#live-runs-dialog tr[data-run-id="${app._practiceId}"]`)
        ?? $('#live-runs-dialog') ?? panel('Run')?.querySelector('.live-manage') ?? panel('Run')], side: 'bottom',
      body: () => run()?.end
        ? `LAMMPS stopped at step ${run().end.stop_observed_step?.toLocaleString('en-US')}. The alarm marks when the anomaly was confirmed; the stop marks when LAMMPS acted on your request. Events records the request, STOP file and observed halt.`
        : 'Manage runs… opens Live runs. Press Stop now… for this practice run, then confirm with Stop run. FFAST writes STOP and LAMMPS halts at its next fix halt check.',
      until: 'live-stopped', accept: sameRun, wait: 'Manage runs… → Stop now… → Stop run', done: () => !!run()?.end, stay: true },
    { title: 'For your own runs', targets: ['#lammps-menu-btn'], side: 'bottom',
      prepare: () => app._live()._runsDialog?.close(),
      body: 'Use the same dialog with your own run folder. First make a reference from your data with LAMMPS → Make Reference…. FFAST adds monitoring and a start snapshot; it never adds the tutorial’s sleep or pause.' },
    { ...watch.steps[12], body: 'It stays in Live runs, marked “practice”, until you delete it. The copied input stays in the practice-runs folder for you to inspect.' },
  ];
  for (const step of steps) step.failOn = 'practice-failed';
  return { id: 'own-lammps', title: 'Run your own LAMMPS', about: '13 steps', lammps: true,
    unavailable: () => app._needsControl() || (!app._conn?.localLammps || app._conn?._url === app._clusterServerUrl ? 'Needs FFAST running on this computer (Linux or macOS)' : ''),
    onStart: () => {
      app._practiceId = null; app._practiceRun = null; app._practiceInput = null;
      send(OUT.PREPARE_PRACTICE_INPUT);
    }, steps };
}

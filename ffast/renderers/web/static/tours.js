// The guided tours (ADR 0088). Each tour is data for tour_runner.js: steps
// that name what to light up, what to say, and which app event ends them.
// The functions here only read the app, except the undo of step 1, which
// deletes the example pair this tour loaded.

import { loadLayout } from './layout_state.js';

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
      body: 'This bar suggests what to try next. To take a tour again, use the File menu.',
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
  return [firstSteps];
}

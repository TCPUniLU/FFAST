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
      body: 'Bright yellow atoms have the largest force error. Dark purple atoms are close to the reference. The colour bar gives the scale. Drag the slider to see it change between frames.',
      prepare: toMainView,
    },
    hints: {
      targets: ['#hint-bar'], side: 'top', title: 'Next steps',
      body: 'This bar suggests what to try next. To take a tour again, use the File menu.',
      skip: () => (loadLayout().hintsDismissed || []).includes('with-prediction'),
    },
  };

  const firstSteps = {
    id: 'first-steps', title: 'First steps', about: '7 steps', isDefault: true,
    unavailable: () => app._needsControl()
      || (app._datasets.size && !(app._currentDatasetFp && app._currentModelFp)
        ? 'Open a dataset with a prediction first, or start with nothing loaded' : ''),
    onStart: () => { loadedHere = null; },
    steps: [steps.loadExample, steps.oneFrame, steps.slider, steps.loaded,
      steps.forceError, steps.readColours, steps.hints],
  };
  return [firstSteps];
}

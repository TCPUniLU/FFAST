/**
 * Browser-only layout state (ADR 0055 rule 3): which sidebar section is open,
 * whether the sidebar is hidden, and similar choices. It lives in this
 * browser's localStorage and never in a session file — saved sessions are
 * never migrated (ADR 0008), so anything written there would be permanent.
 *
 * Storage can be blocked (private window, cleared or blocked site data), so
 * every access is wrapped: the page then lays itself out from defaults and
 * simply does not remember.
 */

const KEY = 'ffast.layout';

/** @returns {Record<string, unknown>} the stored layout, or {} */
export function loadLayout() {
  try {
    const v = JSON.parse(window.localStorage.getItem(KEY) || '{}');
    return v && typeof v === 'object' && !Array.isArray(v) ? v : {};
  } catch {
    return {};
  }
}

/** Merge `patch` into the stored layout. */
export function saveLayout(patch) {
  try {
    window.localStorage.setItem(KEY, JSON.stringify({ ...loadLayout(), ...patch }));
  } catch {
    // Blocked storage: nothing is remembered, nothing breaks.
  }
}

// What this page's main view shows, so a reload shows it again. It is kept in
// sessionStorage: it survives a reload of this browser tab, and no other tab
// or window sees it.
const MAIN_VIEW_KEY = 'ffast.mainView';

/** @returns {{datasetFp: string, modelFp: string|null}|null} */
export function loadMainView() {
  try {
    const v = JSON.parse(window.sessionStorage.getItem(MAIN_VIEW_KEY) || 'null');
    return v && typeof v.datasetFp === 'string' ? { datasetFp: v.datasetFp, modelFp: v.modelFp || null } : null;
  } catch {
    return null;
  }
}

export function saveMainView(datasetFp, modelFp) {
  try {
    window.sessionStorage.setItem(MAIN_VIEW_KEY, JSON.stringify({ datasetFp, modelFp: modelFp || null }));
  } catch {
    // Blocked storage: a reload shows the first dataset, as with no memory.
  }
}

// How each tab is being explored — its own datasets and predictions, the
// plot with SUB ticked, each plot's zoom — so a reload of this browser tab
// brings it back. sessionStorage too, by tab name; never in a tab file.
const TABS_KEY = 'ffast.tabs';

function loadTabs() {
  try {
    const v = JSON.parse(window.sessionStorage.getItem(TABS_KEY) || '{}');
    return v && typeof v === 'object' && !Array.isArray(v) ? v : {};
  } catch {
    return {};
  }
}

/** @returns {{datasets?: string[]|null, models?: string[]|null, sub?: string|null,
 *   zoom?: Object<string, {x: number[], y: number[]|null, series: string}>}|null} */
export function loadTabState(name) {
  const v = loadTabs()[name];
  return v && typeof v === 'object' ? v : null;
}

export function saveTabState(name, state) {
  try {
    window.sessionStorage.setItem(TABS_KEY, JSON.stringify({ ...loadTabs(), [name]: state }));
  } catch {
    // Blocked storage: a reload starts the tab afresh.
  }
}

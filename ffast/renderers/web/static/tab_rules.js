/**
 * Rules about a browser tab layout as a whole (ADR 0056). Pure functions over
 * the TAB_LAYOUT list, so they test without a server or a canvas.
 */

/** Panel kind of a 3D panel in a tab file (ffast/config/models.py). */
export const KIND_3D = '3d';

/** A linked 3D panel shows the main view; it is the default (rule 1). */
export function isLinked3D(spec) {
  return spec.kind === KIND_3D && (spec.view || 'linked') === 'linked';
}

/** Why the last linked 3D panel cannot go (ADR 0056 rule 6). */
export const MAIN_VIEW_RULE = 'At least one tab must show the main view';

/** The built-in "3D" tab (ffast/config/builtin_tabs/00_3d.toml), drawn
 * before a server has sent its layout and whenever a layout has no linked
 * 3D panel, so the main view and the Load Dataset… button always have a place. */
export const DEFAULT_3D_TAB = Object.freeze({
  name: '3D',
  has_data_selector: true,
  selector: null,
  controls: [],
  panels: [{ kind: KIND_3D, row: 0, col: 0, rowspan: 1, colspan: 1, metrics: {} }],
  column_widths: null,
  row_heights: null,
});

/** @param {{panels?: object[]}} tab */
export function showsMainView(tab) {
  return (tab.panels || []).some(isLinked3D);
}

/** `tabs`, with the default 3D tab put first when no tab shows the main view
 * (a server from before 3D panels, or a layout written by hand). */
export function ensureMainView(tabs) {
  const list = tabs || [];
  return list.some(showsMainView) ? list : [structuredClone(DEFAULT_3D_TAB), ...list];
}

/** Why panel `panelIndex` of tab `tabIndex` cannot be removed, or ''. */
export function whyPanelStays(tabs, tabIndex, panelIndex) {
  const panel = tabs[tabIndex]?.panels?.[panelIndex];
  if (!panel || !isLinked3D(panel)) return '';
  const others = tabs.some((tab, ti) => (tab.panels || []).some(
    (p, pi) => isLinked3D(p) && !(ti === tabIndex && pi === panelIndex)));
  return others ? '' : MAIN_VIEW_RULE;
}

/**
 * What a new independent 3D panel shows (ADR 0056 rule 7): the first pair
 * the tab's picker has selected that no other 3D panel in the tab shows,
 * else the first pair, else null (nothing loaded yet).
 * @param {Array<{datasetFp: string, modelFp: string|null}>} tabPairs
 * @param {Array<{datasetFp: string, modelFp: string|null}>} shownPairs
 */
export function startPair(tabPairs, shownPairs) {
  const same = (a, b) => a.datasetFp === b.datasetFp && (a.modelFp || null) === (b.modelFp || null);
  const pick = tabPairs.find((p) => !shownPairs.some((s) => same(p, s))) || tabPairs[0];
  return pick ? { datasetFp: pick.datasetFp, modelFp: pick.modelFp || null } : null;
}

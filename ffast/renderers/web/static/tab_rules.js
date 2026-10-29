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
  // The whole 24-column grid; analysis.js gives a lone cell the whole height.
  panels: [{ kind: KIND_3D, row: 0, col: 0, rowspan: 6, colspan: 24, metrics: {} }],
  grid: 24,
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

/** The caption drop-down's value for the main view. */
export const MAIN_VIEW_CHOICE = 'main';

/**
 * What a 3D panel's caption drop-down offers (to_fix 7): "Main view", then
 * every dataset and subset. Picking a dataset gives a linked panel a view of
 * its own; "Main view" links an own view back. Both change the tab, so when
 * the tab cannot change now (`noSave`), or this is the last panel showing the
 * main view (`stays`, rule 6), those choices are greyed and the last line,
 * greyed too, says why. An own view may still switch datasets.
 * @param {{linked: boolean, shows: string|null,
 *   datasets: Array<[string, string]>, stays?: string, noSave?: string}} p
 *   `shows`: the own view's dataset; `datasets`: [fingerprint, name] in rail order
 * @returns {{options: Array<{value: string, label: string, disabled: boolean}>, value: string}}
 */
export function panelChoices({ linked, shows, datasets, stays = '', noSave = '' }) {
  const why = linked ? (stays ? `${stays}, so this panel stays on it` : noSave) : noSave;
  const options = [{ value: MAIN_VIEW_CHOICE, label: 'Main view', disabled: !linked && !!noSave }];
  for (const [fp, name] of datasets) options.push({ value: fp, label: name, disabled: linked && !!why });
  const value = linked ? MAIN_VIEW_CHOICE : shows || '';
  if (!linked && !shows) options.push({ value: '', label: 'No dataset yet', disabled: true });
  if (why) options.push({ value: '\u0000why', label: why, disabled: true });
  return { options, value };
}

/**
 * The units the loader offers for a dataset's energies and forces: the common
 * ones, with the units this browser used last listed first (browser storage,
 * never the session file). Nothing is ever pre-filled: a field left empty
 * leaves the dataset's unit unnamed ("[unit]", units.js).
 */

const KEY = 'ffast.recentUnits';
const MAX_RECENT_UNITS = 6;

export const COMMON_UNITS = {
  energy: ['eV', 'kcal/mol', 'Hartree'],
  force: ['eV/Å', 'kcal/mol/Å', 'Hartree/Bohr'],
};

/** The units to offer for `dimension`: `recent` first, then the common ones not in it. */
export function unitChoices(dimension, recent = []) {
  const seen = new Set();
  return [...recent, ...(COMMON_UNITS[dimension] || [])].filter((u) => !seen.has(u) && seen.add(u));
}

/** `unit` moved to the front of `list`, without duplicates, capped. */
export function rememberUnit(list, unit) {
  return [unit, ...list.filter((u) => u !== unit)].slice(0, MAX_RECENT_UNITS);
}

/** The stored units of `dimension`, newest first, or [] when storage is blocked or unreadable. */
export function loadRecentUnits(dimension) {
  try {
    const v = JSON.parse(window.localStorage.getItem(KEY) || '{}')[dimension];
    return Array.isArray(v) ? v.filter((u) => typeof u === 'string').slice(0, MAX_RECENT_UNITS) : [];
  } catch {
    return [];
  }
}

export function saveRecentUnit(dimension, unit) {
  try {
    const all = JSON.parse(window.localStorage.getItem(KEY) || '{}');
    window.localStorage.setItem(KEY, JSON.stringify({
      ...all, [dimension]: rememberUnit(loadRecentUnits(dimension), unit) }));
  } catch {
    // Private window or blocked site data: the list is a convenience only.
  }
}

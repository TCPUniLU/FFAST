/**
 * Metric units as the web client shows them.
 *
 * A built-in metric declares a dimension, not a unit: `ffast/metrics/units.py`
 * says "energy" or "force", and nothing in a dataset file says whether that
 * means eV or kcal/mol. The user names the unit of a dataset's energies and
 * forces (SET_DATASET_UNIT); until they do, the label shows "[unit]", which
 * they click to name it. A real unit that a user metric declares in ffast.toml
 * (`unit = "meV"`) shows as written, and any other dimension shows nothing.
 */

/** The dimension tags of `ffast/metrics/units.py` (kept in step by a test). */
export const DIMENSIONS = new Set([
  'energy', 'force', 'acceleration', 'pressure', 'length', 'angle', 'dimensionless',
]);

/** The dimensions a dataset carries a user-named unit for (DatasetLoader.UNIT_DIMENSIONS). */
export const UNIT_DIMENSIONS = ['energy', 'force'];

/** What a label shows, and the user clicks, while a dimension has no unit. */
export const UNIT_PLACEHOLDER = '[unit]';

/** The dimension a user names the unit of for this declared `unit`, or null. */
export function editableDimension(unit) {
  const u = String(unit ?? '').trim();
  return UNIT_DIMENSIONS.includes(u) ? u : null;
}

/** The unit to print for a metric's declared `unit`, or '' when it is only a dimension. */
export function displayUnit(unit) {
  const u = String(unit ?? '').trim();
  return DIMENSIONS.has(u) ? '' : u;
}

/**
 * The unit to print for a metric's declared `unit` on a dataset with `units`
 * ({energy: 'eV', ...}): the dataset's unit for an energy or force, or the
 * placeholder while it has none.
 */
export function resolveUnit(unit, units) {
  const dim = editableDimension(unit);
  if (dim) return (units && units[dim]) || UNIT_PLACEHOLDER;
  return displayUnit(unit);
}

/** `text (unit)`, `text [unit]` for the placeholder, or `text` alone. */
export function withUnit(text, unit) {
  const u = displayUnit(unit);
  if (!u) return text;
  return u === UNIT_PLACEHOLDER ? `${text} ${u}` : `${text} (${u})`;
}

/**
 * The units of dataset `fp`: those of the loaded dataset it was cut from, as a
 * subset's metadata carries none of its own.
 * @param {string} fp
 * @param {Map<string, object>} datasets fingerprint → REMOTE_DATASET_META
 */
export function datasetUnits(fp, datasets) {
  let meta = datasets.get(fp);
  const seen = new Set([fp]);
  while (meta?.parent && datasets.has(meta.parent) && !seen.has(meta.parent)) {
    seen.add(meta.parent);
    meta = datasets.get(meta.parent);
  }
  return meta?.units || {};
}

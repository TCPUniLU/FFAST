/**
 * Metric units as the web client shows them.
 *
 * A built-in metric declares a dimension, not a unit: `ffast/metrics/units.py`
 * says "energy" or "force", and nothing in a dataset says whether that means eV
 * or kcal/mol. Printing the dimension as if it were the unit gave axis titles
 * such as "Energy MAE (energy)". A dimension tag therefore shows nothing; a
 * real unit that a user metric declares in ffast.toml (`unit = "meV"`) shows as
 * written.
 */

/** The dimension tags of `ffast/metrics/units.py` (kept in step by a test). */
export const DIMENSIONS = new Set([
  'energy', 'force', 'acceleration', 'pressure', 'length', 'angle', 'dimensionless',
]);

/** The unit to print for a metric's declared `unit`, or '' when it is only a dimension. */
export function displayUnit(unit) {
  const u = String(unit ?? '').trim();
  return DIMENSIONS.has(u) ? '' : u;
}

/** `text (unit)`, or `text` alone when there is no unit to print. */
export function withUnit(text, unit) {
  const u = displayUnit(unit);
  return u ? `${text} (${u})` : text;
}

/**
 * One colour per loaded dataset and prediction, the same in every panel and
 * tab. Kept in this browser's localStorage by fingerprint — layout state,
 * never the session file. A new object gets the next palette colour not used
 * by another; the rail's swatch changes it. A subset, live or frozen, has a
 * colour of its own.
 */

const KEY = 'ffast.objectColours';

/** Whose colour a plot series takes: a subset's own (its rail row), else
 * its prediction's, or its dataset's for Ground Truth. */
export function seriesColourOwner(datasetFp, modelFp, isSubset) {
  return isSubset ? datasetFp : modelFp || datasetFp;
}

/**
 * How one panel tells its series apart: whose colour each takes and a style
 * number (0 = plain; else the line's dash, the marker's shape, the bar's
 * hatch). Colour goes to what differs between the series:
 * - their owners (seriesColourOwner) all differ: each its owner's colour, as before;
 * - one prediction on several datasets: each its dataset's colour;
 * - several datasets and several predictions: the prediction's colour, and the
 *   dataset's place among the panel's datasets as the style.
 * @param {Array<{datasetFp: string, modelFp: string|null}>} series
 * @param {(datasetFp: string) => boolean} isSubset
 * @returns {Array<{owner: string, style: number}>}
 */
export function seriesLooks(series, isSubset) {
  const owners = series.map((s) => seriesColourOwner(s.datasetFp, s.modelFp, isSubset(s.datasetFp)));
  if (new Set(owners).size === owners.length) return owners.map((owner) => ({ owner, style: 0 }));
  const models = new Set(series.map((s) => s.modelFp || null));
  if (models.size === 1) return series.map((s) => ({ owner: s.datasetFp, style: 0 }));
  const datasets = [...new Set(series.map((s) => s.datasetFp))];
  return series.map((s, i) => ({ owner: owners[i], style: datasets.indexOf(s.datasetFp) }));
}

/** The first palette position not in `used`; the palette starts over when it runs out. */
export function nextIndex(used, size) {
  const taken = new Set(used);
  for (let i = 0; i < size; i++) if (!taken.has(i)) return i;
  return used.length % size;
}

function load() {
  try {
    const v = JSON.parse(window.localStorage.getItem(KEY) || '{}');
    return v && typeof v === 'object' && !Array.isArray(v) ? v : {};
  } catch {
    return {};
  }
}

function save(map) {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(map));
  } catch {
    // Private window or blocked site data: colours then last for this page only.
  }
}

export class ObjectColours {
  /** @param {() => string[]} palette the colours a new object draws from, in the page's theme */
  constructor(palette) {
    this._palette = palette;
    this._map = load();   // fp → palette position (follows the theme) or a picked hex colour
  }

  /** The colour of `fp`: the next unused palette position the first time, so
   * it follows the light and dark themes until one is picked. */
  get(fp) {
    if (this._map[fp] === undefined) {
      const palette = this._palette();
      this._map[fp] = nextIndex(Object.values(this._map).filter(Number.isInteger), palette.length);
      save(this._map);
    }
    const v = this._map[fp];
    return typeof v === 'number' ? this._palette()[v % this._palette().length] : v;
  }

  set(fp, hex) {
    this._map[fp] = hex;
    save(this._map);
  }
}

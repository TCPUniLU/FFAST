/**
 * One colour per loaded dataset and prediction, the same in every panel and
 * tab. Kept in this browser's localStorage by fingerprint — layout state,
 * never the session file. A new object gets the next palette colour not used
 * by another; the rail's swatch changes it.
 */

const KEY = 'ffast.objectColours';

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

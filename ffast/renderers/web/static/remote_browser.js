/**
 * The remote file browser modal (ADR 0050).
 *
 * The browser has no local filesystem, so choosing a file to load means walking
 * the *server's* directories over `LIST_DIR` and, for predictions, probing the
 * chosen file's energy/force keys over `PROBE_DATASET_KEYS` before the load can
 * be issued. That is a small state machine — current directory, parent, home,
 * selected filename, dataset-vs-prediction mode — which lived as five `_fb*`
 * fields on `FFastApp` alongside everything else.
 *
 * The same browser also picks where a session or an export goes (`pick`).
 *
 * Two pure helpers (`joinPath`, `formatSize`) are exported for direct unit
 * testing; the rest needs the modal's DOM.
 */

import { OUT } from './events.js';
import { answerOf } from './requests.js';
import { icon as drawIcon } from './icons.js';
import { trapFocus } from './dialogs.js';
import { UNIT_PLACEHOLDER } from './units.js';
import { loadRecentUnits, saveRecentUnit, unitChoices } from './recent_units.js';
import { withExtension, EXPORT_FORMATS, exportPlan } from './session_ops.js';
import { loadRecentFiles, rememberFile, saveRecentFiles } from './recent_files.js';
import { setBusyText } from './spinner.js';

/** Join a server-side directory and an entry name. Posix separators: the paths
 *  come from the server, so they are formatted in its convention, not the
 *  browser's. */
export function joinPath(dir, name) {
  if (!dir) return name;
  return dir.endsWith('/') ? dir + name : dir + '/' + name;
}

/** The last part of a folder path: `/a/b/` or `C:\a\b` → `b`. */
export function baseName(path) {
  return path.replace(/[\\/]+$/, '').split(/[\\/]/).pop();
}

/**
 * What a key does in the file list (to_fix_v2). Pure, so it tests without a
 * page. `null` means the key is not the list's.
 * @param {string} key a KeyboardEvent.key
 * @param {{index: number, count: number, isDir: boolean}} at the focused row
 * @returns {{focus: number}|{open: true}|{load: true}|{select: true}|{up: true}|null}
 */
export function listKey(key, { index, count, isDir }) {
  const last = count - 1;
  switch (key) {
    case 'ArrowDown': return count ? { focus: Math.min(index + 1, last) } : null;
    case 'ArrowUp': return count ? { focus: Math.max(index - 1, 0) } : null;
    case 'Home': return count ? { focus: 0 } : null;
    case 'End': return count ? { focus: last } : null;
    case 'ArrowRight': return isDir ? { open: true } : null;
    case 'Enter': return isDir ? { open: true } : { load: true };
    case ' ': return isDir ? { open: true } : { select: true };
    case 'ArrowLeft':
    case 'Backspace': return { up: true };
    default: return null;
  }
}

/** Human-readable byte count for the file list. */
export function formatSize(bytes) {
  if (bytes === null || bytes === undefined) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let n = Number(bytes);
  if (!Number.isFinite(n)) return '';
  let u = 0;
  while (n >= 1024 && u < units.length - 1) {
    n /= 1024;
    u += 1;
  }
  return `${u === 0 ? n : n.toFixed(1)} ${units[u]}`;
}

/**
 * What a typed path asks to list: the folder before the last `/` and the
 * name prefix after it. Null when there is no folder to list yet.
 * @param {string} text
 * @returns {{dir: string, prefix: string}|null}
 */
export function typedParts(text) {
  const i = text.lastIndexOf('/');
  if (i < 0) return null;
  return { dir: i === 0 ? '/' : text.slice(0, i), prefix: text.slice(i + 1) };
}

/** The listing entries a typed prefix leaves, case-insensitively, at most `limit`. */
export function matchEntries(entries, prefix, limit = 8) {
  const p = prefix.toLowerCase();
  return (entries || []).filter((e) => e.name.toLowerCase().startsWith(p)).slice(0, limit);
}

/** The browser also chooses where a session or an export goes (web parity). */
export const PICK_MODES = new Set(['save-folder', 'open-folder', 'save-file']);

/** The server path a pick mode returns, or null while there is none:
 *  the folder on screen for 'open-folder', a name inside it otherwise. */
export function pickPath({ mode, dir, name }) {
  if (!dir) return null;
  if (mode === 'open-folder') return dir;
  const n = String(name ?? '').trim();
  return n ? joinPath(dir, n) : null;
}

/**
 * Decide whether the modal's Load button can be pressed.
 *
 * Pure so the rule is testable without the DOM. A dataset needs only a selected
 * file; a prediction additionally needs an energy or a force name chosen and a
 * target dataset to attach to. A dataset waits for its key check.
 */
export function canLoad({
  selected, mode, forceKey, energyKey = '', targetDataset, probing = false,
  modelType = '', dir = null, name = '',
}) {
  if (PICK_MODES.has(mode)) return pickPath({ mode, dir, name }) !== null;
  if (!selected) return false;
  if (mode === 'model') return Boolean(modelType);
  if (mode === 'prediction') return Boolean(targetDataset && (forceKey || energyKey));
  if (mode === 'dataset') return !probing;
  return true;
}

/**
 * Build the energy/force key options offered after a probe.
 *
 * ASE's extxyz reader routes the standard `energy=` / `forces` columns into a
 * SinglePointCalculator rather than `atoms.info` / `atoms.arrays`, so a plain
 * MACE or DFT dump probes with *empty* key lists but `has_calculator_* = true`.
 * Such a file is still loadable: the option's value is the literal
 * `'energy'`/`'forces'` that the ASE loader already maps to the calculator, so
 * no named key is needed. Returns `[{value, label}]`.
 */
export function keyOptions(keys, { allowNone = false, calculator = false, calculatorValue = '' } = {}) {
  const out = [];
  if (allowNone) out.push({ value: '', label: '— none —' });
  if (calculator) out.push({ value: calculatorValue, label: 'calculator (built-in)' });
  for (const k of keys || []) out.push({ value: k, label: k });
  return out;
}

/**
 * The loader type a file most likely needs: the first type whose
 * extension pattern (`*.npz`) the name ends with, else the catch-all
 * `fallback` when it is offered, else the first type.
 * @param {string} name @param {{name: string, extension: string}[]} types
 * @param {string} fallback @returns {string}
 */
export function typeForFile(name, types, fallback = 'ase (auto)') {
  const lower = name.toLowerCase();
  // A reader lists every ending it takes (`extensions`); older rows have one.
  const endings = (t) => (t.extensions || [t.extension]).filter((e) => e && e !== '*');
  const hit = (types || []).find((t) => endings(t)
    .some((e) => lower.endsWith(e.replace(/^\*/, '').toLowerCase())));
  if (hit) return hit.name;
  if ((types || []).some((t) => t.name === fallback)) return fallback;
  return types?.[0]?.name ?? fallback;
}

/**
 * The reader type a file belongs to: the name of the first reader row (a type
 * the server lists with `reader: true`) that takes the file's ending, else
 * null. A type that claims only `*` (ASE's) is not named: the server's
 * readers for the file's ending choose (ADR 0095).
 */
export function readerTypeFor(name, types) {
  const lower = name.toLowerCase();
  const hit = (types || []).find((t) => t.reader
    && (t.extensions || [t.extension]).some((e) => e && e !== '*'
      && lower.endsWith(e.replace(/^\*/, '').toLowerCase())));
  return hit ? hit.name : null;
}

/**
 * The energy and force name a file with these names starts on, as the server
 * pairs them: the first energy name that also names forces, else each side's
 * first. `''` means none.
 */
export function pairNames(energies, forces) {
  const match = (energies || []).find((n) => (forces || []).includes(n));
  if (match) return [match, match];
  return [(energies || [])[0] ?? '', (forces || [])[0] ?? ''];
}

/** Whether a probed file offers a key choice: more than one energy
 * option or more than one force option, the calculator counting as one
 * . */
export function needsKeyChoice(probe) {
  const e = (probe?.energy_keys || []).length + (probe?.has_calculator_energy ? 1 : 0);
  const f = (probe?.force_keys || []).length + (probe?.has_calculator_forces ? 1 : 0);
  return e > 1 || f > 1;
}

/** The key a picker starts on: the first named key, else the calculator,
 * else ''. */
export function defaultKey(keys, calculator, calculatorValue) {
  if ((keys || []).length) return keys[0];
  return calculator ? calculatorValue : '';
}

/** From this size on (bytes) a file gets the large-file note, (3 GB). */
export const LARGE_FILE_BYTES = 3_000_000_000;

/** The loader's stride number for "every Nth frame": 1 (or less, or not
 * a number) means all frames, which the loader spells 0. */
export function strideToSliceNum(stride) {
  const n = Math.floor(Number(stride));
  return Number.isFinite(n) && n > 1 ? n : 0;
}

/** The large-file note: size, frame count when known, and what every
 * Nth frame keeps. */
export function largeFileNote({ size, n = null, stride = 1, error = null, done = true }) {
  const head = `This file is ${formatSize(size)}`;
  if (error) return `${head}. The frames could not be counted (${error}).`;
  if (n === null || n === undefined) return `${head}. Counting frames…`;
  if (!done) return `${head}. Counting frames… ${n.toLocaleString('en-US')} so far.`;
  const step = strideToSliceNum(stride);
  const all = `${head}: ${n.toLocaleString('en-US')} frames.`;
  if (!step) return `${all} Choose Every Nth or Sample by energy under Frames to load fewer.`;
  return `${all} Every ${step}th frame: ~${Math.max(1, Math.ceil(n / step)).toLocaleString('en-US')}.`;
}

/** Whether a count is still running: asked, not yet answered for good. */
export function countPending(large) {
  return Boolean(large) && !large.error && !large.done;
}

/** A prediction's default name from its key: the part before the first
 * `_`, `-` or `.` (MACE_energy → MACE), else 'Model'. */
export function keyPrefix(key) {
  const m = /^([^_.\-]+)[_.\-]/.exec(key || '');
  return m ? m[1] : 'Model';
}

/**
 * The LOAD_DATASET kwargs the loader's choices make. Empty when nothing
 * was chosen, so a plain file sends what it always sent. Extra key pairs
 * (`predictions`) go as `prediction_keys` [energy, force, name]; an empty key
 * means "calculator" to the server.
 * @param {{keysShown?: boolean, energyKey?: string, forceKey?: string,
 *   predictions?: {energyKey: string, forceKey: string, name: string}[],
 *   stride?: number|string, sampling?: object|null}} c  `stride` > 1 sends
 *   `slice_num`, keys shown or not; `sampling` (a sample by energy, ADR 0096)
 *   is sent instead of it, never with it.
 */
export function datasetLoadKwargs({
  keysShown = false, energyKey = '', forceKey = '', predictions = [], stride = 1, sampling = null,
  memoryMode = 'auto',
} = {}) {
  const kw = {};
  if (memoryMode === 'saving') kw.memory_mode = 'saving';
  if (keysShown) {
    kw.selected_energy_key = energyKey || null;
    kw.selected_force_key = forceKey || null;
  }
  if (keysShown && predictions.length) {
    kw.prediction_keys = predictions.map(
      ({ energyKey: e, forceKey: f, name }) => [e || '', f || '', name || 'Model']);
  }
  if (sampling) {
    kw.sampling = sampling;
    return kw;
  }
  const sliceNum = strideToSliceNum(stride);
  if (sliceNum) kw.slice_num = sliceNum;
  return kw;
}

/** The seed a sample by energy uses when the field is empty (ADR 0096). */
export const DEFAULT_SAMPLE_SEED = 42;

/**
 * The settings of a sample by energy from the dialog's fields (ADR 0096), or
 * null when they cannot make one: N a whole number of at least 2, bins empty
 * (automatic) or at least 1, the seed empty (42) or a whole number.
 */
export function sampleSettings({ method = 'even', n = '', bins = '', seed = '', energy = '' } = {}) {
  const blank = (v) => String(v ?? '').trim() === '';
  const whole = (v) => Math.floor(Number(v));
  if (blank(n) || !(whole(n) >= 2)) return null;
  const b = blank(bins) ? null : whole(bins);
  if (b !== null && !(b >= 1)) return null;
  const s = blank(seed) ? DEFAULT_SAMPLE_SEED : whole(seed);
  if (!Number.isFinite(s)) return null;
  return { method: method || 'even', n: whole(n), bins: b, seed: s, energy: energy || null };
}

/** The N a sample starts on: a tenth of the file, at most 5,000, at least 2. */
export function defaultSampleSize(nFrames) {
  return Math.max(2, Math.min(5000, Math.ceil(Number(nFrames) / 10)));
}

/**
 * The load dialog's energy histogram as SVG text (ADR 0096): every frame's
 * bar (`fb-h-all`) with the kept frames' bar over it (`fb-h-kept`), counts on
 * a log scale (a few kept frames next to a million would not show otherwise).
 * Bins beyond one per two pixels are summed into wider bars.
 * @param {{edges?: number[], all?: number[], kept?: number[]|null,
 *   perAtom?: boolean, width?: number, height?: number}} h
 */
export function energyHistogramSvg({
  edges = [], all = [], kept = null, perAtom = false, width = 520, height = 110,
} = {}) {
  const groups = Math.max(1, Math.ceil(all.length / Math.floor(width / 2)));
  const sum = (xs, i) => {
    let t = 0;
    for (let k = i; k < Math.min(i + groups, xs.length); k++) t += Number(xs[k]) || 0;
    return t;
  };
  const allBars = [];
  const keptBars = [];
  for (let i = 0; i < all.length; i += groups) {
    allBars.push(sum(all, i));
    keptBars.push(kept ? sum(kept, i) : 0);
  }
  let top = 1;
  for (const c of allBars) if (c > top) top = c;
  const plotH = height - 24;
  const y = (c) => (c > 0 ? Math.max(1, (Math.log10(1 + c) / Math.log10(1 + top)) * plotH) : 0);
  const w = width / Math.max(1, allBars.length);
  const bar = (cls, i, c) => {
    const h = y(c);
    return `<rect class="${cls}" x="${(i * w).toFixed(2)}" y="${(plotH - h).toFixed(2)}" `
      + `width="${Math.max(0.5, w - 0.5).toFixed(2)}" height="${h.toFixed(2)}"></rect>`;
  };
  let keptTotal = 0;
  for (const c of keptBars) keptTotal += c;
  const num = (v) => (Number.isFinite(v) ? Number(v.toPrecision(4)).toString() : '');
  const label = `${perAtom ? 'Energy per atom' : 'Energy'} · count, log scale`;
  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" `
    + `aria-label="${label}" data-kept="${kept ? keptTotal : ''}" data-bins="${all.length}">`
    + allBars.map((c, i) => bar('fb-h-all', i, c)).join('')
    + (kept ? keptBars.map((c, i) => bar('fb-h-kept', i, c)).join('') : '')
    + `<line class="fb-h-axis" x1="0" x2="${width}" y1="${plotH}" y2="${plotH}"></line>`
    + `<text x="0" y="${height - 6}">${num(edges[0])}</text>`
    + `<text x="${width / 2}" y="${height - 6}" text-anchor="middle">${label}</text>`
    + `<text x="${width}" y="${height - 6}" text-anchor="end">${num(edges[edges.length - 1])}</text>`
    + '</svg>';
}

const $ = (id) => document.getElementById(id);

export class RemoteBrowser {
  /**
   * @param {{
   *   send: (event: string, kwargs?: object, args?: any[]) => void,
   *   getDatasets: () => Map<string, {name?: string, n?: number}>,
   *   getCurrentDatasetFp: () => string | null,
   *   setStatus: (text: string, kind?: string) => void,
   *   serverKey?: () => string,
   *   setUnit?: (fp: string, dimension: string, unit: string) => void,
   *   signal?: (name: string, detail?: object) => void,
   * }} ports
   */
  constructor(ports) {
    this._ports = ports;
    this._mode = 'dataset';   // 'dataset' | 'prediction'
    this._path = null;        // current directory abspath (server-side)
    this._parent = null;      // parent abspath, or null at root
    this._home = null;        // server user's home directory
    this._selected = null;    // selected filename within _path
    this._types = { datasets: [{ name: 'ase (auto)', extension: '*' }], models: [] };
    this._writers = null;     // what the server can write (LOADER_TYPES), else a built-in list
    this._largeBytes = LARGE_FILE_BYTES;   // tests lower it
    this._large = null;   // {path, size, n, error, done} of a large selected file
    this._probe = null;   // the last key check's answer, for new prediction lines
    this._probing = null;   // the key check still out: {path}, matched by identity (ADR 0097)
    this._predReader = null;   // the reader a prediction file is read by (ADR 0095), else null
    this._typeTouched = false; // the user chose the Type: the server's suggestion does not replace it
    this._loadWhenReady = false;   // load once the key check is back
    this._pick = null;   // {onPick}: the browser is choosing where a session or export goes
    this._pending = null;   // {kind, name, path}: the latest load with no outcome yet
    // Loads the server answered (ADR 0097) whose dataset or model this
    // window has not been told of yet: fingerprint → the load.
    this._awaiting = new Map();
    this._navSeq = 0;   // the latest navigation: an older listing is not shown
    this._suggest = { items: [], active: -1, dir: '' };
    this._suggestTimer = null;
    this._selectAfterListing = null;   // a Recent file to select once its folder is listed
    this._focusAfterListing = null;    // the folder just left, focused once the parent is listed
    // A sample by energy (ADR 0096): {path, type, names, nFrames} once the file's
    // energies arrived; the last preview asked (only its answer is drawn).
    this._energies = null;
    this._energiesAsked = null;   // the energies on their way: {path}, matched by identity
    this._previewId = 0;
    this._previewTimer = null;
    this._sampleNTouched = false;  // the user typed N: a new file's default does not replace it
  }

  /** Wire the modal's own controls. Called once, at app setup. */
  bindControls() {
    $('fb-cancel').addEventListener('click', () => this.cancel());
    $('fb-target-ds').addEventListener('change', () => this._signal('target-changed', { datasetFp: $('fb-target-ds').value }));
    $('fb-load').addEventListener('click', () => this._load());
    $('fb-force-key').addEventListener('change', () => this._refreshLoadEnabled());
    $('fb-type').addEventListener('change', () => { this._typeTouched = true; this._refreshDatasetKeys(); });
    $('fb-name').addEventListener('input', () => this._refreshLoadEnabled());
    $('fb-format').addEventListener('change', () => {
      const chosen = (this._pick?.formats || EXPORT_FORMATS).find((f) => f.format === $('fb-format').value);
      if (chosen) $('fb-name').value = withExtension($('fb-name').value, chosen.ext);
      this._refreshExportItems();
      this._refreshLoadEnabled();
    });
    $('fb-name').addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      if (!$('fb-load').disabled) this._load();
    });
    $('fb-stride').addEventListener('input', () => this._drawLarge());
    $('fb-frames').addEventListener('change', () => this._onFramesChoice());
    for (const id of ['fb-sample-method', 'fb-sample-n', 'fb-sample-bins', 'fb-sample-seed', 'fb-sample-energy']) {
      $(id).addEventListener(id === 'fb-sample-method' || id === 'fb-sample-energy' ? 'change' : 'input', () => {
        if (id === 'fb-sample-n') this._sampleNTouched = true;
        this._askPreviewSoon();
        this._refreshLoadEnabled();
      });
    }
    $('fb-add-pred').addEventListener('click', () => this._addPredictionRow());
    $('fb-up').addEventListener('click', () => {
      if (this._parent) this._navigate(this._parent);
    });
    $('fb-home').addEventListener('click', () => this._navigate(this._home || null));
    // A resize drag from the panel corner can end over the backdrop; that click
    // must not close the dialog, so the press has to start on the backdrop too.
    let pressOnBackdrop = false;
    $('fb-modal').addEventListener('mousedown', (e) => {
      pressOnBackdrop = e.target.id === 'fb-modal';
    });
    $('fb-modal').addEventListener('click', (e) => {
      if (e.target.id === 'fb-modal' && pressOnBackdrop) this.cancel();
    });
    $('fb-modal').addEventListener('keydown', (e) => {
      if (e.key === 'Escape') this.cancel();
    });
    // The path can be typed: Enter goes there.
    $('fb-path').addEventListener('keydown', (e) => this._pathKey(e));
    $('fb-path').addEventListener('input', () => {
      this._typedSinceAsk = true;
      this._askSuggestions();
    });
    $('fb-path').addEventListener('blur', () => setTimeout(() => this._hideSuggestions(), 150));
    // One listener for the whole list: its rows are rebuilt on every folder change.
    $('fb-list').addEventListener('keydown', (e) => this._listKey(e));
  }

  /** The server's loaders (LOADER_TYPES, on connect): fill the Type list. */
  setLoaderTypes(kw) {
    this._types = { datasets: kw.datasets || [], models: kw.models || [] };
    this._writers = kw.writers?.length ? kw.writers : null;
    if (!this._types.datasets.length) this._types.datasets = [{ name: 'ase (auto)', extension: '*' }];
    this._fillSelect($('fb-type'), this._types.datasets.map((t) => ({ value: t.name, label: t.name })));
    $('fb-type').value = typeForFile('', this._types.datasets);
    this._fillSelect($('fb-model-type'), this._types.models.map((t) => ({ value: t.name, label: t.name })));
  }

  /** Arrow keys, Enter, Space and Backspace on a row of the file list (to_fix_v2). */
  _listKey(e) {
    const row = e.target.closest('.fb-row');
    if (!row) return;
    const rows = [...$('fb-list').querySelectorAll('.fb-row')];
    const act = listKey(e.key, {
      index: rows.indexOf(row), count: rows.length, isDir: row.classList.contains('dir'),
    });
    if (!act) return;
    e.preventDefault();
    if (act.focus !== undefined) {
      rows[act.focus].focus();
      rows[act.focus].scrollIntoView({ block: 'nearest' });
    } else if (act.open) {
      this._focusFirstRow = true;
      row.click();
    } else if (act.select) {
      row.click();
    } else if (act.load) {
      row.click();
      this._loadWhenPossible();
    } else if (act.up && this._parent) {
      this._focusAfterListing = baseName(this._path);
      this._navigate(this._parent);
    }
  }

  // ── path suggestions ────────────────────────────────────────────────────

  _pathKey(e) {
    const s = this._suggest;
    const open = s.items.length > 0;
    if (e.key === 'Enter') {
      e.preventDefault();
      this._hideSuggestions();
      const path = $('fb-path').value.trim();
      if (path) this._navigate(path);
    } else if (open && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      e.preventDefault();
      const n = s.items.length;
      s.active = (s.active + (e.key === 'ArrowDown' ? 1 : -1) + n) % n;
      this._drawSuggestions();
    } else if (open && e.key === 'Tab') {
      e.preventDefault();
      this._complete(s.items[Math.max(s.active, 0)]);
    } else if (open && e.key === 'Escape') {
      e.stopPropagation();   // closes the list, not the dialog
      this._hideSuggestions();
    }
  }

  /** After a short pause, list the typed folder. Its reply is its own
   * request's (ADR 0097), so it never passes for a navigation's. */
  _askSuggestions() {
    clearTimeout(this._suggestTimer);
    this._suggestTimer = setTimeout(() => {
      const parts = typedParts($('fb-path').value);
      if (!parts) { this._hideSuggestions(); return; }
      answerOf(this._ports.request(OUT.LIST_DIR, {}, [parts.dir])).then((kw) => {
        if (!kw.connectionLost) this._showSuggestions(kw, parts.dir);
      });
    }, 250);
  }

  /** `dir` is the folder the reply was asked for. */
  _showSuggestions(kw, dir) {
    const parts = typedParts($('fb-path').value);
    // An error (no such folder), or the person typed on to another folder.
    if (kw.error || !parts || parts.dir !== dir) {
      this._hideSuggestions();
      return;
    }
    this._suggest = { items: matchEntries(kw.entries, parts.prefix), active: -1, dir };
    this._drawSuggestions();
  }

  _drawSuggestions() {
    const box = $('fb-suggest');
    const s = this._suggest;
    box.replaceChildren(...s.items.map((e, i) => {
      const row = document.createElement('div');
      row.className = 'fb-suggestion' + (i === s.active ? ' active' : '');
      row.setAttribute('role', 'option');
      row.textContent = e.name + (e.is_dir ? '/' : '');
      row.addEventListener('mousedown', (ev) => { ev.preventDefault(); this._complete(e); });
      return row;
    }));
    box.classList.toggle('hidden', !s.items.length);
  }

  _hideSuggestions() {
    clearTimeout(this._suggestTimer);
    this._suggest = { items: [], active: -1, dir: '' };
    $('fb-suggest')?.classList.add('hidden');
  }

  /** Put a suggested name into the box; a folder goes on to its own contents. */
  _complete(entry) {
    const field = $('fb-path');
    field.value = joinPath(this._suggest.dir, entry.name) + (entry.is_dir ? '/' : '');
    this._hideSuggestions();
    field.focus();
    if (entry.is_dir) this._askSuggestions();
  }

  // ── units of a dataset's energies and forces ────────────────────────────

  /** Drop-down choices: the units this browser used last first. The fields
   * start empty — a unit is named only when the person types or picks one. */
  _renderUnitChoices() {
    for (const dimension of ['energy', 'force']) {
      const list = $(`fb-${dimension}-units`);
      list.replaceChildren(...unitChoices(dimension, loadRecentUnits(dimension)).map((u) => {
        const o = document.createElement('option');
        o.value = u;
        return o;
      }));
      const field = $(`fb-${dimension}-unit`);
      field.value = '';
      field.placeholder = UNIT_PLACEHOLDER;
    }
  }

  _unitsTyped() {
    const units = {};
    for (const dimension of ['energy', 'force']) {
      const unit = $(`fb-${dimension}-unit`).value.trim();
      if (unit) units[dimension] = unit;
    }
    return units;
  }

  // ── recent files ────────────────────────────────────────────────────────

  _renderRecent() {
    const box = $('fb-recent');
    const server = this._ports.serverKey?.() || '';
    const list = loadRecentFiles(server).filter((e) => e.kind === this._mode);
    box.hidden = !list.length;
    const head = document.createElement('div');
    head.className = 'fb-recent-head';
    head.textContent = 'Recent';
    box.replaceChildren(head, ...list.map((e) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'fb-recent-row';
      row.textContent = e.path;
      row.title = e.path;
      row.addEventListener('click', () => this._useRecent(e.path));
      return row;
    }));
  }

  /** Fill the box with a recent file. A dataset loads at once, with the
   * server's default keys; a prediction
   * needs its energy and force keys chosen, so its file is selected for that. */
  _useRecent(path) {
    const i = path.lastIndexOf('/');
    const dir = i <= 0 ? '/' : path.slice(0, i);
    const name = path.slice(i + 1);
    $('fb-path').value = path;
    if (this._mode === 'dataset') {
      this._path = dir;
      this._selected = name;
      $('fb-type').value = typeForFile(name, this._types.datasets);   // guessed from the extension (decision 1)
      // A Recent dataset loads with its default keys, no key check
      // (decision 4): drop any Keys row left from a file clicked before.
      $('fb-keys').hidden = true;
      this._probing = null;
      this._loadWhenReady = false;
      this._load();
    } else {
      this._selectAfterListing = name;
      this._navigate(dir);
    }
  }

  _remember(path, kind) {
    const server = this._ports.serverKey?.() || '';
    saveRecentFiles(server, rememberFile(loadRecentFiles(server), { path, kind }));
  }

  /** Tell the guided tours (ADR 0088) that `name` happened. */
  _signal(name, detail = {}) {
    this._ports.signal?.(name, detail);
  }

  /** The mode the dialog is open in, or null when it is hidden. */
  mode() {
    return $('fb-modal').classList.contains('hidden') ? null : this._mode;
  }

  /** The folder on screen, or null when the dialog is hidden. */
  folder() {
    return this.mode() === null ? null : this._path;
  }

  /** The next open starts in `path` (a tour opens the examples folder). */
  startIn(path) {
    this._path = path;
  }

  /** Show folder `path` in the open dialog. */
  goTo(path) {
    this._navigate(path);
  }

  /**
   * @param {'dataset'|'prediction'|'model'} mode
   * @param {{datasetFp?: string|null, folder?: string|null}} [opts]  `folder`: open there
   */
  open(mode = 'dataset', { datasetFp = null, folder = null } = {}) {
    this._mode = mode;
    if (folder) this._path = folder;
    const titles = { dataset: 'Load Remote Dataset', prediction: 'Load Prediction', model: 'Load Model' };
    $('fb-title').textContent = titles[mode];
    $('fb-dataset-fields').style.display = mode === 'dataset' ? '' : 'none';
    $('fb-prediction-fields').style.display = mode === 'prediction' ? 'inline-flex' : 'none';
    $('fb-model-fields').style.display = mode === 'model' ? 'inline-flex' : 'none';
    $('fb-pick-fields').hidden = true;
    $('fb-load').textContent = 'Load';
    this._pick = null;
    const isPred = mode === 'prediction';
    if (isPred) {
      this._populatePredictionTargets();
      // The dataset a panel needs a prediction for, when it is in the list.
      if (datasetFp && this._ports.getDatasets().has(datasetFp)) $('fb-target-ds').value = datasetFp;
    }
    this._renderRecent();
    this._renderUnitChoices();
    this._show();
    this._signal('browser-opened', { mode });
  }

  /**
   * Choose where a session or an export goes, in the same browser: a folder
   * to save into with a name ('save-folder'), a folder to open
   * ('open-folder'), or a file name ('save-file', clicking a file puts its name
   * in the box).
   * @param {{mode: 'save-folder'|'open-folder'|'save-file', title: string, okLabel: string,
   *   name?: string, formats?: Array<{format: string, label: string, ext: string}>|'writers',
   *   format?: string, items?: Array<{key: string, label: string, checked: boolean}>,
   *   dataset?: {variable: boolean, n: number},
   *   onPick: (path: string, choice: {format: string|null, items?: string[]}) => void}} o
   *   `formats: 'writers'` is the server's list of what it can write; `items` are tick boxes
   *   (what to write), greyed out where the format cannot hold them (ADR 0095).
   */
  pick({ mode, title, okLabel, name = '', formats = null, format = null, itemDirs = null,
    items = null, dataset = null, onPick }) {
    this._mode = mode;
    if (formats === 'writers') formats = this._writers || EXPORT_FORMATS;
    // itemDirs: a RegExp; folders whose names match are chosen like files, not opened.
    this._pick = { onPick, formats, itemDirs, items: items?.map((i) => ({ ...i })) ?? null, dataset };
    // Picked from inside another dialog (Run with FFAST, Make reference):
    // open above it, or the browser is hidden behind it and cannot be used.
    $('fb-modal').classList.toggle('over-dialog', !!document.querySelector('.warning-modal:not(.hidden)'));
    $('fb-title').textContent = title;
    $('fb-load').textContent = okLabel;
    $('fb-dataset-fields').style.display = 'none';
    $('fb-prediction-fields').style.display = 'none';
    $('fb-model-fields').style.display = 'none';
    $('fb-recent').hidden = true;
    $('fb-pick-fields').hidden = mode === 'open-folder';
    $('fb-name').value = name;
    $('fb-format').hidden = $('fb-format-label').hidden = !formats;
    if (formats) {
      this._fillSelect($('fb-format'), formats.map((f) => ({ value: f.format, label: f.label })));
      $('fb-format').value = format;
    }
    this._show();
    this._focusFirstRow = mode === 'open-folder';
    if (mode !== 'open-folder') {
      $('fb-name').focus();
      $('fb-name').select();
    }
  }

  /** The Export dialog's tick boxes: what to write beside the geometry. */
  _renderExportItems() {
    const box = $('fb-export-items');
    const items = this._pick?.items;
    box.replaceChildren();
    box.hidden = $('fb-export').hidden = !items?.length;
    $('fb-export-note').hidden = true;
    this._exportBlocked = null;
    if (!items?.length) return;
    for (const item of items) {
      const label = document.createElement('label');
      label.className = 'fb-export-item';
      label.dataset.key = item.key;
      const tick = document.createElement('input');
      tick.type = 'checkbox';
      tick.checked = item.checked;
      tick.addEventListener('change', () => {
        item.checked = tick.checked;
        this._refreshExportItems();
        this._refreshLoadEnabled();
      });
      const why = document.createElement('span');
      why.className = 'fb-export-why';
      label.append(tick, ` ${item.label} `, why);
      box.appendChild(label);
    }
    this._refreshExportItems();
  }

  /** Grey out what the chosen format cannot hold, with the reason beside it,
   * and block the export when the format cannot hold the dataset. */
  _refreshExportItems() {
    const pick = this._pick;
    if (!pick?.items?.length) return;
    const info = (pick.formats || []).find((f) => f.format === $('fb-format').value);
    const plan = exportPlan(info, pick.items, pick.dataset || {});
    for (const label of $('fb-export-items').children) {
      const reason = plan.disabled[label.dataset.key] || '';
      const tick = label.querySelector('input');
      tick.disabled = Boolean(reason);
      label.classList.toggle('unavailable', Boolean(reason));
      label.title = reason;
      label.querySelector('.fb-export-why').textContent = reason;
    }
    this._exportBlocked = plan.blocked;
    $('fb-export-note').hidden = !(plan.blocked || plan.note);
    $('fb-export-note').textContent = plan.blocked || plan.note || '';
  }

  /** The part of opening the modal every mode shares. */
  _show() {
    $('fb-modal').classList.remove('hidden');
    this._renderExportItems();      // the Export tick boxes, or none (another mode)
    this._hideSuggestions();
    this._untrap ||= trapFocus($('fb-modal'));
    // Focus goes into the dialog now, not when the listing arrives: Escape and
    // Tab are heard by the dialog only while focus is inside it, and a slow
    // listing left the first Escape going nowhere.
    const modal = $('fb-modal');
    modal.tabIndex = -1;
    modal.style.outline = 'none';
    modal.focus({ preventScroll: true });
    this._focusFirstRow = true;   // once the listing arrives (_render)
    this._selected = null;
    $('fb-load').disabled = true;
    this._loadWhenReady = false;
    this._probing = null;
    $('fb-keys').hidden = true;
    $('fb-pred-rows').replaceChildren();
    $('fb-stride').value = '1';
    if ($('fb-memory-mode')) $('fb-memory-mode').value = 'auto';
    $('fb-large').hidden = true;
    this._resetSample();
    this._sampleNTouched = false;
    this._dropCount();
    // null path → server starts at its work folder (--workdir, else its start folder)
    this._navigate(this._path || null);
  }

  /** A new server: its folders are not the last one's (a laptop path means
   * nothing on a cluster), so the next open starts in its work folder. */
  forgetFolder() {
    this._path = null;
    this._parent = null;
    this._home = null;
  }

  /** Close without loading (Cancel, a click beside the dialog, Escape). */
  cancel() {
    this.close();
    this._signal('browser-cancelled');
  }

  close() {
    this._dropCount();
    this._loadWhenReady = false;
    this._probing = null;
    this._untrap?.();
    this._untrap = null;
    $('fb-modal').classList.add('hidden');
    $('fb-modal').classList.remove('over-dialog');
    // A hidden field keeps focus until the browser next lays the page out, and
    // a key typed in that gap (the next shortcut) was taken for typing in it.
    const at = document.activeElement;
    if (at && $('fb-modal').contains(at)) at.blur();
  }

  /** Server replied to this window's latest navigation (LIST_DIR). */
  onDirListing(kw) {
    if (kw.error) {
      const err = $('fb-error');
      err.style.display = 'block';
      err.textContent = kw.error;
      $('fb-list').innerHTML = '';
      this._focusAfterListing = null;
      // keep the previous path so ↑ still works
      $('fb-path').value = kw.path || '';
      return;
    }
    this._path = kw.path;
    this._parent = kw.parent;
    if (kw.home) this._home = kw.home;
    $('fb-error').style.display = 'none';
    // A path typed while this listing was on its way is kept: the user's
    // next Enter goes there, not to the folder that just arrived.
    if (!this._typedSinceAsk) $('fb-path').value = kw.path || '';
    $('fb-up').disabled = !kw.parent;
    this._render(kw.entries || []);
    this._refreshLoadEnabled();   // in a pick mode the folder on screen enables the button
    if (this.mode() !== null) this._signal('folder-listed', { mode: this._mode, path: this._path });
    if (this._focusAfterListing) {
      // Going up leaves the cursor on the folder we came from.
      const name = this._focusAfterListing;
      this._focusAfterListing = null;
      const row = [...document.querySelectorAll('#fb-list .fb-row.dir')]
        .find((r) => r.querySelector('.name')?.textContent === name);
      if (row) { row.focus(); row.scrollIntoView({ block: 'nearest' }); }
    }
    if (this._selectAfterListing) {
      const name = this._selectAfterListing;
      this._selectAfterListing = null;
      const row = [...document.querySelectorAll('#fb-list .fb-row.file')]
        .find((r) => r.querySelector('.name')?.textContent === name);
      if (row) this._selectFile(row, name);
    }
  }

  /** Server replied to this window's latest key check (PROBE_DATASET_KEYS). */
  onDatasetKeys(kw) {
    this._probing = null;
    // Readers that take the file's ending are asked can_read, in turn (ADR 0095,
    // decision 9): the Type starts on the one that can read it, unless the user
    // chose; the file is probed again as that reader.
    if (this._mode === 'dataset' && kw.reader && !this._typeTouched
        && kw.reader !== $('fb-type').value && this._isReaderType(kw.reader)) {
      $('fb-type').value = kw.reader;
      this._refreshDatasetKeys();
      return;
    }
    if (this._mode === 'prediction') {
      this._fillPredictionKeys(kw);
    } else if (this._mode === 'dataset') {
      this._showDatasetKeys(kw);
      this._setSampleAvailable(!kw.error && Boolean(kw.energy_sampling));
    }
    this._refreshLoadEnabled();
    this._loadIfReady();
  }

  // ── a sample by energy (ADR 0096) ───────────────────────────────────────

  /** Frames: All, Every Nth (the stride field), or Sample by energy (its
   * fields and histogram). Never both: the other is put back. */
  _framesChoice() {
    return $('fb-frames').value;
  }

  _onFramesChoice() {
    const choice = this._framesChoice();
    $('fb-stride').hidden = choice !== 'nth';
    if (choice !== 'nth') $('fb-stride').value = '1';
    $('fb-sample').hidden = choice !== 'energy';
    if (choice === 'energy') this._askEnergies();
    this._drawLarge();
    this._refreshLoadEnabled();
  }

  /** Back to All frames, with nothing asked (a new file, or the dialog opened). */
  _resetSample() {
    $('fb-frames').value = 'all';
    $('fb-stride').hidden = true;
    $('fb-sample').hidden = true;
    $('fb-sample-plot').replaceChildren();
    $('fb-sample-note').textContent = '';
    $('fb-sample-bins').placeholder = 'auto';
    this._energies = null;
    this._energiesAsked = null;
    this._previewId += 1;        // an answer still on its way is not drawn
    clearTimeout(this._previewTimer);
    this._setSampleAvailable(false);
  }

  /** "Sample by energy" is offered only for a file whose reader lists each
   * frame's energy and that has energies (the key check says so). */
  _setSampleAvailable(ok) {
    const option = $('fb-frames').querySelector("option[value='energy']");
    option.disabled = !ok;
    $('fb-frames').title = ok ? ''
      : "Sample by energy needs a reader that lists each frame's energy, and a file with energies";
    if (!ok && this._framesChoice() === 'energy') {
      $('fb-frames').value = 'all';
      this._onFramesChoice();
    }
  }

  _selectedPath() {
    return this._selected ? joinPath(this._path, this._selected) : null;
  }

  /** Ask for the file's energies, once per file: the first time the server
   * reads every frame's energy, which takes a while for a big file. */
  _askEnergies() {
    const path = this._selectedPath();
    if (!path) return;
    if (this._energies?.path === path) {
      this._askPreview();
      return;
    }
    if (this._energiesAsked?.path === path) return;
    const ask = { path };
    this._energiesAsked = ask;
    setBusyText($('fb-sample-note'), `Reading the energies of ${this._selected}…`);
    answerOf(this._ports.request(OUT.PROBE_DATASET_ENERGIES, {}, [path, $('fb-type').value]))
      .then((kw) => {
        // An answer for a file or Type no longer asked about is dropped.
        if (this._energiesAsked === ask && !kw.connectionLost) this.onDatasetEnergies(path, kw);
      });
  }

  /** Server replied to this window's latest PROBE_DATASET_ENERGIES. */
  onDatasetEnergies(path, kw) {
    this._energiesAsked = null;
    if (kw.error) {
      $('fb-sample-note').textContent = kw.error;
      return;
    }
    this._energies = { path, names: kw.names || [], nFrames: kw.n_frames };
    // The reference energy chosen above, when there is a choice; else the server's.
    const reference = !$('fb-keys').hidden ? $('fb-ds-energy-key').value : '';
    this._fillSelect($('fb-sample-energy'), this._energies.names.map((n) => ({ value: n, label: n })));
    $('fb-sample-energy').value = this._energies.names.includes(reference) ? reference : kw.energy;
    if (!this._sampleNTouched) $('fb-sample-n').value = String(defaultSampleSize(kw.n_frames));
    $('fb-sample-plot').innerHTML = energyHistogramSvg({
      edges: kw.edges, all: kw.counts, perAtom: kw.per_atom });
    this._askPreview();
  }

  /** The settings the fields make, with the energy chosen, or null. */
  _sampleSettings() {
    return sampleSettings({
      method: $('fb-sample-method').value,
      n: $('fb-sample-n').value,
      bins: $('fb-sample-bins').value,
      seed: $('fb-sample-seed').value,
      energy: $('fb-sample-energy').value,
    });
  }

  /** A preview after the user stops typing for 200 ms. */
  _askPreviewSoon() {
    clearTimeout(this._previewTimer);
    this._previewTimer = setTimeout(() => this._askPreview(), 200);
  }

  _askPreview() {
    clearTimeout(this._previewTimer);
    const path = this._selectedPath();
    if (this._framesChoice() !== 'energy' || !this._energies || this._energies.path !== path) return;
    const sampling = this._sampleSettings();
    this._previewId += 1;
    if (!sampling) {
      $('fb-sample-note').textContent =
        'Frames to keep must be a whole number from 2; Bins empty (automatic) or from 1.';
      return;
    }
    this._ports.send(OUT.PREVIEW_ENERGY_SAMPLE, { sampling, preview_id: this._previewId },
      [path, $('fb-type').value]);
  }

  /** Server replied to PREVIEW_ENERGY_SAMPLE: only the answer to the latest ask is drawn. */
  onEnergyPreview(path, kw) {
    if (path !== this._selectedPath() || kw.preview_id !== this._previewId) return;
    if (kw.error) {
      $('fb-sample-note').textContent = kw.error;
      return;
    }
    $('fb-sample-plot').innerHTML = energyHistogramSvg({
      edges: kw.edges, all: kw.counts_all, kept: kw.counts_kept, perAtom: kw.per_atom });
    $('fb-sample-bins').placeholder = `auto (${kw.bins_used})`;
    const count = (x) => Number(x).toLocaleString('en-US');
    const notes = [`Keeps ${count(kw.kept)} of ${count(kw.n_frames)} frames`
      + ` in ${kw.bins_used} bin${kw.bins_used === 1 ? '' : 's'}`
      + ` of ${kw.per_atom ? 'energy per atom' : 'energy'}.`];
    if (kw.kept_all) notes.push('That is every frame with an energy.');
    if (kw.left_out) notes.push(`${count(kw.left_out)} frames have no energy and are left out.`);
    $('fb-sample-note').textContent = notes.join(' ');
  }

  _fillPredictionKeys(kw) {
    this._fillSelect(
      $('fb-energy-key'),
      keyOptions(kw.energy_keys, {
        allowNone: true, calculator: kw.has_calculator_energy, calculatorValue: 'energy',
      }),
    );
    this._fillSelect(
      $('fb-force-key'),
      keyOptions(kw.force_keys, {
        allowNone: true, calculator: kw.has_calculator_forces, calculatorValue: 'forces',
      }),
    );
    if (!kw.error) {
      // Every prediction is read by a reader, named or picked by the server
      // from the file's ending (ADR 0095): names that match start paired, and
      // '' is "none", which an energy-only or forces-only file wants.
      const [energy, force] = pairNames(kw.energy_keys, kw.force_keys);
      $('fb-energy-key').value = energy;
      $('fb-force-key').value = force;
    }
    if (kw.error) this._ports.setStatus(`Probe error: ${kw.error}`, 'error');
  }

  /** Show the Keys row when the file offers a choice; a failed check
   * shows nothing here (the load itself says why it failed). */
  _showDatasetKeys(kw) {
    $('fb-pred-rows').replaceChildren();
    this._probe = kw;
    const choice = !kw.error && needsKeyChoice(kw);
    $('fb-keys').hidden = !choice;
    if (!choice) return;
    this._fillSelect($('fb-ds-energy-key'), keyOptions(kw.energy_keys, {
      calculator: kw.has_calculator_energy, calculatorValue: 'energy' }));
    this._fillSelect($('fb-ds-force-key'), keyOptions(kw.force_keys, {
      calculator: kw.has_calculator_forces, calculatorValue: 'forces' }));
    $('fb-ds-energy-key').value = defaultKey(kw.energy_keys, kw.has_calculator_energy, 'energy');
    $('fb-ds-force-key').value = defaultKey(kw.force_keys, kw.has_calculator_forces, 'forces');
  }

  /** One more key pair to load as a prediction ("Add Prediction"). Starts on the first keys the reference is not using. */
  _addPredictionRow() {
    const kw = this._probe || {};
    const row = document.createElement('div');
    row.className = 'fb-pred-row';
    const pick = (keys, calc, calcValue, taken, cls, label) => {
      const sel = document.createElement('select');
      sel.className = cls;
      sel.setAttribute('aria-label', label);
      this._fillSelect(sel, keyOptions(keys, { calculator: calc, calculatorValue: calcValue }));
      sel.value = (keys || []).find((k) => k !== taken) ?? defaultKey(keys, calc, calcValue);
      return sel;
    };
    const energy = pick(kw.energy_keys, kw.has_calculator_energy, 'energy',
      $('fb-ds-energy-key').value, 'fb-pred-energy', 'Prediction energy key');
    const force = pick(kw.force_keys, kw.has_calculator_forces, 'forces',
      $('fb-ds-force-key').value, 'fb-pred-force', 'Prediction force key');
    const name = document.createElement('input');
    name.className = 'fb-pred-name';
    name.placeholder = 'Model';
    name.setAttribute('aria-label', 'Prediction name');
    name.value = keyPrefix(energy.value);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = 'Remove';
    remove.addEventListener('click', () => row.remove());
    row.append('Energy', energy, 'Force', force, 'Name', name, remove);
    $('fb-pred-rows').appendChild(row);
  }

  /** The prediction lines, as datasetLoadKwargs reads them. */
  _predictionRows() {
    return [...document.querySelectorAll('#fb-pred-rows .fb-pred-row')].map((row) => ({
      energyKey: row.querySelector('.fb-pred-energy').value,
      forceKey: row.querySelector('.fb-pred-force').value,
      name: row.querySelector('.fb-pred-name').value.trim(),
    }));
  }

  /** Load now if it can, else as soon as the key check is back (a
   * double-click or Enter on a file row). */
  _loadWhenPossible() {
    if (!$('fb-load').disabled) this._load();
    else if (this._probing) this._loadWhenReady = true;
  }

  /** A load that waited for the key check goes ahead only when there is
   * nothing left to choose. */
  _loadIfReady() {
    if (!this._loadWhenReady) return;
    this._loadWhenReady = false;
    if (this._mode === 'dataset' && $('fb-keys').hidden && $('fb-large').hidden
        && !$('fb-load').disabled) this._load();
  }

  /** Ask the server which energy/force keys `path` has; its answer is
   * matched to the path (a quicker click on another file wins). */
  _probeKeys(path, type = 'ase (auto)') {
    const ask = { path, type };
    this._probing = ask;
    // Server route requires (path, dataset_type); ASE auto-detect reads the keys,
    // a reader says its names (ADR 0095). Only the latest check's answer is
    // used: an earlier one, for another file or Type, is dropped (ADR 0097).
    answerOf(this._ports.request(OUT.PROBE_DATASET_KEYS, {}, [path, type])).then((kw) => {
      if (this._probing === ask && !kw.connectionLost) this.onDatasetKeys(kw);
    });
  }

  /** Whether the Type list's `type` is a reader: its file can be probed for names. */
  _isReaderType(type) {
    return this._types.datasets.some((t) => t.name === type && t.reader);
  }

  /** The Keys row follows the selected file and the Type: only an ASE
   * file is probed; any other type has no keys to choose. */
  _refreshDatasetKeys() {
    $('fb-keys').hidden = true;
    this._probing = null;
    this._resetSample();   // the key check says again whether this file can be sampled by energy
    const type = $('fb-type').value;
    if (this._selected && (type === 'ase (auto)' || this._isReaderType(type))) {
      this._probeKeys(joinPath(this._path, this._selected), type);
    }
    this._refreshLoadEnabled();
  }

  // ── internals ───────────────────────────────────────────────────────────

  _populatePredictionTargets() {
    // A prediction is loaded *against* an already-loaded dataset.
    const sel = $('fb-target-ds');
    sel.innerHTML = '';
    for (const [fp, meta] of this._ports.getDatasets()) {
      const opt = document.createElement('option');
      opt.value = fp;
      opt.textContent = `${meta.name || fp.slice(0, 8)} (${meta.n} frames)`;
      sel.appendChild(opt);
    }
    const dsFp = this._ports.getCurrentDatasetFp();
    if (dsFp && this._ports.getDatasets().has(dsFp)) sel.value = dsFp;
    $('fb-energy-key').innerHTML = '';
    $('fb-force-key').innerHTML = '';
  }

  _fillSelect(sel, options) {
    sel.innerHTML = '';
    for (const { value, label } of options) {
      const o = document.createElement('option');
      o.value = value;
      o.textContent = label;
      sel.appendChild(o);
    }
  }

  _navigate(path) {
    this._typedSinceAsk = false;
    this._selected = null;
    $('fb-load').disabled = true;
    // path travels as a positional arg; server reads args[0]
    const nav = ++this._navSeq;
    answerOf(this._ports.request(OUT.LIST_DIR, {}, [path])).then((kw) => {
      if (nav === this._navSeq && !kw.connectionLost) this.onDirListing(kw);
    });
  }

  _render(entries) {
    const list = $('fb-list');
    list.innerHTML = '';
    for (const e of entries) {
      const row = document.createElement('div');
      row.className = `fb-row ${e.is_dir ? 'dir' : 'file'}`;
      if (!e.is_dir) row.dataset.size = String(e.size ?? 0);
      const icon = document.createElement('span');
      icon.className = 'fb-icon';
      icon.innerHTML = drawIcon(e.is_dir ? 'folder' : 'file');
      const name = document.createElement('span');
      name.className = 'name';
      name.textContent = e.name;
      row.append(icon, name);
      if (!e.is_dir) {
        const size = document.createElement('span');
        size.className = 'size';
        size.textContent = formatSize(e.size);
        row.append(size);
      }
      row.tabIndex = 0;
      // A pick may treat some folders as one item (a reference's .bundle).
      const item = e.is_dir && this._pick?.itemDirs?.test(e.name);
      if (item) row.classList.add('item');
      if (e.is_dir && !item) {
        row.addEventListener('click', () => this._navigate(joinPath(this._path, e.name)));
      } else {
        row.addEventListener('click', () => this._selectFile(row, e.name));
        row.addEventListener('dblclick', () => {
          this._selectFile(row, e.name);
          this._loadWhenPossible();
        });
      }
      list.appendChild(row);
    }
    // A fresh dialog opens with the first row focused, so the keyboard can pick.
    if (this._focusFirstRow) {
      this._focusFirstRow = false;
      list.querySelector('.fb-row')?.focus();
    }
  }

  _selectFile(row, name) {
    this._selected = name;
    this._signal('file-selected', { mode: this._mode, name, path: joinPath(this._path, name) });
    for (const r of document.querySelectorAll('#fb-list .fb-row.selected')) {
      r.classList.remove('selected');
    }
    row.classList.add('selected');
    if (this._mode === 'prediction') {
      // Probe the chosen file for the energy/force names it holds: through the
      // reader of its ending when one names it, else as an ASE file, where the
      // server's readers for the ending decide (ADR 0095).
      const path = joinPath(this._path, name);
      this._predReader = readerTypeFor(name, this._types.datasets);
      $('fb-energy-key').innerHTML = '<option value="">…probing…</option>';
      $('fb-force-key').innerHTML = '<option value="">…probing…</option>';
      this._probeKeys(path, this._predReader || 'ase (auto)');
    }
    if (this._mode === 'dataset') {
      this._typeTouched = false;
      if ($('fb-memory-mode')) {
        const indexed = /\.(extxyz|xyz)$/i.test(name);
        $('fb-memory-mode').disabled = !indexed;
        $('fb-memory-mode').title = indexed ? '' : 'Save memory is available for XYZ and EXTXYZ files.';
        if (!indexed) $('fb-memory-mode').value = 'auto';
      }
      $('fb-type').value = typeForFile(name, this._types.datasets);
      this._refreshDatasetKeys();
      this._refreshLarge(Number(row.dataset.size || 0));
    }
    if (this._mode === 'model') $('fb-model-type').value = typeForFile(name, this._types.models, '');
    if (this._mode === 'save-file') $('fb-name').value = name;
    this._refreshLoadEnabled();
  }

  /** A file of 3 GB or more shows its size and frame count (counted by
   * the server, ASE files only) so "Every Nth frame" can be chosen. */
  _refreshLarge(size) {
    const big = this._mode === 'dataset' && size >= this._largeBytes;
    const path = this._selected ? joinPath(this._path, this._selected) : null;
    this._dropCount();
    this._large = big ? { path, size, n: null, error: null, done: false } : null;
    $('fb-large').hidden = !big;
    if (!big) return;
    const large = this._large;
    if ($('fb-type').value === 'ase (auto)') {
      // Growing counts are progress replies; the final one has done=true.
      const onProgress = (reply) => this.onDatasetLength(large, reply.kwargs);
      answerOf(this._ports.request(OUT.PROBE_DATASET_LENGTH, {}, [path], { onProgress }))
        .then((kw) => { if (!kw.connectionLost) this.onDatasetLength(large, { ...kw, done: true }); });
    } else this._large.error = 'only ASE files are counted';
    this._drawLarge();
  }

  /** Forget the dialog's count; one still running on the server is stopped. */
  _dropCount() {
    if (countPending(this._large)) this._ports.send(OUT.CANCEL_PROBE_DATASET_LENGTH, {}, []);
    this._large = null;
  }

  _drawLarge() {
    if (!this._large) return;
    setBusyText($('fb-large'), largeFileNote({ ...this._large, stride: $('fb-stride').value }));
  }

  /** Server replied to the count `large` asked for (PROBE_DATASET_LENGTH). */
  onDatasetLength(large, kw) {
    if (!large || this._large !== large) return;   // the dialog moved on
    // A late progress reply must not undo the final one.
    if (this._large.done && kw.done === false) return;
    this._large.n = kw.n ?? null;
    this._large.error = kw.error || null;
    this._large.done = kw.done ?? true;
    this._drawLarge();
  }

  _refreshLoadEnabled() {
    $('fb-load').disabled = !canLoad({
      selected: this._selected,
      mode: this._mode,
      forceKey: $('fb-force-key').value,
      energyKey: $('fb-energy-key').value,
      targetDataset: $('fb-target-ds').value,
      probing: this._mode === 'dataset' && Boolean(this._probing),
      modelType: $('fb-model-type').value,
      dir: this._path,
      name: $('fb-name').value,
    }) || Boolean(this._pick && this._exportBlocked)
      || (this._mode === 'dataset' && !this._pick && this._framesChoice() === 'energy'
        && !this._sampleSettings());
  }

  /** File ▸ Load Zero Model: no file to pick. */
  loadZeroModel() {
    this._ports.setStatus('Loading Zero Model…', 'connected');
    this._startLoad({ kind: 'model', name: 'Zero Model', path: null }, OUT.LOAD_ZERO_MODEL, {}, []);
  }

  /** Send one load request and follow it to its end (ADR 0097): the
   * server's final reply names what loaded, says it was already loaded, or
   * says why it failed. */
  _startLoad(load, event, kwargs, args) {
    this._pending = load;
    const start = () => {
      const asked = this._ports.request(event, kwargs, args);
      asked.then((reply) => this._loadAnswered(load, reply.kwargs),
                 (err) => this._loadFailed(load, err));
    };
    const gate = this._ports.beforeLoad?.(load.path, load.kind);
    if (gate?.then) {
      gate.then((proceed) => {
        if (this._pending !== load) return;  // disconnected or a newer load replaced it
        if (proceed) start();
        else {
          this._end(load);
          this._ports.setStatus(`Loading ${load.name} cancelled`, 'connected');
        }
      }, (err) => this._loadFailed(load, err));
    } else start();
  }

  _loadAnswered(load, kw) {
    if (kw.already) {
      this._ports.setStatus(`"${load.name}" is already loaded`, 'connected');
      if (load.path) this._remember(load.path, load.kind);
      // Units typed in the loader still name the dataset that is there.
      if (kw.fingerprint) this._nameUnits(load, kw.fingerprint);
      this._signal('load-duplicate', { kind: load.kind, name: load.name });
      this._end(load);
      return;
    }
    load.fp = kw.fingerprint;
    // The announcement of what loaded can come before or after this reply.
    if (this._ports.holds(load.kind, load.fp, load.datasetFp)) this._finish(load);
    else this._awaiting.set(load.fp, load);
  }

  /** The window was told of dataset or model `fp` (its announcement). */
  announced(fp) {
    const load = this._awaiting.get(fp);
    if (!load || !this._ports.holds(load.kind, fp, load.datasetFp)) return;
    this._awaiting.delete(fp);
    this._finish(load);
  }

  _finish(load) {
    if (load.path) this._remember(load.path, load.kind);
    this._nameUnits(load, load.fp);
    this._end(load);
    this._ports.loaded?.(load.kind, load.fp, load.name);
  }

  /** The units typed in the loader are named on dataset `fp`. */
  _nameUnits(load, fp) {
    if (load.kind !== 'dataset') return;
    for (const [dimension, unit] of Object.entries(load.units || {})) {
      this._ports.setUnit?.(fp, dimension, unit);
      saveRecentUnit(dimension, unit);
    }
  }

  _loadFailed(load, err) {
    this._end(load);
    if (err?.connectionLost) return;   // the banner says it
    this._ports.setStatus(`Could not load ${load.name}: ${err.message}`, 'error');
    this._signal('load-failed', { kind: load.kind, error: err.message });
  }

  _end(load) {
    if (this._pending === load) this._pending = null;
  }

  /** The connection ended: no announcement will finish a load now. */
  forgetLoads() {
    this._ports.cancelBeforeLoad?.();
    this._awaiting.clear();
    this._pending = null;
  }

  _load() {
    if (this._pick) {
      const path = pickPath({ mode: this._mode, dir: this._path, name: $('fb-name').value });
      if (!path) return;
      const { onPick, formats, items } = this._pick;
      const format = formats ? $('fb-format').value : null;
      // What is ticked and not greyed out (a greyed box holds nothing).
      const chosen = items ? [...$('fb-export-items').children]
        .filter((l) => !l.querySelector('input').disabled && l.querySelector('input').checked)
        .map((l) => l.dataset.key) : null;
      this._pick = null;
      this.close();
      onPick(path, chosen ? { format, items: chosen } : { format });
      return;
    }
    if (!this._selected) return;
    const path = joinPath(this._path, this._selected);
    if (this._mode === 'prediction') {
      const dsFp = $('fb-target-ds').value;
      const reader = this._predReader;
      // The choice goes as it is: '' means none (ADR 0095). Without a named
      // reader the server's readers for the file's ending read it.
      const eKey = $('fb-energy-key').value;
      const fKey = $('fb-force-key').value;
      if (!dsFp || (!fKey && !eKey)) return;
      // LOAD_PREDICTION reads args=[path, dataset_fp] + key kwargs; on success
      // the server fires REMOTE_MODEL_META → _onModelMeta selects it.
      this._ports.setStatus(`Loading prediction ${this._selected}…`, 'connected');
      this._startLoad(
        { kind: 'prediction', name: this._selected, path, datasetFp: dsFp },
        OUT.LOAD_PREDICTION,
        { selected_energy_key: eKey, selected_force_key: fKey, ...(reader ? { reader } : {}) },
        [path, dsFp],
      );
    } else if (this._mode === 'model') {
      const typ = $('fb-model-type').value;
      // LOAD_MODEL reads args=[path, model_type]; the model runs on the server (ADR 0030).
      this._ports.setStatus(`Loading model ${this._selected}…`, 'connected');
      this._startLoad({ kind: 'model', name: this._selected, path }, OUT.LOAD_MODEL, {}, [path, typ]);
    } else {
      const typ = $('fb-type').value;
      // LOAD_DATASET reads args=[path, datasetType]; the keys travel as kwargs
      // when the file offered a choice, else the server's default keys apply.
      const kwargs = datasetLoadKwargs({
        memoryMode: $('fb-memory-mode')?.value || 'auto',
        keysShown: !$('fb-keys').hidden,
        energyKey: $('fb-ds-energy-key').value,
        forceKey: $('fb-ds-force-key').value,
        predictions: this._predictionRows(),
        stride: this._framesChoice() === 'nth' ? $('fb-stride').value : 1,
        sampling: this._framesChoice() === 'energy' ? this._sampleSettings() : null,
      });
      this._ports.setStatus(`Loading ${this._selected}…`, 'connected');
      this._startLoad({ kind: 'dataset', name: this._selected, path, units: this._unitsTyped() },
                      OUT.LOAD_DATASET, kwargs, [path, typ]);
    }
    // The load joins a count still running on the server; cancelling would not stop it.
    this._large = null;
    this.close();
  }
}

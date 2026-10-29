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
import { icon as drawIcon } from './icons.js';
import { trapFocus } from './dialogs.js';
import { UNIT_PLACEHOLDER } from './units.js';
import { loadRecentUnits, saveRecentUnit, unitChoices } from './recent_units.js';
import { withExtension, EXPORT_FORMATS } from './session_ops.js';
import { loadRecentFiles, rememberFile, saveRecentFiles } from './recent_files.js';

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
 * file; a prediction additionally needs a force key (force arrows are the point
 * of loading one; an .npz prediction carries its own E and F, so it needs no
 * key) and a target dataset to attach to. A dataset waits for its key check.
 */
export function canLoad({
  selected, mode, forceKey, targetDataset, probing = false, isNpz = false, modelType = '',
  dir = null, name = '',
}) {
  if (PICK_MODES.has(mode)) return pickPath({ mode, dir, name }) !== null;
  if (!selected) return false;
  if (mode === 'model') return Boolean(modelType);
  if (mode === 'prediction') return Boolean(targetDataset && (forceKey || isNpz));
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
 * `fallback` when it is offered, else the first type. The desktop's
 * file dialog does this with its filter list.
 * @param {string} name @param {{name: string, extension: string}[]} types
 * @param {string} fallback @returns {string}
 */
export function typeForFile(name, types, fallback = 'ase (auto)') {
  const lower = name.toLowerCase();
  const hit = (types || []).find((t) => t.extension && t.extension !== '*'
    && lower.endsWith(t.extension.replace(/^\*/, '').toLowerCase()));
  if (hit) return hit.name;
  if ((types || []).some((t) => t.name === fallback)) return fallback;
  return types?.[0]?.name ?? fallback;
}

/** Whether a probed file offers a key choice: more than one energy
 * option or more than one force option, the calculator counting as one
 * (the desktop's resolve_key_options, UI/menuLogic.py). */
export function needsKeyChoice(probe) {
  const e = (probe?.energy_keys || []).length + (probe?.has_calculator_energy ? 1 : 0);
  const f = (probe?.force_keys || []).length + (probe?.has_calculator_forces ? 1 : 0);
  return e > 1 || f > 1;
}

/** The key a picker starts on: the first named key, else the calculator,
 * else '' (as the desktop's key dialog). */
export function defaultKey(keys, calculator, calculatorValue) {
  if ((keys || []).length) return keys[0];
  return calculator ? calculatorValue : '';
}

/** From this size on (bytes) a file gets the large-file note, as the
 * desktop's BigDatasetWarningDialog (UI/mainMenu.py, 3 GB). */
export const LARGE_FILE_BYTES = 3_000_000_000;

/** The loader's stride number for "every Nth frame": 1 (or less, or not
 * a number) means all frames, which the loader spells 0 (the desktop's
 * stride_to_slice_num). */
export function strideToSliceNum(stride) {
  const n = Math.floor(Number(stride));
  return Number.isFinite(n) && n > 1 ? n : 0;
}

/** The large-file note: size, frame count when known, and what every
 * Nth frame keeps (the desktop's RemoteStrideDialog estimate). */
export function largeFileNote({ size, n = null, stride = 1, error = null }) {
  const head = `This file is ${formatSize(size)}`;
  if (error) return `${head}. The frames could not be counted (${error}).`;
  if (n === null || n === undefined) return `${head}. Counting frames…`;
  const step = strideToSliceNum(stride);
  const all = `${head}: ${n.toLocaleString('en-US')} frames.`;
  if (!step) return `${all} Set "Every Nth frame" to load fewer.`;
  return `${all} Every ${step}th frame: ~${Math.max(1, Math.ceil(n / step)).toLocaleString('en-US')}.`;
}

/** Whether a file name is an .npz file. */
export function isNpzFile(name) {
  return /\.npz$/i.test(name || '');
}

/** A prediction's default name from its key: the part before the first
 * `_`, `-` or `.` (MACE_energy → MACE), else 'Model' (the desktop's
 * KeySelectionDialog prefix rule). */
export function keyPrefix(key) {
  const m = /^([^_.\-]+)[_.\-]/.exec(key || '');
  return m ? m[1] : 'Model';
}

/**
 * The LOAD_DATASET kwargs the loader's choices make. Empty when nothing
 * was chosen, so a plain file sends what it always sent. Extra key pairs
 * (`predictions`) go as `prediction_keys` [energy, force, name]; an empty key
 * means "calculator" to the server, as on the desktop.
 * @param {{keysShown?: boolean, energyKey?: string, forceKey?: string,
 *   predictions?: {energyKey: string, forceKey: string, name: string}[],
 *   stride?: number|string}} c  `stride` > 1 sends `slice_num`, keys shown or not.
 */
export function datasetLoadKwargs({
  keysShown = false, energyKey = '', forceKey = '', predictions = [], stride = 1,
} = {}) {
  const kw = {};
  if (keysShown) {
    kw.selected_energy_key = energyKey || null;
    kw.selected_force_key = forceKey || null;
  }
  if (keysShown && predictions.length) {
    kw.prediction_keys = predictions.map(
      ({ energyKey: e, forceKey: f, name }) => [e || '', f || '', name || 'Model']);
  }
  const sliceNum = strideToSliceNum(stride);
  if (sliceNum) kw.slice_num = sliceNum;
  return kw;
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
    this._largeBytes = LARGE_FILE_BYTES;   // tests lower it
    this._large = null;   // {path, size, n, error} of a large selected file
    this._probe = null;   // the last key check's answer, for new prediction lines
    this._probing = null;   // the path whose key check is still out
    this._loadWhenReady = false;   // load once the key check is back
    this._pick = null;   // {onPick}: the browser is choosing where a session or export goes
    this._pending = null;   // {kind, name, path}: a load with no outcome yet
    this._asks = new Map();   // folder asked for suggestions → replies still to come
    this._suggest = { items: [], active: -1, dir: '' };
    this._suggestTimer = null;
    this._selectAfterListing = null;   // a Recent file to select once its folder is listed
    this._focusAfterListing = null;    // the folder just left, focused once the parent is listed
  }

  /** Wire the modal's own controls. Called once, at app setup. */
  bindControls() {
    $('fb-cancel').addEventListener('click', () => this.close());
    $('fb-load').addEventListener('click', () => this._load());
    $('fb-force-key').addEventListener('change', () => this._refreshLoadEnabled());
    $('fb-type').addEventListener('change', () => this._refreshDatasetKeys());
    $('fb-name').addEventListener('input', () => this._refreshLoadEnabled());
    $('fb-format').addEventListener('change', () => {
      const chosen = (this._pick?.formats || EXPORT_FORMATS).find((f) => f.format === $('fb-format').value);
      if (chosen) $('fb-name').value = withExtension($('fb-name').value, chosen.ext);
      this._refreshLoadEnabled();
    });
    $('fb-name').addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      if (!$('fb-load').disabled) this._load();
    });
    $('fb-stride').addEventListener('input', () => this._drawLarge());
    $('fb-add-pred').addEventListener('click', () => this._addPredictionRow());
    $('fb-up').addEventListener('click', () => {
      if (this._parent) this._navigate(this._parent);
    });
    $('fb-home').addEventListener('click', () => this._navigate(this._home || null));
    $('fb-modal').addEventListener('click', (e) => {
      if (e.target.id === 'fb-modal') this.close();
    });
    $('fb-modal').addEventListener('keydown', (e) => {
      if (e.key === 'Escape') this.close();
    });
    // The path can be typed: Enter goes there.
    $('fb-path').addEventListener('keydown', (e) => this._pathKey(e));
    $('fb-path').addEventListener('input', () => this._askSuggestions());
    $('fb-path').addEventListener('blur', () => setTimeout(() => this._hideSuggestions(), 150));
    // One listener for the whole list: its rows are rebuilt on every folder change.
    $('fb-list').addEventListener('keydown', (e) => this._listKey(e));
  }

  /** The server's loaders (LOADER_TYPES, on connect): fill the Type list. */
  setLoaderTypes(kw) {
    this._types = { datasets: kw.datasets || [], models: kw.models || [] };
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

  /** After a short pause, list the typed folder. The reply is told apart from
   * a navigation's by the path it echoes (`onDirListing`). */
  _askSuggestions() {
    clearTimeout(this._suggestTimer);
    this._suggestTimer = setTimeout(() => {
      const parts = typedParts($('fb-path').value);
      if (!parts) { this._hideSuggestions(); return; }
      this._asks.set(parts.dir, (this._asks.get(parts.dir) || 0) + 1);
      this._ports.send(OUT.LIST_DIR, {}, [parts.dir]);
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

  /** @param {'dataset'|'prediction'|'model'} mode */
  open(mode = 'dataset') {
    this._mode = mode;
    const titles = { dataset: 'Load Remote Dataset', prediction: 'Load Prediction', model: 'Load Model' };
    $('fb-title').textContent = titles[mode];
    $('fb-dataset-fields').style.display = mode === 'dataset' ? '' : 'none';
    $('fb-prediction-fields').style.display = mode === 'prediction' ? 'inline-flex' : 'none';
    $('fb-model-fields').style.display = mode === 'model' ? 'inline-flex' : 'none';
    $('fb-pick-fields').hidden = true;
    $('fb-load').textContent = 'Load';
    this._pick = null;
    const isPred = mode === 'prediction';
    if (isPred) this._populatePredictionTargets();
    this._renderRecent();
    this._renderUnitChoices();
    this._show();
  }

  /**
   * Choose where a session or an export goes, in the same browser: a folder
   * to save into with a name ('save-folder'), a folder to open
   * ('open-folder'), or a file name ('save-file', clicking a file puts its name
   * in the box).
   * @param {{mode: 'save-folder'|'open-folder'|'save-file', title: string, okLabel: string,
   *   name?: string, formats?: Array<{format: string, label: string, ext: string}>,
   *   format?: string, onPick: (path: string, choice: {format: string|null}) => void}} o
   */
  pick({ mode, title, okLabel, name = '', formats = null, format = null, onPick }) {
    this._mode = mode;
    this._pick = { onPick, formats };
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

  /** The part of opening the modal every mode shares. */
  _show() {
    $('fb-modal').classList.remove('hidden');
    this._hideSuggestions();
    this._untrap ||= trapFocus($('fb-modal'));
    this._focusFirstRow = true;   // once the listing arrives (_render)
    this._selected = null;
    $('fb-load').disabled = true;
    this._loadWhenReady = false;
    this._probing = null;
    $('fb-keys').hidden = true;
    $('fb-pred-rows').replaceChildren();
    $('fb-stride').value = '1';
    $('fb-large').hidden = true;
    this._large = null;
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

  close() {
    this._loadWhenReady = false;
    this._probing = null;
    this._untrap?.();
    this._untrap = null;
    $('fb-modal').classList.add('hidden');
  }

  /** Server replied to LIST_DIR. */
  onDirListing(kw, args = []) {
    // A reply to a suggestion request, not a navigation.
    const asked = this._asks.get(args[1]) || 0;
    if (asked > 0) {
      if (asked === 1) this._asks.delete(args[1]); else this._asks.set(args[1], asked - 1);
      this._showSuggestions(kw, args[1]);
      return;
    }
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
    $('fb-path').value = kw.path || '';
    $('fb-up').disabled = !kw.parent;
    this._render(kw.entries || []);
    this._refreshLoadEnabled();   // in a pick mode the folder on screen enables the button
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

  /** Server replied to PROBE_DATASET_KEYS for `path`. */
  onDatasetKeys(path, kw) {
    if (path !== this._probing) return;   // a file no longer selected
    this._probing = null;
    if (this._mode === 'prediction') {
      this._fillPredictionKeys(kw);
    } else if (this._mode === 'dataset') {
      this._showDatasetKeys(kw);
    }
    this._refreshLoadEnabled();
    this._loadIfReady();
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
        calculator: kw.has_calculator_forces, calculatorValue: 'forces',
      }),
    );
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

  /** One more key pair to load as a prediction (as the desktop's
   * "Add Prediction"). Starts on the first keys the reference is not using. */
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
  _probeKeys(path) {
    this._probing = path;
    // Server route requires (path, dataset_type); ASE auto-detect reads the keys.
    this._ports.send(OUT.PROBE_DATASET_KEYS, {}, [path, 'ase (auto)']);
  }

  /** The Keys row follows the selected file and the Type: only an ASE
   * file is probed; any other type has no keys to choose. */
  _refreshDatasetKeys() {
    $('fb-keys').hidden = true;
    this._probing = null;
    if (this._selected && $('fb-type').value === 'ase (auto)') {
      this._probeKeys(joinPath(this._path, this._selected));
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
    this._selected = null;
    $('fb-load').disabled = true;
    // path travels as a positional arg; server reads args[0]
    this._ports.send(OUT.LIST_DIR, {}, [path]);
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
      if (e.is_dir) {
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
    for (const r of document.querySelectorAll('#fb-list .fb-row.selected')) {
      r.classList.remove('selected');
    }
    row.classList.add('selected');
    if (this._mode === 'prediction') {
      if (isNpzFile(name)) {
        // An .npz prediction holds its own E and F: nothing to probe or choose.
        const only = [{ value: '', label: 'from the .npz file' }];
        this._fillSelect($('fb-energy-key'), only);
        this._fillSelect($('fb-force-key'), only);
        this._probing = null;
      } else {
        // Probe the chosen file for the energy/force keys it actually contains.
        const path = joinPath(this._path, name);
        $('fb-energy-key').innerHTML = '<option value="">…probing…</option>';
        $('fb-force-key').innerHTML = '<option value="">…probing…</option>';
        this._probeKeys(path);
      }
    }
    if (this._mode === 'dataset') {
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
    this._large = big ? { path, size, n: null, error: null } : null;
    $('fb-large').hidden = !big;
    if (!big) return;
    if ($('fb-type').value === 'ase (auto)') this._ports.send(OUT.PROBE_DATASET_LENGTH, {}, [path]);
    else this._large.error = 'only ASE files are counted';
    this._drawLarge();
  }

  _drawLarge() {
    if (!this._large) return;
    $('fb-large').textContent = largeFileNote({ ...this._large, stride: $('fb-stride').value });
  }

  /** Server replied to PROBE_DATASET_LENGTH. */
  onDatasetLength(path, kw) {
    if (!this._large || this._large.path !== path) return;
    this._large.n = kw.n ?? null;
    this._large.error = kw.error || null;
    this._drawLarge();
  }

  _refreshLoadEnabled() {
    $('fb-load').disabled = !canLoad({
      selected: this._selected,
      mode: this._mode,
      forceKey: $('fb-force-key').value,
      targetDataset: $('fb-target-ds').value,
      probing: this._mode === 'dataset' && Boolean(this._probing),
      isNpz: this._mode === 'prediction' && isNpzFile(this._selected),
      modelType: $('fb-model-type').value,
      dir: this._path,
      name: $('fb-name').value,
    });
  }

  /** File ▸ Load Zero Model: no file to pick. Its "Loading…" ends like a
   * model's (REMOTE_MODEL_META, or "already loaded" when the task ends). */
  loadZeroModel() {
    this._ports.send(OUT.LOAD_ZERO_MODEL, {}, []);
    this._ports.setStatus('Loading Zero Model…', 'connected');
    this._pending = { kind: 'model', name: 'Zero Model', path: null };
  }

  /** Which load this window is waiting for: 'dataset', 'prediction', 'model' or null. */
  pendingKind() {
    return this._pending?.kind ?? null;
  }

  /** A new dataset or model arrived, or a load failed: the "Loading…" status
   * of a pending load of that `kind` ('dataset', 'prediction' or 'model') is replaced,
   * so a later task end must not touch it. True if one was pending. */
  settled(kind, ok = true, fp = null) {
    if (this._pending?.kind !== kind) return false;
    if (ok && this._pending.path) this._remember(this._pending.path, kind);
    // The units typed in the loader are named on the dataset that arrived.
    if (ok && kind === 'dataset' && fp) {
      for (const [dimension, unit] of Object.entries(this._pending.units || {})) {
        this._ports.setUnit?.(fp, dimension, unit);
        saveRecentUnit(dimension, unit);
      }
    }
    this._pending = null;
    return true;
  }

  /** A task ended. A dataset or prediction the server already holds announces
   * nothing new, so a task that ends while a load is still pending is that
   * duplicate: end its status. */
  onTaskDone() {
    if (!this._pending) return;
    this._ports.setStatus(`"${this._pending.name}" is already loaded`, 'connected');
    if (this._pending.path) this._remember(this._pending.path, this._pending.kind);
    this._pending = null;
  }

  _load() {
    if (this._pick) {
      const path = pickPath({ mode: this._mode, dir: this._path, name: $('fb-name').value });
      if (!path) return;
      const { onPick, formats } = this._pick;
      const format = formats ? $('fb-format').value : null;
      this._pick = null;
      this.close();
      onPick(path, { format });
      return;
    }
    if (!this._selected) return;
    const path = joinPath(this._path, this._selected);
    if (this._mode === 'prediction') {
      const dsFp = $('fb-target-ds').value;
      const eKey = $('fb-energy-key').value || null;
      const fKey = $('fb-force-key').value || null;
      if (!dsFp || (!fKey && !isNpzFile(this._selected))) return;
      // LOAD_PREDICTION reads args=[path, dataset_fp] + key kwargs; on success
      // the server fires REMOTE_MODEL_META → _onModelMeta selects it.
      this._ports.send(
        OUT.LOAD_PREDICTION,
        { selected_energy_key: eKey, selected_force_key: fKey },
        [path, dsFp],
      );
      this._ports.setStatus(`Loading prediction ${this._selected}…`, 'connected');
      this._pending = { kind: 'prediction', name: this._selected, path };
    } else if (this._mode === 'model') {
      const typ = $('fb-model-type').value;
      // LOAD_MODEL reads args=[path, model_type]; the model runs on the server (ADR 0030).
      this._ports.send(OUT.LOAD_MODEL, {}, [path, typ]);
      this._ports.setStatus(`Loading model ${this._selected}…`, 'connected');
      this._pending = { kind: 'model', name: this._selected, path };
    } else {
      const typ = $('fb-type').value;
      // LOAD_DATASET reads args=[path, datasetType]; the keys travel as kwargs
      // when the file offered a choice, else the server's default keys apply.
      const kwargs = datasetLoadKwargs({
        keysShown: !$('fb-keys').hidden,
        energyKey: $('fb-ds-energy-key').value,
        forceKey: $('fb-ds-force-key').value,
        predictions: this._predictionRows(),
        stride: $('fb-stride').value,
      });
      this._ports.send(OUT.LOAD_DATASET, kwargs, [path, typ]);
      this._ports.setStatus(`Loading ${this._selected}…`, 'connected');
      this._pending = { kind: 'dataset', name: this._selected, path, units: this._unitsTyped() };
    }
    this.close();
  }
}

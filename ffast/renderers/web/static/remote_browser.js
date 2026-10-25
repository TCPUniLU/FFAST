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
 * Two pure helpers (`joinPath`, `formatSize`) are exported for direct unit
 * testing; the rest needs the modal's DOM.
 */

import { OUT } from './events.js';
import { icon as drawIcon } from './icons.js';
import { trapFocus } from './dialogs.js';
import { UNIT_PLACEHOLDER } from './units.js';
import { loadRecentUnits, saveRecentUnit, unitChoices } from './recent_units.js';
import { loadRecentFiles, rememberFile, saveRecentFiles } from './recent_files.js';

/** Join a server-side directory and an entry name. Posix separators: the paths
 *  come from the server, so they are formatted in its convention, not the
 *  browser's. */
export function joinPath(dir, name) {
  if (!dir) return name;
  return dir.endsWith('/') ? dir + name : dir + '/' + name;
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

/**
 * Decide whether the modal's Load button can be pressed.
 *
 * Pure so the rule is testable without the DOM. A dataset needs only a selected
 * file; a prediction additionally needs a force key (force arrows are the point
 * of loading one) and a target dataset to attach to.
 */
export function canLoad({ selected, mode, forceKey, targetDataset }) {
  if (!selected) return false;
  if (mode === 'prediction') return Boolean(forceKey && targetDataset);
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
    this._pending = null;   // {kind, name, path}: a load with no outcome yet
    this._asks = new Map();   // folder asked for suggestions → replies still to come
    this._suggest = { items: [], active: -1, dir: '' };
    this._suggestTimer = null;
    this._selectAfterListing = null;   // a Recent file to select once its folder is listed
  }

  /** Wire the modal's own controls. Called once, at app setup. */
  bindControls() {
    $('fb-cancel').addEventListener('click', () => this.close());
    $('fb-load').addEventListener('click', () => this._load());
    $('fb-force-key').addEventListener('change', () => this._refreshLoadEnabled());
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

  /** Fill the box with a recent file. A dataset loads at once; a prediction
   * needs its energy and force keys chosen, so its file is selected for that. */
  _useRecent(path) {
    const i = path.lastIndexOf('/');
    const dir = i <= 0 ? '/' : path.slice(0, i);
    const name = path.slice(i + 1);
    $('fb-path').value = path;
    if (this._mode === 'dataset') {
      this._path = dir;
      this._selected = name;
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

  /** @param {'dataset'|'prediction'} mode */
  open(mode = 'dataset') {
    this._mode = mode;
    const isPred = mode === 'prediction';
    $('fb-title').textContent = isPred ? 'Load Prediction' : 'Load Remote Dataset';
    $('fb-dataset-fields').style.display = isPred ? 'none' : '';
    $('fb-prediction-fields').style.display = isPred ? 'inline-flex' : 'none';
    if (isPred) this._populatePredictionTargets();
    $('fb-modal').classList.remove('hidden');
    this._hideSuggestions();
    this._renderRecent();
    this._renderUnitChoices();
    this._untrap ||= trapFocus($('fb-modal'));
    this._focusFirstRow = true;   // once the listing arrives (_render)
    this._selected = null;
    $('fb-load').disabled = true;
    // null path → server starts at its home directory
    this._navigate(this._path || null);
  }

  close() {
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
    if (this._selectAfterListing) {
      const name = this._selectAfterListing;
      this._selectAfterListing = null;
      const row = [...document.querySelectorAll('#fb-list .fb-row.file')]
        .find((r) => r.querySelector('.name')?.textContent === name);
      if (row) this._selectFile(row, name);
    }
  }

  /** Server replied to PROBE_DATASET_KEYS. Ignored outside prediction mode. */
  onDatasetKeys(path, kw) {
    if (this._mode !== 'prediction') return;
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
      row.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); row.click(); }
      });
      if (e.is_dir) {
        row.addEventListener('click', () => this._navigate(joinPath(this._path, e.name)));
      } else {
        row.addEventListener('click', () => this._selectFile(row, e.name));
        row.addEventListener('dblclick', () => {
          this._selectFile(row, e.name);
          if (!$('fb-load').disabled) this._load();
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
      // Probe the chosen file for the energy/force keys it actually contains.
      const path = joinPath(this._path, name);
      $('fb-energy-key').innerHTML = '<option value="">…probing…</option>';
      $('fb-force-key').innerHTML = '<option value="">…probing…</option>';
      // Server route requires (path, dataset_type); ASE auto-detect reads the keys.
      this._ports.send(OUT.PROBE_DATASET_KEYS, {}, [path, 'ase (auto)']);
    }
    this._refreshLoadEnabled();
  }

  _refreshLoadEnabled() {
    $('fb-load').disabled = !canLoad({
      selected: this._selected,
      mode: this._mode,
      forceKey: $('fb-force-key').value,
      targetDataset: $('fb-target-ds').value,
    });
  }

  /** A new dataset or model arrived, or a load failed: the "Loading…" status
   * of a pending load of that `kind` ('dataset' or 'prediction') is replaced,
   * so a later task end must not touch it. True if one was pending. */
  settled(kind, ok = true, fp = null) {
    if (this._pending?.kind !== kind) return false;
    if (ok) this._remember(this._pending.path, kind);
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
    this._remember(this._pending.path, this._pending.kind);
    this._pending = null;
  }

  _load() {
    if (!this._selected) return;
    const path = joinPath(this._path, this._selected);
    if (this._mode === 'prediction') {
      const dsFp = $('fb-target-ds').value;
      const eKey = $('fb-energy-key').value || null;
      const fKey = $('fb-force-key').value || null;
      if (!dsFp || !fKey) return;
      // LOAD_PREDICTION reads args=[path, dataset_fp] + key kwargs; on success
      // the server fires REMOTE_MODEL_META → _onModelMeta selects it.
      this._ports.send(
        OUT.LOAD_PREDICTION,
        { selected_energy_key: eKey, selected_force_key: fKey },
        [path, dsFp],
      );
      this._ports.setStatus(`Loading prediction ${this._selected}…`, 'connected');
      this._pending = { kind: 'prediction', name: this._selected, path };
    } else {
      const typ = $('fb-type').value;
      // LOAD_DATASET reads args=[path, datasetType]; "ase (auto)" auto-detects keys
      this._ports.send(OUT.LOAD_DATASET, {}, [path, typ]);
      this._ports.setStatus(`Loading ${this._selected}…`, 'connected');
      this._pending = { kind: 'dataset', name: this._selected, path, units: this._unitsTyped() };
    }
    this.close();
  }
}

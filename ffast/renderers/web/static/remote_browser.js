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
   * }} ports
   */
  constructor(ports) {
    this._ports = ports;
    this._mode = 'dataset';   // 'dataset' | 'prediction'
    this._path = null;        // current directory abspath (server-side)
    this._parent = null;      // parent abspath, or null at root
    this._home = null;        // server user's home directory
    this._selected = null;    // selected filename within _path
    this._pendingPrediction = null;   // file name whose load has no outcome yet
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
    $('fb-path').addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      const path = $('fb-path').value.trim();
      if (path) this._navigate(path);
    });
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
  onDirListing(kw) {
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

  /** A model arrived or the load failed: the "Loading prediction…" status is
   * replaced, so a later task end must not touch it. True if one was pending. */
  predictionSettled() {
    const pending = this._pendingPrediction !== null;
    this._pendingPrediction = null;
    return pending;
  }

  /** A task ended. A prediction already held announces nothing new, so a task
   * that ends while one is still pending is that duplicate: end its status. */
  onTaskDone() {
    if (!this._pendingPrediction) return;
    this._ports.setStatus(`"${this._pendingPrediction}" is already loaded`, 'connected');
    this._pendingPrediction = null;
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
      this._pendingPrediction = this._selected;
    } else {
      const typ = $('fb-type').value;
      // LOAD_DATASET reads args=[path, datasetType]; "ase (auto)" auto-detects keys
      this._ports.send(OUT.LOAD_DATASET, {}, [path, typ]);
      this._ports.setStatus(`Loading ${this._selected}…`, 'connected');
    }
    this.close();
  }
}

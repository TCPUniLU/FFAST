/**
 * Application — wires UI, connection, and renderer together.
 */

import { FFastConnection } from './connection.js';
import { MoleculeRenderer } from './renderer.js';

export class FFastApp {
  constructor() {
    this._conn = null;
    this._renderer = null;
    this._datasets = new Map();   // fingerprint → meta
    this._models = new Map();     // model fingerprint → {name, dataset_fingerprints}
    this._currentDatasetFp = null;  // selected dataset (object rail)
    this._currentModelFp = null;    // selected prediction, or null
    this._currentViewId = null;
    this._activeTab = 'loupe';
    this._frameCount = 0;
    this._cameraThrottle = null;
    this._bc = null;          // BroadcastChannel to popped-out loupe tabs
    this._chId = null;
    this._lastScene = null;   // cached snapshot for a late-joining satellite

    // Remote file browser state
    this._fbMode = 'dataset';  // 'dataset' | 'prediction'
    this._fbPath = null;       // current directory abspath (server-side)
    this._fbParent = null;     // parent abspath, or null at root
    this._fbHome = null;       // server user's home directory
    this._fbSelected = null;   // selected filename within _fbPath

    this._initRenderer();
    this._initTabs();
    this._initUI();
    this._applyUrlParams();
  }

  // ── tabs: 3D Loupe + analysis tabs (mirrors the Qt MainContentTabWidget) ──
  // The analysis tabs are placeholders until ADR 0043 ships REQUEST_TAB_LAYOUT
  // (server-owned panel specs) + the metric channel. Names/order mirror the
  // desktop's ffast/config/builtin_tabs/*.toml so the shells read the same.
  _initTabs() {
    const TABS = [
      { id: 'loupe',     name: '3D Loupe' },
      { id: 'basic',     name: 'Basic Errors',     placeholder: true },
      { id: 'subsystem', name: 'Subsystem Errors', placeholder: true },
      { id: 'atomic',    name: 'Atomic Errors',    placeholder: true },
      { id: 'gyration',  name: 'Gyration',         placeholder: true },
    ];
    const tabbar = document.getElementById('tabbar');
    const panels = document.getElementById('tabpanels');
    for (const t of TABS) {
      const tab = document.createElement('div');
      tab.className = 'tab' + (t.id === this._activeTab ? ' active' : '');
      tab.textContent = t.name;
      tab.dataset.tab = t.id;
      tab.addEventListener('click', () => this._selectTab(t.id));
      tabbar.appendChild(tab);

      if (t.placeholder) {
        const panel = document.createElement('div');
        panel.className = 'tabpanel';
        panel.id = `panel-${t.id}`;
        panel.innerHTML =
          `<div class="placeholder">
             <div class="ph-title">${t.name}</div>
             <div class="ph-sub">2D analysis plots are served over the same WebSocket
               as the desktop. This tab activates once ADR 0043 wires the panel-spec
               event and the metric channel into the browser.</div>
           </div>`;
        panels.appendChild(panel);
      }
    }
  }

  _selectTab(id) {
    this._activeTab = id;
    for (const tab of document.querySelectorAll('#tabbar .tab'))
      tab.classList.toggle('active', tab.dataset.tab === id);
    for (const panel of document.querySelectorAll('#tabpanels .tabpanel'))
      panel.classList.toggle('active', panel.id === `panel-${id}`);
    // Opening/returning to the Loupe with a dataset selected ensures a live view.
    if (id === 'loupe' && this._conn && this._currentDatasetFp) this._openView();
  }

  _initRenderer() {
    const canvas = document.getElementById('canvas');
    this._renderer = new MoleculeRenderer(canvas);
    this._renderer._onCameraChange = (cam) => this._sendSetCamera(cam);
  }

  _initUI() {
    document.getElementById('connect-btn').addEventListener('click', () => this._connect());
    document.getElementById('disconnect-btn').addEventListener('click', () => this._disconnect());
    document.getElementById('reset-camera-btn').addEventListener('click', () => {
      this._renderer.resetCamera();
    });
    document.getElementById('popout-btn').addEventListener('click', () => this._openPopout());

    const slider = document.getElementById('frame-slider');
    slider.addEventListener('input', () => this._onFrameSlider());

    // Object rail load actions — dataset vs prediction mode.
    document.getElementById('add-dataset-btn').addEventListener('click', () => this._openFileBrowser('dataset'));
    document.getElementById('add-prediction-btn').addEventListener('click', () => this._openFileBrowser('prediction'));
    document.getElementById('fb-cancel').addEventListener('click', () => this._closeFileBrowser());
    document.getElementById('fb-load').addEventListener('click', () => this._fbLoad());
    document.getElementById('fb-force-key').addEventListener('change', () => this._updateFbLoadEnabled());
    document.getElementById('fb-up').addEventListener('click', () => {
      if (this._fbParent) this._fbNavigate(this._fbParent);
    });
    document.getElementById('fb-home').addEventListener('click', () => {
      this._fbNavigate(this._fbHome || null);
    });
    document.getElementById('fb-modal').addEventListener('click', (e) => {
      if (e.target.id === 'fb-modal') this._closeFileBrowser();
    });
  }

  _applyUrlParams() {
    const p = new URLSearchParams(window.location.search);
    const port  = p.get('port');
    const token = p.get('token');
    if (port) {
      const host = window.location.hostname || 'localhost';
      document.getElementById('ws-url').value = `ws://${host}:${port}`;
    }
    if (token) document.getElementById('token-input').value = token;
  }

  async _connect() {
    const wsUrl = document.getElementById('ws-url').value.trim();
    const token = document.getElementById('token-input').value.trim() || null;

    this._setStatus('Connecting…', '');

    try {
      const conn = new FFastConnection(wsUrl, token);

      conn.on('REMOTE_DATASET_META', (kw, args) => this._onDatasetMeta(args[0], kw));
      conn.on('REMOTE_MODEL_META',   (kw, args) => this._onModelMeta(args[0], kw));
      conn.on('DATASET_KEYS_RESPONSE', (kw, args) => this._onDatasetKeys(args[0], kw));
      conn.on('TASK_CREATED',  (kw) => console.debug('TASK_CREATED', kw));
      conn.on('TASK_PROGRESS', (kw) => console.debug('TASK_PROGRESS', kw));
      conn.on('TASK_DONE',     (kw) => console.debug('TASK_DONE', kw));
      conn.on('TASK_FAILED',   (kw) => console.warn('TASK_FAILED', kw));
      conn.on('DATASET_LOADED', () => {});
      conn.on('MODEL_LOADED',   () => {});
      conn.on('SCENE_SNAPSHOT', (kw) => this._onSceneSnapshot(kw));
      conn.on('SCENE_PATCH',    (kw) => this._onScenePatch(kw));
      conn.on('COMMAND_RESULT', (kw) => this._onCommandResult(kw));
      conn.on('DIR_LISTING',    (kw) => this._onDirListing(kw));

      await conn.connect();
      this._conn = conn;
      this._setupBroadcast(wsUrl);

      this._setStatus(`Connected (${conn.role})`, 'connected');
      document.getElementById('connect-btn').disabled = true;
      document.getElementById('disconnect-btn').disabled = false;
      document.getElementById('add-dataset-btn').disabled = false;
      document.getElementById('add-prediction-btn').disabled = false;
      this._renderObjects();

    } catch (err) {
      console.error('Connection failed:', err);
      this._setStatus(`Error: ${err.message}`, 'error');
    }
  }

  _disconnect() {
    if (this._conn) {
      this._conn.close();
      this._conn = null;
    }
    if (this._bc) {
      this._bc.postMessage({ t: 'bye' });   // tell popped-out loupes the main view is gone
      this._bc.close();
      this._bc = null;
    }
    this._lastScene = null;
    this._datasets.clear();
    this._models.clear();
    this._currentDatasetFp = null;
    this._currentModelFp = null;
    this._currentViewId = null;
    this._renderObjects();
    document.getElementById('connect-btn').disabled = false;
    document.getElementById('disconnect-btn').disabled = true;
    document.getElementById('add-dataset-btn').disabled = true;
    document.getElementById('add-prediction-btn').disabled = true;
    document.getElementById('reset-camera-btn').disabled = true;
    document.getElementById('popout-btn').disabled = true;
    document.getElementById('frame-slider').disabled = true;
    this._closeFileBrowser();
    this._setStatus('Disconnected', '');
  }

  _onDatasetMeta(fp, meta) {
    this._datasets.set(fp, meta);
    // The first dataset auto-selects so the Loupe has something to show.
    const firstSelect = !this._currentDatasetFp;
    if (firstSelect) this._currentDatasetFp = fp;
    this._renderObjects();
    if (firstSelect && this._conn && this._activeTab === 'loupe') this._openView();
  }

  _onModelMeta(fp, meta) {
    // meta = {name, dataset_fingerprints}. Fired on connect-replay and after
    // a LOAD_PREDICTION completes (the ghost model registers its forces cache).
    this._models.set(fp, meta || {});
    // Auto-select a freshly loaded prediction that applies to the current
    // dataset and refresh the view so its force overlay appears.
    if (this._currentDatasetFp &&
        (meta?.dataset_fingerprints || []).includes(this._currentDatasetFp)) {
      this._currentModelFp = fp;
      this._setStatus(`Prediction "${meta.name || fp.slice(0,8)}" ready`, 'connected');
      if (this._activeTab === 'loupe') this._openView();
    }
    this._renderObjects();
  }

  // ── object rail: datasets + predictions as selectable rows ──────────────
  _renderObjects() {
    this._renderDatasetList();
    this._renderModelList();
  }

  _renderDatasetList() {
    const list = document.getElementById('dataset-list');
    list.innerHTML = '';
    if (this._datasets.size === 0) {
      list.innerHTML = '<div class="obj-empty">— none loaded —</div>';
      return;
    }
    for (const [fp, meta] of this._datasets) {
      const row = document.createElement('div');
      row.className = 'obj-row' + (fp === this._currentDatasetFp ? ' selected' : '');
      row.dataset.fp = fp;
      row.innerHTML =
        `<span class="name">${meta.name || fp.slice(0,8)}</span>` +
        `<span class="meta">${meta.n} fr</span>`;
      row.addEventListener('click', () => this._selectDataset(fp));
      list.appendChild(row);
    }
  }

  _renderModelList() {
    const list = document.getElementById('model-list');
    list.innerHTML = '';
    if (this._models.size === 0) {
      list.innerHTML = '<div class="obj-empty">— none —</div>';
      return;
    }
    const dsFp = this._currentDatasetFp;
    for (const [fp, meta] of this._models) {
      const fps = meta.dataset_fingerprints || [];
      // A prediction applies only to the dataset it was computed for.
      const applies = !dsFp || !fps.length || fps.includes(dsFp);
      const row = document.createElement('div');
      row.className = 'obj-row'
        + (fp === this._currentModelFp ? ' selected' : '')
        + (applies ? '' : ' disabled');
      row.dataset.fp = fp;
      row.innerHTML = `<span class="name">${meta.name || fp.slice(0,8)}</span>`;
      if (applies) row.addEventListener('click', () => this._selectModel(fp));
      list.appendChild(row);
    }
  }

  _selectDataset(fp) {
    this._currentDatasetFp = fp;
    // Drop a prediction that no longer applies to the selected dataset.
    const m = this._models.get(this._currentModelFp);
    const fps = m?.dataset_fingerprints || [];
    if (this._currentModelFp && fps.length && !fps.includes(fp)) this._currentModelFp = null;
    this._renderObjects();
    if (this._activeTab === 'loupe') this._openView();
    else this._selectTab('loupe');
  }

  _selectModel(fp) {
    this._currentModelFp = fp;
    this._renderObjects();
    if (this._activeTab === 'loupe') this._openView();
    else this._selectTab('loupe');
  }

  _openView() {
    if (!this._conn || !this._currentDatasetFp) return;
    this._currentViewId = 'view-0';
    this._conn.send('OPEN_VIEW', {
      view_id: this._currentViewId,
      dataset_ref: this._currentDatasetFp,
      prediction_ref: this._currentModelFp,   // null clears the force overlay
    });
    if (this._bc) document.getElementById('popout-btn').disabled = false;
  }

  // ── remote file browser ────────────────────────────────────────────────

  _openFileBrowser(mode = 'dataset') {
    if (!this._conn) return;
    this._fbMode = mode;
    const isPred = mode === 'prediction';
    document.getElementById('fb-title').textContent = isPred ? 'Load Prediction' : 'Load Remote Dataset';
    document.getElementById('fb-dataset-fields').style.display = isPred ? 'none' : '';
    document.getElementById('fb-prediction-fields').style.display = isPred ? 'inline-flex' : 'none';
    if (isPred) this._populatePredictionTargets();
    document.getElementById('fb-modal').classList.remove('hidden');
    this._fbSelected = null;
    document.getElementById('fb-load').disabled = true;
    // null path → server starts at its home directory
    this._fbNavigate(this._fbPath || null);
  }

  _populatePredictionTargets() {
    // A prediction is loaded *against* an already-loaded dataset.
    const sel = document.getElementById('fb-target-ds');
    sel.innerHTML = '';
    for (const [fp, meta] of this._datasets) {
      const opt = document.createElement('option');
      opt.value = fp;
      opt.textContent = `${meta.name || fp.slice(0,8)} (${meta.n} frames)`;
      sel.appendChild(opt);
    }
    const dsFp = this._currentDatasetFp;
    if (dsFp && this._datasets.has(dsFp)) sel.value = dsFp;
    document.getElementById('fb-energy-key').innerHTML = '';
    document.getElementById('fb-force-key').innerHTML = '';
  }

  _closeFileBrowser() {
    document.getElementById('fb-modal').classList.add('hidden');
  }

  _fbNavigate(path) {
    if (!this._conn) return;
    this._fbSelected = null;
    document.getElementById('fb-load').disabled = true;
    // path travels as a positional arg; server reads args[0]
    this._conn.send('LIST_DIR', {}, [path]);
  }

  _onDirListing(kw) {
    if (kw.error) {
      const err = document.getElementById('fb-error');
      err.style.display = 'block';
      err.textContent = kw.error;
      document.getElementById('fb-list').innerHTML = '';
      // keep the previous path so ↑ still works
      document.getElementById('fb-path').value = kw.path || '';
      return;
    }
    this._fbPath = kw.path;
    this._fbParent = kw.parent;
    if (kw.home) this._fbHome = kw.home;
    document.getElementById('fb-error').style.display = 'none';
    document.getElementById('fb-path').value = kw.path || '';
    document.getElementById('fb-up').disabled = !kw.parent;
    this._fbRender(kw.entries || []);
  }

  _fbRender(entries) {
    const list = document.getElementById('fb-list');
    list.innerHTML = '';
    for (const e of entries) {
      const row = document.createElement('div');
      row.className = `fb-row ${e.is_dir ? 'dir' : 'file'}`;
      const icon = document.createElement('span');
      icon.className = 'icon';
      icon.textContent = e.is_dir ? '📁' : '📄';
      const name = document.createElement('span');
      name.className = 'name';
      name.textContent = e.name;
      row.append(icon, name);
      if (!e.is_dir) {
        const size = document.createElement('span');
        size.className = 'size';
        size.textContent = this._fmtSize(e.size);
        row.append(size);
      }
      if (e.is_dir) {
        row.addEventListener('click', () => this._fbNavigate(this._fbJoin(this._fbPath, e.name)));
      } else {
        row.addEventListener('click', () => this._fbSelectFile(row, e.name));
        row.addEventListener('dblclick', () => {
          this._fbSelectFile(row, e.name);
          if (!document.getElementById('fb-load').disabled) this._fbLoad();
        });
      }
      list.appendChild(row);
    }
  }

  _fbSelectFile(row, name) {
    this._fbSelected = name;
    for (const r of document.querySelectorAll('#fb-list .fb-row.selected')) r.classList.remove('selected');
    row.classList.add('selected');
    if (this._fbMode === 'prediction') {
      // Probe the chosen file for the energy/force keys it actually contains.
      const path = this._fbJoin(this._fbPath, name);
      document.getElementById('fb-energy-key').innerHTML = '<option value="">…probing…</option>';
      document.getElementById('fb-force-key').innerHTML = '<option value="">…probing…</option>';
      // Server route requires (path, dataset_type); ASE auto-detect reads the keys.
      this._conn.send('PROBE_DATASET_KEYS', {}, [path, 'ase (auto)']);
    }
    this._updateFbLoadEnabled();
  }

  _onDatasetKeys(path, kw) {
    if (this._fbMode !== 'prediction') return;
    const fillKeys = (sel, keys, allowNone) => {
      sel.innerHTML = '';
      if (allowNone) {
        const o = document.createElement('option');
        o.value = ''; o.textContent = '— none —';
        sel.appendChild(o);
      }
      for (const k of (keys || [])) {
        const o = document.createElement('option');
        o.value = k; o.textContent = k;
        sel.appendChild(o);
      }
    };
    fillKeys(document.getElementById('fb-energy-key'), kw.energy_keys, true);  // energy optional
    fillKeys(document.getElementById('fb-force-key'), kw.force_keys, false);   // force required for arrows
    if (kw.error) this._setStatus(`Probe error: ${kw.error}`, 'error');
    this._updateFbLoadEnabled();
  }

  _updateFbLoadEnabled() {
    const btn = document.getElementById('fb-load');
    if (!this._fbSelected) { btn.disabled = true; return; }
    if (this._fbMode === 'prediction') {
      const fKey = document.getElementById('fb-force-key').value;
      const tgt = document.getElementById('fb-target-ds').value;
      btn.disabled = !(fKey && tgt);
    } else {
      btn.disabled = false;
    }
  }

  _fbLoad() {
    if (!this._conn || !this._fbSelected) return;
    const path = this._fbJoin(this._fbPath, this._fbSelected);
    if (this._fbMode === 'prediction') {
      const dsFp = document.getElementById('fb-target-ds').value;
      const eKey = document.getElementById('fb-energy-key').value || null;
      const fKey = document.getElementById('fb-force-key').value || null;
      if (!dsFp || !fKey) return;
      // LOAD_PREDICTION reads args=[path, dataset_fp] + key kwargs; on success
      // the server fires REMOTE_MODEL_META → _onModelMeta selects it.
      this._conn.send('LOAD_PREDICTION',
        { selected_energy_key: eKey, selected_force_key: fKey },
        [path, dsFp]);
      this._setStatus(`Loading prediction ${this._fbSelected}…`, 'connected');
    } else {
      const typ = document.getElementById('fb-type').value;
      // LOAD_DATASET reads args=[path, datasetType]; "ase (auto)" auto-detects keys
      this._conn.send('LOAD_DATASET', {}, [path, typ]);
      this._setStatus(`Loading ${this._fbSelected}…`, 'connected');
    }
    this._closeFileBrowser();
  }

  _fbJoin(dir, name) {
    if (!dir) return name;
    return dir.endsWith('/') ? dir + name : dir + '/' + name;
  }

  _fmtSize(bytes) {
    if (!bytes) return '';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0, n = bytes;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
  }

  _onSceneSnapshot(kw) {
    const scene = kw.scene;
    if (!scene) return;
    this._renderer.applyScene(scene);
    this._renderer.frameAtoms();
    document.getElementById('overlay').classList.add('hidden');
    document.getElementById('reset-camera-btn').disabled = false;

    // Update frame slider
    if (scene.view_id) this._currentViewId = scene.view_id;
    const fp = this._getViewDataset(scene);
    if (fp) {
      const meta = this._datasets.get(fp);
      const n = meta?.n || 1;
      this._frameCount = n;
      const slider = document.getElementById('frame-slider');
      slider.max = n - 1;
      slider.value = 0;
      slider.disabled = false;
      this._updateFrameLabel(0, n);
    }

    // Mirror to any popped-out loupe tab.
    this._lastScene = scene;
    this._broadcastScene();
  }

  _onScenePatch(kw) {
    const patch = kw.patch || kw;
    if (!patch) return;
    const changed = patch.changed || [];
    this._renderer.applyPatch(patch, changed);
    if (this._bc) this._bc.postMessage({ t: 'patch', patch, changed });
  }

  _onCommandResult(kw) {
    const result = kw.result || kw;
    if (!result?.success) {
      console.warn('VIEW_COMMAND failed:', result?.error);
    }
  }

  _onFrameSlider() {
    const frame = parseInt(document.getElementById('frame-slider').value, 10);
    this._updateFrameLabel(frame, this._frameCount);
    this._sendSetFrame(frame);
    this._broadcastMeta();  // keep any popped-out loupe's slider in step
  }

  _sendSetFrame(frame) {
    if (!this._conn || !this._currentViewId) return;
    this._conn.send('VIEW_COMMAND', {
      type: 'SET_FRAME',
      view_id: this._currentViewId,
      view_version: 0,  // server applies SET_FRAME without version check
      frame_index: frame,
    });
  }

  // ── popped-out loupe (BroadcastChannel satellite) ───────────────────────
  // The main tab owns the single WebSocket; a popped tab renders scenes we
  // relay here and posts frame intents back, which we drive over the WS.
  _setupBroadcast(wsUrl) {
    if (typeof BroadcastChannel === 'undefined') return;  // unsupported browser
    this._chId = 'ffast-loupe:' + wsUrl;
    this._bc = new BroadcastChannel(this._chId);
    this._bc.onmessage = (e) => this._onBroadcast(e.data);
  }

  _broadcastScene() {
    if (!this._bc || !this._lastScene) return;
    this._bc.postMessage({ t: 'scene', scene: this._lastScene });
    this._broadcastMeta();
  }

  _broadcastMeta() {
    if (!this._bc) return;
    const slider = document.getElementById('frame-slider');
    this._bc.postMessage({
      t: 'meta',
      frameCount: this._frameCount,
      frameIndex: parseInt(slider.value, 10) || 0,
      title: this._datasets.get(this._currentDatasetFp)?.name || '',
    });
  }

  _onBroadcast(msg) {
    if (!msg || !msg.t) return;
    if (msg.t === 'hello') {
      // A satellite loupe just opened — hand it the current scene + meta.
      this._broadcastScene();
    } else if (msg.t === 'frame') {
      // Satellite drove the frame; reflect it here and over the WS. Do NOT
      // re-broadcast meta (the satellite's own slider is already there).
      const slider = document.getElementById('frame-slider');
      slider.value = msg.index;
      this._updateFrameLabel(msg.index, this._frameCount);
      this._sendSetFrame(msg.index);
    }
  }

  _openPopout() {
    if (!this._chId || !this._currentDatasetFp) return;
    const url = new URL(window.location.href);
    url.searchParams.set('mode', 'loupe');
    url.searchParams.set('ch', this._chId);
    window.open(url.toString(), '_blank', 'noopener');
    // The new tab sends 'hello' on load; _onBroadcast replies with the scene.
  }

  _sendSetCamera(cam) {
    if (!this._conn || !this._currentViewId) return;
    clearTimeout(this._cameraThrottle);
    this._cameraThrottle = setTimeout(() => {
      this._conn.send('VIEW_COMMAND', {
        type: 'SET_CAMERA',
        view_id: this._currentViewId,
        camera: cam,
      });
    }, 100);
  }

  _updateFrameLabel(frame, total) {
    document.getElementById('frame-label').textContent = `${frame} / ${Math.max(0, total - 1)}`;
  }

  _getViewDataset(scene) {
    // The scene belongs to the currently selected dataset (object rail).
    return this._currentDatasetFp;
  }

  _setStatus(text, cls) {
    const el = document.getElementById('status');
    el.textContent = text;
    el.className = cls;
  }
}

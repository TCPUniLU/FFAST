/**
 * The independent 3D panels of every tab (ADR 0056 rules 2, 3 and 7): which
 * exist, the server view each opens, the data it shows, and how its frame and
 * camera follow the main view.
 *
 * A panel keeps its view across a layout rebuild that leaves it in the same
 * place with the same settings, and its view goes when a layout drops it. A
 * new panel shows the first pair its tab's picker has that no other 3D panel
 * in the tab shows. A frame-linked panel shows the main view's structure; a
 * camera-linked one turns with it.
 *
 * What is the sidebar's (the starting look, per-panel settings, the Panel
 * section, playback) stays in the app, reached through the ports.
 */

import { OUT } from './events.js';
import { linkedFrame } from './frame_links.js';
import { cleanStart } from './start_settings.js';
import { isLinked3D, showsMainView, startPair } from './tab_rules.js';
import { IndependentView } from './views3d.js';

/** An independent 3D panel's key: its tab and its place in the grid, which
 * removing another panel does not change (its index in the list would). */
export function independentKey(tabName, spec) {
  return `${tabName}#${spec.row},${spec.col}`;
}

/**
 * @typedef {{
 *   send: (event: string, kwargs: object) => void,
 *   connected: () => boolean,
 *   datasets: () => Map<string, object>,
 *   main: () => {datasetFp: string|null, modelFp: string|null, frame: number},
 *   mainView: () => import('./views3d.js').ViewRenderers,
 *   sendMainCamera: (cam: object) => void,
 *   focused: () => IndependentView|null,
 *   activeTab: () => object|null,
 *   tabPairs: (tabId: string) => Array<{datasetFp: string, modelFp: string|null}>,
 *   pairName: (datasetFp: string, modelFp: string|null) => string,
 *   framed: () => Set<string>,
 *   placePanel: (el: HTMLElement, panel: object) => void,
 *   atomScale: () => number,
 *   start: (view: IndependentView) => void,
 *   retired: (view: IndependentView) => void,
 *   chosen: (view: IndependentView) => void,
 *   focusedCamera: (cam: object) => void,
 *   frameShown: (view: IndependentView) => void,
 *   sceneAtoms: (view: IndependentView, atoms: object) => void,
 * }} IndependentPorts
 * `framed`: view ids whose first snapshot fitted the atoms (shared with the
 * main view). `start`: give a newly opened view its starting look.
 * `retired`: a layout dropped the view (the sidebar lets go of it). `chosen`:
 * the focused view's data changed. `focusedCamera`: the focused view's camera
 * moved. `frameShown`: a view was set to a frame. `sceneAtoms`: a view's
 * scene brought atoms.
 */

export class IndependentPanels {
  /** @param {IndependentPorts} ports */
  constructor(ports) {
    this._ports = ports;
    /** @type {Map<string, IndependentView>} by `independentKey` */
    this._views = new Map();
    this._count = 0;
    this._linkingCameras = false;
  }

  get(key) { return this._views.get(key); }
  values() { return this._views.values(); }

  /** The independent view a scene belongs to, if any. */
  byViewId(viewId) {
    for (const view of this._views.values()) if (view.viewId === viewId) return view;
    return null;
  }

  /** The independent views of a tab, in panel order. */
  forTab(tab) {
    return (tab?.cells3d || []).filter((cell) => !isLinked3D(cell.spec))
      .map((cell) => this._views.get(independentKey(tab.name, cell.spec))).filter(Boolean);
  }

  // ── layout ────────────────────────────────────────────────────────────

  /** A layout rebuild begins: the views so far, for `keep` and `endLayout`. */
  startLayout() {
    const old = this._views;
    this._views = new Map();
    return old;
  }

  /** An independent panel in the new layout keeps its view when the old
   * layout had it in the same place with the same settings; otherwise it
   * starts again (a saved edit changed it). */
  keep(old, key, cell) {
    let view = old.get(key);
    const settings = (spec) => JSON.stringify(
      [spec.view, spec.link_frame !== false, spec.link_camera !== false, cleanStart(spec.start)]);
    if (view && settings(view.spec) !== settings(cell.spec)) view = null;
    if (view) {
      old.delete(key);
      view.spec = cell.spec;
      if (view.panel) {
        view.panel.spec = cell.spec;
        view.panel.mount(cell.el);
        this._ports.placePanel(cell.el, view.panel);
      }
    } else {
      view = new IndependentView(key, cell.spec, `ind-${++this._count}`,
        (v, cam) => this._onCamera(v, cam));
      const own = view;
      own.renderers.atomScale = () => (this._ports.focused() === own ? this._ports.atomScale() : own.atomScale);
    }
    this._views.set(key, view);
  }

  /** The rebuild is done: the views it did not keep go. */
  endLayout(old) {
    for (const view of old.values()) this._retire(view);
  }

  /** An independent panel the layout dropped: its server view goes. */
  _retire(view) {
    this._ports.retired(view);
    clearTimeout(view.cameraThrottle);
    if (view.isOpen) this._ports.send(OUT.CLOSE_VIEW, { view_id: view.viewId });
  }

  // ── data ──────────────────────────────────────────────────────────────

  /** A new independent panel shows the first pair its tab's picker has
   * selected that no other 3D panel in the tab shows; after that it keeps
   * its choice (rule 7). */
  ensureData(view, tab) {
    if (!view.datasetFp) {
      const main = this._ports.main();
      const datasets = this._ports.datasets();
      const shown = [];
      if (showsMainView(tab.spec) && main.datasetFp)
        shown.push({ datasetFp: main.datasetFp, modelFp: main.modelFp });
      for (const other of this.forTab(tab))
        if (other !== view && other.datasetFp) shown.push({ datasetFp: other.datasetFp, modelFp: other.modelFp });
      const pairs = this._ports.tabPairs(tab.id).filter((p) => datasets.has(p.datasetFp));
      const pick = startPair(pairs, shown);
      if (!pick) { this.syncCaption(view); return; }
      view.datasetFp = pick.datasetFp;
      view.modelFp = pick.modelFp;
    }
    this.open(view);
    if (view === this._ports.focused()) this._ports.chosen(view);
  }

  /** The tab on screen's independent panels choose data once there is some. */
  ensureShown() {
    const tab = this._ports.activeTab();
    for (const view of this.forTab(tab)) this.ensureData(view, tab);
  }

  /** Open (or point) an independent panel's server view at its data. */
  open(view) {
    if (!this._ports.connected() || !view.datasetFp || !this._ports.datasets().has(view.datasetFp)) return;
    const want = `${view.datasetFp}|${view.modelFp || ''}`;
    if (view.openedPair === want) return;
    const datasetChanged = !view.openedPair.startsWith(`${view.datasetFp}|`);
    view.openedPair = want;
    this._ports.send(OUT.OPEN_VIEW, {
      view_id: view.viewId, dataset_ref: view.datasetFp, prediction_ref: view.modelFp,
    });
    this._ports.start(view);
    if (view.links.frame) this.followMainFrame(view);
    else if (datasetChanged) this.setFrame(view, 0);
    this.syncCaption(view);
  }

  /** An independent panel with nothing left to show: its server view goes,
   * and it waits for data like a new panel. */
  empty(view) {
    if (view.isOpen) this._ports.send(OUT.CLOSE_VIEW, { view_id: view.viewId });
    view.openedPair = '';
    view.started = false;
    view.frame = null;
    this._ports.framed().delete(view.viewId);
    view.renderers.clear();
    this.syncCaption(view);
  }

  /** An independent panel's view sent its scene. */
  onSnapshot(view, scene) {
    const framed = this._ports.framed();
    view.version = scene.version;
    // The scene's camera (and a first fit) is the panel's own: it must not
    // turn the main view through the camera link.
    view.applyingScene = true;
    try {
      view.renderers.applyScene(scene);
      if (!framed.has(scene.view_id)) {
        framed.add(scene.view_id);
        view.renderers.frameAtoms();
      }
    } finally {
      view.applyingScene = false;
    }
    const mainCamera = this._ports.mainView().camera;
    if (view.links.camera && mainCamera) view.renderers.setCamera(mainCamera, { keepCenter: true });
    if (view.frame == null && Number.isInteger(scene.frame_index)) view.frame = scene.frame_index;
    if (scene.atoms) this._ports.sceneAtoms(view, scene.atoms);
    this._ports.frameShown(view);
    this.syncCaption(view);
  }

  /** An independent panel's caption ('aspirin · MACE') and note. */
  syncCaption(view) {
    const panel = view.panel;
    if (!panel) return;
    panel.showCaption(view.datasetFp ? this._ports.pairName(view.datasetFp, view.modelFp) : 'No dataset yet', view.note);
    panel.showNote(view.missing, { dim: true });
  }

  // ── frame link ────────────────────────────────────────────────────────

  /** Show frame `frame` in an independent panel. */
  setFrame(view, frame) {
    view.frame = frame;
    if (view.isOpen) {
      this._ports.send(OUT.VIEW_COMMAND, { type: 'SET_FRAME', view_id: view.viewId, view_version: 0, frame_index: frame });
    }
    this._ports.frameShown(view);
  }

  /** A frame-linked independent panel takes the main view's frame: the same
   * structure where both are cut from one dataset, else the same number,
   * else it greys out and says why (rule 3). */
  followMainFrame(view) {
    const main = this._ports.main();
    if (!view.isOpen || !main.datasetFp) return;
    const linked = linkedFrame(main.datasetFp, main.frame, view.datasetFp, this._ports.datasets());
    view.missing = linked.missing || '';
    if ('frame' in linked && linked.frame !== view.frame) this.setFrame(view, linked.frame);
    this.syncCaption(view);
  }

  /** Every frame-linked independent panel follows the main view's frame. */
  syncLinkedFrames() {
    for (const view of this.forTab(this._ports.activeTab())) if (view.links.frame) this.followMainFrame(view);
  }

  // ── camera link ───────────────────────────────────────────────────────

  /** An independent panel's camera moved (orbit, zoom, a camera command). */
  _onCamera(view, cam) {
    clearTimeout(view.cameraThrottle);
    view.cameraThrottle = setTimeout(() => {
      if (view.isOpen) this._ports.send(OUT.VIEW_COMMAND, { type: 'SET_CAMERA', view_id: view.viewId, camera: cam });
    }, 100);
    if (view === this._ports.focused()) this._ports.focusedCamera(cam);
    if (view.links.camera && !view.applyingScene) this.linkCameras(view, cam);
  }

  /** Turn every view whose camera follows the main view to `cam`'s angle and
   * zoom (ADR 0056 rule 2), each still looking at its own atoms. `source` is
   * the independent view that moved, or null for the main view. */
  linkCameras(source, cam) {
    if (this._linkingCameras) return;
    this._linkingCameras = true;
    try {
      if (source) {
        const mainView = this._ports.mainView();
        mainView.setCamera(cam, { keepCenter: true });
        if (mainView.camera) this._ports.sendMainCamera(mainView.camera);
      }
      for (const view of this._views.values()) {
        if (view !== source && view.links.camera) view.renderers.setCamera(cam, { keepCenter: true });
      }
    } finally {
      this._linkingCameras = false;
    }
  }
}

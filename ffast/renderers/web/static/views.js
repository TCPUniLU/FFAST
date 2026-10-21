/**
 * What the browser knows about one server view (ADR 0060).
 *
 * A `View` holds the server view id, the version stamped on every command, the
 * frame, and the timers of the camera. It imports nothing that draws, so a test
 * can build one without a canvas. `MainViews` owns the main view's `View`s:
 * the server keeps one `view-<n>` per dataset, and the browser reuses it when
 * the user comes back to that dataset.
 */

import { copySettings, defaultSettings } from './view_settings.js';

export class View {
  /**
   * @param {object} init
   * @param {string} init.viewId `view-<n>` (main) or `ind-<n>` (independent)
   * @param {string|null} [init.datasetFp] what it shows
   * @param {string|null} [init.modelFp] the prediction its last OPEN_VIEW carried
   * @param {object|null} [init.renderers] the ViewRenderers that draw it
   * @param {object|null} [init.settings] its sidebar settings
   */
  constructor({ viewId, datasetFp = null, modelFp = null, renderers = null, settings = null }) {
    this.viewId = viewId;
    this.datasetFp = datasetFp;
    this.modelFp = modelFp;
    this.version = 0;            // stamped on every VIEW_COMMAND, resynced from replies
    this.frame = 0;              // its frame, as last set or reported
    this.openedPair = '';        // `dataset|prediction` of its last OPEN_VIEW; '' = not open
    this.framed = false;         // its first snapshot fitted the atoms
    this.frameOnSnapshot = null; // a frame to show once its next snapshot is in
    this.cameraThrottle = null;  // pending SET_CAMERA timer
    this.cameraSend = null;      // and the send it will make
    this.settings = settings;
    this.renderers = renderers;
  }

  /** Its server view is open. */
  get isOpen() { return !!this.openedPair; }

  /** It is a main view (`view-<n>`), not an independent panel's. */
  get isMain() { return this.viewId.startsWith('view-'); }

  /**
   * The VIEW_COMMAND payload for `fields`, stamped with the version, which then
   * advances; null when the view is not open.
   */
  command(fields) {
    if (!this.isOpen) return null;
    return { view_id: this.viewId, view_version: this.version++, ...fields };
  }
}

export class MainViews {
  /**
   * @param {object} init
   * @param {object} init.renderers the one ViewRenderers all linked panels share
   * @param {(datasetFp: string) => string|null} [init.parentOf] the dataset a
   *   frame subset takes its look from until it has its own, else null
   */
  constructor({ renderers, parentOf = () => null }) {
    this._renderers = renderers;
    this._parentOf = parentOf;
    this._byDataset = new Map();
    this._count = 0;             // never goes back: a dataset loaded again is a new server view
    this._current = null;
  }

  /** The View the main view shows now, or null. */
  get current() { return this._current; }

  /** Make `view` the current one (null: none). */
  point(view) { this._current = view; }

  /** The View of `datasetFp`, made on first use as `view-<n>`; a frame
   * subset's starts from a copy of its parent's settings. */
  ensure(datasetFp) {
    let view = this._byDataset.get(datasetFp);
    if (!view) {
      const parent = this._parentOf(datasetFp);
      const parentSettings = parent ? this._byDataset.get(parent)?.settings : null;
      view = new View({
        viewId: `view-${this._count++}`, datasetFp, renderers: this._renderers,
        settings: parentSettings ? copySettings(parentSettings) : defaultSettings(),
      });
      this._byDataset.set(datasetFp, view);
    }
    return view;
  }

  /** The View of `datasetFp`, or undefined. */
  get(datasetFp) { return this._byDataset.get(datasetFp); }

  /** Every main View made so far. */
  all() { return [...this._byDataset.values()]; }

  /** The main View with this server id, or null. */
  byViewId(viewId) {
    for (const view of this._byDataset.values()) if (view.viewId === viewId) return view;
    return null;
  }

  /** Forget the View of `datasetFp` and return it (null if none); the caller sends CLOSE_VIEW. */
  drop(datasetFp) {
    const view = this._byDataset.get(datasetFp);
    if (!view) return null;
    this._byDataset.delete(datasetFp);
    if (this._current === view) this._current = null;
    return view;
  }

  /** A disconnect: forget every view. */
  clear() {
    this._byDataset.clear();
    this._current = null;
  }
}

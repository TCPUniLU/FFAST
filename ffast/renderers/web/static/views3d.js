/**
 * 3D panels (ADR 0056): grid cells that show a visualization view, beside the
 * 2D panels of a tab.
 *
 * Every 3D panel draws on its own canvas with its own MoleculeRenderer, so
 * each one keeps its camera, picking and PNG export to itself, scrolls with
 * the grid, and sits under menus like any other cell. One shared canvas split
 * into viewports was the alternative; measured 2026-10-07 (Apple M3 Pro,
 * pixel ratio 2, 500-atom rich look, render plus GPU finish), the two cost
 * the same within about a millisecond: 1 panel 3.2 vs 2.5 ms a frame,
 * 2 panels 3.7 vs 3.6, 4 panels 5.9 vs 7.1. The cost of separate canvases is
 * one WebGL context per panel, and browsers allow a page only a handful, so a
 * renderer is made the first time its tab is shown and given back when the
 * tab layout drops the panel. A panel in a tab you are not looking at does
 * not draw.
 */

import { MoleculeRenderer } from './renderer.js';
import { PickController } from './picking.js';
import { isLinked3D } from './tab_rules.js';

/** One 3D panel: its viewport element, canvas and, once shown, renderer. */
export class Panel3D {
  /** @param {object} spec the panel's entry in the tab layout */
  constructor(spec) {
    this.spec = spec;
    this.viewport = document.createElement('div');
    this.viewport.className = 'viewport';
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'view3d';
    this.viewport.appendChild(this.canvas);
    /** @type {MoleculeRenderer|null} */
    this.renderer = null;
    /** @type {PickController|null} */
    this.pick = null;
  }

  get linked() { return isLinked3D(this.spec); }

  /** Put the panel into a grid cell (moving it there if it was elsewhere). */
  mount(cell) {
    if (this.viewport.parentElement !== cell) cell.appendChild(this.viewport);
  }

  /** Make the renderer on first use, once the canvas has a size to draw at. */
  ensureRenderer(onPick) {
    if (!this.renderer) {
      this.renderer = new MoleculeRenderer(this.canvas);
      this.pick = new PickController(this.canvas, this.viewport, this.renderer, { onPick });
    }
    return this.renderer;
  }

  dispose() {
    this.pick?.dispose();
    this.renderer?.dispose();
    this.viewport.remove();
    this.pick = null;
    this.renderer = null;
  }
}

/**
 * The renderers drawing one visualization view. Every linked 3D panel draws
 * the main view, so a scene or a setting has to reach all of them: this keeps
 * the scene (the last snapshot with every later patch merged in) so a panel
 * shown later starts from it, keeps the browser-side settings (background,
 * axes, bond style, Atom size), and moves the cameras together, since linked
 * panels show the same thing.
 */
export class ViewRenderers {
  /** @param {{onCameraChange?: (cam: object) => void}} [opts] */
  constructor({ onCameraChange } = {}) {
    /** @type {MoleculeRenderer[]} */
    this._renderers = [];
    this._scene = null;
    this._camera = null;
    this._settings = { background: null, gizmo: false, bondStyle: null };
    this._syncing = false;
    this.onCameraChange = onCameraChange || null;
    /** The Atom size setting, read by the rich look (renderer.atomScale). */
    this.atomScale = () => 1;
  }

  get renderers() { return this._renderers; }

  /** The renderer to measure, pick or export with: one on screen if any. */
  get shown() {
    return this._renderers.find((r) => r.drawing) || this._renderers[0] || null;
  }

  /** The scene as drawn, for a renderer added later and for bond recovery. */
  get scene() { return this._scene; }

  add(renderer) {
    if (this._renderers.includes(renderer)) return;
    this._renderers.push(renderer);
    renderer.atomScale = () => this.atomScale();
    const { background, gizmo, bondStyle } = this._settings;
    if (background) renderer.setBackgroundColor(background);
    renderer.setGizmoEnabled(gizmo);
    if (bondStyle) renderer.setBondStyle(...bondStyle);
    this._quietly(() => {
      if (this._scene) renderer.applyScene(this._scene);
      if (this._camera) renderer._applyCamera(this._camera);
    });
    renderer._onCameraChange = (cam) => this._onRendererCamera(renderer, cam);
  }

  remove(renderer) {
    this._renderers = this._renderers.filter((r) => r !== renderer);
    if (renderer._onCameraChange) renderer._onCameraChange = null;
  }

  applyScene(scene) {
    this._scene = { ...scene };
    this._quietly(() => { for (const r of this._renderers) r.applyScene(scene); });
    if (scene.camera) this._cameraFrom(this.shown);
  }

  /** A patch's camera is the renderers' own sent back (see
   * MoleculeRenderer.applyPatch), so it is not kept either. */
  applyPatch(patch, changed) {
    const keys = (Array.isArray(changed) ? changed : Object.keys(changed || {}))
      .filter((key) => key !== 'camera');
    if (this._scene) {
      for (const key of keys) if (key in patch) this._scene[key] = patch[key];
      this._scene.only_forces = keys.includes('only_forces');
    }
    this._quietly(() => { for (const r of this._renderers) r.applyPatch(patch, keys); });
  }

  /** Fit the atoms in the panel on screen; the others follow its camera. */
  frameAtoms() { this.shown?.frameAtoms(); }

  clear() {
    this._scene = null;
    this._camera = null;
    for (const r of this._renderers) r.clear();
  }

  setBackgroundColor(hex) {
    this._settings.background = hex;
    for (const r of this._renderers) r.setBackgroundColor(hex);
  }

  setGizmoEnabled(enabled) {
    this._settings.gizmo = !!enabled;
    for (const r of this._renderers) r.setGizmoEnabled(enabled);
  }

  setBondStyle(width, color, chosen = false) {
    this._settings.bondStyle = [width, color, chosen];
    for (const r of this._renderers) r.setBondStyle(width, color, chosen);
  }

  /** A renderer's camera moved (orbit, zoom, a camera command): the others
   * take the same camera, and the app hears it once. */
  _onRendererCamera(source, cam) {
    if (this._syncing) return;
    this._camera = cam;
    this._quietly(() => {
      for (const r of this._renderers) if (r !== source) r._applyCamera(cam);
    });
    this.onCameraChange?.(cam);
  }

  _cameraFrom(renderer) {
    if (!renderer) return;
    this._camera = renderer._exportCamera();
    this._quietly(() => {
      for (const r of this._renderers) if (r !== renderer) r._applyCamera(this._camera);
    });
    this.onCameraChange?.(this._camera);
  }

  /** Run `fn` without the camera changes it causes echoing back here. */
  _quietly(fn) {
    const was = this._syncing;
    this._syncing = true;
    try { fn(); } finally { this._syncing = was; }
  }
}

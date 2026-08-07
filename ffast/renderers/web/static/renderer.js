/**
 * Three.js molecular renderer.
 */

import * as THREE from 'three';
import { OrbitControls } from './vendor/three/OrbitControls.js';
import { mapColorBy } from './colormap.js';

export class MoleculeRenderer {
  constructor(canvas) {
    this._canvas = canvas;

    // alpha:true + preserveDrawingBuffer:true are for PNG export (ADR 0045
    // issue 19): a real alpha buffer lets a transparent-background export use
    // clear-alpha 0 (no near-black→alpha keying the vispy path needed), and
    // preserving the drawing buffer makes `toDataURL()` read reliable pixels
    // regardless of the continuous render loop. Normal appearance is unchanged
    // (clear alpha stays 1 for on-screen rendering).
    this._renderer = new THREE.WebGLRenderer({
      canvas, antialias: true, alpha: true, preserveDrawingBuffer: true,
    });
    this._renderer.setPixelRatio(window.devicePixelRatio);
    this._renderer.setClearColor(0x000000, 1);  // Qt loupe default (default.json loupeBGColor)

    this._scene   = new THREE.Scene();
    this._perspCamera = new THREE.PerspectiveCamera(60, 1, 0.01, 5000);
    this._perspCamera.position.set(0, 0, 20);
    this._orthoCamera = new THREE.OrthographicCamera(-10, 10, 10, -10, 0.01, 5000);
    this._orthoCamera.position.set(0, 0, 20);
    this._camera = this._perspCamera;   // active camera (issue 04: orthographic toggle)

    this._controls = new OrbitControls(this._camera, canvas);
    this._controls.enableDamping = true;
    this._controls.dampingFactor = 0.1;

    // Axis orientation gizmo (issue 04): a small AxesHelper rendered into a
    // scissored corner viewport, oriented to mirror the main camera.
    this._gizmoEnabled = false;
    this._gizmoScene = new THREE.Scene();
    this._gizmoScene.add(new THREE.AxesHelper(1));
    this._gizmoCamera = new THREE.OrthographicCamera(-1.5, 1.5, 1.5, -1.5, 0.1, 10);

    this._bondRadius = 0.10;   // world units; set via setBondStyle (issue 06)
    this._bondColor  = new THREE.Color(0x888888);
    this._lastBonds  = null;  // cached BondScene, for rebuilding on style change

    // Lighting
    const ambient = new THREE.AmbientLight(0xffffff, 0.4);
    const dir = new THREE.DirectionalLight(0xffffff, 1.0);
    dir.position.set(5, 10, 8);
    this._scene.add(ambient, dir);

    // Scene objects (replaced on each snapshot/patch)
    this._atomMesh     = null;
    this._bondLines    = null;
    this._forceGroup   = null;
    this._unitCellLines = null;
    this._labelSprites = [];           // THREE.Sprite[] for text labels
    this._selectionMeshes = new Map(); // overlay name → THREE.InstancedMesh
    this._cachedAtomPositions = null;  // atoms.positions from last _updateAtoms call
    this._cachedAtomSizes = null;      // atoms.sizes from last _updateAtoms call
    this._cachedAtomIds = null;        // atoms.atom_ids (displayed→scientific, ADR 0015)

    // Geometry template for atoms (shared, low-poly sphere)
    this._sphereGeo = new THREE.SphereGeometry(1, 10, 8);
    this._atomMat   = new THREE.MeshStandardMaterial({ roughness: 0.35, metalness: 0.1 });

    // RAF handle
    this._rafId = null;

    this._onCameraChange = null; // set by app to send SET_CAMERA commands

    this._controls.addEventListener('change', () => {
      if (this._onCameraChange) {
        const cam = this._exportCamera();
        this._onCameraChange(cam);
      }
    });

    this._resize();
    window.addEventListener('resize', () => this._resize());
    this._startLoop();
  }

  _resize() {
    const w = this._canvas.clientWidth;
    const h = this._canvas.clientHeight;
    this._renderer.setSize(w, h, false);
    this._perspCamera.aspect = w / h;
    this._perspCamera.updateProjectionMatrix();
    this._updateOrthoFrustum(w / h);
  }

  /** Size the ortho frustum from the perspective camera's current distance,
   * so toggling projection doesn't change the apparent object scale. */
  _updateOrthoFrustum(aspect) {
    const dist = this._camera.position.distanceTo(this._controls.target);
    const halfHeight = Math.max(0.01, dist * Math.tan((this._perspCamera.fov / 2) * Math.PI / 180));
    const halfWidth = halfHeight * aspect;
    this._orthoCamera.left = -halfWidth;
    this._orthoCamera.right = halfWidth;
    this._orthoCamera.top = halfHeight;
    this._orthoCamera.bottom = -halfHeight;
    this._orthoCamera.updateProjectionMatrix();
  }

  /** @param {boolean} enabled */
  setOrthographic(enabled) {
    const next = enabled ? this._orthoCamera : this._perspCamera;
    if (next === this._camera) return;
    next.position.copy(this._camera.position);
    this._camera = next;
    this._controls.object = this._camera;
    this._updateOrthoFrustum(this._canvas.clientWidth / this._canvas.clientHeight);
    this._controls.update();
  }

  /**
   * Set one or more camera angles/distance, keeping the rest at their current
   * live value (issue 04: preset buttons + manual az/el/dist entry). Fires
   * the same 'change' event as an interactive drag, so SET_CAMERA sends and
   * the camera pane's fields stay in sync.
   * @param {{azimuth?: number, elevation?: number, distance?: number}} overrides
   */
  setCameraAngles(overrides) {
    this._applyCamera({ ...this._exportCamera(), ...overrides });
  }

  /** @param {string} hex CSS colour, e.g. '#000000' */
  setBackgroundColor(hex) {
    this._renderer.setClearColor(new THREE.Color(hex), 1);
  }

  /**
   * Render one frame at the requested background and return it as a PNG data
   * URL (ADR 0045 issue 19). `transparent` clears to alpha 0 (real WebGL
   * alpha, no colour-keying); otherwise the scene is drawn over `background`.
   * The orientation gizmo is omitted so the export is a clean figure. The
   * previous clear colour/alpha is restored so on-screen rendering continues
   * unchanged.
   * @param {{transparent?: boolean, background?: string}} [opts]
   * @returns {string} a `data:image/png;base64,…` URL
   */
  capturePng({ transparent = false, background = '#000000' } = {}) {
    const r = this._renderer;
    const prevColor = new THREE.Color();
    r.getClearColor(prevColor);
    const prevAlpha = r.getClearAlpha();

    if (transparent) r.setClearColor(0x000000, 0);
    else r.setClearColor(new THREE.Color(background), 1);
    r.setViewport(0, 0, this._canvas.clientWidth, this._canvas.clientHeight);
    r.render(this._scene, this._camera);
    const url = this._canvas.toDataURL('image/png');

    r.setClearColor(prevColor, prevAlpha);
    return url;
  }

  /** @param {boolean} enabled */
  setGizmoEnabled(enabled) {
    this._gizmoEnabled = enabled;
  }

  /** @param {number} width Qt-style 10..100 slider value @param {string} colorHex */
  setBondStyle(width, colorHex) {
    this._bondRadius = Math.max(0.01, (width / 100) * 0.12);
    this._bondColor = new THREE.Color(colorHex);
    if (this._lastBonds) this._updateBonds(this._lastBonds);
  }

  _startLoop() {
    const loop = () => {
      this._rafId = requestAnimationFrame(loop);
      this._controls.update();
      this._renderer.setViewport(0, 0, this._canvas.clientWidth, this._canvas.clientHeight);
      this._renderer.render(this._scene, this._camera);
      if (this._gizmoEnabled) this._renderGizmo();
    };
    loop();
  }

  _renderGizmo() {
    const size = 70, margin = 10;
    const w = this._canvas.clientWidth, h = this._canvas.clientHeight;
    const dir = this._camera.position.clone().sub(this._controls.target).normalize();
    this._gizmoCamera.position.copy(dir).multiplyScalar(3);
    this._gizmoCamera.up.copy(this._camera.up);
    this._gizmoCamera.lookAt(0, 0, 0);
    this._gizmoCamera.updateProjectionMatrix();
    const r = this._renderer;
    r.setViewport(w - size - margin, h - size - margin, size, size);
    r.setScissor(w - size - margin, h - size - margin, size, size);
    r.setScissorTest(true);
    r.render(this._gizmoScene, this._gizmoCamera);
    r.setScissorTest(false);
  }

  // ── scene update ──────────────────────────────────────────────────────────

  /** @param {import('./protocol.js').RenderScene} scene */
  applyScene(scene) {
    if (scene.atoms)      this._updateAtoms(scene.atoms);
    if (scene.bonds)      this._updateBonds(scene.bonds);
    if (scene.forces)     this._updateForces(scene.forces);
    if (scene.unit_cell)  this._updateUnitCell(scene.unit_cell);
    if (!scene.atoms && !scene.bonds) this._clearAtoms();
    if (!scene.unit_cell) this._clearUnitCell();
    this._updateLabels(scene.labels || null);
    this._updateSelections(scene.selections || []);
    if (scene.camera)     this._applyCamera(scene.camera);
  }

  /**
   * @param {import('./protocol.js').ScenePatchKwargs} patch
   * @param {string[]} changed
   */
  applyPatch(patch, changed) {
    const c = new Set(Array.isArray(changed) ? changed : Object.keys(changed));
    if (c.has('atoms'))      { if (patch.atoms)      this._updateAtoms(patch.atoms);         else this._clearAtoms(); }
    if (c.has('bonds'))      { if (patch.bonds)       this._updateBonds(patch.bonds);         else this._clearBonds(); }
    if (c.has('forces'))     { if (patch.forces)      this._updateForces(patch.forces);       else this._clearForces(); }
    if (c.has('unit_cell'))  { if (patch.unit_cell)   this._updateUnitCell(patch.unit_cell);  else this._clearUnitCell(); }
    if (c.has('labels'))     this._updateLabels(patch.labels || null);
    if (c.has('selections')) this._updateSelections(patch.selections || []);
    if (c.has('camera') && patch.camera) this._applyCamera(patch.camera);
  }

  /** @param {import('./protocol.js').AtomScene} atoms */
  _updateAtoms(atoms) {
    this._clearAtoms();
    const n = atoms.positions.length;
    if (n === 0) return;
    this._cachedAtomPositions = atoms.positions;
    this._cachedAtomSizes = atoms.sizes;
    this._cachedAtomIds = atoms.atom_ids || null;

    // Value-driven coloring (ADR 0016): map color_by.values→RGB client-side;
    // fall back to the server's baked element colors on an unrecognized
    // colormap (the browser twin of the vispy adapter's _map_color_by).
    let colors = atoms.colors;
    if (atoms.color_by) {
      const mapped = mapColorBy(atoms.color_by);
      if (mapped) colors = mapped;
      else console.warn(`MoleculeRenderer: unknown colormap '${atoms.color_by.colormap}' — using element colors`);
    }

    const mesh = new THREE.InstancedMesh(this._sphereGeo, this._atomMat.clone(), n);
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);

    const dummy = new THREE.Object3D();
    const color = new THREE.Color();

    for (let i = 0; i < n; i++) {
      const [x, y, z] = atoms.positions[i];
      const r = atoms.sizes[i] || 0.5;
      dummy.position.set(x, y, z);
      dummy.scale.setScalar(r);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);

      const [cr, cg, cb] = colors[i];
      color.setRGB(cr, cg, cb);
      mesh.setColorAt(i, color);
    }
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;

    this._atomMesh = mesh;
    this._scene.add(mesh);
  }

  /**
   * Bonds render as cylinder instances (not THREE.Line) so width/colour
   * (issue 06) actually change appearance — WebGL's LineBasicMaterial
   * linewidth is clamped to 1px on most platforms.
   * @param {import('./protocol.js').BondScene} bonds
   */
  _updateBonds(bonds) {
    this._clearBonds();
    this._lastBonds = bonds;
    const segs = bonds.segments;
    if (!segs || segs.length === 0) return;
    const n = Math.floor(segs.length / 2);

    const geo = new THREE.CylinderGeometry(1, 1, 1, 6, 1);
    const mat = new THREE.MeshStandardMaterial({ color: this._bondColor, roughness: 0.6, metalness: 0.05 });
    const mesh = new THREE.InstancedMesh(geo, mat, n);
    const dummy = new THREE.Object3D();
    const up = new THREE.Vector3(0, 1, 0);
    const start = new THREE.Vector3(), end = new THREE.Vector3(), dir = new THREE.Vector3();
    let count = 0;
    for (let i = 0; i < n; i++) {
      start.set(segs[2 * i][0], segs[2 * i][1], segs[2 * i][2]);
      end.set(segs[2 * i + 1][0], segs[2 * i + 1][1], segs[2 * i + 1][2]);
      dir.copy(end).sub(start);
      const length = dir.length();
      if (length < 1e-6) continue;
      dir.normalize();
      dummy.position.copy(start).add(end).multiplyScalar(0.5);
      dummy.quaternion.setFromUnitVectors(up, dir);
      dummy.scale.set(this._bondRadius, length, this._bondRadius);
      dummy.updateMatrix();
      mesh.setMatrixAt(count++, dummy.matrix);
    }
    mesh.count = count;
    mesh.instanceMatrix.needsUpdate = true;
    this._bondLines = mesh;
    this._scene.add(mesh);
  }

  /** @param {import('./protocol.js').ForceScene} forces */
  _updateForces(forces) {
    this._clearForces();
    if (!forces.starts || forces.starts.length === 0) return;

    const group = new THREE.Group();
    const scale = 0.5; // visual scale factor for force vectors
    for (let i = 0; i < forces.starts.length; i++) {
      const [ox, oy, oz] = forces.starts[i];
      const [vx, vy, vz] = forces.vectors[i];
      // One RGBA per arrow is a ForceScene invariant (ADR 0052) — no local default.
      const [cr, cg, cb] = forces.colors[i];
      const len = Math.sqrt(vx*vx + vy*vy + vz*vz) * scale;
      if (len < 0.001) continue;
      const dir = new THREE.Vector3(vx, vy, vz).normalize();
      const origin = new THREE.Vector3(ox, oy, oz);
      const arrow = new THREE.ArrowHelper(dir, origin, len, new THREE.Color(cr, cg, cb), 0.3 * len, 0.15 * len);
      group.add(arrow);
    }
    this._forceGroup = group;
    this._scene.add(group);
  }

  /** @param {import('./protocol.js').UnitCellScene|null} unitCell */
  _updateUnitCell(unitCell) {
    this._clearUnitCell();
    const segs = unitCell?.segments;
    if (!segs || segs.length === 0) return;
    const positions = new Float32Array(segs.length * 3);
    for (let i = 0; i < segs.length; i++) {
      positions[i * 3]     = segs[i][0];
      positions[i * 3 + 1] = segs[i][1];
      positions[i * 3 + 2] = segs[i][2];
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const mat = new THREE.LineBasicMaterial({ color: 0x888888, linewidth: 1, opacity: 0.6, transparent: true });
    this._unitCellLines = new THREE.LineSegments(geo, mat);
    this._scene.add(this._unitCellLines);
  }

  _clearAtoms() {
    if (this._atomMesh) {
      this._scene.remove(this._atomMesh);
      this._atomMesh.dispose?.();
      this._atomMesh = null;
    }
    this._cachedAtomPositions = null;
    this._cachedAtomSizes = null;
    this._cachedAtomIds = null;
  }
  _clearBonds()     { if (this._bondLines)    { this._scene.remove(this._bondLines);    this._bondLines.geometry.dispose();     this._bondLines    = null; } this._lastBonds = null; }
  _clearForces()    { if (this._forceGroup)   { this._scene.remove(this._forceGroup);                                            this._forceGroup   = null; } }
  _clearUnitCell()  { if (this._unitCellLines){ this._scene.remove(this._unitCellLines); this._unitCellLines.geometry.dispose(); this._unitCellLines = null; } }

  _clearLabels() {
    for (const sprite of this._labelSprites) {
      this._scene.remove(sprite);
      sprite.material.map?.dispose();
      sprite.material.dispose();
    }
    this._labelSprites = [];
  }

  _clearSelections() {
    for (const mesh of this._selectionMeshes.values()) {
      this._scene.remove(mesh);
      mesh.material.dispose();
    }
    this._selectionMeshes.clear();
  }

  clear() {
    this._clearAtoms();
    this._clearBonds();
    this._clearForces();
    this._clearUnitCell();
    this._clearLabels();
    this._clearSelections();
  }

  /** @param {import('./protocol.js').LabelScene|null} labels */
  _updateLabels(labels) {
    this._clearLabels();
    if (!labels || !labels.texts || labels.texts.length === 0) return;

    for (let i = 0; i < labels.texts.length; i++) {
      const [x, y, z] = labels.positions[i];
      const rgba = labels.colors[i] || [1, 1, 1, 1];
      const text = labels.texts[i];

      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      ctx.font = 'bold 28px sans-serif';
      const tw = Math.ceil(ctx.measureText(text).width) + 16;
      canvas.width = tw;
      canvas.height = 36;
      ctx.font = 'bold 28px sans-serif';
      ctx.fillStyle = `rgba(${Math.round(rgba[0]*255)},${Math.round(rgba[1]*255)},${Math.round(rgba[2]*255)},${rgba[3] ?? 1})`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(text, tw / 2, 18);

      const texture = new THREE.CanvasTexture(canvas);
      const mat = new THREE.SpriteMaterial({ map: texture, depthTest: false, transparent: true });
      const sprite = new THREE.Sprite(mat);
      sprite.position.set(x, y, z);
      sprite.scale.set((tw / 36) * 0.5, 0.5, 1);
      this._scene.add(sprite);
      this._labelSprites.push(sprite);
    }
  }

  /** @param {import('./protocol.js').SelectionOverlay[]} selections */
  _updateSelections(selections) {
    this._clearSelections();
    if (!selections || selections.length === 0 || !this._cachedAtomPositions) return;

    for (const overlay of selections) {
      const indices = overlay.atom_indices;
      if (!indices || indices.length === 0) continue;
      const [cr, cg, cb, ca] = overlay.color || [1, 0, 0, 0.5];
      const n = indices.length;
      const mat = new THREE.MeshStandardMaterial({
        color: new THREE.Color(cr, cg, cb),
        opacity: ca ?? 0.5,
        transparent: true,
        roughness: 0.35,
        metalness: 0.1,
      });
      const mesh = new THREE.InstancedMesh(this._sphereGeo, mat, n);
      const dummy = new THREE.Object3D();
      for (let j = 0; j < n; j++) {
        const idx = indices[j];
        const [x, y, z] = this._cachedAtomPositions[idx];
        const r = (this._cachedAtomSizes?.[idx] || 0.5) * 1.15;
        dummy.position.set(x, y, z);
        dummy.scale.setScalar(r);
        dummy.updateMatrix();
        mesh.setMatrixAt(j, dummy.matrix);
      }
      mesh.instanceMatrix.needsUpdate = true;
      this._scene.add(mesh);
      this._selectionMeshes.set(overlay.name, mesh);
    }
  }

  /** @param {import('./protocol.js').CameraState} cam */
  _applyCamera(cam) {
    // cam fields: center, distance, azimuth, elevation, fov, projection
    const { center = [0,0,0], distance = 10, azimuth = 0, elevation = 30, fov = 60, projection = 'perspective' } = cam;
    this.setOrthographic(projection === 'orthographic');
    this._perspCamera.fov = fov;
    this._perspCamera.updateProjectionMatrix();

    const phi   = (90 - elevation) * Math.PI / 180;
    const theta = azimuth * Math.PI / 180;
    const x = center[0] + distance * Math.sin(phi) * Math.sin(theta);
    const y = center[1] + distance * Math.cos(phi);
    const z = center[2] + distance * Math.sin(phi) * Math.cos(theta);
    this._camera.position.set(x, y, z);
    this._controls.target.set(center[0], center[1], center[2]);
    this._updateOrthoFrustum(this._canvas.clientWidth / this._canvas.clientHeight);
    this._controls.update();
  }

  /** @returns {import('./protocol.js').CameraState} */
  _exportCamera() {
    const pos = this._camera.position;
    const target = this._controls.target;
    const dx = pos.x - target.x;
    const dy = pos.y - target.y;
    const dz = pos.z - target.z;
    const distance = Math.sqrt(dx*dx + dy*dy + dz*dz);
    const elevation = 90 - Math.atan2(Math.sqrt(dx*dx + dz*dz), dy) * 180 / Math.PI;
    const azimuth   = Math.atan2(dx, dz) * 180 / Math.PI;
    return {
      center: [target.x, target.y, target.z],
      distance,
      azimuth,
      elevation,
      fov: this._perspCamera.fov,
      projection: this._camera === this._orthoCamera ? 'orthographic' : 'perspective',
    };
  }

  resetCamera() {
    // Re-fit the molecule to view (recenter + frame), not the OrbitControls
    // initial pose. frameAtoms() calls _controls.update(), which fires the
    // 'change' event → SET_CAMERA syncs the new pose to the server.
    this.frameAtoms();
  }

  frameAtoms() {
    if (!this._atomMesh) return;
    const box = new THREE.Box3().setFromObject(this._atomMesh);
    const center = box.getCenter(new THREE.Vector3());
    const size   = box.getSize(new THREE.Vector3()).length();
    this._controls.target.copy(center);
    this._camera.position.copy(center).addScaledVector(new THREE.Vector3(0, 0, 1), size * 1.5);
    this._updateOrthoFrustum(this._canvas.clientWidth / this._canvas.clientHeight);
    this._controls.update();
  }

  /** Shift the look-at point to `point`, preserving azimuth/elevation/distance
   * (issue 04: origin-on-centre-of-mass tracking). Fires the 'change' event. */
  recenterTo(point) {
    const [x, y, z] = point;
    const delta = new THREE.Vector3(x, y, z).sub(this._controls.target);
    if (delta.lengthSq() < 1e-12) return;
    this._controls.target.add(delta);
    this._camera.position.add(delta);
    this._controls.update();
  }

  // ── picking (ADR 0045 issue 10 / ADR 0015) ───────────────────────────────
  // The browser twin of the vispy adapter's pick_at/pick_in_rect: project the
  // cached displayed atom centres to canvas pixels through the live camera and
  // resolve by screen distance + depth. Displayed indices map to scientific
  // atom ids via atom_ids (ADR 0015), the same space SET_SELECTION expects.

  /** Suspend/resume orbit while a pick tool owns the pointer. */
  setControlsEnabled(enabled) {
    this._controls.enabled = !!enabled;
  }

  /** Displayed index → scientific atom id (identity when the server ships no map). */
  atomIdOf(displayIndex) {
    const ids = this._cachedAtomIds;
    return ids && ids[displayIndex] != null ? ids[displayIndex] : displayIndex;
  }

  get atomCount() {
    return this._cachedAtomPositions ? this._cachedAtomPositions.length : 0;
  }

  /** Project displayed atom `i` to canvas-local CSS pixels; returns null when
   * it is behind the camera / outside the clip volume. `_v` is a scratch vec. */
  _projectAtom(i, rect, v) {
    const p = this._cachedAtomPositions[i];
    v.set(p[0], p[1], p[2]).project(this._camera);
    if (v.z < -1 || v.z > 1) return null;   // clipped (behind camera or beyond far)
    return {
      x: (v.x * 0.5 + 0.5) * rect.width,
      y: (-v.y * 0.5 + 0.5) * rect.height,
      ndcZ: v.z,
    };
  }

  /**
   * Occlusion-correct click-pick: nearest visible atom within `radiusPx` of the
   * canvas-local point, tie-broken by depth (mirrors adapter.pick_at).
   * @returns {{displayIndex:number, atomId:number}|null}
   */
  pickAtom(px, py, radiusPx = 12) {
    if (!this._cachedAtomPositions) return null;
    const rect = this._canvas.getBoundingClientRect();
    const v = new THREE.Vector3();
    const r2 = radiusPx * radiusPx;
    let best = null, bestZ = Infinity;
    for (let i = 0; i < this._cachedAtomPositions.length; i++) {
      const s = this._projectAtom(i, rect, v);
      if (!s) continue;
      const dx = s.x - px, dy = s.y - py;
      if (dx * dx + dy * dy > r2) continue;
      if (s.ndcZ < bestZ) { bestZ = s.ndcZ; best = i; }
    }
    return best == null ? null : { displayIndex: best, atomId: this.atomIdOf(best) };
  }

  /**
   * Box-select: every atom whose projected centre falls inside the canvas-local
   * rectangle (mirrors adapter.pick_in_rect). No occlusion test, by design.
   * @returns {{displayIndex:number, atomId:number}[]}
   */
  boxSelect(x0, y0, x1, y1) {
    if (!this._cachedAtomPositions) return [];
    const rect = this._canvas.getBoundingClientRect();
    const v = new THREE.Vector3();
    const lo = { x: Math.min(x0, x1), y: Math.min(y0, y1) };
    const hi = { x: Math.max(x0, x1), y: Math.max(y0, y1) };
    const out = [];
    for (let i = 0; i < this._cachedAtomPositions.length; i++) {
      const s = this._projectAtom(i, rect, v);
      if (!s) continue;
      if (s.x >= lo.x && s.x <= hi.x && s.y >= lo.y && s.y <= hi.y)
        out.push({ displayIndex: i, atomId: this.atomIdOf(i) });
    }
    return out;
  }

  /** Canvas-local CSS-pixel position of a displayed atom, for tests/tooltips. */
  atomScreenPosition(displayIndex) {
    if (!this._cachedAtomPositions || !this._cachedAtomPositions[displayIndex]) return null;
    const rect = this._canvas.getBoundingClientRect();
    return this._projectAtom(displayIndex, rect, new THREE.Vector3());
  }

  /** World-space position of a displayed atom (for client-side measurements). */
  atomPosition(displayIndex) {
    const p = this._cachedAtomPositions?.[displayIndex];
    return p ? [p[0], p[1], p[2]] : null;
  }
}

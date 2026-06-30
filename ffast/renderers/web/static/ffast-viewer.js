/**
 * FFAST Web Renderer — Three.js + inline msgpack
 *
 * Connects to ffast-server over WebSocket, performs the HELLO handshake,
 * receives Scene Snapshots and Patches, and renders a molecular scene using
 * Three.js InstancedMesh (atoms), LineSegments (bonds), and ArrowHelper (forces).
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// ─────────────────────────────────────────────────────────────────────────────
// Minimal msgpack codec
// Handles all types produced by Python's msgpack.packb(use_bin_type=True).
// ─────────────────────────────────────────────────────────────────────────────

const msgpack = (() => {
  const te = new TextEncoder();
  const td = new TextDecoder();

  function encodeValue(v, out) {
    if (v === null || v === undefined) { out.push(0xc0); return; }
    if (v === false) { out.push(0xc2); return; }
    if (v === true)  { out.push(0xc3); return; }
    if (typeof v === 'number') {
      if (Number.isInteger(v)) {
        encodeInt(v, out);
      } else {
        out.push(0xcb);
        const b = new ArrayBuffer(8);
        new DataView(b).setFloat64(0, v, false);
        for (const byte of new Uint8Array(b)) out.push(byte);
      }
      return;
    }
    if (typeof v === 'string') { encodeStr(v, out); return; }
    if (v instanceof Uint8Array || v instanceof ArrayBuffer) {
      const arr = v instanceof ArrayBuffer ? new Uint8Array(v) : v;
      encodeBin(arr, out); return;
    }
    if (Array.isArray(v)) { encodeArray(v, out); return; }
    if (typeof v === 'object') { encodeMap(v, out); }
  }

  function encodeInt(n, out) {
    if (n >= 0) {
      if (n < 128)         { out.push(n); return; }
      if (n < 256)         { out.push(0xcc, n); return; }
      if (n < 65536)       { out.push(0xcd, n >> 8, n & 0xff); return; }
      if (n < 4294967296)  {
        out.push(0xce, (n >>> 24) & 0xff, (n >>> 16) & 0xff, (n >>> 8) & 0xff, n & 0xff);
        return;
      }
      // 64-bit: approximate via float64
      out.push(0xcb);
      const b = new ArrayBuffer(8);
      new DataView(b).setFloat64(0, n, false);
      for (const byte of new Uint8Array(b)) out.push(byte);
    } else {
      if (n >= -32)   { out.push(n & 0xff); return; }
      if (n >= -128)  { out.push(0xd0, n & 0xff); return; }
      if (n >= -32768){ out.push(0xd1, (n >> 8) & 0xff, n & 0xff); return; }
      out.push(0xd2, (n >> 24) & 0xff, (n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff);
    }
  }

  function encodeStr(s, out) {
    const bytes = te.encode(s);
    const len = bytes.length;
    if (len < 32)    { out.push(0xa0 | len); }
    else if (len < 256)   { out.push(0xd9, len); }
    else if (len < 65536) { out.push(0xda, len >> 8, len & 0xff); }
    else { out.push(0xdb, (len >>> 24) & 0xff, (len >>> 16) & 0xff, (len >>> 8) & 0xff, len & 0xff); }
    for (const b of bytes) out.push(b);
  }

  function encodeBin(arr, out) {
    const len = arr.length;
    if (len < 256)   { out.push(0xc4, len); }
    else if (len < 65536) { out.push(0xc5, len >> 8, len & 0xff); }
    else { out.push(0xc6, (len >>> 24) & 0xff, (len >>> 16) & 0xff, (len >>> 8) & 0xff, len & 0xff); }
    for (const b of arr) out.push(b);
  }

  function encodeArray(arr, out) {
    const len = arr.length;
    if (len < 16)    { out.push(0x90 | len); }
    else if (len < 65536) { out.push(0xdc, len >> 8, len & 0xff); }
    else { out.push(0xdd, (len >>> 24) & 0xff, (len >>> 16) & 0xff, (len >>> 8) & 0xff, len & 0xff); }
    for (const item of arr) encodeValue(item, out);
  }

  function encodeMap(obj, out) {
    const keys = Object.keys(obj);
    const len = keys.length;
    if (len < 16)    { out.push(0x80 | len); }
    else if (len < 65536) { out.push(0xde, len >> 8, len & 0xff); }
    else { out.push(0xdf, (len >>> 24) & 0xff, (len >>> 16) & 0xff, (len >>> 8) & 0xff, len & 0xff); }
    for (const k of keys) { encodeStr(k, out); encodeValue(obj[k], out); }
  }

  function encode(value) {
    const out = [];
    encodeValue(value, out);
    return new Uint8Array(out);
  }

  function decode(buffer) {
    const view = new DataView(buffer instanceof ArrayBuffer ? buffer : buffer.buffer);
    let pos = buffer instanceof ArrayBuffer ? 0 : (buffer.byteOffset || 0);

    function readByte()  { return view.getUint8(pos++); }
    function readU8()    { return view.getUint8(pos++); }
    function readU16()   { const v = view.getUint16(pos, false); pos += 2; return v; }
    function readU32()   { const v = view.getUint32(pos, false); pos += 4; return v; }
    function readI8()    { const v = view.getInt8(pos); pos++; return v; }
    function readI16()   { const v = view.getInt16(pos, false); pos += 2; return v; }
    function readI32()   { const v = view.getInt32(pos, false); pos += 4; return v; }
    function readF32()   { const v = view.getFloat32(pos, false); pos += 4; return v; }
    function readF64()   { const v = view.getFloat64(pos, false); pos += 8; return v; }
    function readBytes(n){ const b = new Uint8Array(view.buffer, pos, n); pos += n; return b; }
    function readStr(n)  { return td.decode(readBytes(n)); }

    function readArray(n) { const a = []; for (let i = 0; i < n; i++) a.push(readValue()); return a; }
    function readMap(n)   {
      const o = {};
      for (let i = 0; i < n; i++) { const k = readValue(); o[k] = readValue(); }
      return o;
    }

    function readValue() {
      const b = readByte();
      if (b === 0xc0) return null;
      if (b === 0xc2) return false;
      if (b === 0xc3) return true;
      if ((b & 0x80) === 0)   return b;                  // positive fixint
      if ((b & 0xe0) === 0xe0) return b - 256;            // negative fixint
      if ((b & 0xe0) === 0xa0) return readStr(b & 0x1f); // fixstr
      if ((b & 0xf0) === 0x90) return readArray(b & 0x0f); // fixarray
      if ((b & 0xf0) === 0x80) return readMap(b & 0x0f);   // fixmap
      if (b === 0xcc) return readU8();
      if (b === 0xcd) return readU16();
      if (b === 0xce) return readU32();
      if (b === 0xcf) { const hi = readU32(); const lo = readU32(); return hi * 2**32 + lo; }
      if (b === 0xd0) return readI8();
      if (b === 0xd1) return readI16();
      if (b === 0xd2) return readI32();
      if (b === 0xd3) { const hi = readI32(); const lo = readU32(); return hi * 2**32 + lo; }
      if (b === 0xca) return readF32();
      if (b === 0xcb) return readF64();
      if (b === 0xd9) return readStr(readU8());
      if (b === 0xda) return readStr(readU16());
      if (b === 0xdb) return readStr(readU32());
      if (b === 0xc4) { const n = readU8();  return readBytes(n); }
      if (b === 0xc5) { const n = readU16(); return readBytes(n); }
      if (b === 0xc6) { const n = readU32(); return readBytes(n); }
      if (b === 0xdc) return readArray(readU16());
      if (b === 0xdd) return readArray(readU32());
      if (b === 0xde) return readMap(readU16());
      if (b === 0xdf) return readMap(readU32());
      // ext types: skip
      if (b === 0xd4) { pos += 2; return null; }
      if (b === 0xd5) { pos += 3; return null; }
      if (b === 0xd6) { pos += 5; return null; }
      if (b === 0xd7) { pos += 9; return null; }
      if (b === 0xd8) { pos += 17; return null; }
      if (b === 0xc7) { const n = readU8(); pos += 1 + n; return null; }
      if (b === 0xc8) { const n = readU16(); pos += 1 + n; return null; }
      if (b === 0xc9) { const n = readU32(); pos += 1 + n; return null; }
      throw new Error(`Unknown msgpack byte 0x${b.toString(16)} at pos ${pos - 1}`);
    }

    return readValue();
  }

  return { encode, decode };
})();

// ─────────────────────────────────────────────────────────────────────────────
// WebSocket connection with HELLO handshake
// ─────────────────────────────────────────────────────────────────────────────

class FFastConnection {
  constructor(wsUrl, token) {
    this._url = wsUrl;
    this._token = token || null;
    this._ws = null;
    this._handlers = new Map();
    this.role = 'READ_ONLY';
  }

  connect() {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(this._url);
      ws.binaryType = 'arraybuffer';
      this._ws = ws;

      // Buffer messages until handshake is complete
      const buf = [];
      let handshakeDone = false;

      ws.onmessage = (evt) => {
        if (handshakeDone) this._dispatch(evt.data);
        else buf.push(evt.data);
      };
      ws.onerror = () => { if (!handshakeDone) reject(new Error('WebSocket error')); };
      ws.onclose = () => { if (!handshakeDone) reject(new Error('WebSocket closed during handshake')); };

      ws.onopen = async () => {
        try {
          this.role = await this._handshake(buf);
          handshakeDone = true;
          ws.onclose = null;
          // process any messages that arrived during handshake
          for (const data of buf) this._dispatch(data);
          buf.length = 0;
          resolve(this.role);
        } catch (e) {
          reject(e);
        }
      };
    });
  }

  async _handshake(buf) {
    // 1. ping / pong
    this._ws.send('ping');
    await this._pollBuf(buf, d => typeof d === 'string' && d === 'pong', 10000);

    // 2. HELLO
    this._ws.send(msgpack.encode({
      event: 'HELLO', args: [], kwargs: {
        protocol_version: '1.0',
        renderer: 'webgl',
        session_token: this._token,
        supported_codecs: ['raw'],
        features: [],
      },
    }));

    // 3. HELLO_ACK (5 s timeout for backward compat)
    let role = 'READ_ONLY';
    try {
      const ackData = await this._pollBuf(
        buf,
        d => {
          if (!(d instanceof ArrayBuffer)) return false;
          try { return msgpack.decode(d)?.event === 'HELLO_ACK'; }
          catch { return false; }
        },
        5000,
      );
      role = msgpack.decode(ackData)?.kwargs?.role || 'READ_ONLY';
    } catch (_) {
      console.warn('FFAST: no HELLO_ACK received — using READ_ONLY (backward compat)');
    }
    return role;
  }

  _pollBuf(buf, predicate, timeout) {
    return new Promise((resolve, reject) => {
      const deadline = Date.now() + timeout;
      const tick = () => {
        const i = buf.findIndex(predicate);
        if (i >= 0) { resolve(buf.splice(i, 1)[0]); return; }
        if (Date.now() > deadline) { reject(new Error('Timeout')); return; }
        setTimeout(tick, 10);
      };
      tick();
    });
  }

  _dispatch(data) {
    try {
      const msg = data instanceof ArrayBuffer
        ? msgpack.decode(data)
        : null;
      if (!msg || !msg.event) return;
      const h = this._handlers.get(msg.event);
      if (h) h(msg.kwargs || {}, msg.args || []);
    } catch (e) {
      console.warn('FFAST: dispatch error', e);
    }
  }

  send(event, kwargs = {}, args = []) {
    if (this._ws?.readyState === WebSocket.OPEN)
      this._ws.send(msgpack.encode({ event, args, kwargs }));
  }

  on(event, handler)   { this._handlers.set(event, handler); }
  off(event)           { this._handlers.delete(event); }

  close() {
    this.send('GRACEFUL_DISCONNECT', {});
    this._ws?.close();
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Three.js molecular renderer
// ─────────────────────────────────────────────────────────────────────────────

class MoleculeRenderer {
  constructor(canvas) {
    this._canvas = canvas;

    this._renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    this._renderer.setPixelRatio(window.devicePixelRatio);
    this._renderer.setClearColor(0x1a1a1e, 1);

    this._scene   = new THREE.Scene();
    this._camera  = new THREE.PerspectiveCamera(60, 1, 0.01, 5000);
    this._camera.position.set(0, 0, 20);

    this._controls = new OrbitControls(this._camera, canvas);
    this._controls.enableDamping = true;
    this._controls.dampingFactor = 0.1;

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
    this._camera.aspect = w / h;
    this._camera.updateProjectionMatrix();
  }

  _startLoop() {
    const loop = () => {
      this._rafId = requestAnimationFrame(loop);
      this._controls.update();
      this._renderer.render(this._scene, this._camera);
    };
    loop();
  }

  // ── scene update ──────────────────────────────────────────────────────────

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

  _updateAtoms(atoms) {
    this._clearAtoms();
    const n = atoms.positions.length;
    if (n === 0) return;
    this._cachedAtomPositions = atoms.positions;
    this._cachedAtomSizes = atoms.sizes;

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

      const [cr, cg, cb] = atoms.colors[i];
      color.setRGB(cr, cg, cb);
      mesh.setColorAt(i, color);
    }
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;

    this._atomMesh = mesh;
    this._scene.add(mesh);
  }

  _updateBonds(bonds) {
    this._clearBonds();
    const segs = bonds.segments;
    if (!segs || segs.length === 0) return;

    const positions = new Float32Array(segs.length * 3);
    for (let i = 0; i < segs.length; i++) {
      positions[i * 3]     = segs[i][0];
      positions[i * 3 + 1] = segs[i][1];
      positions[i * 3 + 2] = segs[i][2];
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const mat = new THREE.LineBasicMaterial({ color: 0x888888, linewidth: 1 });
    this._bondLines = new THREE.LineSegments(geo, mat);
    this._scene.add(this._bondLines);
  }

  _updateForces(forces) {
    this._clearForces();
    if (!forces.starts || forces.starts.length === 0) return;

    const group = new THREE.Group();
    const scale = 0.5; // visual scale factor for force vectors
    for (let i = 0; i < forces.starts.length; i++) {
      const [ox, oy, oz] = forces.starts[i];
      const [vx, vy, vz] = forces.vectors[i];
      const [cr, cg, cb] = forces.colors[i] || [0.9, 0.4, 0.1];
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
  }
  _clearBonds()     { if (this._bondLines)    { this._scene.remove(this._bondLines);    this._bondLines.geometry.dispose();     this._bondLines    = null; } }
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

  _applyCamera(cam) {
    // cam fields: center, distance, azimuth, elevation, fov, projection
    const { center = [0,0,0], distance = 10, azimuth = 0, elevation = 30, fov = 60 } = cam;
    this._camera.fov = fov;
    this._camera.updateProjectionMatrix();

    const phi   = (90 - elevation) * Math.PI / 180;
    const theta = azimuth * Math.PI / 180;
    const x = center[0] + distance * Math.sin(phi) * Math.sin(theta);
    const y = center[1] + distance * Math.cos(phi);
    const z = center[2] + distance * Math.sin(phi) * Math.cos(theta);
    this._camera.position.set(x, y, z);
    this._controls.target.set(center[0], center[1], center[2]);
    this._controls.update();
  }

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
      fov: this._camera.fov,
      projection: 'perspective',
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
    this._controls.update();
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Application — wires UI, connection, and renderer together
// ─────────────────────────────────────────────────────────────────────────────

class FFastApp {
  constructor() {
    this._conn = null;
    this._renderer = null;
    this._datasets = new Map();   // fingerprint → meta
    this._models = new Map();     // model fingerprint → {name, dataset_fingerprints}
    this._currentViewId = null;
    this._frameCount = 0;
    this._cameraThrottle = null;

    // Remote file browser state
    this._fbMode = 'dataset';  // 'dataset' | 'prediction'
    this._fbPath = null;       // current directory abspath (server-side)
    this._fbParent = null;     // parent abspath, or null at root
    this._fbHome = null;       // server user's home directory
    this._fbSelected = null;   // selected filename within _fbPath

    this._initRenderer();
    this._initUI();
    this._applyUrlParams();
  }

  _initRenderer() {
    const canvas = document.getElementById('canvas');
    this._renderer = new MoleculeRenderer(canvas);
    this._renderer._onCameraChange = (cam) => this._sendSetCamera(cam);
  }

  _initUI() {
    document.getElementById('connect-btn').addEventListener('click', () => this._connect());
    document.getElementById('disconnect-btn').addEventListener('click', () => this._disconnect());
    document.getElementById('open-view-btn').addEventListener('click', () => this._openView());
    document.getElementById('reset-camera-btn').addEventListener('click', () => {
      this._renderer.resetCamera();
    });

    const slider = document.getElementById('frame-slider');
    slider.addEventListener('input', () => this._onFrameSlider());

    // Dataset selection drives which predictions (models) are applicable.
    document.getElementById('dataset-select').addEventListener('change', () => this._updateModelSelect());

    // Remote file browser — dataset vs prediction mode
    document.getElementById('load-remote-btn').addEventListener('click', () => this._openFileBrowser('dataset'));
    document.getElementById('load-prediction-btn').addEventListener('click', () => this._openFileBrowser('prediction'));
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

      this._setStatus(`Connected (${conn.role})`, 'connected');
      document.getElementById('connect-btn').disabled = true;
      document.getElementById('disconnect-btn').disabled = false;
      document.getElementById('open-view-btn').disabled = false;
      document.getElementById('load-remote-btn').disabled = false;
      document.getElementById('load-prediction-btn').disabled = false;

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
    this._datasets.clear();
    this._models.clear();
    this._currentViewId = null;
    this._updateDatasetSelect();
    this._updateModelSelect();
    document.getElementById('connect-btn').disabled = false;
    document.getElementById('disconnect-btn').disabled = true;
    document.getElementById('open-view-btn').disabled = true;
    document.getElementById('load-remote-btn').disabled = true;
    document.getElementById('load-prediction-btn').disabled = true;
    document.getElementById('reset-camera-btn').disabled = true;
    document.getElementById('frame-slider').disabled = true;
    this._closeFileBrowser();
    this._setStatus('Disconnected', '');
  }

  _onDatasetMeta(fp, meta) {
    this._datasets.set(fp, meta);
    this._updateDatasetSelect();
  }

  _updateDatasetSelect() {
    const sel = document.getElementById('dataset-select');
    const prev = sel.value;
    sel.innerHTML = '<option value="">— none —</option>';
    for (const [fp, meta] of this._datasets) {
      const opt = document.createElement('option');
      opt.value = fp;
      opt.textContent = `${meta.name || fp.slice(0,8)} (${meta.n} frames)`;
      sel.appendChild(opt);
    }
    if (this._datasets.has(prev)) sel.value = prev;
    // Prediction applicability depends on the selected dataset.
    this._updateModelSelect();
  }

  _onModelMeta(fp, meta) {
    // meta = {name, dataset_fingerprints}. Fired on connect-replay and after
    // a LOAD_PREDICTION completes (the ghost model registers its forces cache).
    this._models.set(fp, meta || {});
    this._updateModelSelect();
    // Auto-select a freshly loaded prediction for the current dataset so the
    // user can just click Open View.
    const dsFp = document.getElementById('dataset-select').value;
    if (dsFp && (meta?.dataset_fingerprints || []).includes(dsFp)) {
      document.getElementById('model-select').value = fp;
      this._setStatus(`Prediction "${meta.name || fp.slice(0,8)}" ready`, 'connected');
    }
  }

  _updateModelSelect() {
    const sel = document.getElementById('model-select');
    const prev = sel.value;
    const dsFp = document.getElementById('dataset-select').value || null;
    sel.innerHTML = '<option value="">— none —</option>';
    for (const [fp, meta] of this._models) {
      // Only offer predictions computed for the selected dataset.
      const fps = meta.dataset_fingerprints || [];
      if (dsFp && fps.length && !fps.includes(dsFp)) continue;
      const opt = document.createElement('option');
      opt.value = fp;
      opt.textContent = meta.name || fp.slice(0, 8);
      sel.appendChild(opt);
    }
    if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
  }

  _openView() {
    if (!this._conn) return;
    const fp = document.getElementById('dataset-select').value || null;
    const modelFp = document.getElementById('model-select').value || null;
    this._currentViewId = 'view-0';
    this._conn.send('OPEN_VIEW', {
      view_id: this._currentViewId,
      dataset_ref: fp || null,
      prediction_ref: modelFp,   // null clears the force overlay
    });
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
    const dsFp = document.getElementById('dataset-select').value;
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
      this._conn.send('PROBE_DATASET_KEYS', {}, [path]);
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
  }

  _onScenePatch(kw) {
    const patch = kw.patch || kw;
    if (!patch) return;
    const changed = patch.changed || [];
    this._renderer.applyPatch(patch, changed);
    if (changed.includes?.('structure_index') || changed.structure_index) {
      // frame label update handled by slider
    }
  }

  _onCommandResult(kw) {
    const result = kw.result || kw;
    if (!result?.success) {
      console.warn('VIEW_COMMAND failed:', result?.error);
    }
  }

  _onFrameSlider() {
    if (!this._conn || !this._currentViewId) return;
    const slider = document.getElementById('frame-slider');
    const frame = parseInt(slider.value, 10);
    this._updateFrameLabel(frame, this._frameCount);
    this._conn.send('VIEW_COMMAND', {
      type: 'SET_FRAME',
      view_id: this._currentViewId,
      view_version: 0,  // server applies SET_FRAME without version check
      frame_index: frame,
    });
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
    // Try to find which dataset the scene belongs to by checking the current view
    const sel = document.getElementById('dataset-select');
    return sel.value || null;
  }

  _setStatus(text, cls) {
    const el = document.getElementById('status');
    el.textContent = text;
    el.className = cls;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Bootstrap
// ─────────────────────────────────────────────────────────────────────────────

const app = new FFastApp();

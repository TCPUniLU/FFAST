/**
 * Loupe satellite — a popped-out 3D view in its own browser tab.
 *
 * It holds NO WebSocket. It renders scenes the main tab relays over a
 * BroadcastChannel and posts frame intents back, which the main tab drives over
 * its single (controlling) connection. This mirrors the Qt "separate Loupe
 * window" without a second server connection (the server is single-client:
 * one shared outbound queue, one CONTROLLING client).
 */

import { MoleculeRenderer } from './renderer.js';

export class LoupeSatelliteApp {
  constructor(chId) {
    document.body.classList.add('loupe-only');
    this._renderer = new MoleculeRenderer(document.getElementById('canvas'));
    this._frameCount = 0;

    this._bc = new BroadcastChannel(chId);
    this._bc.onmessage = (e) => this._onMessage(e.data);

    this._initUI();
    this._setStatus('Linked loupe — waiting for main view…');
    this._bc.postMessage({ t: 'hello' });   // ask the main tab for current state
  }

  _initUI() {
    document.getElementById('frame-slider').addEventListener('input', () => {
      const frame = parseInt(document.getElementById('frame-slider').value, 10);
      this._updateFrameLabel(frame, this._frameCount);
      this._bc.postMessage({ t: 'frame', index: frame });
    });
    document.getElementById('reset-camera-btn').addEventListener('click', () => {
      this._renderer.resetCamera();
    });
  }

  _onMessage(msg) {
    if (!msg || !msg.t) return;
    if (msg.t === 'scene') {
      this._renderer.applyScene(msg.scene);
      this._renderer.frameAtoms();
      document.getElementById('overlay').classList.add('hidden');
      document.getElementById('reset-camera-btn').disabled = false;
    } else if (msg.t === 'patch') {
      this._renderer.applyPatch(msg.patch, msg.changed);
    } else if (msg.t === 'meta') {
      this._frameCount = msg.frameCount || 0;
      const slider = document.getElementById('frame-slider');
      slider.max = Math.max(0, this._frameCount - 1);
      if (typeof msg.frameIndex === 'number') slider.value = msg.frameIndex;
      slider.disabled = this._frameCount <= 1;
      this._updateFrameLabel(parseInt(slider.value, 10) || 0, this._frameCount);
      this._setStatus(msg.title ? `Linked loupe — ${msg.title}` : 'Linked loupe', 'connected');
    } else if (msg.t === 'bye') {
      this._setStatus('Main view disconnected', 'error');
    }
  }

  _updateFrameLabel(frame, total) {
    document.getElementById('frame-label').textContent = `${frame} / ${Math.max(0, total - 1)}`;
  }

  _setStatus(text, cls = '') {
    const el = document.getElementById('status');
    el.textContent = text;
    el.className = cls;
  }
}

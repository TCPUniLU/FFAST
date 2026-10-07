/**
 * Camera pane (ADR 0045 issue 04): presets, projection, manual angle entry,
 * COM tracking, axis gizmo, background colour. Mirrors modules/loupe/loupeCamera.py.
 * The exact azimuth/elevation/distance fields sit behind an "Exact angles"
 * link (ADR 0055): dragging and the presets cover most uses.
 */

import { createPane, checkboxRow, numberRow, colorRow, buttonGroup } from '../sidebar.js';

/**
 * @param {HTMLElement} sidebarEl
 * @param {{
 *   onOrtho: (enabled: boolean) => void,
 *   onPreset: (azimuth: number, elevation: number) => void,
 *   onManual: (azimuth: number, elevation: number, distance: number) => void,
 *   onCOM: (enabled: boolean) => void,
 *   onGizmo: (enabled: boolean) => void,
 *   onBackground: (hex: string) => void,
 * }} callbacks
 */
export function createCameraPane(sidebarEl, callbacks) {
  const { el, body } = createPane('Camera');
  sidebarEl.appendChild(el);

  const comInput = checkboxRow(body, 'Origin COM', true, callbacks.onCOM);
  const orthInput = checkboxRow(body, 'Orthographic', false, callbacks.onOrtho);
  const gizInput = checkboxRow(body, 'Axes gizmo', false, callbacks.onGizmo);

  const exactLink = document.createElement('button');
  exactLink.className = 'link-btn';
  exactLink.id = 'exact-angles-link';
  exactLink.textContent = 'Exact angles';
  exactLink.setAttribute('aria-expanded', 'false');
  const exact = document.createElement('div');
  exact.className = 'exact-angles hidden';
  body.append(exactLink, exact);
  const showExact = (shown) => {
    exact.classList.toggle('hidden', !shown);
    exactLink.setAttribute('aria-expanded', String(shown));
  };
  exactLink.addEventListener('click', () => showExact(exact.classList.contains('hidden')));

  const azInput = numberRow(exact, 'Azimuth (°)', 0, { step: 0.1 }, () => _sendManual());
  const elInput = numberRow(exact, 'Elevation (°)', 30, { step: 0.1 }, () => _sendManual());
  const distInput = numberRow(exact, 'Distance', 10, { min: 0.1, step: 0.1 }, () => _sendManual());

  function _sendManual() {
    callbacks.onManual(parseFloat(azInput.value), parseFloat(elInput.value), parseFloat(distInput.value));
  }

  buttonGroup(body, [
    { text: 'XY', title: 'Top view (az 0°, el 90°)', onClick: () => callbacks.onPreset(0, 90) },
    { text: 'XZ', title: 'Front view (az 0°, el 0°)', onClick: () => callbacks.onPreset(0, 0) },
    { text: 'YZ', title: 'Side view (az 90°, el 0°)', onClick: () => callbacks.onPreset(90, 0) },
  ]);

  const backgroundRow = colorRow(body, 'Background', '#000000', callbacks.onBackground);

  return {
    orthStatus: new Map(), // fp -> true/false;
    gizStatus: new Map(), // fp -> true/fasle;
    backgroundStatus: new Map(), //fp -> hex string;
    /** Reflect the renderer's live camera into the manual fields (no events fired). */
    syncFromCamera(cam) {
      azInput.value = cam.azimuth.toFixed(1);
      elInput.value = cam.elevation.toFixed(1);
      distInput.value = cam.distance.toFixed(2);
    },
    /** Show or hide the rows behind "Exact angles" (sidebar search opens them). */
    showExactAngles: showExact,
    /** Set the Origin COM checkbox without firing onCOM (per-dataset restore). */
    setCOM(enabled) {
      comInput.checked = enabled;
    },

    saveState(fp) {
      this.orthStatus.set(fp, orthInput.checked);
      this.gizStatus.set(fp, gizInput.checked);
      this.backgroundStatus.set(fp, backgroundRow.value);
    },

    loadState(fp) {
      orthInput.checked = this.orthStatus.get(fp) || false;
      gizInput.checked = this.gizStatus.get(fp) || false;
      backgroundRow.value = this.backgroundStatus.get(fp) || '#000000';

      const randomEvent = new Event('change');
      const randomInputEvent = new Event('input');
      orthInput.dispatchEvent(randomEvent);
      gizInput.dispatchEvent(randomEvent);
      backgroundRow.dispatchEvent(randomInputEvent);
    },
  };
}

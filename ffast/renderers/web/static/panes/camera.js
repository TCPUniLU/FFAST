/**
 * Camera pane (ADR 0045 issue 04): presets, projection, manual angle entry,
 * COM tracking, axis gizmo, background colour. Mirrors modules/loupe/loupeCamera.py.
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
  checkboxRow(body, 'Orthographic', false, callbacks.onOrtho);
  checkboxRow(body, 'Axes gizmo', false, callbacks.onGizmo);

  const azInput = numberRow(body, 'Azimuth (°)', 0, { step: 0.1 }, () => _sendManual());
  const elInput = numberRow(body, 'Elevation (°)', 30, { step: 0.1 }, () => _sendManual());
  const distInput = numberRow(body, 'Distance', 10, { min: 0.1, step: 0.1 }, () => _sendManual());

  function _sendManual() {
    callbacks.onManual(parseFloat(azInput.value), parseFloat(elInput.value), parseFloat(distInput.value));
  }

  buttonGroup(body, [
    { text: 'XY', title: 'Top view (az 0°, el 90°)', onClick: () => callbacks.onPreset(0, 90) },
    { text: 'XZ', title: 'Front view (az 0°, el 0°)', onClick: () => callbacks.onPreset(0, 0) },
    { text: 'YZ', title: 'Side view (az 90°, el 0°)', onClick: () => callbacks.onPreset(90, 0) },
  ]);

  colorRow(body, 'Background', '#000000', callbacks.onBackground);

  return {
    /** Reflect the renderer's live camera into the manual fields (no events fired). */
    syncFromCamera(cam) {
      azInput.value = cam.azimuth.toFixed(1);
      elInput.value = cam.elevation.toFixed(1);
      distInput.value = cam.distance.toFixed(2);
    },
    /** Set the Origin COM checkbox without firing onCOM (per-dataset restore). */
    setCOM(enabled) {
      comInput.checked = enabled;
    },
  };
}

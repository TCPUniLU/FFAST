/**
 * Export pane (ADR 0045 Phase 4, issue 19). Mirrors the Qt VIEW > EXPORT pane:
 * export the current 3D frame as a PNG, either opaque over a chosen background
 * colour or with a real transparent background.
 *
 * The image is produced entirely in the browser from the WebGL canvas
 * (renderer.capturePng → toDataURL); the near-black→alpha keying the vispy
 * path used is unnecessary here (a real alpha buffer, ADR 0045 issue 19).
 */

import { createPane, colorRow, buttonRow } from '../sidebar.js';

/**
 * @param {HTMLElement} sidebarEl
 * @param {{
 *   onExport: (opts: {transparent: boolean, background: string}) => void,
 *   getBackground?: () => string,
 * }} callbacks
 */
export function createExportPane(sidebarEl, callbacks) {
  const { el, body } = createPane('Export');
  sidebarEl.appendChild(el);

  const bg = colorRow(body, 'Background', '#000000', () => {});

  buttonRow(body, '', 'Export PNG (opaque)', () => {
    callbacks.onExport({ transparent: false, background: bg.value });
  }).title = 'Download a PNG with the chosen background colour';

  buttonRow(body, '', 'Export PNG (transparent)', () => {
    callbacks.onExport({ transparent: true, background: bg.value });
  }).title = 'Download a PNG with a transparent background';

  return {
    /** Reflect the live viewport background into the picker (keeps parity with
     * the Camera pane's background control). */
    setBackground(hex) {
      if (hex) bg.value = hex;
    },
  };
}

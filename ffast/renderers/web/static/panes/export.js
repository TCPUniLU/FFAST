/**
 * Export pane (ADR 0045 Phase 4, issue 19). Mirrors the Qt VIEW > EXPORT pane:
 * export the current 3D frame as a PNG, either opaque over a chosen background
 * colour or with a real transparent background.
 *
 * The image is produced entirely in the browser from the WebGL canvas
 * (renderer.capturePng → toDataURL); the near-black→alpha keying the vispy
 * path used is unnecessary here (a real alpha buffer, ADR 0045 issue 19).
 * The pane shows one view's `export` settings (`show`) and writes into them.
 */

import { createPane, colorRow, buttonRow } from '../sidebar.js';
import { defaultSettings } from '../view_settings.js';

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

  let state = defaultSettings().export;   // the shown view's settings
  const bg = colorRow(body, 'Background', '#000000', (hex) => { state.background = hex; });

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
      if (!hex) return;
      bg.value = hex;
      state.background = hex;
    },

    /** Show a view's `export` settings; later changes are written into them. */
    show(settings) {
      state = settings;
      bg.value = settings.background;
    },
  };
}

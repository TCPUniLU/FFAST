/**
 * The sidebar settings of one 3D view (ADR 0060): one plain object a `View`
 * carries, shown by the panes of the focused view and written to as their
 * controls change. Pure, so it tests without a canvas.
 */

import { DEFAULT_LOOK, startCommands } from './start_settings.js';

/** A new view's settings, today's defaults. */
export function defaultSettings() {
  return {
    colorBy: { source: 'element', prediction: null, colormap: 'viridis', norm: 'None', vmin: null, vmax: null },
    camera: { ortho: false, gizmo: false, background: '#000000' },
    display: { atomSize: 1, hide: '', highlight: '', unitCell: true },
    bonds: { width: 100, color: DEFAULT_LOOK.bondColour, bondType: 'Dynamic', fixedIndices: [] },
    extract: { indices: '' },
    export: { background: '#000000' },
    align: { kabsch: false, heavyOnly: true, atomAlign: false, idxInput: '', indices: [] },
    forces: { show: false, modelKey: null, length: DEFAULT_LOOK.forces.length,
      normalised: DEFAULT_LOOK.forces.normalised, onlyForces: false,
      filterEnabled: false, atomIndices: [] },
    view: { originCenterOfMass: true, videoFPS: 30, videoSkipFrames: 0 },
  };
}

/** An independent copy of `settings`. */
export function copySettings(settings) {
  return structuredClone(settings);
}

/**
 * An independent panel's starting look: its tab file's `start` keys over the
 * defaults (ADR 0056 rule 13).
 * @param {object|null} start the panel's `start` keys
 * @param {{modelFp: string|null, catalog: Array<{id: string}>}} ctx
 */
export function startSettings(start, { modelFp, catalog }) {
  const { settings: look } = startCommands(start, { modelFp, catalog });
  const s = defaultSettings();
  s.colorBy.source = look.source;
  s.colorBy.prediction = look.prediction;
  s.colorBy.colormap = look.colormap;
  s.display.atomSize = look.atomSize;
  s.bonds.width = look.bondWidth;
  s.bonds.color = look.bondColour;
  s.forces.show = look.forces.show;
  s.forces.modelKey = look.forces.show ? modelFp : null;
  s.forces.length = look.forces.length;
  s.forces.normalised = look.forces.normalised;
  return s;
}

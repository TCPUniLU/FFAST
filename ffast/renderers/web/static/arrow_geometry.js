/**
 * Force arrow sizes, in the scene's length units (Å) — the same numbers as the
 * Qt view's ffast/renderers/vispy/arrow_mesh.py. Pure, so it tests without a canvas.
 */

export const SHAFT_RADIUS = 0.05;
export const HEAD_RADIUS = 0.12;
export const HEAD_LENGTH = 0.25;

/**
 * The parts of one arrow of total `length`. An arrow shorter than its head
 * gets a proportionally smaller head (and no shaft). `thickness` scales both radii.
 * @param {number} length
 * @param {number} [thickness] 1 = the Qt sizes
 * @returns {{shaftLength: number, headLength: number, shaftRadius: number, headRadius: number}}
 */
export function arrowParts(length, thickness = 1) {
  const headLength = Math.min(HEAD_LENGTH, length);
  return {
    shaftLength: length - headLength,
    headLength,
    shaftRadius: SHAFT_RADIUS * thickness,
    headRadius: HEAD_RADIUS * thickness * (headLength / HEAD_LENGTH),
  };
}

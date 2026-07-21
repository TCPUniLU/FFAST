/**
 * Geometry read-outs for the Info pick tool (ADR 0045 issue 11).
 *
 * Exact ports of client/mathUtils.py so the browser reports the same numbers
 * as the Qt loupe: distance is Euclidean; angle is the unsigned bond angle at
 * the middle atom; dihedral is the UNSIGNED angle between the two plane
 * normals (arccos of normalised cross products), range 0–180° — Qt does NOT
 * use atan2 and does not sign the dihedral.
 */

const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const cross = (u, v) => [
  u[1] * v[2] - u[2] * v[1],
  u[2] * v[0] - u[0] * v[2],
  u[0] * v[1] - u[1] * v[0],
];
const dot = (u, v) => u[0] * v[0] + u[1] * v[1] + u[2] * v[2];
const norm = (u) => Math.sqrt(dot(u, u));
function unit(u) {
  const n = norm(u) || 1;
  return [u[0] / n, u[1] / n, u[2] / n];
}
const DEG = 180 / Math.PI;
const clampAcos = (x) => Math.acos(Math.max(-1, Math.min(1, x)));

/** Euclidean distance ‖a − b‖. */
export function distance(a, b) {
  return norm(sub(a, b));
}

/** Unsigned bond angle at vertex `b` (degrees), acos(unit(c−b)·unit(a−b)). */
export function angle(a, b, c) {
  return clampAcos(dot(unit(sub(c, b)), unit(sub(a, b)))) * DEG;
}

/**
 * Unsigned dihedral over (a, b, c, d) in degrees. Mirrors mathUtils.getDihedral:
 * b0 = b−a, b1 = b−c, b2 = c−d; n1 = b0×b1, n2 = b2×b1; acos(unit(n1)·unit(n2)).
 */
export function dihedral(a, b, c, d) {
  const b0 = sub(b, a);
  const b1 = sub(b, c);
  const b2 = sub(c, d);
  const n1 = unit(cross(b0, b1));
  const n2 = unit(cross(b2, b1));
  return clampAcos(dot(n1, n2)) * DEG;
}

/**
 * Format the Info read-out for a set of picked world-space positions
 * (1→position, 2→distance, 3→angle, 4→dihedral).
 * @param {number[][]} pts world-space coordinates of the picked atoms, in pick order
 * @param {number[]} ids scientific atom ids, parallel to `pts`
 */
export function infoReadout(pts, ids) {
  if (!pts || pts.length === 0) return '';
  if (pts.length === 1) {
    const [x, y, z] = pts[0];
    return `Atom ${ids[0]}: (${x.toFixed(2)}, ${y.toFixed(2)}, ${z.toFixed(2)})`;
  }
  if (pts.length === 2) return `Distance: ${distance(pts[0], pts[1]).toFixed(2)} Å`;
  if (pts.length === 3) return `Angle: ${angle(pts[0], pts[1], pts[2]).toFixed(1)}°`;
  return `Dihedral: ${dihedral(pts[0], pts[1], pts[2], pts[3]).toFixed(1)}°`;
}

/**
 * Geometry read-outs for the Info pick tool (ADR 0045 issue 11).
 *
 * The browser's copy of ffast/chemistry/geometry.py — the one Python version,
 * used by the server metrics and the Qt Info tool. The browser keeps its own
 * copy so the read-out is instant (it already holds the positions);
 * tests/ffast/geometry_cases.json pins both copies to the same answers
 * (tests/ffast/renderers/web/test_measure_parity.py), so change them together.
 *
 * Distance is Euclidean; angle is the unsigned angle at the middle atom;
 * dihedral is the UNSIGNED angle between the two plane normals, 0–180°.
 * Undefined geometry — an outer atom on the vertex, or three atoms in a line —
 * returns NaN rather than a plausible-looking number.
 */

const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const cross = (u, v) => [
  u[1] * v[2] - u[2] * v[1],
  u[2] * v[0] - u[0] * v[2],
  u[0] * v[1] - u[1] * v[0],
];
const dot = (u, v) => u[0] * v[0] + u[1] * v[1] + u[2] * v[2];
const norm = (u) => Math.sqrt(dot(u, u));
/** Squared length below which a direction is undefined (same as geometry.py). */
const UNDEFINED_BELOW = 1e-12;
/** Unit vector, or null when the vector is too short to have a direction. */
function unit(u) {
  const n2 = dot(u, u);
  if (n2 < UNDEFINED_BELOW) return null;
  const n = Math.sqrt(n2);
  return [u[0] / n, u[1] / n, u[2] / n];
}
const DEG = 180 / Math.PI;
const clampAcos = (x) => Math.acos(Math.max(-1, Math.min(1, x)));
/** Angle between two unit vectors in degrees; NaN if either is undefined. */
const angleBetween = (u, v) => (u && v ? clampAcos(dot(u, v)) * DEG : NaN);

/** Euclidean distance ‖a − b‖. */
export function distance(a, b) {
  return norm(sub(a, b));
}

/** Unsigned angle at vertex `b` (degrees), acos(unit(a−b)·unit(c−b)); NaN if undefined. */
export function angle(a, b, c) {
  return angleBetween(unit(sub(a, b)), unit(sub(c, b)));
}

/**
 * Unsigned dihedral over (a, b, c, d) in degrees, as geometry.dihedral:
 * axis = b−c; n1 = (b−a)×axis, n2 = (c−d)×axis; acos(unit(n1)·unit(n2)).
 * NaN when three consecutive atoms are in a line (a plane is undefined).
 */
export function dihedral(a, b, c, d) {
  const axis = sub(b, c);
  return angleBetween(unit(cross(sub(b, a), axis)), unit(cross(sub(c, d), axis)));
}

/** Read-out text for an angle in degrees; undefined geometry says so. */
const degrees = (v) => (Number.isNaN(v) ? 'undefined (atoms in a line)' : `${v.toFixed(1)}°`);

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
  if (pts.length === 3) return `Angle: ${degrees(angle(pts[0], pts[1], pts[2]))}`;
  return `Dihedral: ${degrees(dihedral(pts[0], pts[1], pts[2], pts[3]))}`;
}

/**
 * Camera angles and the camera's place, one convention for the whole browser
 * client (to_fix 9): the renderer, the Exact angles fields, a linked panel and
 * the server's CameraState all go through these two functions.
 *
 * The camera turns by R = Ry(azimuth) · Rx(−elevation) · Rz(roll). It sits at
 * `center + distance · R·(0, 0, 1)`, looks at `center`, and its up is
 * `R·(0, 1, 0)`. Azimuth turns about the world's y axis from +z towards +x,
 * elevation lifts the camera towards +y, and a positive roll turns the
 * picture clockwise. With roll 0 the up is the world's y as seen from the
 * camera, which is what the turntable camera before free rotation showed, so a
 * view saved without a roll looks the same.
 *
 * At the poles (elevation ±90°) azimuth and roll turn the same way; reading a
 * place back then gives roll 0 and puts the whole turn into azimuth, so the
 * same picture can come back with other numbers.
 */

const RAD = Math.PI / 180;

const rotY = (a) => [[Math.cos(a), 0, Math.sin(a)], [0, 1, 0], [-Math.sin(a), 0, Math.cos(a)]];
const rotX = (a) => [[1, 0, 0], [0, Math.cos(a), -Math.sin(a)], [0, Math.sin(a), Math.cos(a)]];
const rotZ = (a) => [[Math.cos(a), -Math.sin(a), 0], [Math.sin(a), Math.cos(a), 0], [0, 0, 1]];
const mul = (a, b) => a.map((row) => [0, 1, 2].map((j) => row[0] * b[0][j] + row[1] * b[1][j] + row[2] * b[2][j]));
const column = (m, j) => [m[0][j], m[1][j], m[2][j]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = (v) => {
  const n = Math.hypot(...v);
  return n ? v.map((x) => x / n) : v;
};

/** An angle in degrees, in (−180, 180]. */
const wrap = (deg) => {
  const d = ((deg + 180) % 360 + 360) % 360 - 180;
  return d === -180 ? 180 : d;
};

/**
 * The camera's place for a set of angles.
 * @param {{center?: number[], distance?: number, azimuth?: number, elevation?: number, roll?: number}} cam
 *   degrees; a missing roll is 0
 * @returns {{position: number[], up: number[]}}
 */
export function poseFromAngles({ center = [0, 0, 0], distance = 10, azimuth = 0, elevation = 0, roll = 0 }) {
  const r = mul(mul(rotY(azimuth * RAD), rotX(-elevation * RAD)), rotZ((roll || 0) * RAD));
  const back = column(r, 2);
  return {
    position: center.map((c, i) => c + distance * back[i]),
    up: column(r, 1),
  };
}

/**
 * The angles of a camera at `position` looking at `center` with `up` (which
 * need not be exactly square to the view: only its part across it counts).
 * @returns {{distance: number, azimuth: number, elevation: number, roll: number}} degrees
 */
export function anglesFromPose(position, center, up) {
  const offset = position.map((p, i) => p - center[i]);
  const distance = Math.hypot(...offset);
  const z = unit(offset);
  const y = unit(up.map((u, i) => u - dot(up, z) * z[i]));
  const x = cross(y, z);
  // R's columns are x, y, z; R = Ry(a) · Rx(b) · Rz(r) with b = −elevation
  const sinB = -z[1];
  const elevation = -Math.asin(Math.max(-1, Math.min(1, sinB))) / RAD;
  let azimuth;
  let roll;
  if (Math.abs(Math.cos(elevation * RAD)) > 1e-9) {
    azimuth = Math.atan2(z[0], z[2]) / RAD;
    roll = Math.atan2(x[1], y[1]) / RAD;
  } else {
    // at a pole: all of the turn is azimuth
    azimuth = Math.atan2(-x[2], x[0]) / RAD;
    roll = 0;
  }
  return { distance, azimuth: wrap(azimuth), elevation, roll: wrap(roll) };
}

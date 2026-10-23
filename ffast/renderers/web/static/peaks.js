// The worst frames of a timeline, picked and labelled (ADR 0063). Pure
// functions; panels.js draws the marks and analysis.js opens the frame.

/**
 * The `n` highest peaks of a per-frame series, by frame.
 * A peak is a finite value higher than the neighbour before it (if that one
 * is finite) and at least as high as the one after, so a level stretch gives
 * one peak, at its first frame. Two peaks never sit closer than
 * max(1, round(length / 50)) frames, the lower of a close pair being dropped.
 * Under 10 frames, or when every finite value is the same, there are none.
 * @param {ArrayLike<number>} y
 * @param {number} [n]
 * @returns {Array<{frame: number, value: number}>}
 */
export function pickPeaks(y, n = 5) {
  const len = y.length;
  if (len < 10) return [];
  const finite = (v) => Number.isFinite(v);
  let first = null, flat = true;
  for (let i = 0; i < len && flat; i++) {
    if (!finite(y[i])) continue;
    if (first === null) first = y[i];
    else if (y[i] !== first) flat = false;
  }
  if (flat) return [];
  const candidates = [];
  for (let i = 0; i < len; i++) {
    if (!finite(y[i])) continue;
    if (i > 0 && finite(y[i - 1]) && y[i] <= y[i - 1]) continue;
    if (i < len - 1 && finite(y[i + 1]) && y[i] < y[i + 1]) continue;
    candidates.push(i);
  }
  candidates.sort((a, b) => (y[b] - y[a]) || (a - b));
  const gap = Math.max(1, Math.round(len / 50));
  const taken = [];
  for (const i of candidates) {
    if (taken.length >= n) break;
    if (taken.some((j) => Math.abs(i - j) <= gap)) continue;
    taken.push(i);
  }
  return taken.sort((a, b) => a - b).map((frame) => ({ frame, value: y[frame] }));
}

/** The key under which a frame you opened is remembered. */
export function visitedKey(datasetFp, modelFp, frame) {
  return `${datasetFp}|${modelFp}|${frame}`;
}

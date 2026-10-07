/**
 * Frame links (ADR 0056 rule 3): a linked frame means the same structure,
 * not the same frame number.
 *
 * Datasets are related through their lineage, which the server announces
 * with each dataset (REMOTE_DATASET_META `parent`, `parent_frames`): a frame
 * subset's frame i is its parent's frame `parent_frames[i]`; an atom subset
 * keeps every frame of its parent. Following parents up to the dataset that
 * was loaded gives each frame its configuration: (root dataset, root frame).
 * A prediction is computed for its dataset's frames, so it needs no lineage.
 * Pure functions over the dataset metadata map, so they test without a server.
 */

/**
 * The configuration a frame is: the loaded dataset it comes from, and its
 * frame there. Null for a frame the dataset does not have.
 * @param {string} fp @param {number} frame
 * @param {Map<string, {n?: number, parent?: string|null, parent_frames?: number[]|null}>} datasets
 * @returns {{root: string, frame: number}|null}
 */
export function configurationOf(fp, frame, datasets) {
  let cur = fp, f = frame;
  for (let depth = 0; depth < 64; depth++) {
    const meta = datasets.get(cur);
    if (!meta) return null;
    if (!Number.isInteger(f) || f < 0 || (meta.n != null && f >= meta.n)) return null;
    if (!meta.parent) return { root: cur, frame: f };
    if (meta.parent_frames) f = meta.parent_frames[f];
    cur = meta.parent;
  }
  return null;   // a lineage this deep is a loop
}

/**
 * The frame of `toFp` that is the same configuration as frame `frame` of
 * `fromFp`, or null when `toFp` does not hold it (unrelated datasets, or a
 * subset that left that frame out).
 */
export function sameConfiguration(fromFp, frame, toFp, datasets) {
  if (fromFp === toFp) return datasets.has(fromFp) ? frame : null;
  const want = configurationOf(fromFp, frame, datasets);
  if (!want) return null;
  const n = datasets.get(toFp)?.n ?? 0;
  for (let j = 0; j < n; j++) {
    const have = configurationOf(toFp, j, datasets);
    if (!have) return null;   // toFp is not part of any known lineage
    if (have.root !== want.root) return null;
    if (have.frame === want.frame) return j;
  }
  return null;
}

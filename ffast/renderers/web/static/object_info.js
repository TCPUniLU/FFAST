/**
 * What a dataset or prediction is, as the rail menu's Info dialog shows it
 * (web parity plan 2026-10-29). Pure: it reads only the metadata a window
 * already has (REMOTE_DATASET_META, REMOTE_MODEL_META), as the desktop's
 * `client/object_info.py` describe() does from the object itself.
 */

import { elementSymbol } from './panels.js';
import { datasetUnits } from './units.js';
import { framesText } from './rail.js';

/** The desktop's formula rule (ffast/loaders/dataset.py zToChemicalFormula):
 * C then H with their counts always written, then the other symbols in
 * alphabetical order, a count only from 2 up. */
export function chemicalFormula(zs) {
  const count = new Map();
  for (const z of zs || []) {
    const sym = elementSymbol(z);
    count.set(sym, (count.get(sym) || 0) + 1);
  }
  let out = '';
  if (count.has('C')) out += `C${count.get('C')}`;
  if (count.has('H')) out += `H${count.get('H')}`;
  for (const [sym, n] of [...count].sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))) {
    if (sym === 'C' || sym === 'H') continue;
    out += `${sym}${n >= 2 ? n : ''}`;
  }
  return out;
}

const nameOf = (fp, datasets) => datasets.get(fp)?.name || String(fp).slice(0, 8);

/**
 * @param {'dataset'|'model'} kind
 * @param {string} fp
 * @param {object} meta REMOTE_DATASET_META or REMOTE_MODEL_META kwargs
 * @param {Map<string, object>} datasets fingerprint → dataset meta
 * @returns {{title: string, rows: Array<[string, string]>}}
 */
export function objectInfo(kind, fp, meta, datasets) {
  const title = meta.name || fp.slice(0, 8);
  if (kind !== 'dataset') {
    const names = (meta.dataset_fingerprints || []).map((d) => nameOf(d, datasets)).join(', ');
    return { title, rows: [['Kind', 'Prediction'], ['Results for', names], ['Fingerprint', fp]]
      .filter(([, v]) => v !== '') };
  }
  const els = meta.elements || [];
  const offsets = meta.offsets || null;
  let kindText = `${meta.source_type || 'Dataset'} file`;
  if (meta.parent_frames) kindText = meta.frozen ? 'Frozen subset' : 'Subset';
  else if (meta.parent) kindText = 'Atom subset';
  let atoms = '';
  let formula = '';
  if (meta.variable && offsets && offsets.length > 1) {
    // A loop, not Math.min(...sizes): 1.77M arguments overflow the stack.
    let lo = Infinity, hi = -Infinity;
    for (let i = 1; i < offsets.length; i++) {
      const size = offsets[i] - offsets[i - 1];
      if (size < lo) lo = size;
      if (size > hi) hi = size;
    }
    atoms = lo === hi ? String(lo) : `${lo}–${hi}`;
    // The first structure's elements (every atom's is not sent).
    const first = meta.first_elements || els.slice(offsets[0], offsets[1]);
    formula = `varies (first: ${chemicalFormula(first)})`;
  } else if (els.length) {
    atoms = String(els.length);
    formula = chemicalFormula(els);
  }
  const units = datasetUnits(fp, datasets);
  const rows = [
    ['Kind', kindText],
    ['Cut from', meta.parent ? nameOf(meta.parent, datasets) : ''],
    ['Frames', meta.n == null ? '' : framesText(meta.n)],
    ['Atoms per structure', atoms],
    ['Formula', formula],
    ['Forces', meta.has_forces == null ? '' : meta.has_forces ? 'yes' : 'no'],
    ['Energy unit', units.energy || ''],
    ['Force unit', units.force || ''],
    ['File', meta.path || ''],
    ['Fingerprint', fp],
  ];
  return { title, rows: rows.filter(([, v]) => v !== '') };
}

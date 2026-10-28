/**
 * The hint bar's steps while a pick tool is on, by how many atoms are picked.
 * Pure, so the wording tests without a page. Each step is `[lead, strong, tail]`
 * as in app.js `HINTS`; the key says which hint a dismissal belongs to.
 */

/** The browser-storage key of a tool's hint (the dismissal is remembered by it). */
export function toolHintKey(tool) {
  return `tool-${tool}`;
}

const INFO = [
  [['Click an atom: ', 'its position']],
  [['Position shown. ', 'Click a 2nd atom', ' for the distance']],
  [['Distance shown. ', 'Click a 3rd atom', ' for the angle']],
  [['Angle shown. ', 'Click a 4th atom', ' for the dihedral']],
  // A fifth click drops the oldest pick (app.js `_togglePick`): a rolling window of four.
  [['Dihedral shown. ', 'The next click', ' replaces the oldest pick']],
];

/**
 * @param {string} tool a PICK_TOOLS id
 * @param {number} picked atoms picked now
 * @returns {Array<[string, string, string?]>|null} null for a tool without hints
 */
export function toolHint(tool, picked) {
  switch (tool) {
    case 'info':
      return INFO[Math.min(Math.max(picked, 0), INFO.length - 1)];
    case 'bonds':
      return picked === 0
        ? [['Click 2 atoms: ', 'a bond between them is made, or removed if it exists']]
        : [['Click ', 'one more atom', ' to finish the pair']];
    case 'align':
      return [['Click 3 atoms: ', `${Math.min(picked, 3)} of 3`, ' make the frame']];
    case 'extract':
      // What the tool is for, then what to do next (to_fix_v2: it did not say).
      return picked === 0
        ? [['Makes a new dataset with only the atoms you pick. ', 'Click atoms or drag a box', ''],
          ['', 'Esc', ' stops']]
        : [[`${picked} atom${picked === 1 ? '' : 's'} in the Extract box. `, 'Extract as Subset Dataset',
          ' makes the new dataset'], ['Click more to add, ', 'Esc', ' stops']];
    case 'forces':
      // Arrows show on all atoms until a pick narrows them (to_fix_v2).
      return picked === 0
        ? [['Arrows on all atoms. ', 'Click atoms or drag a box', ' to keep arrows only on those'],
          ['', 'Esc', ' stops']]
        : [[`${picked} atom${picked === 1 ? '' : 's'} with arrows. `, 'Click more', ' to add'],
          ['', 'Esc', ' stops']];
    default:
      return null;
  }
}

/**
 * In-place help (ADR 0055): a "?" button that shows or hides a short note
 * saying what a section or control is for. Used by the sidebar sections and
 * the analysis tab picker.
 */

import { icon } from './icons.js';

/** What each sidebar section is for, behind its "?" (ADR 0055). The first
 * sentence is also the button's hover text, so each says what the section is
 * for on its own and stays under 90 characters. */
export const SECTION_HELP = {
  'Colour By': 'Chooses what colours the atoms: element, movement or a per-atom metric. '
    + 'A metric can be force error, for example. Any colouring other than Elements shows a colour bar.',
  'Camera': 'Sets the viewing angle and zoom of the 3D view. Drag to orbit, scroll to zoom. XY, XZ and YZ snap to an '
    + 'axis view; "Exact angles" sets the angles by number. '
    + 'Keys: ←/→ frame, Space play, Esc puts down a pick tool, / search.',
  'Display': 'Hides or highlights atoms in the 3D view. Hide atoms by index (0 1 2) or element (C, -H); '
    + 'highlight atoms by index.',
  'Bonds': 'Draws bonds between atoms, found from distances or from your own list. '
    + 'Dynamic bonds follow atom distances in every frame. Fixed bonds use your own '
    + 'list of pairs; the Bonds pick tool adds or removes a pair.',
  'Force Vectors': 'Draws an arrow for the force on each atom. Source is the reference data '
    + '(Ground Truth) or a loaded prediction. The Force pick tool limits the arrows to chosen atoms.',
  'Extract Subset': 'Makes a new dataset from only the atoms you choose. It keeps them in every frame, '
    + 'for example one molecule out of a solvent box. Type atom numbers (0 1 2) or elements '
    + '(C keeps carbon, -H drops hydrogen), or arm the Extract pick tool and click atoms. '
    + 'Then press Extract as Subset Dataset: the new dataset appears in the list on the left, '
    + 'and it can be plotted, coloured and exported like any other.',
  'Export': 'Saves the current 3D frame as a PNG, on a background colour or transparent.',
  'Alignment': 'Rotates every frame onto a reference so the motion you see is internal. Kabsch '
    + 'aligns the whole structure; 3-atom frame align uses three atoms you pick or type.',
};

/** The analysis tab picker's note. */
export const PICKER_HELP = 'Chooses which datasets and predictions this tab compares. '
  + 'Each tab keeps its own choice. Outlined buttons follow the selection in the left list; '
  + "click one to pin this tab's choice, then click others to add them. The 3D view is not affected.";

/** The first sentence of `text`: up to the first `. `, `? ` or `! ` before a
 * capital letter, or all of it. */
export function firstSentence(text) {
  const m = /^[\s\S]*?[.?!](?=\s+[A-Z"(]|$)/.exec(text);
  return (m ? m[0] : text).trim();
}

/**
 * @param {string} text
 * @param {{subject?: string, open?: boolean, onToggle?: (open: boolean) => void}} [opts]
 *   `subject` names what the note is about, for the button's name
 *   ("What is Colour By?"): the page has many "?" buttons.
 * @returns {{button: HTMLButtonElement, note: HTMLParagraphElement}}
 */
export function helpToggle(text, { subject = '', open = false, onToggle = () => {} } = {}) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'help-btn';
  button.innerHTML = icon('help');
  const name = subject ? `What is ${subject}?` : 'What is this?';
  button.title = `${firstSentence(text)} Click for more.`;
  button.setAttribute('aria-label', name);
  const note = document.createElement('p');
  note.className = 'help-text';
  note.textContent = text;
  const show = (on) => {
    note.hidden = !on;
    button.setAttribute('aria-expanded', String(on));
  };
  show(open);
  button.addEventListener('click', (e) => {
    e.stopPropagation();   // a "?" in a section header must not fold the section
    show(note.hidden);
    onToggle(!note.hidden);
  });
  return { button, note };
}

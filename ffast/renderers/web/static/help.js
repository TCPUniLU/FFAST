/**
 * In-place help (ADR 0055): a "?" button that shows or hides a short note
 * saying what a section or control is for. Used by the sidebar sections and
 * the analysis tab picker.
 */

/**
 * @param {string} text
 * @param {{open?: boolean, onToggle?: (open: boolean) => void}} [opts]
 * @returns {{button: HTMLButtonElement, note: HTMLParagraphElement}}
 */
export function helpToggle(text, { open = false, onToggle = () => {} } = {}) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'help-btn';
  button.textContent = '?';
  button.title = 'What is this?';
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

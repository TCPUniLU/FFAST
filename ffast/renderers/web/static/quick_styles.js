/**
 * Quick styles (ADR 0055): one-click looks that only set existing sidebar
 * controls, firing the same events a person's own change would, so they add
 * no new path to the server. They live in the browser only and are not the
 * glossary's "Presentation preset" (named, shared, server-side settings).
 *
 * A style is a list of [section, row label, value] steps, applied in order:
 * Coloring goes before Prediction because Prediction waits for a colouring.
 */

/**
 * Colour atoms by force error with the desktop's force_error colour map.
 * Force arrows are left as they are.
 * @param {string} predictionLabel the prediction's name in the selectors
 */
export function forceErrorStyle(predictionLabel) {
  return [
    ['Colour By', 'Coloring', 'Force Error (per atom)'],
    ['Colour By', 'Colormap', 'force_error'],
    ['Colour By', 'Prediction', predictionLabel],
  ];
}

export const PUBLICATION_STYLE = [
  ['Camera', 'Background', '#ffffff'],   // the Export background follows it
  ['Camera', 'Orthographic', true],
  ['Camera', 'Axes gizmo', false],
];

/** Puts back everything the other styles change. */
export const RESET_STYLE = [
  ['Colour By', 'Coloring', 'Elements'],
  ['Colour By', 'Colormap', 'viridis'],
  ['Colour By', 'Prediction', 'Ground Truth'],
  ['Camera', 'Background', '#000000'],
  ['Camera', 'Orthographic', false],
  ['Camera', 'Axes gizmo', false],
];

/**
 * Set one row's control as a person would, firing its event only when the
 * value changes. Returns false when the row or the option does not exist.
 */
function setControl(sidebarEl, section, label, value) {
  const el = sidebarEl.querySelector(
    `.pane[data-pane="${section}"] .ctl-row[data-label="${label}"] :is(select, input)`);
  if (!el) return false;
  if (el.type === 'checkbox') {
    if (el.checked === value) return true;
    el.checked = value;
  } else {
    if (el.value === value) return true;
    if (el.tagName === 'SELECT' && ![...el.options].some((o) => o.value === value)) return false;
    el.value = value;
  }
  el.dispatchEvent(new Event(el.type === 'color' ? 'input' : 'change'));
  return true;
}

/** Apply a style's steps; returns the steps that could not be applied. */
export function applyStyle(sidebarEl, steps) {
  return steps.filter(([section, label, value]) => !setControl(sidebarEl, section, label, value));
}

// The client's drawn icons (ADR 0063): one stroke, `currentColor`, 16x16.
// Every icon is drawn here and nowhere else; no character glyphs stand in for
// a button's picture.

const PATHS = {
  pencil: '<path d="M10.5 2.5l3 3L5.5 13.5H2.5v-3z"/><path d="M9 4l3 3"/>',
  plus: '<path d="M8 3v10M3 8h10"/>',
  more: '<g fill="currentColor" stroke="none"><circle cx="3.5" cy="8" r="1.4"/><circle cx="8" cy="8" r="1.4"/><circle cx="12.5" cy="8" r="1.4"/></g>',
  play: '<path d="M5 3l8 5-8 5z"/>',
  pause: '<path d="M5.5 3v10M10.5 3v10"/>',
  'step-back': '<path d="M4 3v10"/><path d="M13 3L6 8l7 5z"/>',
  'step-forward': '<path d="M12 3v10"/><path d="M3 3l7 5-7 5z"/>',
  gear: '<circle cx="8" cy="8" r="2.6"/><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4"/>',
  close: '<path d="M3.5 3.5l9 9M12.5 3.5l-9 9"/>',
  'new-window': '<path d="M9 2.5h4.5V7M13.5 2.5l-6 6"/><path d="M12 9.5V13H3V4h3.5"/>',
  'chevron-right': '<path d="M6 3l5 5-5 5"/>',
  'chevron-down': '<path d="M3 6l5 5 5-5"/>',
  folder: '<path d="M2 4.5h4l1.5 1.5H14v6.5H2z"/>',
  file: '<path d="M4 2h5.5l3 3v9H4z"/><path d="M9.5 2v3h3"/>',
  'arrow-up': '<path d="M8 13V3M3.5 7.5L8 3l4.5 4.5"/>',
  home: '<path d="M2.5 8L8 3l5.5 5"/><path d="M4 7v6.5h8V7"/>',
  info: '<circle cx="8" cy="8" r="6"/><path d="M8 7.5v4M8 5v.01"/>',
  bond: '<circle cx="4" cy="12" r="2"/><circle cx="12" cy="4" r="2"/><path d="M5.4 10.6l5.2-5.2"/>',
  align: '<path d="M8 3l5 9H3z"/>',
  force: '<path d="M3 13L13 3"/><path d="M7 3h6v6"/>',
  scissors: '<circle cx="4" cy="4" r="2"/><circle cx="4" cy="12" r="2"/><path d="M5.5 5.5L13 12M5.5 10.5L13 4"/>',
  sliders: '<path d="M2 4h1.4M6.6 4H14M2 8h7.4M12.6 8H14M2 12h3.4M8.6 12H14"/><circle cx="5" cy="4" r="1.6"/><circle cx="11" cy="8" r="1.6"/><circle cx="7" cy="12" r="1.6"/>',
  help: '<circle cx="8" cy="8" r="6"/><path d="M6.2 6.3a1.9 1.9 0 1 1 2.6 1.8c-.5.3-.8.7-.8 1.3M8 11.6v.01"/>',
  check: '<path d="M3 8.5l3.5 3.5L13 4.5"/>',
};

/** The names `icon()` knows. */
export const ICON_NAMES = Object.keys(PATHS);

/** An inline `<svg>` string for `name`, or '' for an unknown name. */
export function icon(name) {
  const body = PATHS[name];
  if (body === undefined) return '';
  return '<svg class="icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
    + 'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" '
    + `aria-hidden="true">${body}</svg>`;
}

/** Ids of the buttons index.html declares, with their icon, label and text. */
const STATIC_BUTTONS = {
  'tab-edit-btn': ['pencil', 'Edit tab layout'],
  'tab-new-btn': ['plus', 'New tab'],
  'tab-menu-btn': ['more', 'More'],
  'add-dataset-btn': ['plus', 'Load dataset'],
  'add-prediction-btn': ['plus', 'Load prediction'],
  'prev-frame-btn': ['step-back', 'Previous frame'],
  'play-pause-btn': ['play', 'Play'],
  'next-frame-btn': ['step-forward', 'Next frame'],
  'playback-gear': ['gear', 'Playback settings'],
  'pick-clear': ['close', 'Clear'],
  'hint-dismiss': ['close', 'Dismiss'],
  'popout-btn': ['new-window', 'New window', 'New window'],
  'fb-up': ['arrow-up', 'Parent directory'],
  'fb-home': ['home', 'Home'],
  'sidebar-toggle': ['sliders', 'Settings', 'Settings'],
};

/** Draw the icon (and text, where there is one) in each static button. */
export function applyStaticIcons(root = document) {
  for (const [id, [name, label, text]] of Object.entries(STATIC_BUTTONS)) {
    const el = root.getElementById(id);
    if (!el) continue;
    el.innerHTML = icon(name) + (text ? ` ${text}` : '');
    el.setAttribute('aria-label', label);
  }
}

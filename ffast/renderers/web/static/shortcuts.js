/** The file shortcuts: the desktop's letters (UI/mainMenu.py) with
 *  Alt+Shift (Option+Shift on a Mac), so no key fights the browser.
 *  Matched by `code`, because Option on a Mac changes `key`. */
export const FILE_SHORTCUTS = [
  { action: 'save-session', code: 'KeyS' },
  { action: 'load-session', code: 'KeyL' },
  { action: 'load-dataset', code: 'KeyD' },
  { action: 'load-prediction', code: 'KeyP' },
  // Added by the loading plan (tasks 8 and 9) and the cluster plan; until
  // those are built the keys do nothing.
  { action: 'load-model', code: 'KeyM' },
  { action: 'load-zero-model', code: 'Digit0' },
  { action: 'open-popout', code: 'KeyN' },
  { action: 'connect-cluster', code: 'KeyC' },
];

/** Is this a Mac (Option and ⇧ symbols in the menu)? */
export function isMac(nav = globalThis.navigator) {
  const p = nav?.userAgentData?.platform || nav?.platform || '';
  return /mac|iphone|ipad/i.test(p);
}

/** The shortcut a key press means, or null. `e` is a KeyboardEvent (or a
 *  plain object with the same fields); `inText`: focus is in a text
 *  field, where keys belong to the typing. */
export function matchShortcut(e, { inText = false } = {}) {
  if (inText || !e.altKey || !e.shiftKey || e.ctrlKey || e.metaKey) return null;
  return FILE_SHORTCUTS.find((s) => s.code === e.code) || null;
}

/** How a menu shows the keys: "Alt+Shift+S", or "⌥⇧S" on a Mac. */
export function shortcutLabel(s, mac = false) {
  const letter = s.code.replace(/^Key|^Digit/, '');
  return mac ? `⌥⇧${letter}` : `Alt+Shift+${letter}`;
}

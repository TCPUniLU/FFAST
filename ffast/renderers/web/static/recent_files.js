/**
 * Recently loaded files, per server (the loader's "Recent" list): the last ten
 * files actually loaded — not folders browsed — kept in this browser's
 * localStorage, never the session file. A path means nothing on another
 * server, so each server address has its own list.
 */

const KEY = 'ffast.recentFiles';
export const MAX_RECENT_FILES = 10;

/** `entry` ({path, kind}) moved to the front of `list`, without duplicates, capped. */
export function rememberFile(list, entry) {
  const same = (e) => e.path === entry.path && e.kind === entry.kind;
  return [entry, ...list.filter((e) => !same(e))].slice(0, MAX_RECENT_FILES);
}

const valid = (e) => e && typeof e.path === 'string' && (e.kind === 'dataset' || e.kind === 'prediction' || e.kind === 'model');

function readAll() {
  try {
    const v = JSON.parse(window.localStorage.getItem(KEY) || '{}');
    return v && typeof v === 'object' && !Array.isArray(v) ? v : {};
  } catch {
    return {};
  }
}

/** The stored list for `server`, or [] when storage is blocked, empty or unreadable. */
export function loadRecentFiles(server) {
  const list = readAll()[server];
  return Array.isArray(list) ? list.filter(valid).slice(0, MAX_RECENT_FILES) : [];
}

export function saveRecentFiles(server, list) {
  try {
    window.localStorage.setItem(KEY, JSON.stringify({ ...readAll(), [server]: list }));
  } catch {
    // Private window or blocked site data: the list is a convenience only.
  }
}

// ── pinned folders ─────────────────────────────────────────────────────────
// Folders the user pinned in the loader's side list, per server like the
// recent files (a laptop folder means nothing on a cluster).

const PIN_KEY = 'ffast.pinnedFolders';
export const MAX_PINS = 20;

/** `list` with `path` pinned (at the end) or, when it is there, unpinned. */
export function togglePin(list, path) {
  return list.includes(path) ? list.filter((p) => p !== path) : [...list, path].slice(-MAX_PINS);
}

function readPins() {
  try {
    const v = JSON.parse(window.localStorage.getItem(PIN_KEY) || '{}');
    return v && typeof v === 'object' && !Array.isArray(v) ? v : {};
  } catch {
    return {};
  }
}

/** The folders pinned on `server`, or [] when storage is blocked or empty. */
export function loadPins(server) {
  const list = readPins()[server];
  return Array.isArray(list) ? list.filter((p) => typeof p === 'string').slice(-MAX_PINS) : [];
}

export function savePins(server, list) {
  try {
    window.localStorage.setItem(PIN_KEY, JSON.stringify({ ...readPins(), [server]: list }));
  } catch {
    // Private window or blocked site data: pins are a convenience only.
  }
}

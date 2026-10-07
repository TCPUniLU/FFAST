/**
 * Recently connected servers (ADR 0055): addresses only, newest first, five at
 * most, kept in this browser's localStorage — layout state, never the session
 * file. Tokens are never stored: a saved token would give control of the
 * session to anyone at this browser.
 */

const KEY = 'ffast.recentServers';
export const MAX_RECENT_SERVERS = 5;

/** `url` moved to the front of `list`, without duplicates, capped. */
export function rememberServer(list, url) {
  return [url, ...list.filter((u) => u !== url)].slice(0, MAX_RECENT_SERVERS);
}

/** The stored list, or [] when storage is blocked, empty or unreadable. */
export function loadRecentServers() {
  try {
    const v = JSON.parse(window.localStorage.getItem(KEY) || '[]');
    return Array.isArray(v)
      ? v.filter((u) => typeof u === 'string').slice(0, MAX_RECENT_SERVERS)
      : [];
  } catch {
    return [];
  }
}

export function saveRecentServers(list) {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    // Private window or blocked site data: the list is a convenience only.
  }
}

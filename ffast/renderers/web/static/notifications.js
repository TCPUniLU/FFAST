/**
 * The bell's list of warnings and errors: every pop-up is also kept here, so
 * one that closed by itself can still be read. Kept in this browser tab
 * (sessionStorage), so a reload does not lose it; a new tab starts empty.
 *
 * A notice is {text, level: 'warning'|'error', time (ms), count, unread}.
 * The list is newest first. The rules are pure; `remember` stores and tells
 * the bell (a `ffast-notices` event on window).
 */

export const MAX_NOTICES = 200;
const KEY = 'ffast.notifications';

/**
 * The list with `notice` added: the same text again counts on its entry and
 * moves it to the top, unread. "critical" is kept as an error.
 * @param {object[]} list @param {{text: string, level?: string, time: number}} notice
 * @returns {object[]}
 */
export function addNotice(list, { text, level = 'warning', time }) {
  const lvl = level === 'warning' ? 'warning' : 'error';
  const old = list.find((n) => n.text === text);
  const entry = old
    ? { ...old, level: old.level === 'error' ? 'error' : lvl, time, count: old.count + 1, unread: true }
    : { text, level: lvl, time, count: 1, unread: true };
  return [entry, ...list.filter((n) => n !== old)].slice(0, MAX_NOTICES);
}

/** The list with every notice read. */
export function markRead(list) {
  return list.map((n) => (n.unread ? { ...n, unread: false } : n));
}

/**
 * What the badge shows: how many notices are unread, and 'error' when one of
 * them is an error, else 'warning' (null when none).
 * @returns {{count: number, level: string|null}}
 */
export function unreadSummary(list) {
  const unread = list.filter((n) => n.unread);
  const level = !unread.length ? null : unread.some((n) => n.level === 'error') ? 'error' : 'warning';
  return { count: unread.length, level };
}

let inPage = [];   // the list when storage is blocked: this page's notices

/** The stored list; this page's own when storage is blocked or unreadable. */
export function loadNotices() {
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (raw === null) return inPage;
    const list = JSON.parse(raw);
    return Array.isArray(list) ? list : inPage;
  } catch {
    return inPage;
  }
}

/** Store `list` (best effort) and tell the bell. */
export function saveNotices(list) {
  inPage = list;
  try {
    window.sessionStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    // storage blocked: `inPage` keeps the list for this page
  }
  window.dispatchEvent(new CustomEvent('ffast-notices', { detail: list }));
}

/** Keep a warning or error the page has just popped up. */
export function remember(text, level = 'warning') {
  saveNotices(addNotice(loadNotices(), { text: String(text), level, time: Date.now() }));
}

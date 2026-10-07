/**
 * Browser-only layout state (ADR 0055 rule 3): which sidebar section is open,
 * whether the sidebar is hidden, and similar choices. It lives in this
 * browser's localStorage and never in a session file — saved sessions are
 * never migrated (ADR 0008), so anything written there would be permanent.
 *
 * Storage can be blocked (private window, cleared or blocked site data), so
 * every access is wrapped: the page then lays itself out from defaults and
 * simply does not remember.
 */

const KEY = 'ffast.layout';

/** @returns {Record<string, unknown>} the stored layout, or {} */
export function loadLayout() {
  try {
    const v = JSON.parse(window.localStorage.getItem(KEY) || '{}');
    return v && typeof v === 'object' && !Array.isArray(v) ? v : {};
  } catch {
    return {};
  }
}

/** Merge `patch` into the stored layout. */
export function saveLayout(patch) {
  try {
    window.localStorage.setItem(KEY, JSON.stringify({ ...loadLayout(), ...patch }));
  } catch {
    // Blocked storage: nothing is remembered, nothing breaks.
  }
}

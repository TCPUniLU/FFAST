/**
 * Does the server run the same FFAST code as this page?
 *
 * The page asks the process that served it for its code fingerprint
 * (`/code-fingerprint.json`, ffast/renderers/web/serve.py); the server sends
 * its own in HELLO_ACK. They differ when this page, served by the local app,
 * connects to a server on a cluster that still runs older code. The text
 * matches the desktop's (`ffast.session.code_fingerprint.server_code_notice`).
 */

/** The warning to show, or null when the code matches. A server that sends no
 *  fingerprint predates the check, so it is older. */
export function serverCodeNotice(serverFingerprint, localFingerprint) {
  if (serverFingerprint === localFingerprint) return null;
  const what = serverFingerprint
    ? 'different FFAST code than this app'
    : 'older FFAST code than this app';
  return `The server runs ${what}, so some features may fail. Restart the `
    + 'server to update it (on a cluster: disconnect and submit a new job).';
}

/** This page's fingerprint, or null when the page was not served by FFAST
 *  (then nothing is compared). */
export async function pageCodeFingerprint() {
  try {
    const r = await fetch('/code-fingerprint.json', { cache: 'no-store' });
    if (!r.ok) return null;
    return (await r.json()).code_fingerprint || null;
  } catch {
    return null;
  }
}

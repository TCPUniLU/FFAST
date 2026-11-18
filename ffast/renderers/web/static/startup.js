/* Runs before the large libraries and module graph, so startup delays are
 * visible even when the page's normal connection timing has not begun. */
(() => {
  const launch = new URLSearchParams(location.search).get('launch');
  if (!launch) return;
  const marks = { document: performance.now() };
  const page = globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2);
  let finished = false;
  let port = null;
  window.ffastStartup = (phase, socketUrl) => {
    if (finished || marks[phase] !== undefined) return;
    marks[phase] = performance.now();
    if (!['socket', 'open', 'failed'].includes(phase)) return;
    if (socketUrl) {
      try { const url = new URL(socketUrl); port = Number(url.port || (url.protocol === 'wss:' ? 443 : 80)); }
      catch { /* The connection itself reports an invalid address. */ }
    }
    const ms = (end, start) => marks[end] === undefined || marks[start] === undefined
      ? null : Math.round(marks[end] - marks[start]);
    const nav = performance.getEntriesByType('navigation')[0];
    const resources = performance.getEntriesByType('resource');
    const startup = {
      page, phase, ws_port: port,
      browser: /Firefox\//.test(navigator.userAgent) ? 'Firefox' : 'other',
      elapsed_ms: Math.round(performance.now()),
      document_ms: nav ? Math.round(nav.responseEnd) : null,
      scripts_ms: ms('modules', 'document'),
      assets_ready_ms: Math.round(Math.max(0, ...resources
        .filter(r => r.initiatorType === 'script').map(r => r.responseEnd))),
      interface_ms: ms('interface', 'modules'),
      target_ms: ms('target', 'interface'),
      socket_ms: ms(phase === 'failed' ? 'failed' : 'open', 'socket'),
      resources: resources.length,
    };
    // Diagnostics must never hold up connecting, or leak the launch secret
    // into log lines. The existing authenticated timing endpoint owns them.
    fetch('/timing', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-FFAST-Launch': launch },
      // Older launchers understand only lines; keep this readable there too.
      body: JSON.stringify({ startup, lines: [JSON.stringify({ event: 'page_startup', ...startup })] }),
    }).then(response => response.text()).catch(() => {});
    if (phase === 'open' || phase === 'failed') finished = true;
  };
})();

/**
 * The page's half of the trace (ffast-server --trace).
 *
 * While the server's trace is on, this reports to it what the user did —
 * each click, each changed field, each shortcut key — and each error in the
 * page, including the warnings and errors written to the browser console. The
 * server writes them into its trace file between the messages and handler
 * runs it records itself, so the file reads as one timeline.
 *
 * Listeners run in the capture phase, so a click is reported before the
 * button's own handler sends anything. Each line is sent at once: they are
 * few, and sending at once keeps the order right.
 */
import { OUT } from './events.js';

let _conn = null;
let _installed = false;

/** Report to ``conn`` from now on; ``null`` stops reporting. */
export function traceTo(conn) {
  _conn = conn;
  if (conn && !_installed) install();
}

function report(kind, text) {
  if (!_conn) return;
  try { _conn.send(OUT.TRACE, { kind, text: String(text).slice(0, 2000) }); }
  catch (_) { /* never let the trace break the page */ }
}

/** Report an error with the first lines of its stack. */
export function reportError(where, err) {
  const stack = err?.stack ? '\n' + String(err.stack).split('\n').slice(0, 6).join('\n') : '';
  report('error', `${where}: ${err?.message ?? err}${stack}`);
}

const CONTROL = 'button, a, summary, label, select, input, textarea, [role], [data-action], [title], li';

/** A short name for an element: its label, then where it is. */
export function describe(el) {
  if (!(el instanceof Element)) return String(el);
  const target = el.closest(CONTROL) || el;
  const label = (
    target.getAttribute('aria-label') || target.getAttribute('title') ||
    target.textContent?.trim().replace(/\s+/g, ' ').slice(0, 40) ||
    target.getAttribute('placeholder') || target.getAttribute('name') || ''
  );
  const tag = target.tagName.toLowerCase() + (target.id ? `#${target.id}` : '');
  const where = target.id ? '' : target.closest('[id]')?.id;
  return `${tag} "${label}"${where ? `  (in #${where})` : ''}`;
}

function valueOf(el) {
  if (el.type === 'password' || el.id === 'token-input') return '<hidden>';
  if (el.type === 'checkbox' || el.type === 'radio') return el.checked ? 'on' : 'off';
  if (el.tagName === 'SELECT') return JSON.stringify(el.selectedOptions[0]?.textContent?.trim() ?? el.value);
  return JSON.stringify(String(el.value ?? '').slice(0, 80));
}

function install() {
  _installed = true;
  document.addEventListener('click', (e) => report('ui', `click ${describe(e.target)}`), true);
  document.addEventListener('dblclick', (e) => report('ui', `double-click ${describe(e.target)}`), true);
  document.addEventListener('contextmenu', (e) => report('ui', `right-click ${describe(e.target)}`), true);
  document.addEventListener('change', (e) => report('ui', `change ${describe(e.target)} = ${valueOf(e.target)}`), true);
  document.addEventListener('keydown', (e) => {
    // Shortcuts, and Enter/Escape in a field — not the letters typed into it.
    if (/^(Shift|Control|Alt|Meta|CapsLock)$/.test(e.key)) return;
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
    const shortcut = e.ctrlKey || e.metaKey || e.altKey;
    if (typing && !shortcut && !/^(Enter|Escape)$/.test(e.key)) return;
    const mods = [e.ctrlKey && 'Ctrl', e.metaKey && 'Cmd', e.altKey && 'Alt', e.shiftKey && 'Shift'].filter(Boolean);
    report('ui', `key ${[...mods, e.key].join('+')}  on ${describe(e.target)}`);
  }, true);
  window.addEventListener('error', (e) => reportError(`page error at ${e.filename?.split('/').pop()}:${e.lineno}`, e.error ?? e.message));
  window.addEventListener('unhandledrejection', (e) => reportError('unhandled promise rejection', e.reason));
  for (const level of ['warn', 'error']) {
    const original = console[level].bind(console);
    console[level] = (...args) => {
      original(...args);
      const err = args.find((a) => a instanceof Error);
      const text = args.map((a) => (a instanceof Error ? a.message : typeof a === 'object' ? safeJson(a) : String(a))).join(' ');
      if (err) reportError(`console.${level}`, Object.assign(new Error(text), { stack: err.stack }));
      else report(level === 'error' ? 'error' : 'warn', `console.${level}: ${text}`);
    };
  }
}

function safeJson(v) {
  try { return JSON.stringify(v).slice(0, 300); } catch (_) { return String(v); }
}

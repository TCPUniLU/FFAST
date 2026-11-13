/**
 * The turning circle shown while work runs (a Running Task, a panel's
 * metric, a cluster connection). Styled by `.spinner` in index.html: it
 * stays invisible for its first 300 ms, so work that ends sooner never
 * flashes one.
 */

/** A spinner element; `large` for the middle of an empty panel. */
export function spinner(large = false) {
  const el = document.createElement('span');
  el.className = large ? 'spinner spinner-lg' : 'spinner';
  el.setAttribute('role', 'status');
  el.setAttribute('aria-label', 'Working');
  return el;
}

/** Is `text` a wait: "Loading x…", "Counting frames… 1,200 so far."? An
 * ellipsis at the end or before a space marks work still running. */
export function isWaitText(text) {
  return /…(\s|$)/.test(String(text ?? ''));
}

/** Show `text` in `el`, behind a spinner while it is a wait; the next text
 * replaces both. The text stays exactly `text` (code reads it back); the gap
 * after the spinner is CSS (.spinner-lead). */
export function setBusyText(el, text, busy = isWaitText(text)) {
  if (!busy) { el.textContent = text; return; }
  const turn = spinner();
  turn.classList.add('spinner-lead');
  el.replaceChildren(turn, text);
}

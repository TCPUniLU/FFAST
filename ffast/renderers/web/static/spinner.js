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

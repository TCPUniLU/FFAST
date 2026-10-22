// What makes a tab read as a journal figure (ADR 0063): every panel has a
// letter in screen reading order and, under it, a one-line caption. Pure
// functions only; analysis.js draws what they return.

const LETTERS = 'abcdefghijklmnopqrstuvwxyz';

/** The letter of the n-th panel: a…z, then aa, ab…az, ba… */
function letterAt(n) {
  if (n < 26) return LETTERS[n];
  const m = n - 26;
  return LETTERS[Math.floor(m / 26) % 26] + LETTERS[m % 26];
}

/**
 * Letters for a tab's panels, in the order a reader meets them: by row, then
 * column, then the panel's place in the tab file.
 * @param {Array<{index: number, row: number, col: number}>} cells
 * @returns {Map<number, string>} panel index → letter
 */
export function panelLetters(cells) {
  return new Map([...readingOrder(cells)].map(([index, rank]) => [index, letterAt(rank)]));
}

/**
 * Each panel's place in reading order: by row, then column, then the panel's
 * place in the tab file. A narrow grid stacks its panels in this order.
 * @param {Array<{index: number, row: number, col: number}>} cells
 * @returns {Map<number, number>} panel index → 0, 1, 2…
 */
export function readingOrder(cells) {
  const order = [...cells].sort((a, b) => (a.row - b.row) || (a.col - b.col) || (a.index - b.index));
  return new Map(order.map((cell, rank) => [cell.index, rank]));
}

/**
 * The caption of a panel (without its letter); '' when it has none, or when
 * the axis title it would quote is empty.
 * `info` is {xTitle, yTitle, nSeries, peaks}: the axis titles the builder
 * drew (unit already in them) and, for a one-series timeline, the peaks it
 * marked (frame numbers, or {frame} objects).
 */
export function caption(kind, info = {}) {
  const { xTitle = '', yTitle = '', nSeries = 1, peaks = [] } = info;
  switch (kind) {
    case 'timeline':
    case 'overlay_timeline':
      if (!yTitle) return '';
      if (nSeries > 1) return `${yTitle}, ${nSeries} series.`;
      if (kind === 'timeline' && peaks.length) {
        const frames = peaks.map((p) => (typeof p === 'object' ? p.frame : p)).sort((a, b) => a - b);
        return `${yTitle}. Top ${frames.length} peaks: frames ${frames.join(', ')}.`;
      }
      return `${yTitle}.`;
    case 'density':
    case 'grouped_density':
      if (!xTitle) return '';
      return `Distribution of ${xTitle}.`;
    case 'scatter':
      if (!xTitle || !yTitle) return '';
      return `${yTitle} against ${xTitle}.`;
    default:
      return '';
  }
}

/**
 * Split a caption into text runs, marking each "[unit]" with the axis whose
 * title it sits in, so the caller can make it clickable like the axis title.
 * @returns {Array<{text: string, axis?: 'x'|'y'}>}
 */
export function captionSegments(text, { xTitle = '', yTitle = '' } = {}, placeholder = '[unit]') {
  const span = (title) => {
    const at = title ? text.indexOf(title) : -1;
    return at < 0 ? null : [at, at + title.length];
  };
  const ranges = { x: span(xTitle), y: span(yTitle) };
  const parts = [];
  let from = 0;
  for (let at = text.indexOf(placeholder); at >= 0; at = text.indexOf(placeholder, from)) {
    const axis = ['y', 'x'].find((a) => ranges[a] && at >= ranges[a][0] && at < ranges[a][1]);
    if (at > from) parts.push({ text: text.slice(from, at) });
    parts.push(axis ? { text: placeholder, axis } : { text: placeholder });
    from = at + placeholder.length;
  }
  if (from < text.length) parts.push({ text: text.slice(from) });
  return parts;
}

// The plots' colours and sizes, read from the page's CSS custom properties
// (ADR 0063) so a plot and the page around it cannot drift apart. Journal page
// and Density work by changing those properties and drawing the plots again.

const DARK = {
  surface: '#1a1d21',
  line: '#3a4047',
  ink2: '#c3c8cf',
  ink3: '#9aa1ab',
  fsPlot: 11,
  font: '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, "Helvetica Neue", Arial, sans-serif',
  peak: '#d55e00',
  peakInk: '#ff9a55',
  median: '#2bbf93',
  series: ['#56b4e9', '#e69f00', '#2bbf93', '#f0e442', '#cc79a7', '#d55e00', '#8fc3ff', '#c3c8cf'],
};

/** The font size, in px, that `--fs-plot` resolves to (it is a calc()). */
function plotFontPx(host) {
  const probe = document.createElement('span');
  probe.style.cssText = 'position:absolute;visibility:hidden;font-size:var(--fs-plot)';
  host.appendChild(probe);
  const px = parseFloat(getComputedStyle(probe).fontSize);
  probe.remove();
  return Number.isFinite(px) && px > 0 ? px : DARK.fsPlot;
}

/** Colours, font and series list for Plotly, as a plain object. */
export function plotTheme() {
  if (typeof document === 'undefined') return { ...DARK, series: [...DARK.series] };
  // Journal page redefines the tokens on the figure area, not on the whole
  // page, so they are read where the plots are.
  const host = document.querySelector('.analysis-grid') || document.documentElement;
  const css = getComputedStyle(host);
  const get = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  const series = get('--series', '').split(',').map(c => c.trim()).filter(Boolean);
  return {
    surface: get('--surface', DARK.surface),
    line: get('--line', DARK.line),
    ink2: get('--ink-2', DARK.ink2),
    ink3: get('--ink-3', DARK.ink3),
    fsPlot: plotFontPx(host),
    font: get('--font', DARK.font),
    peak: get('--peak', DARK.peak),
    peakInk: get('--peak-ink', DARK.peakInk),
    median: get('--median', DARK.median),
    series: series.length ? series : [...DARK.series],
  };
}

/** `#rrggbb` and an alpha as an `rgba(...)` string. */
export function hexAlpha(hex, alpha) {
  const h = hex.replace('#', '');
  const n = parseInt(h.length === 3 ? h.replace(/./g, '$&$&') : h, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

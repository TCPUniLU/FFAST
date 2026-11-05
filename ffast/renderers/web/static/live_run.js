/**
 * Pure helpers for live LAMMPS runs (ADR 0087). A run's tab comes from the
 * server like any other; its score panel (a timeline whose spec names the run
 * in `live_run`) is drawn by analysis.js from LIVE_RUN_UPDATE, the message
 * that carries the run's state and whole score series.
 */

/** One line saying where the run and its scorer are. */
export function liveRunStatus(kw) {
  const parts = [`${kw.n_frames || 0} frames`];
  const p = kw.progress || {};
  // The scores drawn, not the scorer's own count, which may be ahead of them.
  const scored = kw.series?.exceedance?.length ?? p.scored_frames;
  if (scored != null) parts.push(`${scored} scored`);
  if (p.identity) parts.push(p.identity === 'proven' ? 'atom order proven' : 'atom order unverified');
  if (kw.end) parts.push(`run ${kw.end.state}`);
  else parts.push('running');
  if (p.state && !['scoring', 'done', 'ready'].includes(p.state)) parts.push(`scorer ${p.state}`);
  return parts.join(' · ');
}

/** The newest problem worth showing (a refusal, a warning, a read error), or ''. */
export function liveRunProblem(kw) {
  if (kw.error) return kw.error;
  const p = kw.progress || {};
  if (p.reason) return p.reason;
  const ev = [...(kw.events || [])].reverse().find((e) => e.kind !== 'identity');
  return ev ? (ev.reason || ev.text || '') : '';
}

/** The line drawn across the plot: every test's threshold is 1 on this scale. */
export function liveRunThreshold() {
  return 1;
}

/** The score series in the shape panels draw: x is the step, y the worst
 * test's score ÷ its threshold; `frames[i]` is the run's frame that point i
 * scores, for a click. */
export function liveRunSeries(kw) {
  const s = kw.series || { frame: [], step: [], exceedance: [] };
  const y = s.exceedance || [];
  return [{
    name: 'Score ÷ threshold', datasetFp: kw.fingerprint, modelFp: null, frames: s.frame,
    data: { x: { nd: { values: s.step, shape: [s.step.length] } },
            y: { nd: { values: y, shape: [y.length] } } },
  }];
}

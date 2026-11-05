/**
 * Pure helpers for live LAMMPS runs (ADR 0087). A run's tab comes from the
 * server like any other; its score panel (a timeline whose spec names the run
 * in `live_run`) and its `live` panels (status, events, attribution) are drawn
 * by analysis.js from LIVE_RUN_UPDATE, the message that carries the run's
 * state and whole score series.
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
  const ev = [...(kw.events || [])].reverse()
    .find((e) => ['warning', 'refused', 'scorer_failed'].includes(e.kind));
  return ev ? (ev.reason || ev.text || '') : '';
}

const fmt = (n) => (n == null ? '?' : Number(n).toLocaleString('en-US'));

/** A probability as a short percentage: 0.018 → "1.8 %", 3e-7 → "< 0.001 %". */
export function percent(p) {
  if (!Number.isFinite(p)) return '?';
  const v = p * 100;
  if (v > 0 && v < 0.001) return '< 0.001 %';
  return `${v >= 10 ? v.toFixed(0) : v >= 1 ? v.toFixed(1) : Number(v.toPrecision(2))} %`;
}

/** Seconds ago, short: 3 → "3 s", 240 → "4 min". */
function ago(s) {
  return s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`;
}

const STALLED_S = 30;   // 3 × the scorer's 10 s heartbeat (runs_list.py)

/**
 * The status strip's chips: [{text, tone}], tone '' | 'ok' | 'warn' | 'bad'.
 * Producer progress, scored-through progress and the scorer's heartbeat are
 * separate, so a stalled scorer is not read as a stalled simulation.
 * @param {object} kw  the run's LIVE_RUN_UPDATE
 * @param {number} nowMs  Date.now()
 */
export function liveRunChips(kw, nowMs) {
  const p = kw.progress || {};
  const chips = [];
  const last = kw.producer?.last_step;
  const end = p.planned_end_step;
  chips.push({ text: `LAMMPS step ${fmt(last)}${end != null ? ` / ${fmt(end)}` : ''}`, tone: '' });
  if (p.scored_through_step != null) chips.push({ text: `scored through ${fmt(p.scored_through_step)}`, tone: '' });
  if (p.heartbeat_utc && !kw.end && !['done', 'refused', 'failed'].includes(p.state)) {
    const age = Math.max(0, (nowMs - Date.parse(p.heartbeat_utc)) / 1000);
    chips.push(age > STALLED_S
      ? { text: `scorer heartbeat ${ago(age)} ago — stalled?`, tone: 'warn' }
      : { text: `scorer heartbeat ${ago(age)} ago`, tone: 'ok' });
  } else if (p.state && !['scoring', 'done', 'ready'].includes(p.state)) {
    chips.push({ text: `scorer ${p.state}`, tone: p.state === 'starting' ? '' : 'bad' });
  }
  if (p.identity) {
    chips.push(p.identity === 'proven' ? { text: 'atom order proven', tone: '' }
      : { text: 'atom order unverified', tone: 'warn' });
  }
  for (const a of p.alarms || []) {
    chips.push({ text: `${a.kind === 'chem' ? 'CHEMISTRY ALARM' : 'ALARM'} at step ${fmt(a.confirmation?.step)}`, tone: 'bad' });
  }
  if (kw.end) {
    const st = kw.end.state;
    const at = kw.end.stop_observed_step;
    const text = st === 'stopped' ? `stopped at step ${fmt(at)}`
      : st === 'terminated' ? `terminated; last saved frame at step ${fmt(at)}` : `run ${st}`;
    chips.push({ text, tone: st === 'finished' ? 'ok' : st === 'stopped' || st === 'terminated' ? 'bad' : 'warn' });
  } else if (kw.stop_requested) {
    chips.push({ text: 'stop requested', tone: 'bad' });
  }
  return chips;
}

/** The alarm rule and its estimated false-alarm chance, in words; '' before the rule is known. */
export function liveRunRuleText(kw) {
  const p = kw.progress || {};
  const r = p.rule;
  if (!r) return '';
  const every = r.frame_ps * r.step_frames;
  let text = `Alarm rule: ${Math.round(r.fraction_threshold * 100)} % of frames marked within `
    + `${Number(r.persist_ps.toPrecision(3))} ps (${fmt(r.window_frames)} frames), checked every `
    + `${Number(every.toPrecision(3))} ps.`;
  const fa = p.false_alarm;
  if (fa) {
    const over = fa.over === 'run' && p.planned_end_step != null && p.first_step != null
      ? `the planned ${fmt(p.planned_end_step - p.first_step)} steps` : 'one window (planned length unknown)';
    const sign = fa.at_least ? '≥' : '≤';
    text += ` False-alarm chance over ${over}: ${sign} ${percent(fa.probability)} (estimated`
      + (fa.at_least ? `; frames taken as uncorrelated until ${fmt(200)} are scored)` : `)`) + '.';
  }
  return text;
}

/**
 * Marks, chemistry flags, alarm bands and the three moments, for the score
 * plot (panels.js addOverlays). x is the step.
 */
export function liveRunOverlays(kw) {
  const s = kw.series || {};
  const steps = s.step || [];
  const pick = (flags) => steps.filter((_, i) => flags?.[i]);
  const overlays = {
    ticks: [{ xs: pick(s.marked), color: 'warn', row: 0, name: 'marked' },
            { xs: pick(s.chem), color: '#cc79a7', row: 1, name: 'chemistry flag' }],
    bands: [], lines: [],
  };
  for (const a of kw.progress?.alarms || []) {
    const chem = a.kind === 'chem';
    const x0 = a.onset?.step, x1 = a.confirmation?.step;
    if (x0 == null || x1 == null) continue;
    overlays.bands.push({ x0, x1, color: 'danger' });
    overlays.lines.push({ x: x0, label: chem ? 'chemistry onset' : 'estimated onset', color: 'danger', dash: 'dot' });
    overlays.lines.push({ x: x1, label: chem ? 'chemistry confirmed' : 'confirmed', color: 'danger' });
  }
  const at = kw.stop?.observed_step ?? kw.end?.stop_observed_step;
  if (at != null) overlays.lines.push({ x: at, label: 'stop observed', color: 'ink', width: 1.6 });
  return overlays;
}

/** One event as a line of the events list: {time, text, tone}. */
export function liveEventLine(e) {
  const t = (e.t || '').slice(11, 19);
  const step = (v) => `step ${fmt(v)}`;
  switch (e.kind) {
    case 'start': return { time: t, text: 'Start · segment 0', tone: '' };
    case 'identity': return { time: t, text: `First frame (${step(e.step)}): atom order ${e.identity}`, tone: e.identity === 'proven' ? '' : 'warn' };
    case 'planned': return { time: t, text: `Planned length: to ${step(e.end_step)} (${fmt(e.frames)} saved frames)`, tone: '' };
    case 'alarm': return { time: t, tone: 'bad',
      text: `${e.alarm === 'chem' ? 'Chemistry alarm' : 'Alarm'} · estimated onset ${step(e.onset?.step)} · `
        + `confirmed at ${step(e.confirmation?.step)}${e.test ? ` · ${e.test.join(' ')}` : ''}`,
      report: e.report };
    case 'stop_policy': return { time: t, text: `Stop on alarm turned ${e.stop_on_alarm ? 'on' : 'off'}${e.by ? ` (${e.by})` : ''}`, tone: '' };
    case 'stop_requested': return { time: t, text: `Stop requested (${e.by === 'alarm' ? 'stop on alarm' : 'Stop now'})${e.reason ? `: ${e.reason}` : ''}`, tone: 'bad' };
    case 'stop_sent': return { time: t, text: e.method === 'halt' ? 'STOP written for fix halt' : `Terminating LAMMPS: ${e.text || ''}`, tone: 'bad' };
    case 'stop_observed': return { time: t, tone: 'bad',
      text: e.how === 'halt' ? `Stop observed at ${step(e.step)} (fix halt line in log.lammps)`
        : `Terminated; last saved frame at ${step(e.step)}` };
    case 'lammps_exit': return { time: t, text: `LAMMPS exited (code ${e.exit_code})`, tone: '' };
    case 'end': return { time: t, text: `Run ${e.state}`, tone: e.state === 'finished' ? '' : 'warn' };
    case 'refused': return { time: t, text: `Scorer refused: ${e.reason}`, tone: 'bad' };
    case 'scorer_failed': return { time: t, text: `Scorer failed: ${e.reason}`, tone: 'bad' };
    default: return { time: t, text: e.text || e.reason || e.kind, tone: e.kind === 'warning' ? 'warn' : '' };
  }
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

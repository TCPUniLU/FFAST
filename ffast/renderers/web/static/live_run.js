import { icon } from './icons.js';

/**
 * Pure helpers for live LAMMPS runs (ADR 0087). A run's tab comes from the
 * server like any other (ADR 0089); its `live` panels (status, events,
 * attribution, scores) draw the runs ticked in the tab's selector, from
 * LIVE_RUN_UPDATE, the message that carries a run's state and whole score
 * series.
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

/** The newest problem that stops scores (a read error, a refusal, a failed
 * scorer), or ''. Warnings, such as "the input writes no restart file", are
 * in the Events list only: there is nothing to do about them here. */
export function liveRunProblem(kw) {
  if (kw.error) return kw.error;
  const p = kw.progress || {};
  if (p.reason) return p.reason;
  const ev = [...(kw.events || [])].reverse()
    .find((e) => ['refused', 'scorer_failed'].includes(e.kind));
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
 * The scorer's cell, the same in the Run panel and the Live Runs list: plain
 * while it works, coloured when it is silent, refused or failed.
 * @param {{state?: string, ended: boolean, ageS: number|null, stalled: boolean}} x
 *   `ageS` is seconds since its last heartbeat, null before the first.
 * @returns {{text: string, tone: string, title?: string}}
 */
export function liveScorerCell({ state, ended, ageS, stalled }) {
  if (!ended && ageS != null && !['done', 'refused', 'failed'].includes(state)) {
    return stalled ? { text: `silent ${ago(ageS)} — stalled?`, tone: 'warn' }
      : { text: 'running', tone: '', title: `Last heartbeat ${ago(ageS)} ago` };
  }
  const words = { scoring: 'running', ready: 'ready', done: 'done', starting: 'starting' };
  if (state) return { text: words[state] || state, tone: words[state] ? '' : 'bad' };
  return { text: 'not started', tone: '' };
}

/**
 * The State cell, the same in the Run panel and the Live Runs list: the alarms,
 * then how the run ended or that a stop is asked for. An alarm or a terminated
 * run is trouble (bad); a stop the user asked for is not.
 * @param {{alarms?: {kind: string, step: number}[], end?: {state: string, step: number}|null,
 *   stopRequested?: boolean, paused?: boolean}} x
 * @returns {{text: string, tone: string}}
 */
export function liveStateCell({ alarms = [], end = null, stopRequested = false, paused = false }) {
  const words = alarms.map((a) => `${a.kind === 'chem' ? 'Chemistry alarm' : 'Alarm'} at step ${fmt(a.step)}`);
  let tone = words.length ? 'bad' : '';
  if (end) {
    const { state: st, step: at } = end;
    words.push(st === 'stopped' ? `stopped at step ${fmt(at)}`
      : st === 'terminated' ? `terminated; last saved frame at step ${fmt(at)}` : st);
    if (st === 'terminated') tone = 'bad';
    else if (!['finished', 'stopped'].includes(st)) tone ||= 'warn';
  } else if (stopRequested) {
    words.push('stop requested');
    tone ||= 'warn';
  }
  if (!end && paused && !stopRequested) words.push('Paused before the heat');
  const text = words.length ? words.join(' · ') : 'No alarm';
  return { text: text[0].toUpperCase() + text.slice(1), tone };
}

/** Fill a table cell with a cell of the cells above: trouble gets its colour and
 * the warning icon, never colour alone. */
export function tonedText(td, { text, tone, title }) {
  if (tone) {
    td.classList.add(tone);
    td.innerHTML = icon('warning');
  }
  td.append(text);
  if (title) td.title = title;
  return td;
}

/**
 * One run's row of the Run panel's table: each cell {text, tone, title?},
 * tone '' | 'warn' | 'bad'. Colour is kept for trouble (an alarm, a crash, a
 * stalled scorer, an unproven atom order); a run that is fine reads plain.
 * Producer progress, scored-through progress and the scorer's heartbeat are
 * separate cells, so a stalled scorer is not read as a stalled simulation.
 * `lammps.fraction` is how far the run is (0–1), or null when the planned end
 * is unknown.
 * @param {object} kw  the run's LIVE_RUN_UPDATE
 * @param {number} nowMs  Date.now()
 */
export function liveRunRow(kw, nowMs) {
  const p = kw.progress || {};
  const last = kw.producer?.last_step;
  const end = p.planned_end_step;
  const first = p.first_step ?? 0;
  const lammps = {
    text: last == null ? 'no frame yet' : `${fmt(last)}${end != null ? ` / ${fmt(end)}` : ''}`,
    tone: '',
    fraction: last != null && end != null && end > first
      ? Math.min(1, Math.max(0, (last - first) / (end - first))) : null,
  };
  const scored = { text: p.scored_through_step != null ? fmt(p.scored_through_step) : '—', tone: '' };

  const ageS = p.heartbeat_utc ? Math.max(0, (nowMs - Date.parse(p.heartbeat_utc)) / 1000) : null;
  const scorer = liveScorerCell({ state: p.state, ended: !!kw.end, ageS, stalled: ageS > STALLED_S });

  const atoms = !p.identity ? { text: '—', tone: '' }
    : p.identity === 'proven' ? { text: 'proven', tone: '' } : { text: 'unverified', tone: 'warn' };

  const state = liveStateCell({
    alarms: (p.alarms || []).map((x) => ({ kind: x.kind, step: x.confirmation?.step })),
    end: kw.end ? { state: kw.end.state, step: kw.end.stop_observed_step } : null,
    stopRequested: !!kw.stop_requested, paused: !!kw.paused,
  });

  return { name: kw.name || kw.tab || kw.run_id, lammps, scored, scorer, atoms, state };
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

// Chemistry flags and the chemistry alarm: violet, far from the OOD alarm's
// red, the score's blue and the marks' orange on both themes.
const CHEM_COLOUR = '#9b6dff';

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
            { xs: pick(s.chem), color: CHEM_COLOUR, row: 1, name: 'chemistry flag' }],
    bands: [], lines: [],
  };
  // Every moment has a line style of its own (colour: which alarm; dotted:
  // onset, solid: confirmed), so the legend names each line however close
  // the lines are.
  for (const a of kw.progress?.alarms || []) {
    const chem = a.kind === 'chem';
    const x0 = a.onset?.step, x1 = a.confirmation?.step;
    if (x0 == null || x1 == null) continue;
    const color = chem ? CHEM_COLOUR : 'danger';
    overlays.bands.push({ x0, x1, color });
    overlays.lines.push({ x: x0, label: chem ? 'chemistry onset' : 'estimated onset', color, dash: 'dot' });
    overlays.lines.push({ x: x1, label: chem ? 'chemistry confirmed' : 'confirmed', color, dash: 'solid' });
  }
  const at = kw.stop?.observed_step ?? kw.end?.stop_observed_step;
  if (at != null) overlays.lines.push({ x: at, label: 'stop observed', color: 'ink', dash: 'solid', width: 2 });
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

// Attribution strength ÷ threshold on a fixed scale (ADR 0087): 0 grey, 1 the
// peak orange, 2 and above red. The same colour means the same on every frame.
const ATT_STOPS = [[0, [0x4a, 0x50, 0x59]], [1, [0xd5, 0x5e, 0x00]], [2, [0xff, 0x3d, 0x00]]];
export const ATTRIBUTION_MAX = 2;

/** RGB (0–1) of an attribution strength ÷ threshold, clamped to 0–2. */
export function attributionRgb(v) {
  const x = Math.min(ATTRIBUTION_MAX, Math.max(0, Number.isFinite(v) ? v : 0));
  const k = x >= 1 ? 1 : 0;
  const [x0, c0] = ATT_STOPS[k], [x1, c1] = ATT_STOPS[k + 1];
  const t = (x - x0) / (x1 - x0);
  return c0.map((a, i) => (a + (c1[i] - a) * t) / 255);
}

/**
 * The renderer's colours for one attribution reply: every atom by its
 * strength; a bond by its own group's strength when it is one (bonds track),
 * otherwise by the weaker of its two atoms, so only bonds inside a strong
 * group light up.
 * @returns {{atoms: number[][], bond: (i: number, j: number) => number[]}}
 */
export function attributionColours(result) {
  const atom = result.atom_strength || [];
  const own = result.bond_strength || {};
  return {
    atoms: atom.map(attributionRgb),
    bond: (i, j) => {
      const key = i < j ? `${i}-${j}` : `${j}-${i}`;
      const v = key in own ? own[key] : Math.min(atom[i] ?? 0, atom[j] ?? 0);
      return attributionRgb(v);
    },
  };
}

/** Which of a tab's `count` 3D panels (in layout order) an attribution card
 * explains: its `explains` option, else the first. */
export function explainsIndex(spec, count) {
  const i = spec?.options?.explains;
  return Number.isInteger(i) && i >= 0 && i < count ? i : 0;
}

/** Whether two LIVE_RUN_UPDATEs say the same: the server sends a run again
 * every 10 s for windows that connect later, mostly unchanged. Compares
 * plain decoded values (objects, arrays, numbers, strings, null). */
export function sameRunUpdate(a, b) {
  if (Object.is(a, b)) return true;
  if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a)) {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (!sameRunUpdate(a[i], b[i])) return false;
    return true;
  }
  const keys = Object.keys(a);
  if (keys.length !== Object.keys(b).length) return false;
  return keys.every((k) => Object.prototype.hasOwnProperty.call(b, k) && sameRunUpdate(a[k], b[k]));
}

// ── several runs in one tab (ADR 0089) ───────────────────────────────────────
// A tab draws every run ticked in its selector. These take `runs`, a list of
// {name, kw}: the run's name in plots and its last LIVE_RUN_UPDATE.

/** Marks, bands and lines of every run, for one score plot. Each run's marks
 * keep rows of their own (the first run lowest): pooled, one run's marks read
 * as another's. With several runs each mark row takes its run's curve colour
 * (`series`: the run's position in `runs`, which is the plot's series order),
 * and each mark row's and line's label starts with its run's name, so the
 * legend says whose it is. */
export function liveRunsOverlays(runs) {
  const merged = { ticks: [], bands: [], lines: [] };
  const many = runs.length > 1;
  runs.forEach(({ name, kw }, i) => {
    const o = liveRunOverlays(kw);
    const label = (text) => (many ? `${name}: ${text}` : text);
    merged.ticks.push(...o.ticks.map((t) => ({ ...t, curve: i,
      row: i * o.ticks.length + (t.row || 0), name: label(t.name), ...(many ? { series: i } : {}) })));
    // `curve`: hidden with its run's curve in the legend.
    merged.bands.push(...o.bands.map((b) => ({ ...b, curve: i })));
    merged.lines.push(...o.lines.map((l) => ({ ...l, curve: i, label: label(l.label) })));
  });
  return merged;
}

/** The events of every run in one list, by time (oldest first, like one run's
 * list): [{run, event}]. */
export function liveRunsEvents(runs) {
  const all = runs.flatMap(({ name, kw }) => (kw.events || []).map((event) => ({ run: name, event })));
  return all.map((x, i) => [x, i]).sort(([a, i], [b, j]) =>
    String(a.event.t || '').localeCompare(String(b.event.t || '')) || i - j).map(([x]) => x);
}

/** What a plain dataset ticked in a live panel says instead of a score. */
export function liveNoScoreMessage(name) {
  return `No score for ${name}: only live runs have scores for now.`;
}

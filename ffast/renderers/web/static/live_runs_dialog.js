/**
 * The "Live runs" dialog (ADR 0087): every run the wrapper started on the
 * server's cluster account, newest first. LAMMPS progress, scored-through
 * progress and the scorer's heartbeat are shown apart, so a stalled scorer is
 * not read as a stalled simulation. Refreshed every few seconds while open.
 */
import { labelBy, trapFocus } from './dialogs.js';

const fmt = (n) => (n == null ? '?' : Number(n).toLocaleString('en-US'));
export const REFRESH_MS = 5000;

function ago(s) {
  return s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`;
}

/** Share of the planned steps a step reaches, 0–1, or null when unknown. */
function share(step, row) {
  const first = row.first_step ?? 0;
  const end = row.planned_end_step;
  if (step == null || end == null || end <= first) return null;
  return Math.max(0, Math.min(1, (step - first) / (end - first)));
}

/**
 * What one row shows (pure, for tests).
 * @returns {{name: string, sub: string, lammps: string, lammpsShare: number|null,
 *   scored: string, scoredShare: number|null, scorer: {text: string, tone: string},
 *   alarm: {text: string, tone: string}, state: {text: string, tone: string}, canOpen: boolean}}
 */
export function liveRunRow(row) {
  if (!row.exists) {
    return { name: row.name, sub: row.run_dir, lammps: '', lammpsShare: null, scored: '', scoredShare: null,
      scorer: { text: '', tone: '' }, alarm: { text: '', tone: '' },
      state: { text: 'folder not found', tone: 'warn' }, canOpen: false };
  }
  const end = row.planned_end_step;
  const lammps = `LAMMPS ${fmt(row.last_step)}${end != null ? ` / ${fmt(end)}` : ''}`;
  const scored = row.scored_through_step == null ? 'nothing scored yet' : `scored ${fmt(row.scored_through_step)}`;
  let scorer;
  if (row.end || ['done', 'refused', 'failed'].includes(row.scorer_state)) {
    scorer = { text: row.scorer_state || 'not started', tone: ['refused', 'failed'].includes(row.scorer_state) ? 'bad' : '' };
  } else if (row.heartbeat_age_s == null) {
    scorer = { text: 'not started yet', tone: '' };
  } else {
    scorer = row.stalled ? { text: `${ago(row.heartbeat_age_s)} ago — stalled?`, tone: 'warn' }
      : { text: `${ago(row.heartbeat_age_s)} ago`, tone: 'ok' };
  }
  const alarms = row.alarms || [];
  const alarm = alarms.length
    ? { text: alarms.map((a) => `${a.kind === 'chem' ? 'chemistry ' : ''}step ${fmt(a.step)}`).join(', '), tone: 'bad' }
    : { text: 'none', tone: '' };
  const st = row.state || 'running';
  const at = row.end?.stop_observed_step;
  const state = st === 'stopped' ? { text: `stopped at step ${fmt(at)}`, tone: 'bad' }
    : st === 'terminated' ? { text: `terminated at step ${fmt(at)}`, tone: 'bad' }
    : st === 'finished' ? { text: 'finished', tone: '' }
    : st === 'running' ? { text: 'running', tone: 'ok' } : { text: st, tone: 'warn' };
  return { name: row.name, sub: [row.bundle, row.run_dir].filter(Boolean).join(' · '),
    lammps, lammpsShare: share(row.last_step, row), scored, scoredShare: share(row.scored_through_step, row),
    scorer, alarm, state, canOpen: true };
}

/**
 * Open the dialog. `ask()` asks the server for the rows (they arrive through
 * `update`), `open(path)` follows a run, `openByPath()` asks for a folder,
 * `forget(runId)` drops a gone run from the list.
 * @returns {{update: (rows: object[]) => void, close: () => void}}
 */
export function openLiveRunsDialog({ ask, open, openByPath, forget }) {
  document.getElementById('live-runs-dialog')?.remove();
  const modal = document.createElement('div');
  modal.id = 'live-runs-dialog';
  modal.className = 'warning-modal';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  const panel = document.createElement('div');
  panel.className = 'warning-modal-panel live-runs-panel';
  const head = document.createElement('div');
  head.className = 'warning-modal-title';
  head.textContent = 'Live runs';
  labelBy(modal, head);
  const body = document.createElement('div');
  body.className = 'live-runs-body';
  body.textContent = 'Asking the server…';
  const foot = document.createElement('div');
  foot.className = 'warning-modal-footer';
  const byPath = document.createElement('button');
  byPath.textContent = 'Open run folder by path…';
  const closeBtn = document.createElement('button');
  closeBtn.textContent = 'Close';
  foot.append(byPath, closeBtn);
  panel.append(head, body, foot);
  modal.appendChild(panel);

  let untrap = () => {};
  let timer = null;
  const close = () => { clearInterval(timer); modal.remove(); untrap(); };
  closeBtn.addEventListener('click', close);
  byPath.addEventListener('click', () => { close(); openByPath(); });
  modal.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  untrap = trapFocus(modal);
  document.body.appendChild(modal);
  closeBtn.focus();
  ask();
  timer = setInterval(() => { if (modal.isConnected) ask(); else clearInterval(timer); }, REFRESH_MS);

  const cell = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  const bar = (part, cls) => {
    const b = cell('div', 'live-runs-bar');
    const i = cell('i', cls);
    i.style.width = part == null ? '0' : `${Math.round(part * 100)}%`;
    b.appendChild(i);
    return b;
  };
  const chip = ({ text, tone }) => (text ? cell('span', `live-chip${tone ? ` ${tone}` : ''}`, text) : cell('span'));

  function update(rows) {
    if (!modal.isConnected) return;
    if (!rows.length) {
      body.textContent = 'No live runs yet. Runs started with ffast-lammps on this account appear here.';
      return;
    }
    const table = cell('table', 'live-runs-table');
    const hr = cell('tr');
    for (const h of ['Run', 'Progress', 'Scorer', 'Alarm', 'State', '']) hr.appendChild(cell('th', '', h));
    table.appendChild(hr);
    for (const row of rows) {
      const v = liveRunRow(row);
      const tr = cell('tr');
      tr.dataset.runId = row.run_id;
      const name = cell('td');
      name.append(cell('b', '', v.name), cell('div', 'live-runs-sub', v.sub));
      const prog = cell('td');
      if (row.exists) {
        prog.append(cell('div', 'live-runs-sub', v.lammps), bar(v.lammpsShare, 'lammps'),
          cell('div', 'live-runs-sub', v.scored), bar(v.scoredShare, row.stalled ? 'stalled' : 'scored'));
      }
      const scorer = cell('td'); scorer.appendChild(chip(v.scorer));
      const alarm = cell('td'); alarm.appendChild(chip(v.alarm));
      const state = cell('td', `live-runs-state ${v.state.tone}`, v.state.text);
      const act = cell('td');
      const btn = cell('button', v.canOpen ? 'live-runs-open' : 'live-runs-forget', v.canOpen ? 'Open' : 'Forget');
      btn.addEventListener('click', () => {
        if (v.canOpen) { close(); open(row.run_dir); } else forget(row.run_id);
      });
      act.appendChild(btn);
      tr.append(name, prog, scorer, alarm, state, act);
      table.appendChild(tr);
    }
    body.replaceChildren(table);
  }
  return { update, close };
}

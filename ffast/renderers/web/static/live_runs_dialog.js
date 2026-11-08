/**
 * The "Live runs" dialog (ADR 0087): every run the wrapper started on the
 * server's cluster account, newest first. LAMMPS progress, scored-through
 * progress and the scorer's heartbeat are shown apart, so a stalled scorer is
 * not read as a stalled simulation. Refreshed every few seconds while open.
 */
import { labelBy, trapFocus } from './dialogs.js';
import { liveScorerCell, liveStateCell, tonedText } from './live_run.js';

const fmt = (n) => (n == null ? '?' : Number(n).toLocaleString('en-US'));
export const REFRESH_MS = 5000;

/** Share of the planned steps a step reaches, 0–1, or null when unknown. */
function share(step, row) {
  const first = row.first_step ?? 0;
  const end = row.planned_end_step;
  if (step == null || end == null || end <= first) return null;
  return Math.max(0, Math.min(1, (step - first) / (end - first)));
}

/**
 * What one row shows (pure, for tests).
 * Scorer and State say it as the Run panel does (live_run.js): plain text,
 * colour only for trouble, the alarms part of State.
 * @returns {{name: string, sub: string, lammps: string, lammpsShare: number|null,
 *   scored: string, scoredShare: number|null, scorer: {text: string, tone: string, title?: string},
 *   state: {text: string, tone: string}, canOpen: boolean}}
 */
export function liveRunRow(row) {
  // Where the run lives is detail, behind "More info"; the row names the reference.
  const more = [['Folder', row.run_dir], ['Run ID', row.run_id], ['Slurm job', row.slurm_job_id]]
    .filter(([, v]) => v);
  if (!row.exists) {
    return { name: row.name, sub: '', more, lammps: '', lammpsShare: null, scored: '', scoredShare: null,
      scorer: { text: '', tone: '' }, state: { text: 'Folder not found', tone: 'warn' },
      canOpen: false, canStop: false };
  }
  const end = row.planned_end_step;
  const lammps = `LAMMPS ${fmt(row.last_step)}${end != null ? ` / ${fmt(end)}` : ''}`;
  const scored = row.scored_through_step == null ? 'nothing scored yet' : `scored ${fmt(row.scored_through_step)}`;
  const scorer = liveScorerCell({ state: row.scorer_state, ended: !!row.end,
    ageS: row.heartbeat_age_s, stalled: !!row.stalled });
  // The run's end, from its record; a state with no record (a failure) still counts.
  const ended = row.end ? { state: row.end.state || row.state, step: row.end.stop_observed_step }
    : row.state && row.state !== 'running' ? { state: row.state, step: null } : null;
  const state = liveStateCell({
    alarms: (row.alarms || []).map((x) => ({ kind: x.kind, step: x.step })),
    end: ended, stopRequested: !!(row.stop_requested || row.stop),
  });
  return { name: row.name, sub: row.bundle || '', more,
    lammps, lammpsShare: share(row.last_step, row), scored, scoredShare: share(row.scored_through_step, row),
    scorer, state, canOpen: true,
    // Stopping is for a run this server follows that has not ended (ADR 0089).
    canStop: !!row.open && !row.end };
}

/**
 * Open the dialog. `ask()` asks the server for the rows (they arrive through
 * `update`), `open(path)` follows a run, `openByPath()` picks any other run
 * folder ("Open run folder…": the only way to one not in the list),
 * `forget(runId)` drops a gone run from the list, `stop(row)` asks to stop a
 * running run, `highlight` is a run ID whose row is marked and scrolled to.
 * @returns {{update: (rows: object[]) => void, close: () => void}}
 */
export function openLiveRunsDialog({ ask, open, openByPath, forget, stop = () => {}, highlight = null }) {
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
  byPath.textContent = 'Open run folder…';
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

  let scrolled = false;
  const openMore = new Set();   // rows whose "More info" is open, kept across refreshes
  function update(rows) {
    if (!modal.isConnected) return;
    if (!rows.length) {
      body.textContent = 'No live runs yet. Runs started with ffast-lammps on this account appear here.';
      return;
    }
    const table = cell('table', 'live-runs-table');
    const hr = cell('tr');
    for (const h of ['Run', 'Progress', 'Scorer', 'State', '']) hr.appendChild(cell('th', '', h));
    table.appendChild(hr);
    for (const row of rows) {
      const v = liveRunRow(row);
      const tr = cell('tr');
      tr.dataset.runId = row.run_id;
      if (row.run_id === highlight) tr.className = 'live-runs-highlight';
      const name = cell('td');
      name.append(cell('b', '', v.name));
      if (v.sub) name.appendChild(cell('div', 'live-runs-sub', v.sub));
      const more = cell('details', 'live-runs-more');
      more.open = openMore.has(row.run_id);   // the list refreshes every few seconds
      more.addEventListener('toggle', () => (more.open ? openMore.add(row.run_id) : openMore.delete(row.run_id)));
      const dl = cell('dl');
      for (const [k, val] of v.more) dl.append(cell('dt', '', k), cell('dd', '', val));
      more.append(cell('summary', '', 'More info'), dl);
      name.appendChild(more);
      const prog = cell('td');
      if (row.exists) {
        prog.append(cell('div', 'live-runs-sub', v.lammps), bar(v.lammpsShare, 'lammps'),
          cell('div', 'live-runs-sub', v.scored), bar(v.scoredShare, row.stalled ? 'stalled' : 'scored'));
      }
      const scorer = tonedText(cell('td'), v.scorer);
      const state = tonedText(cell('td', 'live-runs-state'), v.state);
      const act = cell('td');
      const btn = cell('button', v.canOpen ? 'live-runs-open' : 'live-runs-forget', v.canOpen ? 'Open' : 'Forget');
      btn.addEventListener('click', () => {
        if (v.canOpen) { close(); open(row.run_dir); } else forget(row.run_id);
      });
      act.appendChild(btn);
      if (v.canStop) {
        const stopBtn = cell('button', 'danger live-runs-stop', row.stop_requested || row.stop ? 'Stop requested' : 'Stop now…');
        stopBtn.disabled = !!(row.stop_requested || row.stop);
        stopBtn.addEventListener('click', () => stop(row));
        act.appendChild(stopBtn);
      }
      tr.append(name, prog, scorer, state, act);
      table.appendChild(tr);
    }
    body.replaceChildren(table);
    if (highlight && !scrolled) {
      const hit = table.querySelector('tr.live-runs-highlight');
      if (hit) { hit.scrollIntoView?.({ block: 'nearest' }); scrolled = true; }
    }
  }
  return { update, close };
}

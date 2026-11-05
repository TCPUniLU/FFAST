/**
 * The live-run view (ADR 0087): one small floating panel per opened LAMMPS run
 * folder, with the run's state and its score against simulation step. The
 * frames themselves are a dataset (`live:<run id>`) shown in the main 3D view
 * like any other; clicking a point of the plot shows that frame there.
 *
 * The server sends LIVE_RUN_UPDATE with the whole score series on each change;
 * this module only draws it.
 */

import { renderPanel, clearPlot } from './panels.js';

const STYLE = `
.live-run { position: fixed; right: 16px; bottom: 16px; width: min(480px, calc(100vw - 32px));
  background: var(--surface); color: var(--text); border: 1px solid var(--line);
  border-radius: 6px; box-shadow: 0 4px 16px rgba(0,0,0,0.18); z-index: 40;
  display: flex; flex-direction: column; font-size: var(--fs-s, 12px); }
.live-run + .live-run { bottom: 340px; }
.live-run-head { display: flex; align-items: center; gap: 8px; padding: 6px 8px;
  border-bottom: 1px solid var(--line); }
.live-run-title { flex: 1; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.live-run-close { border: 0; background: none; color: inherit; cursor: pointer; font-size: 16px; line-height: 1; }
.live-run-status { padding: 4px 8px; color: var(--muted); }
.live-run-status .live-run-problem { color: var(--danger); }
.live-run-plot { height: 240px; min-height: 180px; }
.live-run-empty { padding: 24px 8px; color: var(--muted); text-align: center; }
`;

function ensureStyle() {
  if (document.getElementById('live-run-style')) return;
  const s = document.createElement('style');
  s.id = 'live-run-style';
  s.textContent = STYLE;
  document.head.appendChild(s);
}

/** One line saying where the run and its scorer are; pure, for tests. */
export function liveRunStatus(kw) {
  const parts = [`${kw.n_frames || 0} frames`];
  const p = kw.progress || {};
  if (p.scored_frames != null) parts.push(`${p.scored_frames} scored`);
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

/** Panel spec and series for the score plot; pure, for tests. */
export function liveRunPlot(kw) {
  const s = kw.series || { step: [], t2: [] };
  const threshold = kw.thresholds ? (kw.thresholds.centroid ?? Object.values(kw.thresholds)[0]) : null;
  const spec = { kind: 'timeline', x_label: 'Step', y_label: 'T² (geometric)',
    y_line: Number.isFinite(threshold) ? threshold : undefined };
  const series = [{ name: 'T²', datasetFp: kw.fingerprint, modelFp: null,
    data: { x: { nd: { values: s.step, shape: [s.step.length] } },
            y: { nd: { values: s.t2, shape: [s.t2.length] } } } }];
  return { spec, series };
}

class LiveRunPanel {
  constructor(runId, { onFrame, onClose }) {
    ensureStyle();
    this.runId = runId;
    this.kw = null;
    const el = this.el = document.createElement('section');
    el.className = 'live-run';
    el.setAttribute('role', 'region');
    el.setAttribute('aria-label', `Live run ${runId}`);
    el.dataset.runId = runId;
    el.innerHTML = `
      <div class="live-run-head">
        <span class="live-run-title"></span>
        <button type="button" class="live-run-close" aria-label="Close live run panel" title="Close">×</button>
      </div>
      <div class="live-run-status" aria-live="polite"></div>
      <div class="live-run-plot"></div>`;
    el.querySelector('.live-run-title').textContent = `Live run ${runId}`;
    el.querySelector('.live-run-close').addEventListener('click', () => onClose(this));
    this.plot = el.querySelector('.live-run-plot');
    this._onFrame = onFrame;
    this._bound = false;
    document.body.appendChild(el);
  }

  update(kw) {
    this.kw = kw;
    const status = this.el.querySelector('.live-run-status');
    status.textContent = liveRunStatus(kw);
    const problem = liveRunProblem(kw);
    if (problem) {
      const span = document.createElement('div');
      span.className = 'live-run-problem';
      span.textContent = problem;
      status.appendChild(span);
    }
    const { spec, series } = liveRunPlot(kw);
    if (!series[0].data.y.nd.values.length) {
      clearPlot(this.plot);
      this.plot.innerHTML = '<div class="live-run-empty">No scores yet.</div>';
      this._bound = false;
      return;
    }
    if (this.plot.querySelector('.live-run-empty')) this.plot.innerHTML = '';
    renderPanel(this.plot, spec, series, { peaks: false, units: {} });
    if (!this._bound && this.plot.on) {
      this._bound = true;
      // The plot's points are the scored frames, in order: frame = row's frame index.
      this.plot.on('plotly_click', (ev) => {
        const i = ev?.points?.[0]?.pointIndex;
        const frame = this.kw?.series?.frame?.[i];
        if (frame != null && this.kw.fingerprint) this._onFrame(this.kw.fingerprint, frame);
      });
    }
  }

  remove() {
    clearPlot(this.plot);
    this.el.remove();
  }
}

/** Every live-run panel of this window, by run ID. */
export class LiveRuns {
  /** @param {{onFrame: (fp: string, frame: number) => void, onError: (text: string) => void}} hooks */
  constructor(hooks) {
    this.hooks = hooks;
    this.panels = new Map();
    this.closed = new Set();   // closed here: later updates do not reopen them
  }

  /** Show the panel of a run the user just asked to open, even if it was closed. */
  expect(path) { this._expected = path; }

  update(kw) {
    if (!kw.run_id) {
      if (kw.error) this.hooks.onError(kw.error);
      return null;
    }
    if (this._expected && kw.path && (kw.path === this._expected || kw.path.startsWith(this._expected.replace(/\/run\.json$/, '')))) {
      this.closed.delete(kw.run_id);
      this._expected = null;
    }
    if (this.closed.has(kw.run_id)) return null;
    let panel = this.panels.get(kw.run_id);
    if (!panel) {
      panel = new LiveRunPanel(kw.run_id, {
        onFrame: this.hooks.onFrame,
        onClose: (p) => { p.remove(); this.panels.delete(p.runId); this.closed.add(p.runId); },
      });
      this.panels.set(kw.run_id, panel);
    }
    panel.update(kw);
    return panel;
  }
}

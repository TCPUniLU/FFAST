/**
 * What the page does for a live run's controls (ADR 0086, 0087):
 * - asks before "Stop now", and sends the stop and stop-on-alarm requests
 *   (the server writes them into the run folder; the run's wrapper and scorer
 *   act on them);
 * - asks for the attribution of a clicked frame, shows it in the run's
 *   attribution panel and colours the main view by it while that frame is
 *   shown. A frame change takes the colours off; the list stays, naming its step.
 */
import { askDialog } from './dialogs.js';
import { OUT } from './events.js';
import { attributionColours } from './live_run.js';
import { openLiveRunsDialog } from './live_runs_dialog.js';

/** What "Stop now" will do, by the number of LAMMPS processes. */
export function stopMessage(processes) {
  if (processes === 1) {
    return 'LAMMPS stops cleanly at its next check (fix halt): the commands after the run '
      + 'still execute. Without a restart file the run cannot be continued.';
  }
  const n = processes ? `${processes} processes` : 'several processes (or an unknown number)';
  return `This run uses ${n}: LAMMPS will be terminated, so nothing after the run executes. `
    + 'Without a restart file the run cannot be continued.';
}

export class LiveUi {
  /**
   * @param {{send: (event: string, kw: object) => void,
   *   analysis?: {setLiveAttribution: (runId: string, state: object) => void},
   *   mainView?: {setAttribution: (att: object|null) => void},
   *   shown?: () => {datasetFp: string|null, frame: number|null},
   *   follow?: (path: string) => void, askPath?: () => void}} deps
   */
  constructor({ send, analysis = null, mainView = null, shown = () => ({ datasetFp: null, frame: null }),
    follow = () => {}, askPath = () => {} }) {
    this._send = send;
    this._follow = follow;
    this._askPath = askPath;
    this._runsDialog = null;
    this._analysis = analysis;
    this._mainView = mainView;
    this._shown = shown;
    /** run id → {frame, mode, pending, result} */
    this._att = new Map();
    this._coloured = null;   // the run id whose attribution colours the main view
  }

  /** A command from a live panel: {action: 'stop' | 'stop_on_alarm' | 'attribute' | 'attribution_mode', run_id, …}. */
  async command(cmd) {
    if (cmd.action === 'stop_on_alarm') {
      this._send(OUT.LIVE_SET_STOP_ON_ALARM, { run_id: cmd.run_id, on: !!cmd.on });
    } else if (cmd.action === 'stop') {
      const answer = await askDialog({ title: 'Stop this run?', message: stopMessage(cmd.processes),
        buttons: ['Cancel', 'Stop run'], danger: 'Stop run' });
      if (answer === 'Stop run') this._send(OUT.LIVE_STOP, { run_id: cmd.run_id });
    } else if (cmd.action === 'attribute') {
      const old = this._att.get(cmd.run_id);
      this._set(cmd.run_id, { frame: cmd.frame, mode: old?.mode || 'attribution', pending: true, result: null });
      const kw = { run_id: cmd.run_id, frame: cmd.frame };
      if (cmd.track && cmd.score) Object.assign(kw, { track: cmd.track, score: cmd.score });
      this._send(OUT.LIVE_ATTRIBUTION, kw);
    } else if (cmd.action === 'attribution_mode') {
      const state = this._att.get(cmd.run_id);
      if (state) this._set(cmd.run_id, { ...state, mode: cmd.mode });
    }
  }

  /** The "Live runs" dialog; its rows come back in LIVE_RUNS (onRuns). */
  openRuns() {
    this._runsDialog = openLiveRunsDialog({
      ask: () => this._send(OUT.LIST_LIVE_RUNS, {}),
      open: (path) => this._follow(path),
      openByPath: () => this._askPath(),
      forget: (runId) => this._send(OUT.FORGET_LIVE_RUN, { run_id: runId }),
    });
  }

  /** LIVE_RUNS: the list's rows. */
  onRuns(kw) {
    this._runsDialog?.update(kw.runs || []);
  }

  /** LIVE_ATTRIBUTION_RESULT: the server's answer for one frame. */
  onResult(kw) {
    const state = this._att.get(kw.run_id);
    if (!state || state.frame !== kw.frame) return;   // a newer click replaced it
    this._set(kw.run_id, { ...state, pending: false, result: kw });
  }

  /** The main view changed (a new frame or dataset): colours only on the explained frame. */
  sync() {
    let want = null;
    const shown = this._shown();
    for (const [runId, s] of this._att) {
      const r = s.result;
      if (s.mode === 'attribution' && r && !r.error && r.fingerprint === shown.datasetFp
          && r.frame === shown.frame) want = runId;
    }
    if (want === this._coloured) return;
    this._coloured = want;
    this._mainView?.setAttribution(want ? attributionColours(this._att.get(want).result) : null);
  }

  _set(runId, state) {
    this._att.set(runId, state);
    this._analysis?.setLiveAttribution(runId, state);
    this._coloured = undefined;   // the colours may have changed with the state
    this.sync();
  }
}

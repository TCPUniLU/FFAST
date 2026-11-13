/**
 * What the page does for a live run's controls (ADR 0086, 0087):
 * - asks before "Stop now", and sends the stop and stop-on-alarm requests
 *   (the server writes them into the run folder; the run's wrapper and scorer
 *   act on them);
 * - asks for the attribution of the frame the 3D panel its attribution card
 *   explains shows (the frame slider, playback, a clicked score point; the
 *   main view where no card says), shows it in the card and colours that
 *   panel by it. One request per run
 *   is in flight: frames passed while it is computed are skipped and the
 *   last one is asked next, so dragging the slider does not queue work. Until
 *   the new frame's answer comes the colours are off and the list stays,
 *   naming its step.
 */
import { askDialog } from './dialogs.js';
import { IN, OUT } from './events.js';
import { attributionColours } from './live_run.js';
import { openLiveRunsDialog } from './live_runs_dialog.js';
import { openMakeReferenceDialog } from './make_reference_dialog.js';
import { openRunWithFfastDialog } from './run_with_ffast_dialog.js';

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
   *   shown?: () => {datasetFp: string|null, frame: number|null,
   *     view?: {setAttribution: (att: object|null) => void}|null},
   *   runOf?: (datasetFp: string|null) => {run_id: string, bundle: string|null}|null,
   *   follow?: (path: string) => void, askPath?: () => void, whyNoOpen?: () => string}} deps
   */
  constructor({ send, request = null, analysis = null, mainView = null, shown = () => ({ datasetFp: null, frame: null }),
    runOf = () => null, follow = () => {}, askPath = () => {}, whyNoOpen = () => '' }) {
    this._send = send;
    /** Requests that wait for their own final reply (ADR 0097). */
    this._request = request;
    this._follow = follow;
    this._askPath = askPath;
    this._whyNoOpen = whyNoOpen;
    this._runsDialog = null;
    this._analysis = analysis;
    this._mainView = mainView;
    this._shown = shown;
    this._runOf = runOf;
    /** run id → {frame, test, mode, pending, result}: the frame wanted and the last answer. */
    this._att = new Map();
    /** run id → the request in flight ("frame|track|score"). */
    this._inFlight = new Map();
    this._coloured = null;   // the run id whose attribution colours a view
    this._colouredView = null;   // ...and that view
  }

  /** A command from a live panel: {action: 'stop' | 'manage' | 'stop_on_alarm' | 'attribute' | 'attribution_mode', run_id, …}. */
  async command(cmd) {
    if (cmd.action === 'stop_on_alarm') {
      this._send(OUT.LIVE_SET_STOP_ON_ALARM, { run_id: cmd.run_id, on: !!cmd.on });
    } else if (cmd.action === 'stop') {
      const answer = await askDialog({ title: `Stop ${cmd.name || 'this run'}?`, message: stopMessage(cmd.processes),
        buttons: ['Cancel', 'Stop run'], danger: 'Stop run' });
      if (answer === 'Stop run') this._send(OUT.LIVE_STOP, { run_id: cmd.run_id });
    } else if (cmd.action === 'manage') {
      this.openRuns({ highlight: cmd.run_id });
    } else if (cmd.action === 'attribute') {
      this._want(cmd.run_id, cmd.frame, cmd.track && cmd.score ? [cmd.track, cmd.score] : null);
    } else if (cmd.action === 'attribution_mode') {
      const state = this._att.get(cmd.run_id);
      if (state) this._set(cmd.run_id, { ...state, mode: cmd.mode });
    }
  }

  /** The "Live runs" dialog; its rows come back in LIVE_RUNS (onRuns). */
  openRuns({ highlight = null } = {}) {
    this._runsDialog = openLiveRunsDialog({
      deletePractice: (runId) => this._send(OUT.DELETE_PRACTICE_RUN, { run_id: runId }),
      highlight, whyNoOpen: this._whyNoOpen,
      stop: (row) => this.command({ action: 'stop', run_id: row.run_id, name: row.name, processes: row.processes ?? null }),
      ask: () => this._send(OUT.LIST_LIVE_RUNS, {}),
      open: (path) => this._follow(path),
      openByPath: () => this._askPath(),
      forget: (runId) => this._send(OUT.FORGET_LIVE_RUN, { run_id: runId }),
    });
  }

  /** The "Make reference" dialog over the loaded datasets; its answer comes in REFERENCE_MADE. */
  openMakeReference({ datasets, pickFolder = null }) {
    this._refDialog = openMakeReferenceDialog({
      datasets, pickFolder, send: (kw) => this._send(OUT.MAKE_REFERENCE, kw),
    });
  }

  /** REFERENCE_MADE: the bundle written, or why not. */
  onReferenceMade(kw) {
    this._refDialog?.done(kw);
  }

  /** "Run with FFAST" (ADR 0086): pick, check, review, submit, follow to the first frame. */
  openRunWithFfast({ pick, laptop, submitFromLaptop, status = () => {}, ...options }) {
    this._rwf = openRunWithFfastDialog({
      send: (event, kw) => this._send(event, kw), askServer: this._request,
      pick, laptop, submitFromLaptop, status, ...options,
      openRun: (folder) => this._follow(folder),
    });
  }

  /** RUN_FOLDER_SCANNED, RUN_WITH_FFAST_PREPARED, RUN_WITH_FFAST_WRITTEN, JOB_SUBMITTED, JOB_STATE. */
  onRunWithFfast(event, kw) {
    const d = this._rwf;
    if (!d) return;
    if (event === IN.RUN_FOLDER_SCANNED) d.onScanned(kw);
    else if (event === IN.RUN_WITH_FFAST_PREPARED) d.onPrepared(kw);
    else if (event === IN.RUN_WITH_FFAST_WRITTEN) d.onWritten(kw);
    else if (event === IN.JOB_SUBMITTED) d.onSubmitted(kw);
    else if (event === IN.JOB_STATE) d.onJobState(kw);
  }

  /** LIVE_RUNS: the list's rows. */
  onRuns(kw) {
    this._runsDialog?.update(kw.runs || []);
  }

  /** LIVE_ATTRIBUTION_RESULT: the server's answer for one frame. */
  onResult(kw) {
    const sent = this._inFlight.get(kw.run_id);
    this._inFlight.delete(kw.run_id);
    const state = this._att.get(kw.run_id);
    if (!state) return;
    // An answer for a frame passed meanwhile is dropped; the wanted one is asked next.
    if (state.frame === kw.frame) this._set(kw.run_id, { ...state, pending: _key(state) !== sent, result: kw });
    this._ask(kw.run_id);
  }

  /** Ask for frame `frame` of run `runId` (test: [track, score], or null for the frame's worst). */
  _want(runId, frame, test) {
    const old = this._att.get(runId);
    this._set(runId, { frame, test, mode: old?.mode || 'attribution', pending: true, result: old?.result ?? null });
    this._ask(runId);
  }

  /** Send the wanted request unless one is in flight (its answer sends the next). */
  _ask(runId) {
    const state = this._att.get(runId);
    if (!state?.pending || this._inFlight.has(runId)) return;
    this._inFlight.set(runId, _key(state));
    const kw = { run_id: runId, frame: state.frame };
    if (state.test) Object.assign(kw, { track: state.test[0], score: state.test[1] });
    this._send(OUT.LIVE_ATTRIBUTION, kw);
  }

  /** The shown 3D view changed (a new frame, dataset, or another view) or a
   * run's state came: a live run's shown frame is asked for; colours only on
   * the explained frame, in the view that shows it (`shown().view`, else the
   * main view). */
  sync() {
    const shown = this._shown();
    const run = this._runOf(shown.datasetFp);
    // No bundle yet means no scores to explain: the run's next update brings one.
    if (run?.bundle && Number.isInteger(shown.frame) && this._att.get(run.run_id)?.frame !== shown.frame) {
      this._want(run.run_id, shown.frame, null);   // _set calls sync again, now with that frame
      return;
    }
    let want = null;
    for (const [runId, s] of this._att) {
      const r = s.result;
      if (s.mode === 'attribution' && r && !r.error && r.fingerprint === shown.datasetFp
          && r.frame === shown.frame) want = runId;
    }
    const target = shown.view ?? this._mainView;
    if (want === this._coloured && target === this._colouredView) return;
    // The colours leave a view the attribution no longer explains.
    if (this._colouredView && this._colouredView !== target) this._colouredView.setAttribution(null);
    this._coloured = want;
    this._colouredView = target;
    target?.setAttribution(want ? attributionColours(this._att.get(want).result) : null);
  }

  _set(runId, state) {
    this._att.set(runId, state);
    this._analysis?.setLiveAttribution(runId, state);
    this._coloured = undefined;   // the colours may have changed with the state
    this.sync();
  }
}

/** One request's identity: its frame and test. */
function _key(state) {
  return `${state.frame}|${state.test ? state.test.join('|') : ''}`;
}

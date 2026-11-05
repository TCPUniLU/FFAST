/**
 * What the page does for a live run's controls (ADR 0086, 0087): asks before
 * "Stop now", and sends the stop and stop-on-alarm requests. The server
 * writes them into the run folder; the run's wrapper and scorer act on them.
 */
import { askDialog } from './dialogs.js';
import { OUT } from './events.js';

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
  /** @param {{send: (event: string, kw: object) => void}} deps */
  constructor({ send }) {
    this._send = send;
  }

  /** A command from a live panel: {action: 'stop' | 'stop_on_alarm', run_id, …}. */
  async command(cmd) {
    if (cmd.action === 'stop_on_alarm') {
      this._send(OUT.LIVE_SET_STOP_ON_ALARM, { run_id: cmd.run_id, on: !!cmd.on });
    } else if (cmd.action === 'stop') {
      const answer = await askDialog({ title: 'Stop this run?', message: stopMessage(cmd.processes),
        buttons: ['Cancel', 'Stop run'], danger: 'Stop run' });
      if (answer === 'Stop run') this._send(OUT.LIVE_STOP, { run_id: cmd.run_id });
    }
  }
}

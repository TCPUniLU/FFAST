"""``ffast-lammps``: run LAMMPS with the FFAST scorer beside it (ADR 0086).

In a job script, ``srun lmp -in run.in`` becomes ``ffast-lammps srun lmp -in run.in``.
The wrapper

1. claims a fresh run folder (``$FFAST_RUN_DIR`` or ``./ffast-runs``, never a
   path with spaces),
2. starts ``ffast-live`` (one thread, its own log, the Python modules and venv
   from ``~/.ffast/live.json`` written by the cluster bootstrap; LAMMPS keeps the
   job's own environment),
3. runs the command with ``-var ffast_on 1 -var ffast_dir <run folder>``,
4. stops LAMMPS when ``stop_request.json`` appears (written by the scorer on an
   alarm with stop on alarm, or by the viewer's "Stop now"): one process →
   writes ``STOP`` and LAMMPS's ``fix halt … error soft`` ends the run cleanly;
   several processes → terminates LAMMPS (``fix halt`` is not synchronised
   across processes),
5. when LAMMPS exits, writes ``producer_done.json``, lets the scorer read to the
   end, and records how the run ended in ``run.json`` (the only writer):
   finished, stopped, terminated, crashed or interrupted.

Its exit code is LAMMPS's. Multi-partition runs are refused. Each run is listed
for the viewer by a pointer in ``~/.ffast/live-runs/``.

Standard library only, Python 3.6 compatible: it runs under whatever
``python3`` the job has. The cluster copy lives in ``~/.ffast/bin`` next to
``run_folder.py`` and ``lammps_log.py``.
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time

try:
    from ffast.live import lammps_log
    from ffast.live import run_folder as rf
except ImportError:  # the copy in ~/.ffast/bin, run with python3 -I
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import lammps_log  # noqa: E402
    import run_folder as rf  # noqa: E402

SETTINGS_ENV = "FFAST_LIVE_SETTINGS"          # path of live.json
SCORER_CMD_ENV = "FFAST_LIVE_SCORER_CMD"      # JSON list, replaces the scorer command (tests)
DEFAULT_SETTINGS = "~/.ffast/live.json"
DRAIN_S = 600.0              # after a normal end: the scorer reads the rest of the dump
DRAIN_INTERRUPTED_S = 20.0   # after a signal: Slurm kills everything about 30 s later
SCORER_START_S = 300.0       # a scorer that has not started by then is reported
POLL_S = 1.0
LOG_TAIL_LINES = 20
GRID_WAIT_S = 30.0           # a stop request with no process count yet: terminate after this

USAGE = """usage: ffast-lammps <command that runs LAMMPS>
  e.g.  ffast-lammps srun lmp -in run.in
Runs LAMMPS with FFAST watching it; the input must include the file written by
`ffast bundle`. Run folders go to $FFAST_RUN_DIR, or ./ffast-runs.
  ffast-lammps --replay <recorded run folder> [--sleep S] [--gate-step N]
  --practice-gate N   mark the run as practice; use ~/.ffast/practice-runs
  --exit-with-parent PID   interrupt the run when its starting process exits"""


class UsageError(Exception):
    pass


def lammps_index(cmd):
    """Index of the LAMMPS executable in ``cmd`` (basename starting with ``lmp``), or None."""
    for i, arg in enumerate(cmd):
        name = os.path.basename(arg)
        if name.startswith("lmp") or name == "lammps":
            return i
    return None


def check_command(cmd):
    """Refuse an empty command and LAMMPS multi-partition runs."""
    if not cmd:
        raise UsageError(USAGE)
    i = lammps_index(cmd)
    lmp_args = cmd[i + 1:] if i is not None else cmd
    bad = ("-partition", "-p") if i is not None else ("-partition",)
    for arg in lmp_args:
        if arg in bad:
            raise UsageError(
                "ffast-lammps: multi-partition LAMMPS runs (%s) are not supported; "
                "run without -partition" % arg)


def lammps_command(cmd, run_dir):
    """The command with FFAST's variables (and a log in the run folder unless one is set)."""
    i = lammps_index(cmd)
    lmp_args = cmd[i + 1:] if i is not None else cmd
    extra = ["-var", "ffast_on", "1", "-var", "ffast_dir", str(run_dir)]
    if "-log" not in lmp_args and "-l" not in lmp_args:
        extra += ["-log", str(run_dir / rf.LOG)]
    return list(cmd) + extra


def scorer_command(run_dir, environ):
    """(argv, env) that starts ``ffast-live`` for ``run_dir``."""
    env = dict(environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(key, None)
    env.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
               NUMEXPR_NUM_THREADS="1", PYTHONUNBUFFERED="1")
    override = environ.get(SCORER_CMD_ENV)
    if override:
        return json.loads(override) + [str(run_dir)], env
    settings_path = os.path.expanduser(environ.get(SETTINGS_ENV) or DEFAULT_SETTINGS)
    settings = rf.read_json(settings_path)
    if settings and settings.get("venv"):
        venv = os.path.expanduser(settings["venv"])
        steps = ["module --force purge >/dev/null 2>&1 || true"]
        steps += ["module load %s" % m for m in settings.get("modules", [])]
        steps += ["source %s/bin/activate" % venv, 'exec ffast-live "$0"']
        return ["bash", "-lc", " && ".join(steps), str(run_dir)], env
    found = shutil.which("ffast-live")
    if found:
        return [found, str(run_dir)], env
    raise UsageError(
        "ffast-lammps: cannot start the scorer: no %s (connect FFAST to this cluster once) "
        "and no ffast-live on PATH" % settings_path)


def _tail(path, lines=LOG_TAIL_LINES):
    try:
        with open(str(path), "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 8192))
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def producer_progress(run_dir):
    """Dump size and last step written (from the end of the file), LAMMPS version."""
    out = {"dump_bytes": 0, "last_step": None}
    dump = run_dir / rf.FRAMES
    try:
        out["dump_bytes"] = os.path.getsize(str(dump))
        with open(str(dump), "rb") as fh:
            fh.seek(max(0, out["dump_bytes"] - 65536))
            tail = fh.read().decode("ascii", "replace")
        at = tail.rfind("ITEM: TIMESTEP\n")
        if at >= 0:
            rest = tail[at + len("ITEM: TIMESTEP\n"):].split("\n", 1)
            if len(rest) == 2 and rest[0].strip().isdigit():
                out["last_step"] = int(rest[0])
    except OSError:
        pass
    return out


def lammps_version(run_dir):
    try:
        with open(str(run_dir / rf.LOG)) as fh:
            first = fh.readline().strip()
    except OSError:
        return None
    return first if first.startswith("LAMMPS") else None


class Wrapper:
    def __init__(
        self,
        cmd,
        environ=None,
        cwd=None,
        drain_s=DRAIN_S,
        drain_interrupted_s=DRAIN_INTERRUPTED_S,
        poll_s=POLL_S,
        practice_gate=None,
        parent_pid=None,
        practice_sleep=None,
    ):
        self.practice_sleep = practice_sleep
        self.practice_gate = practice_gate
        self.parent_pid = parent_pid
        self.cmd = list(cmd)
        self.environ = dict(os.environ if environ is None else environ)
        self.cwd = cwd or os.getcwd()
        self.drain_s = drain_s
        self.drain_interrupted_s = drain_interrupted_s
        self.poll_s = poll_s
        self.signal_received = None
        self.lammps = None
        self.run_dir = None
        self.run = {}
        self.log = None
        self.stop = None        # the stop request being carried out, as stored in run.json
        self._stop_seen_t = None

    # ── run.json (this process is its only writer) ───────────────────────────
    def _save(self):
        analyses = {}
        adir = self.run_dir / rf.ANALYSIS_DIR
        if adir.is_dir():
            for d in sorted(adir.iterdir()):
                p = rf.read_json(d / rf.PROGRESS)
                if p is not None:
                    analyses[d.name] = p
        self.run["analyses"] = analyses
        self.run["producer"] = producer_progress(self.run_dir)
        self.run["lammps_version"] = self.run.get("lammps_version") or lammps_version(self.run_dir)
        units = [p.get("units") for p in analyses.values() if p.get("units")]
        if units:
            self.run["units"] = units[0]
            self.run["time_unit"] = [p.get("time_unit") for p in analyses.values()][0]
        self.run["updated_utc"] = rf.utc_now()
        rf.write_json_atomic(self.run_dir / rf.RUN_JSON, self.run)

    def _on_signal(self, signum, _frame):
        self.signal_received = signum
        if self.lammps is not None and self.lammps.poll() is None:
            try:
                self.lammps.send_signal(signum)
            except OSError:
                pass

    def main(self):
        check_command(self.cmd)
        run_env = self.environ
        if self.practice_gate is not None:
            run_env = {rf.RUN_DIR_ENV: str(rf.practice_runs_dir(self.environ))}
        base = rf.run_base(run_env, self.cwd)
        run_id, self.run_dir = rf.claim_run_folder(base)
        scorer_argv, scorer_env = scorer_command(self.run_dir, self.environ)
        lmp_cmd = lammps_command(self.cmd, self.run_dir)
        self.run = {
            "format": rf.RUN_FORMAT,
            "format_version": rf.RUN_FORMAT_VERSION,
            "run_id": run_id,
            "run_dir": str(self.run_dir),
            "segments": [{"id": 0, "started_utc": rf.utc_now()}],
            "command": lmp_cmd,
            "cwd": self.cwd,
            "slurm_job_id": self.environ.get("SLURM_JOB_ID"),
            # the job's name (#SBATCH -J) names the run in the list and its tab
            "slurm_job_name": self.environ.get("SLURM_JOB_NAME"),
            "end": None,
        }
        if self.practice_gate is not None:
            self.run["practice"] = {"gate_step": self.practice_gate}
            if self.practice_sleep is not None:
                self.run["practice"]["sleep_s"] = self.practice_sleep
        rf.append_event(
            self.run_dir,
            "start",
            run_id=run_id,
            command=lmp_cmd,
            slurm_job_id=self.environ.get("SLURM_JOB_ID"),
        )
        rf.append_event(self.run_dir, "segment", segment=0)
        self._save()
        self.log = lammps_log.LogFollower(rf.log_path(self.run_dir, lmp_cmd, self.cwd))
        sys.stderr.write("ffast-lammps: run folder %s\n" % self.run_dir)

        log = open(str(self.run_dir / rf.SCORER_LOG), "wb")
        scorer = None
        previous = {
            s: signal.signal(s, self._on_signal)
            for s in (signal.SIGTERM, signal.SIGINT)
        }
        try:
            scorer = subprocess.Popen(
                scorer_argv,
                env=scorer_env,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.lammps = subprocess.Popen(
                lmp_cmd, env=self.environ, cwd=self.cwd
            )
            # A pointer is the local launcher's startup handoff. Publish it
            # only after both children could actually be started.
            rf.register_run(run_id, self.run_dir, self.environ)
            return self._watch(scorer, log)
        except OSError:
            if scorer is not None and scorer.poll() is None:
                try:
                    os.killpg(scorer.pid, signal.SIGTERM)
                except OSError:
                    pass
                try:
                    scorer.wait(10)
                except subprocess.TimeoutExpired:
                    scorer.kill()
                    scorer.wait()
            raise
        finally:
            for s, h in previous.items():
                signal.signal(s, h)
            log.close()

    def _watch(self, scorer, log):
        t0 = time.monotonic()
        scorer_reported = False
        while self.lammps.poll() is None:
            if (
                self.parent_pid is not None
                and os.getppid() != self.parent_pid
                and not self.signal_received
            ):
                self._on_signal(signal.SIGTERM, None)
            if not scorer_reported and scorer.poll() is not None:
                scorer_reported = True
                self._scorer_ended_early(scorer.returncode)
            elif (
                not scorer_reported
                and time.monotonic() - t0 > SCORER_START_S
                and not any(
                    e["kind"] == "scorer_start"
                    for e in rf.read_events(self.run_dir)
                )
            ):
                scorer_reported = True
                rf.append_event(
                    self.run_dir,
                    "warning",
                    text="the scorer has not started after %d s"
                    % SCORER_START_S,
                    scorer_log=_tail(self.run_dir / rf.SCORER_LOG),
                )
            self.log.poll()
            self._check_stop()
            self._save()
            time.sleep(self.poll_s)
        rc = self.lammps.returncode
        self.log.poll()
        rf.write_json_atomic(self.run_dir / rf.PRODUCER_DONE, {"exit_code": rc, "ended_utc": rf.utc_now()})
        rf.append_event(self.run_dir, "lammps_exit", exit_code=rc)

        budget = self.drain_interrupted_s if self.signal_received else self.drain_s
        t_end = time.monotonic()
        try:
            scorer.wait(budget)
            drained = True
        except subprocess.TimeoutExpired:
            drained = False
            try:
                os.killpg(scorer.pid, signal.SIGTERM)
                scorer.wait(10)
            except (OSError, subprocess.TimeoutExpired):
                scorer.kill()
                scorer.wait()
        drain_s = round(time.monotonic() - t_end, 2)
        rf.append_event(self.run_dir, "drained" if drained else "warning",
                        seconds=drain_s, scorer_exit_code=scorer.returncode,
                        **({} if drained else {"text": "the scorer did not finish within %d s" % budget}))

        state = self._end_state(rc)
        self.run["end"] = {"state": state, "exit_code": rc, "ended_utc": rf.utc_now(),
                           "drained": drained, "drain_s": drain_s,
                           "signal": self.signal_received}
        if self.stop is not None:
            self.run["end"]["stop_observed_step"] = self.stop.get("observed_step")
        self._save()
        rf.append_event(self.run_dir, "end", state=state, exit_code=rc)
        sys.stderr.write("ffast-lammps: run %s (%s), folder %s\n" % (state, rc, self.run_dir))
        if (self.signal_received or state == "terminated") and rc is not None and rc < 0:
            return 128 - rc
        return rc

    # ── stopping (ADR 0086: one process halts cleanly, several are terminated) ──
    def _check_stop(self):
        if self.stop is not None:
            return
        req = rf.read_json(self.run_dir / rf.STOP_REQUEST)
        if not isinstance(req, dict):
            return                    # none, or still being written
        if self._stop_seen_t is None:
            self._stop_seen_t = time.monotonic()
        procs = self.log.processes
        if procs is None and time.monotonic() - self._stop_seen_t < GRID_WAIT_S:
            return                    # the processor grid line comes before the first step
        method = "halt" if procs == 1 else "terminate"
        self.stop = {"by": req.get("by"), "reason": req.get("reason"),
                     "requested_utc": req.get("utc"), "method": method, "processes": procs,
                     "sent_utc": rf.utc_now()}
        self.run["stop"] = self.stop
        if method == "halt":
            with open(str(self.run_dir / rf.STOP), "w") as fh:
                fh.write("stop requested by %s\n" % req.get("by"))
            rf.append_event(self.run_dir, "stop_sent", method="halt",
                            text="STOP written; LAMMPS's fix halt ends the run at its next check")
        else:
            why = ("%d processes: fix halt is not synchronised across processes" % procs
                   if procs else "process count unknown after %d s" % GRID_WAIT_S)
            rf.append_event(self.run_dir, "stop_sent", method="terminate", text="terminating LAMMPS; " + why)
            try:
                self.lammps.send_signal(signal.SIGTERM)
            except OSError:
                pass

    def _end_state(self, rc):
        if self.signal_received:
            return "interrupted"
        if self.stop is not None:
            if self.stop["method"] == "halt":
                step = self.log.halt_step
                if step is not None:
                    self.stop.update(observed_step=step, observed="halt line in log.lammps")
                    rf.append_event(self.run_dir, "stop_observed", step=step, how="halt")
                    return "stopped" if rc == 0 else "crashed"
                rf.append_event(self.run_dir, "warning",
                                text="a stop was requested, but LAMMPS ended before it checked for it")
            else:
                step = producer_progress(self.run_dir)["last_step"]
                self.stop.update(observed_step=step, observed="terminated; last saved frame")
                rf.append_event(self.run_dir, "stop_observed", step=step, how="terminated")
                return "terminated"
        return "finished" if rc == 0 else "crashed"

    def _scorer_ended_early(self, code):
        events = rf.read_events(self.run_dir)
        if code == 0 and any(e["kind"] in ("refused", "warning") for e in events):
            return    # it said why itself
        rf.append_event(self.run_dir, "warning",
                        text="the scorer stopped while LAMMPS runs (exit code %s); LAMMPS "
                             "continues and the dump is kept" % code,
                        scorer_log=_tail(self.run_dir / rf.SCORER_LOG))


def main(argv=None):
    cmd = list(sys.argv[1:] if argv is None else argv)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd or cmd[0] in ("-h", "--help"):
        sys.stderr.write(USAGE + "\n")
        return 0 if cmd else 2
    try:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--practice-gate", type=int)
        parser.add_argument("--exit-with-parent", type=int)
        parser.add_argument("--replay")
        parser.add_argument("--sleep", type=float, default=0.3)
        parser.add_argument("--gate-step", type=int)
        # Only wrapper options preceding the LAMMPS command are parsed.
        options_end = 0
        wrapper_options = {
            "--practice-gate",
            "--exit-with-parent",
            "--replay",
            "--sleep",
            "--gate-step",
        }
        while (
            options_end < len(cmd)
            and cmd[options_end].split("=", 1)[0] in wrapper_options
        ):
            options_end += 1 if "=" in cmd[options_end] else 2
        options = parser.parse_args(cmd[:options_end])
        command = cmd[options_end:]
        if command[:1] == ["--"]:
            command = command[1:]
        if options.practice_gate is not None and options.practice_gate < 0:
            raise UsageError("--practice-gate must be nonnegative")
        if options.replay:
            command = [
                sys.executable,
                "-m",
                "ffast.live.replay",
                options.replay,
                "--sleep",
                str(options.sleep),
            ] + command
            if options.gate_step is not None:
                command += ["--gate-step", str(options.gate_step)]
        return Wrapper(
            command,
            practice_gate=options.practice_gate,
            parent_pid=options.exit_with_parent,
            practice_sleep=options.sleep if options.replay else None,
        ).main()
    except (UsageError, ValueError, OSError) as exc:
        sys.stderr.write("%s\n" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())

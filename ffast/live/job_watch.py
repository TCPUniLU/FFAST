"""Follow a submitted "Run with FFAST" job until its first frame (plan P14).

The server asks Slurm for the job's state (``squeue``, then ``sacct`` once the
job has left the queue; both work on a compute node) and looks for the run
folder the wrapper claimed: the pointer in ``~/.ffast/live-runs/`` whose
``slurm_job_id`` is this job (the pointer, or the run's ``run.json``). The
watch ends when the dump has its first frame (the page then opens the run) or
when the job ended without one.

States: ``queued`` (pending), ``running`` (no run folder yet), ``started``
(folder claimed, no frame), ``frame`` (open it), ``ended`` (no frame; the
reason and the scorer log's tail when there is a folder).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from ffast.live import run_folder as rf

POLL_S = 5.0
ENDED = ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED",
         "BOOT_FAIL", "DEADLINE")
POINTERS_SCANNED = 50

Run = Callable[[list], "tuple[int, str]"]


def _subprocess(argv: list) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return p.returncode, p.stdout.decode(errors="replace")


def slurm_state(job_id: str, run: Run = _subprocess) -> str | None:
    """PENDING, RUNNING, COMPLETED, … or None when Slurm cannot be asked."""
    rc, out = run(["squeue", "--noheader", "-o", "%T", "-j", job_id])
    if rc == 0 and out.strip():
        return out.split()[0].upper()
    rc, out = run(["sacct", "-j", job_id, "--noheader", "--format=State", "-X"])
    if rc == 0 and out.strip():
        return out.split()[0].rstrip("+").upper()
    return None


def find_run(job_id: str, pointer_dir: Path) -> Path | None:
    """The run folder the wrapper claimed in job ``job_id`` (newest pointers first)."""
    try:
        pointers = sorted(pointer_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for p in pointers[:POINTERS_SCANNED]:
        ptr = rf.read_json(p)
        if not isinstance(ptr, dict) or not ptr.get("run_dir"):
            continue
        run_dir = Path(ptr["run_dir"])
        jid = ptr.get("slurm_job_id")
        if jid is None:
            jid = (rf.read_json(run_dir / rf.RUN_JSON) or {}).get("slurm_job_id")
        if str(jid) == str(job_id):
            return run_dir
    return None


def has_frame(run_dir: Path) -> bool:
    """True once the dump holds the end of a frame's atom lines (a ``TIMESTEP``
    after which more data follows): the live view can open it."""
    from ffast.live.dump_reader import DumpReader

    path = run_dir / rf.FRAMES
    if not path.exists():
        return False
    try:
        return bool(DumpReader(path, max_bytes=4 << 20).poll())
    except Exception:
        return False


def _tail(path: Path, n: int = 12) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])


class JobWatch:
    """One submitted job; ``poll()`` returns its state for the page."""

    def __init__(self, job_id: str, *, pointer_dir: Path | None = None, run: Run | None = None):
        self.job_id = str(job_id)
        self.pointer_dir = pointer_dir or rf.live_runs_dir()
        self.run = run or (lambda argv: _subprocess(argv))
        self.run_dir: Path | None = None
        self.done = False

    def poll(self) -> dict:
        state = slurm_state(self.job_id, self.run)
        if self.run_dir is None:
            self.run_dir = find_run(self.job_id, self.pointer_dir)
        out = {"job_id": self.job_id, "slurm": state, "folder": str(self.run_dir) if self.run_dir else None}
        if self.run_dir is not None and has_frame(self.run_dir):
            self.done = True
            return {**out, "state": "frame"}
        if state in ENDED or (state is not None and state.startswith("CANCELLED")):
            self.done = True
            end = {**out, "state": "ended", "reason": f"the job ended ({state}) before its first frame"}
            if self.run_dir is not None:
                run = rf.read_json(self.run_dir / rf.RUN_JSON) or {}
                end["end"] = run.get("end")
                end["scorer_log"] = _tail(self.run_dir / rf.SCORER_LOG)
            return end
        if state == "PENDING":
            return {**out, "state": "queued"}
        if self.run_dir is not None:
            return {**out, "state": "started"}
        if state is None:
            return {**out, "state": "unknown", "reason": "Slurm did not answer; trying again"}
        return {**out, "state": "running"}

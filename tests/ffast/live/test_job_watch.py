"""Following a submitted "Run with FFAST" job until its first frame."""

from __future__ import annotations

import asyncio
import types

import pytest

from ffast.live import run_folder as rf
from ffast.live.job_watch import JobWatch, find_run, slurm_state
from tests.ffast.live.dump_helpers import dump_text, random_frames


class Slurm:
    """squeue/sacct answers, one per poll; ``None`` = left the queue."""

    def __init__(self, *states, sacct="COMPLETED"):
        self.states = list(states)
        self.sacct = sacct

    def __call__(self, argv):
        if argv[0] == "squeue":
            s = self.states.pop(0) if self.states else None
            return (0, s + "\n") if s else (0, "")
        return 0, f"{self.sacct}\n"


def _pointer(pointers, run_dir, job_id, in_pointer=True):
    pointers.mkdir(exist_ok=True)
    run_dir.mkdir()
    rf.write_json_atomic(run_dir / rf.RUN_JSON, {"run_id": run_dir.name, "slurm_job_id": job_id})
    ptr = {"run_id": run_dir.name, "run_dir": str(run_dir)}
    if in_pointer:
        ptr["slurm_job_id"] = job_id
    rf.write_json_atomic(pointers / f"{run_dir.name}.json", ptr)


def test_slurm_state_asks_sacct_once_the_job_left_the_queue():
    assert slurm_state("1", Slurm("PENDING")) == "PENDING"
    assert slurm_state("1", Slurm(None, sacct="CANCELLED+")) == "CANCELLED"
    assert slurm_state("1", lambda argv: (1, "error")) is None


def test_the_run_is_found_by_job_id_in_the_pointer_or_its_run_json(tmp_path):
    p = tmp_path / "pointers"
    _pointer(p, tmp_path / "a", "11")
    _pointer(p, tmp_path / "b", "22", in_pointer=False)    # a pointer from an older wrapper
    assert find_run("11", p) == tmp_path / "a"
    assert find_run("22", p) == tmp_path / "b"
    assert find_run("33", p) is None


def test_queued_running_started_then_frame(tmp_path):
    p = tmp_path / "pointers"
    p.mkdir()
    watch = JobWatch("42", pointer_dir=p, run=Slurm("PENDING", "RUNNING", "RUNNING", "RUNNING"))
    assert watch.poll()["state"] == "queued"
    assert watch.poll()["state"] == "running"
    run = tmp_path / "run1"
    _pointer(p, run, "42")
    (run / rf.FRAMES).write_text(dump_text(random_frames(1))[:-40])     # half a frame
    got = watch.poll()
    assert got["state"] == "started" and got["folder"] == str(run)
    (run / rf.FRAMES).write_text(dump_text(random_frames(1)))
    got = watch.poll()
    assert got["state"] == "frame" and watch.done


def test_a_job_that_ends_without_a_frame_says_why(tmp_path):
    p = tmp_path / "pointers"
    run = tmp_path / "run1"
    _pointer(p, run, "7")
    (run / rf.SCORER_LOG).write_text("line\nffast-live: refused: units lj\n")
    rf.write_json_atomic(run / rf.RUN_JSON, {"slurm_job_id": "7", "end": {"state": "crashed", "exit_code": 1}})
    got = JobWatch("7", pointer_dir=p, run=Slurm(None, sacct="FAILED")).poll()
    assert got["state"] == "ended" and "FAILED" in got["reason"]
    assert got["end"]["state"] == "crashed" and "units lj" in got["scorer_log"]


def test_a_job_cancelled_while_queued_ends_the_watch(tmp_path):
    watch = JobWatch("8", pointer_dir=tmp_path, run=Slurm(None, sacct="CANCELLED by 123"))
    assert watch.poll()["state"] == "ended" and watch.done


def test_the_server_pushes_each_change_once_and_stops_at_the_frame(tmp_path, monkeypatch):
    from ffast.live import job_watch
    from ffast.protocol import control
    from ffast.protocol.rpc import unpack
    from ffast.session.server_session import ServerSession

    p = tmp_path / "pointers"
    p.mkdir()
    monkeypatch.setenv("FFAST_LIVE_RUNS", str(p))
    slurm = Slurm("PENDING", "PENDING", "RUNNING", "RUNNING")
    monkeypatch.setattr(job_watch, "_subprocess", slurm)
    monkeypatch.setattr(job_watch, "POLL_S", 0.01)

    queue = asyncio.Queue()
    session = ServerSession(types.SimpleNamespace(), queue)

    async def go():
        await session.dispatch(control.WATCH_JOB, (), {"job_id": "42"})
        await session.dispatch(control.WATCH_JOB, (), {"job_id": "42"})   # a second ask: same watch
        assert len(session._job_watches) == 1
        for _ in range(200):
            if len(slurm.states) <= 1:
                break
            await asyncio.sleep(0.01)
        run = tmp_path / "run1"
        _pointer(p, run, "42")
        (run / rf.FRAMES).write_text(dump_text(random_frames(1)))
        await asyncio.wait_for(asyncio.gather(*session._job_watches.values()), 5)

    asyncio.run(go())
    states = []
    while not queue.empty():
        event, _, kw = unpack(queue.get_nowait())
        assert event == control.JOB_STATE
        states.append(kw["state"])
    assert states[0] == "queued" and states[-1] == "frame"
    assert states.count("queued") == 1                      # no repeat for an unchanged state
    assert not session._job_watches

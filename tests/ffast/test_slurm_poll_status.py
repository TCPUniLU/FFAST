"""A job missing from squeue for a moment is not a failed job.

MeluXina, 2026-11-02: job 5413922 started at 18:10:58 and its server came
up, but the connect gave up ("failed before reaching RUNNING state"): when
squeue does not list a job, its sacct state decided, and anything but
COMPLETED counted as failed, RUNNING and PENDING included.
"""
from __future__ import annotations

import asyncio

import pytest

from cluster.backend import JobStatus
from cluster.slurm import RemoteSlurmBackend


class _Backend(RemoteSlurmBackend):
    """squeue lists nothing; sacct answers ``state``."""

    def __init__(self, state):
        super().__init__("host")
        self.state = state

    async def _run_remote(self, *cmd):
        return ("" if cmd[0] == "squeue" else f"{self.state}\n"), "", 0


@pytest.mark.parametrize("state, status", [
    ("RUNNING", JobStatus.RUNNING),
    ("PENDING", JobStatus.PENDING),
    ("COMPLETED", JobStatus.COMPLETED),
    ("FAILED", JobStatus.FAILED),
    ("CANCELLED+", JobStatus.FAILED),          # sacct truncates long names with "+"
    ("CANCELLED by 12345", JobStatus.FAILED),
])
def test_a_job_squeue_does_not_list_takes_its_sacct_state(state, status):
    assert asyncio.run(_Backend(state).poll_status("1")) == status


# ── waiting for a queued job ──────────────────────────────────────────────────
# The connect cancelled any job still queued after 10 min; on 2026-11-02
# jobs waited 0-15 min. It now waits until the job starts, or the user
# presses Cancel.

class _Queued:
    """Pending ``n`` polls, then ``final``."""

    def __init__(self, n, final=JobStatus.RUNNING):
        self.n, self.final, self.polls, self.cancelled = n, final, 0, False

    async def poll_status(self, job_id):
        self.polls += 1
        return JobStatus.PENDING if self.polls <= self.n else self.final

    async def cancel_job(self, job_id):
        self.cancelled = True


def test_a_queued_job_is_waited_for_with_no_time_limit():
    from cluster.connection import _POLL_TIMEOUT, _wait_until_running

    assert _POLL_TIMEOUT is None
    backend = _Queued(200)
    asyncio.run(_wait_until_running(backend, "1", poll_interval=0, poll_timeout=None))
    assert backend.polls == 201 and not backend.cancelled


def test_a_time_limit_still_cancels_when_given():
    from cluster.backend import ClusterError
    from cluster.connection import _wait_until_running

    backend = _Queued(10**9)
    with pytest.raises(ClusterError, match="Timed out"):
        asyncio.run(_wait_until_running(backend, "1", poll_interval=0.01, poll_timeout=0.05))
    assert backend.cancelled


def test_a_job_that_fails_while_queued_is_reported():
    from cluster.backend import ClusterError
    from cluster.connection import _wait_until_running

    with pytest.raises(ClusterError, match="failed before reaching RUNNING"):
        asyncio.run(_wait_until_running(_Queued(2, JobStatus.FAILED), "1",
                                        poll_interval=0, poll_timeout=None))

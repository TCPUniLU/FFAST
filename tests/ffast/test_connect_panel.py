"""
Offline tests for the connecting-panel feature (issue #12).

No real cluster, SSH, or UI needed.  Uses a FakeSlurmBackend that
simulates each stage with configurable delays.  Ported from the original
root-level ``test_connect_panel.py`` manual harness into the pytest suite
(``asyncio_mode = "auto"`` runs the ``async def test_*`` functions).
"""

import asyncio
import unittest.mock as mock
from dataclasses import dataclass

import pytest

from cluster.backend import ClusterBackend, ClusterError, JobStatus


# ── Fake backend ──────────────────────────────────────────────────────────────


class FakeSlurmBackend(ClusterBackend):
    """Simulates SLURM without SSH.  Transitions: PENDING → RUNNING."""

    def __init__(self, fail_at=None, job_id="FAKE-42"):
        """
        fail_at : str | None
            One of 'submit', 'poll', 'node', 'ws' to simulate failure
            at that stage.
        """
        self._fail_at = fail_at
        self._job_id = job_id
        self._polls = 0

    async def submit_job(self, spec):
        await asyncio.sleep(0.01)
        if self._fail_at == "submit":
            raise ClusterError("sbatch: error: fake submit failure")
        return self._job_id

    async def poll_status(self, job_id):
        await asyncio.sleep(0.01)
        if self._fail_at == "poll":
            return JobStatus.FAILED
        self._polls += 1
        # Pretend to be PENDING for 2 polls then RUNNING
        return JobStatus.RUNNING if self._polls >= 2 else JobStatus.PENDING

    async def get_node_address(self, job_id):
        await asyncio.sleep(0.01)
        if self._fail_at == "node":
            raise ClusterError("scontrol: node lookup failed")
        return "gpu01.fake.cluster"

    async def cancel_job(self, job_id):
        await asyncio.sleep(0.01)


# ── Fake profile ──────────────────────────────────────────────────────────────


@dataclass
class FakeProfile:
    name: str = "fake-profile"
    host: str = "login.fake.cluster"
    username: str = "testuser"
    identity_file: str = ""
    partition: str = "gpu"
    account: str = ""
    qos: str = ""
    job_name: str = "ffast"
    ffast_server_cmd: str = "ffast-server"

    def to_job_spec(self, command):
        from cluster.backend import JobSpec

        return JobSpec(
            cores=1,
            memory_mb=4096,
            time_limit="01:00:00",
            command=command,
            partition=self.partition,
        )


# ── Patched connect_to_cluster that uses FakeSlurmBackend ────────────────────


async def fake_connect(
    profile,
    fake_backend,
    fail_ws=False,
    progress_cb=None,
    on_job_submitted=None,
    poll_interval=0.02,
    ws_recv_fn=None,
):
    """Runs the real connect_to_cluster logic but injects the fake backend
    and skips actual SSH / WebSocket by monkey-patching at import time."""

    import cluster.slurm as slurm_mod
    import cluster.connection as session_mod

    # Patch RemoteSlurmBackend constructor to return our fake
    with mock.patch.object(
        slurm_mod, "RemoteSlurmBackend", return_value=fake_backend
    ):
        # Patch _find_free_port
        with mock.patch.object(
            session_mod, "_find_free_port", return_value=54321
        ):
            # Patch subprocess.Popen (SSH tunnel)
            fake_proc = mock.MagicMock()
            fake_proc.poll.return_value = None  # tunnel alive
            with mock.patch("subprocess.Popen", return_value=fake_proc):
                # Patch websockets.connect
                if fail_ws:
                    with mock.patch(
                        "websockets.connect",
                        side_effect=OSError("refused"),
                    ):
                        return await session_mod.connect_to_cluster(
                            profile,
                            poll_interval=poll_interval,
                            progress_cb=progress_cb,
                            on_job_submitted=on_job_submitted,
                        )
                else:
                    fake_ws = mock.MagicMock()
                    fake_ws.send = mock.AsyncMock()
                    fake_ws.recv = ws_recv_fn or mock.AsyncMock(return_value="pong")
                    fake_ws.close = mock.AsyncMock()

                    # websockets.connect is awaited: make it an async callable.
                    # Accept **kwargs so the stub tolerates connect options the
                    # real cluster.connection passes (e.g. max_size, ping_interval).
                    async def _ws_connect(url, *args, **kwargs):
                        return fake_ws

                    with mock.patch(
                        "websockets.connect", side_effect=_ws_connect
                    ):
                        return await session_mod.connect_to_cluster(
                            profile,
                            poll_interval=poll_interval,
                            progress_cb=progress_cb,
                            on_job_submitted=on_job_submitted,
                        )


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_happy_path():
    messages = []
    job_ids_seen = []
    profile = FakeProfile()
    backend = FakeSlurmBackend(job_id="12345")

    # Simulate menuHandler's _progress wrapper (adds [Job …] prefix)
    def _on_job_submitted(jid):
        job_ids_seen.append(jid)

    def _progress(msg):
        prefix = f"[Job {job_ids_seen[-1]}] " if job_ids_seen else ""
        messages.append(f"{prefix}{msg}")

    session = await fake_connect(
        profile,
        backend,
        progress_cb=_progress,
        on_job_submitted=_on_job_submitted,
    )

    assert len(job_ids_seen) == 1, "on_job_submitted should fire once"
    assert job_ids_seen[0] == "12345"
    assert any(
        "[Job 12345]" in m for m in messages
    ), f"messages should contain job prefix: {messages}"
    assert any("Connected" in m for m in messages)
    assert session.job_id == "12345"


async def test_submit_failure():
    profile = FakeProfile()
    backend = FakeSlurmBackend(fail_at="submit")

    with pytest.raises(ClusterError):
        await fake_connect(profile, backend, progress_cb=lambda m: None)


async def test_cancel_triggers_scancel():
    scancel_called = []
    profile = FakeProfile()
    backend = FakeSlurmBackend(job_id="99")

    orig_cancel = backend.cancel_job

    async def _mock_cancel(job_id):
        scancel_called.append(job_id)
        await orig_cancel(job_id)

    backend.cancel_job = _mock_cancel

    _job_id = None
    _submitted_event = asyncio.Event()

    def _on_job_submitted(jid):
        nonlocal _job_id
        _job_id = jid
        _submitted_event.set()

    async def _scancel(jid):
        await backend.cancel_job(jid)

    # Block after ping/pong so the task is still running when we cancel it.
    _recv_calls = 0

    async def _blocking_recv():
        nonlocal _recv_calls
        _recv_calls += 1
        if _recv_calls == 1:
            return "pong"
        await asyncio.sleep(3600)

    async def _connectTask():
        nonlocal _job_id
        try:
            await fake_connect(
                profile,
                backend,
                on_job_submitted=_on_job_submitted,
                poll_interval=0.02,
                ws_recv_fn=_blocking_recv,
            )
        except asyncio.CancelledError:
            if _job_id is not None:
                asyncio.create_task(_scancel(_job_id))
            raise

    task = asyncio.create_task(_connectTask())
    await _submitted_event.wait()  # cancel exactly after job submission
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    await asyncio.sleep(0.05)

    assert _job_id is not None, "job_id should be captured before cancel"
    assert scancel_called == ["99"], f"scancel job_id: {scancel_called}"


async def test_error_kwarg_propagated():
    # Simulate what _connectTask does: on failure, push TASK_PROGRESS error=True
    error_events = []

    def fake_event_push(event, taskID, message=None, error=False, **kw):
        if event == "TASK_PROGRESS":
            error_events.append({"message": message, "error": error})

    async def _connectTask(taskID="T1"):
        try:
            raise ClusterError("fake failure")
        except ClusterError as exc:
            fake_event_push(
                "TASK_PROGRESS",
                taskID,
                message=f"Connection failed: {exc}",
                error=True,
            )

    await _connectTask()

    assert len(error_events) == 1 and error_events[0]["error"]
    assert "fake failure" in error_events[0]["message"]

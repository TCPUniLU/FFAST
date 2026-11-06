"""Submitting a "Run with FFAST" job script: from the server's node, or from the laptop over SSH."""

from __future__ import annotations

import asyncio
import subprocess
import types

import pytest

from ffast.live.job_submit import SubmitError, slurm_free_env, submit_on_node


def _done(rc=0, out="Submitted batch job 5431234\n", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out.encode(), stderr=err.encode())


def test_server_submit_runs_sbatch_in_the_folder_without_the_server_jobs_slurm_variables():
    seen = {}

    def run(argv, **kw):
        seen.update(argv=argv, **kw)
        return _done()

    env = {"HOME": "/home/u", "PATH": "/usr/bin", "SLURM_MEM_PER_CPU": "2000", "SLURM_JOB_ID": "1",
           "SBATCH_ACCOUNT": "p1", "SLURMISH": "keep"}
    assert submit_on_node("/w/aspirin", "job.ffast.sh", run=run, environ=env) == "5431234"
    assert seen["argv"] == ["sbatch", "job.ffast.sh"] and seen["cwd"] == "/w/aspirin"
    assert seen["env"] == {"HOME": "/home/u", "PATH": "/usr/bin", "SLURMISH": "keep"}


def test_server_submit_reports_sbatch_errors():
    with pytest.raises(SubmitError, match="Invalid account"):
        submit_on_node("/w", "job.sh", run=lambda *a, **k: _done(1, "", "sbatch: error: Invalid account"),
                       environ={})

    def missing(*a, **k):
        raise FileNotFoundError("sbatch")

    with pytest.raises(SubmitError, match="not available"):
        submit_on_node("/w", "job.sh", run=missing, environ={})

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("sbatch", 60)

    with pytest.raises(SubmitError, match="did not answer"):
        submit_on_node("/w", "job.sh", run=slow, environ={})


def test_slurm_free_env_keeps_everything_else():
    assert slurm_free_env({"A": "1", "SLURM_X": "2", "SBATCH_Y": "3"}) == {"A": "1"}


def test_remote_submit_changes_folder_first_and_quotes(monkeypatch):
    from cluster.slurm import RemoteSlurmBackend

    backend = RemoteSlurmBackend("login.lxp.lu", "u")
    calls = []

    async def fake(*args, stdin=None):
        calls.append(args)
        return "Submitted batch job 77\n", "", 0

    monkeypatch.setattr(backend, "_run_remote", fake)
    job = asyncio.run(backend.submit_file("/project/home/it's here", "job.ffast.sh"))
    assert job == "77"
    assert calls == [("cd '/project/home/it'\"'\"'s here' && sbatch job.ffast.sh",)]


def test_remote_submit_error_carries_stderr(monkeypatch):
    from cluster.backend import ClusterError
    from cluster.slurm import RemoteSlurmBackend

    backend = RemoteSlurmBackend("login.lxp.lu")

    async def fake(*args, stdin=None):
        return "", "sbatch: error: Batch job submission failed: Invalid qos", 1

    monkeypatch.setattr(backend, "_run_remote", fake)
    with pytest.raises(ClusterError) as exc:
        asyncio.run(backend.submit_file("/w", "job.sh"))
    assert "Invalid qos" in exc.value.stderr


def _connected_broker(submit_file):
    from cluster.config import ClusterProfile
    from cluster.web_broker import ClusterBroker

    broker = ClusterBroker(config=types.SimpleNamespace(all_profiles=lambda: []), submit_file=submit_file)
    broker._session = types.SimpleNamespace(job_id="1", ssh_proc=types.SimpleNamespace(poll=lambda: None))
    broker._profile = ClusterProfile(name="mel", host="login.lxp.lu")
    return broker


def test_broker_submits_over_ssh_while_connected_through_a_profile(monkeypatch):
    from cluster import connection

    monkeypatch.setattr(connection, "load_session_records", lambda: [])
    got = []

    async def submit_file(profile, folder, script):
        got.append((profile.host, folder, script))
        return "991"

    broker = _connected_broker(submit_file)
    status, body = broker.handle("POST", "/cluster/submit", {"folder": "/w/aspirin", "script": "job.ffast.sh"})
    assert (status, body) == (200, {"job_id": "991", "via": "laptop"})
    assert got == [("login.lxp.lu", "/w/aspirin", "job.ffast.sh")]
    assert broker.state()["can_submit"] is True


def test_broker_refuses_without_a_profile_connection(monkeypatch):
    from cluster import connection
    from cluster.web_broker import ClusterBroker

    monkeypatch.setattr(connection, "load_session_records", lambda: [])
    broker = ClusterBroker(config=types.SimpleNamespace(all_profiles=lambda: []))
    status, body = broker.handle("POST", "/cluster/submit", {"folder": "/w", "script": "job.sh"})
    assert status == 400 and "server's node" in body["error"]
    assert broker.state()["can_submit"] is False


def test_broker_passes_sbatch_errors_to_the_page(monkeypatch):
    from cluster.backend import ClusterError

    async def submit_file(profile, folder, script):
        raise ClusterError("sbatch failed on login.lxp.lu (exit 1)", "Invalid qos")

    status, body = _connected_broker(submit_file).handle(
        "POST", "/cluster/submit", {"folder": "/w", "script": "job.sh"})
    assert status == 200 and body["error"] == "sbatch failed on login.lxp.lu (exit 1): Invalid qos"


def test_broker_wants_a_plain_script_name():
    status, body = _connected_broker(None).handle("POST", "/cluster/submit", {"folder": "/w", "script": "../x.sh"})
    assert status == 400

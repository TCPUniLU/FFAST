"""
ServerConnection and connect_to_cluster() — end-to-end remote connection flow.

Flow
----
1. Build JobSpec with command = "ffast-server --port <remote_port>"
2. SlurmBackend.submit_job() → job_id
3. Poll poll_status() until RUNNING (timeout → cancel + raise)
4. get_node_address() → node hostname
5. Spawn ssh -N -L local_port:node:remote_port user@host  (key auth only)
6. Retry websockets.connect() until tunnel is ready
7. Verify with ping/pong
8. Return ServerConnection

Cleanup
-------
ServerConnection.disconnect() closes the WebSocket and kills the SSH process.
The SLURM job keeps running until its time limit (server lifetime policy).
"""

import asyncio
import json
import logging
import os
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Optional

from ffast.protocol import control

logger = logging.getLogger("FFAST")

# ── session record persistence ────────────────────────────────────────────────
# Saved under ~/.ffast/sessions.json so the reconnect UI can find running jobs.

_SESSIONS_FILE = os.path.expanduser(
    os.path.join("~", ".ffast", "sessions.json")
)


def _load_session_records() -> list:
    """Read session records from ~/.ffast/sessions.json (returns [] on error)."""
    if not os.path.exists(_SESSIONS_FILE):
        return []
    try:
        with open(_SESSIONS_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _write_session_records(records: list) -> None:
    os.makedirs(os.path.dirname(_SESSIONS_FILE), exist_ok=True)
    try:
        with open(_SESSIONS_FILE, "w") as f:
            json.dump(records, f, indent=2)
    except Exception as exc:
        logger.warning("Failed to write sessions.json: %s", exc)


def save_session_record(
    job_id: str,
    profile_name: str,
    node: str,
    remote_port: int,
    token: str = "",
) -> None:
    """Upsert a session record for the given job."""
    records = _load_session_records()
    records = [r for r in records if r.get("job_id") != job_id]
    records.append(
        {
            "job_id": job_id,
            "profile_name": profile_name,
            "node": node,
            "remote_port": remote_port,
            "token": token,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
    )
    _write_session_records(records)
    logger.info("Session record saved: job=%s node=%s", job_id, node)


def recover_token_for_job(job_id: str) -> str:
    """Return the Session Token saved for ``job_id``, or "" if none is recorded.

    Used on reconnect to reclaim the CONTROLLING role (ADR 0012): the token is
    persisted at first connect by :func:`save_session_record`.
    """
    for rec in _load_session_records():
        if str(rec.get("job_id")) == str(job_id):
            return rec.get("token") or ""
    return ""


def delete_session_record(job_id: str) -> None:
    """Remove the session record for a job (call on user-initiated disconnect)."""
    records = _load_session_records()
    records = [r for r in records if r.get("job_id") != job_id]
    _write_session_records(records)
    logger.info("Session record deleted: job=%s", job_id)


def load_session_records() -> list:
    """Return all saved session records.  Used by the reconnect UI."""
    return _load_session_records()

_DEFAULT_REMOTE_PORT = 8765
_POLL_INTERVAL = 5        # seconds between squeue polls
# Seconds to wait for RUNNING state; None waits until the job starts or the
# user presses Cancel. MeluXina queued QoS-test jobs 0-15 min (2026-11-02),
# and the old 600 s cancelled the ones that would have started.
_POLL_TIMEOUT = None
_TUNNEL_RETRIES = 100      # WebSocket attempts (×delay below) — the window must
                          # cover one-time in-job provisioning (pip install)
                          # before the server starts listening (ADR 0028)
_TUNNEL_RETRY_DELAY = 3   # seconds between WebSocket connect retries → ~300s window


def _find_free_port() -> int:
    """Return an available local TCP port."""
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@dataclass
class ServerConnection:
    """The resources of one live tunnel to a cluster server: SSH process, WebSocket, ports."""

    job_id: str
    ssh_proc: subprocess.Popen
    websocket: object          # websockets.ClientConnection
    profile: object            # cluster.config.ClusterProfile
    local_port: int
    remote_port: int
    token_plaintext: str = ""  # plaintext sent in HELLO; "" = no token / READ_ONLY
    # The server's code fingerprint from HELLO_ACK; None from a server older
    # than the field (ffast.session.code_fingerprint).
    server_code_fingerprint: Optional[str] = None

    async def push_event(
        self, event: str, *args, **kwargs
    ) -> None:
        """Serialize and send an event to the remote ffast-server.

        Args must be msgpack-serializable primitives (str, int, float,
        list, dict, None).  Qt objects and numpy arrays must not be passed.

        Example::

            await session.push_event(
                "LOAD_DATASET",
                "/cluster/data/mol.xyz",
                "ase (auto)",
                slice_num=0,
            )
        """
        from ffast.protocol.rpc import pack

        data = pack(event, args, kwargs)
        await self.websocket.send(data)

    async def close_websocket(self) -> None:
        """Say goodbye and close the WebSocket, but keep the SSH tunnel open.

        The web launcher's broker connects only to check the server answers;
        the page then makes its own connection through the same tunnel
        (web parity plan 2026-10-29). GRACEFUL_DISCONNECT tells the server this
        close is on purpose. Best-effort, like disconnect().
        """
        try:
            await self.push_event(control.GRACEFUL_DISCONNECT)
        except Exception:
            pass
        try:
            await self.websocket.close()
        except Exception:
            pass

    async def disconnect(self) -> None:
        """
        Close WebSocket and kill SSH tunnel.
        SLURM job continues running until its time limit.

        This is the *intentional* shutdown path, so it first sends
        ``GRACEFUL_DISCONNECT`` — the server marks the disconnect as clean and
        skips the recovery-window hold it applies to unexpected drops (an
        unexpected drop never reaches here; the socket just dies). Best-effort:
        a send failure on an already-dead socket must not block the close.
        """
        await self.close_websocket()
        try:
            self.ssh_proc.terminate()
            self.ssh_proc.wait(timeout=5)
        except Exception:
            pass
        logger.info(
            "Disconnected from job %s "
            "(SSH tunnel closed; job still running on cluster)",
            self.job_id,
        )


async def _do_hello(websocket, token: str = "", renderer: str = "webgl") -> dict:
    """Send HELLO and await HELLO_ACK after ping/pong completes.

    Called by _establish_connection once the WebSocket is open and ping/pong has already been exchanged. Returns the HELLO_ACK
    fields ({} when none arrived).
    """
    from ffast.protocol.rpc import pack, unpack
    from ffast.visualization.protocol import ClientCapabilities, PROTOCOL_VERSION

    caps = ClientCapabilities(
        protocol_version=PROTOCOL_VERSION,
        renderer=renderer,
        session_token=token if token else None,
    )
    hello = pack(control.HELLO, [], caps.model_dump())
    await websocket.send(hello)

    try:
        ack_msg = await asyncio.wait_for(websocket.recv(), timeout=10)
        if isinstance(ack_msg, bytes):
            ack_event, _, ack_kwargs = unpack(ack_msg)
            if ack_event == control.HELLO_ACK:
                logger.info("HELLO_ACK received: role=%s", ack_kwargs.get("role"))
                return dict(ack_kwargs)
            else:
                logger.warning("Expected HELLO_ACK, got %r", ack_event)
        else:
            logger.warning("Expected binary HELLO_ACK, got text: %r", ack_msg)
    except asyncio.TimeoutError:
        logger.warning("HELLO_ACK not received within 10s — continuing as READ_ONLY")
    return {}


def _build_backend(profile):
    """Return RemoteSlurmBackend or SlurmBackend depending on profile.host."""
    from cluster.slurm import RemoteSlurmBackend, SlurmBackend

    if profile.host:
        return RemoteSlurmBackend(
            host=profile.host,
            username=profile.username,
            identity_file=profile.identity_file,
        )
    return SlurmBackend()


async def _establish_connection(
    profile,
    job_id: str,
    node: str,
    remote_port: int,
    progress_cb: Optional[Callable[[str], None]],
    token: str = "",
) -> ServerConnection:
    """Spawn SSH tunnel, connect WebSocket, verify with ping/pong.

    Shared implementation used by both connect_to_cluster and
    reconnect_to_cluster.  Both call this after resolving the node address
    by their own means (submit+poll vs. verify existing job).

    Parameters
    ----------
    profile : ClusterProfile
        Used for SSH credentials (host, username, identity_file).
    job_id : str
        SLURM job ID — used for logging and session record persistence.
    node : str
        Compute node hostname resolved by the caller (e.g. ``"gpu001"``).
    remote_port : int
        Port ffast-server is listening on inside the job.
    progress_cb : callable(str) | None
        Optional progress callback forwarded to the UI task progress system.

    Returns
    -------
    ServerConnection

    Raises
    ------
    OSError
        SSH tunnel died or WebSocket could not connect within the retry budget.
    """
    import websockets

    def _progress(msg: str) -> None:
        logger.info(msg)
        if progress_cb is not None:
            progress_cb(msg)

    # ── SSH port-forward ──────────────────────────────────────────────────
    local_port = _find_free_port()
    login_target = (
        f"{profile.username}@{profile.host}"
        if profile.username
        else profile.host
    )
    identity_file = os.path.expanduser(
        getattr(profile, "identity_file", "") or ""
    )
    # Forward local_port → compute_node:remote_port through the login node.
    # The login node TCP-connects to the compute node (no SSH auth to compute
    # node required; only the login node needs key auth).
    ssh_cmd = [
        "ssh",
        "-N",                             # no remote command
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",            # key-auth only, no password prompts
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=30",
    ]
    if identity_file:
        ssh_cmd += ["-i", identity_file]
    ssh_cmd += [
        "-L", f"{local_port}:{node}:{remote_port}",
        login_target,
    ]
    _progress(
        f"Opening SSH tunnel: localhost:{local_port}"
        f" → {node}:{remote_port} via {profile.host}"
    )
    ssh_proc = subprocess.Popen(
        ssh_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    # ── WebSocket connect (retry until tunnel ready) ──────────────────────
    ws_url = f"ws://localhost:{local_port}"
    _progress(f"Connecting to {ws_url}…")

    websocket = None
    last_exc: Optional[Exception] = None
    for attempt in range(1, _TUNNEL_RETRIES + 1):
        if ssh_proc.poll() is not None:
            stderr = ssh_proc.stderr.read().decode(errors="replace").strip()
            raise OSError(
                f"SSH tunnel exited unexpectedly "
                f"(exit {ssh_proc.returncode}).\n{stderr}"
            )
        try:
            websocket = await websockets.connect(ws_url, max_size=None)
            break
        except Exception as exc:
            last_exc = exc
            logger.debug(
                "WebSocket connect attempt %d/%d failed: %s",
                attempt, _TUNNEL_RETRIES, exc,
            )
            await asyncio.sleep(_TUNNEL_RETRY_DELAY)

    if websocket is None:
        ssh_proc.terminate()
        raise OSError(
            f"Could not connect to {ws_url} after "
            f"{_TUNNEL_RETRIES} attempts: {last_exc}"
        )

    # ── ping/pong verification ────────────────────────────────────────────
    _progress("Verifying connection…")
    try:
        await websocket.send("ping")
        reply = await asyncio.wait_for(websocket.recv(), timeout=10)
    except Exception as exc:
        ssh_proc.terminate()
        await websocket.close()
        raise OSError(f"Ping/pong handshake failed: {exc}") from exc

    if reply != "pong":
        ssh_proc.terminate()
        await websocket.close()
        raise OSError(f"Unexpected ping reply: {reply!r}")

    # ── HELLO/HELLO_ACK ───────────────────────────────────────────────────
    ack = await _do_hello(websocket, token)

    _progress("Connected!")
    logger.info(
        "ServerConnection established: job=%s node=%s local_port=%d",
        job_id, node, local_port,
    )

    save_session_record(job_id, profile.name, node, remote_port, token=token)

    return ServerConnection(
        job_id=job_id,
        ssh_proc=ssh_proc,
        websocket=websocket,
        profile=profile,
        local_port=local_port,
        remote_port=remote_port,
        token_plaintext=token,
        server_code_fingerprint=ack.get("code_fingerprint"),
    )


async def _wait_until_running(backend, job_id, poll_interval, poll_timeout=None) -> None:
    """Poll until the job runs. A job that fails or ends first raises;
    ``poll_timeout`` seconds (None: no limit) cancel it and raise."""
    from cluster.backend import ClusterError, JobStatus

    loop = asyncio.get_event_loop()
    deadline = None if poll_timeout is None else loop.time() + poll_timeout

    while True:
        status = await backend.poll_status(job_id)

        if status == JobStatus.RUNNING:
            return
        if status == JobStatus.FAILED:
            raise ClusterError(
                f"Job {job_id} failed before reaching RUNNING state"
            )
        if status == JobStatus.COMPLETED:
            raise ClusterError(
                f"Job {job_id} completed immediately — ffast-server exited early"
            )
        if deadline is not None and loop.time() > deadline:
            await backend.cancel_job(job_id)
            raise ClusterError(
                f"Timed out waiting {poll_timeout}s for job {job_id}"
            )

        await asyncio.sleep(poll_interval)


async def connect_to_cluster(
    profile,
    remote_port: int = _DEFAULT_REMOTE_PORT,
    poll_interval: float = _POLL_INTERVAL,
    poll_timeout: Optional[float] = _POLL_TIMEOUT,
    progress_cb: Optional[Callable[[str], None]] = None,
    on_job_submitted: Optional[Callable[[str], None]] = None,
) -> ServerConnection:
    """Full connect flow: SLURM submit → poll → SSH tunnel → WebSocket.

    Parameters
    ----------
    profile : ClusterProfile
        Connection + resource profile.
    remote_port : int
        Port ffast-server listens on inside the job (default 8765).
    poll_interval : float
        Seconds between squeue status checks.
    poll_timeout : float | None
        Maximum seconds to wait for the job to reach RUNNING state; None
        (the default) waits until it starts or the connect is cancelled.
    progress_cb : callable(str) | None
        Optional callback invoked with a human-readable progress message at
        each stage — useful for forwarding to the UI task progress system.
    on_job_submitted : callable(str) | None
        Called once with the SLURM job ID immediately after submission.
        Use this to capture the job ID for cancellation purposes.

    Returns
    -------
    ServerConnection

    Raises
    ------
    ClusterError
        SLURM submit failed, job failed/timed-out before RUNNING.
    OSError
        SSH tunnel died or WebSocket could not connect.
    """
    from cluster.backend import ClusterError, JobStatus

    def _progress(msg: str) -> None:
        logger.info(msg)
        if progress_cb is not None:
            progress_cb(msg)

    backend = _build_backend(profile)

    from ffast.session.token import SessionToken

    # Auto-bootstrap (ADR 0028): build+push the ffast wheel and provision a venv
    # on the login node, then launch the server from it. Falls back to the
    # manual ffast_server_cmd when provisioning is off.
    if getattr(profile, "provision", False):
        from cluster.bootstrap import provision_node

        server_cmd = await provision_node(profile, progress_cb=progress_cb)
    else:
        server_cmd = (
            getattr(profile, "ffast_server_cmd", "ffast-server") or "ffast-server"
        )
    snap_interval = getattr(profile, "snapshot_interval_minutes", 5)

    # Generate token before job submission so the hash can be embedded in the
    # server command and the plaintext can be sent in HELLO after connecting.
    session_token = SessionToken.generate()
    command = (
        f"{server_cmd} --port {remote_port}"
        f" --snapshot-interval {snap_interval}"
        f" --token-hash {session_token.hash}"
    )

    # ── 1. submit SLURM job ───────────────────────────────────────────────
    _progress("Submitting SLURM job…")
    spec = profile.to_job_spec(command)
    job_id = await backend.submit_job(spec)
    if on_job_submitted is not None:
        on_job_submitted(job_id)
    _progress(f"Job submitted: {job_id}")

    # ── 2. poll until RUNNING ─────────────────────────────────────────────
    _progress("Waiting for job to reach RUNNING state…")
    await _wait_until_running(backend, job_id, poll_interval, poll_timeout)

    # ── 3. resolve node address ───────────────────────────────────────────
    node = await backend.get_node_address(job_id)
    _progress(f"Job running on node: {node}")

    # ── 4–6. SSH tunnel → WebSocket → ping/pong → HELLO → ServerConnection ──
    return await _establish_connection(
        profile, job_id, node, remote_port, progress_cb, token=session_token.plaintext
    )


async def reconnect_to_cluster(
    profile,
    job_id: str,
    remote_port: int = _DEFAULT_REMOTE_PORT,
    progress_cb: Optional[Callable[[str], None]] = None,
    token: str = "",
) -> ServerConnection:
    """Reconnect to a RUNNING cluster job without submitting a new SLURM job.

    Use when the client disconnected (network blip, laptop sleep, etc.) but
    the server is still running.  Skips job submission and polling; goes
    directly to node resolution → SSH tunnel → WebSocket.

    Parameters
    ----------
    profile : ClusterProfile
        Connection profile (used for SSH credentials).
    job_id : str
        SLURM job ID of the already-running server.
    remote_port : int
        Port ffast-server is listening on inside the job (default 8765).
    progress_cb : callable(str) | None
        Optional progress callback for UI integration.

    Returns
    -------
    ServerConnection

    Raises
    ------
    ClusterError
        Job is no longer running.
    OSError
        SSH tunnel died or WebSocket could not connect.
    """
    from cluster.backend import ClusterError, JobStatus

    def _progress(msg: str) -> None:
        logger.info(msg)
        if progress_cb is not None:
            progress_cb(msg)

    # Recover the Session Token saved at first connect (ADR 0012) so we reclaim
    # the CONTROLLING role. Without it the HELLO is tokenless, the server grants
    # READ_ONLY, and the client cannot drive metric generation — plots stay
    # empty and phantom tasks never complete.
    if not token:
        token = recover_token_for_job(job_id)
        if token:
            _progress(f"Recovered session token for job {job_id} (reclaiming control)")
        else:
            logger.warning(
                "Reconnect: no saved token for job %s — connection will be READ_ONLY",
                job_id,
            )

    backend = _build_backend(profile)

    # ── 1. verify job still RUNNING ───────────────────────────────────────
    _progress(f"Verifying job {job_id} is still running…")
    status = await backend.poll_status(job_id)
    if status != JobStatus.RUNNING:
        raise ClusterError(
            f"Job {job_id} is no longer running (status: {status.name})"
        )

    # ── 2. resolve node address ───────────────────────────────────────────
    node = await backend.get_node_address(job_id)
    _progress(f"Job {job_id} running on node: {node}")

    # ── 3–5. SSH tunnel → WebSocket → ping/pong → HELLO → ServerConnection ──
    return await _establish_connection(
        profile, job_id, node, remote_port, progress_cb, token=token
    )

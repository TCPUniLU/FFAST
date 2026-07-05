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
from typing import TYPE_CHECKING, Callable, Optional

from ffast.protocol import control

if TYPE_CHECKING:
    from cluster.inbound_router import ListenerHandle

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
_POLL_TIMEOUT = 600       # seconds to wait for RUNNING state
_TUNNEL_RETRIES = 100      # WebSocket attempts (×delay below) — the window must
                          # cover one-time in-job provisioning (pip install)
                          # before the server starts listening (ADR 0028)
_TUNNEL_RETRY_DELAY = 3   # seconds between WebSocket connect retries → ~300s window


def _find_free_port() -> int:
    """Return an available local TCP port."""
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


# ── request/reply correlation ──────────────────────────────────────────────────


class PendingRequests:
    """Correlates async request/reply traffic over a single WebSocket.

    Every in-flight request registers a Future under a ``(channel, key)`` pair;
    the listener resolves it when the matching reply arrives.  This replaces the
    five hand-rolled ``_pending_*`` dicts (each duplicating create-future / store
    / await-with-timeout / pop), and — crucially — the correlation logic is pure
    data, so it is unit-testable without a live socket.

    ``channel`` namespaces the key space: two channels may legitimately use the
    same key (the key and length probes are both keyed by file path).
    """

    def __init__(self):
        self._pending: dict = {}

    def _inflight(self, channel, key):
        """Return a not-yet-done future for ``(channel, key)``, else ``None``."""
        fut = self._pending.get((channel, key))
        return fut if (fut is not None and not fut.done()) else None

    def resolve(self, channel, key, payload) -> bool:
        """Complete and remove the future for ``(channel, key)``.

        Returns ``True`` if a pending future was found and resolved, ``False``
        if there was no awaiter (an unexpected / duplicate reply).
        """
        fut = self._pending.pop((channel, key), None)
        if fut is not None and not fut.done():
            fut.set_result(payload)
            return True
        return False

    async def request(self, channel, key, send, *, timeout, coalesce=False):
        """Send a request and await its correlated reply.

        Parameters
        ----------
        channel, key
            Identify the reply this awaiter expects.  ``channel`` is the reply
            event name; ``key`` is whatever the reply carries to correlate by.
        send : callable() -> awaitable
            Pushes the request on the wire.  Called only for a fresh request,
            never on a coalesced join.
        timeout : float
            Seconds to wait for the reply.
        coalesce : bool
            When ``True``, an identical request already in flight is joined
            instead of re-sent — every awaiter is served by the one reply.
            Shielded so one awaiter timing out cannot cancel the shared future.
        """
        if coalesce:
            existing = self._inflight(channel, key)
            if existing is not None:
                return await asyncio.wait_for(
                    asyncio.shield(existing), timeout=timeout
                )

        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self._pending[(channel, key)] = fut
        await send()
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            # On reply, resolve() already popped us. On timeout the listener
            # never did (it only pops on a reply), so drop the dead future here
            # — otherwise a later identical request coalesces onto it forever.
            if self._pending.get((channel, key)) is fut:
                self._pending.pop((channel, key), None)


# ── reply-channel extractors ────────────────────────────────────────────────────
# Each maps a reply (args, kwargs) → (correlation key, payload). The listener is
# a thin router: look the event up here, extract, hand to PendingRequests.resolve.


def _reply_probe(args, kwargs):
    # DATASET_KEYS_RESPONSE / DATASET_LENGTH_RESPONSE: keyed by file path.
    return args[0], kwargs


def _reply_prediction(args, kwargs):
    from ffast.protocol.rpc import unpack_arrays

    return (args[0], args[1]), unpack_arrays(kwargs)


def _reply_metric(args, kwargs):
    from ffast.protocol.rpc import unpack_metric_result

    return args[0], unpack_metric_result(kwargs)


def _reply_dir_listing(args, kwargs):
    # DIR_LISTING: keyed by the ECHOED requested path (args[1]) so None→home
    # and relative inputs correlate; falls back to the abspath (args[0]) if an
    # older server did not echo the request.
    key = args[1] if len(args) > 1 else args[0]
    return key, kwargs


def _reply_arrays(args, kwargs):
    from ffast.protocol.rpc import unpack_arrays

    # model_names is a plain str→str dict packed alongside the arrays.
    return args[0], {
        "arrays": unpack_arrays(kwargs),
        "model_names": kwargs.get("model_names") or {},
    }


# reply event → (min args required, extractor). Keys are drift-tested against
# control.REPLY_EVENTS (ADR 0033) — see tests/ffast/test_control_events.py.
_REPLY_CHANNELS = {
    control.DATASET_KEYS_RESPONSE: (1, _reply_probe),
    control.DATASET_LENGTH_RESPONSE: (1, _reply_probe),
    control.PREDICTION_ARRAYS: (2, _reply_prediction),
    control.METRIC_RESULT: (1, _reply_metric),
    control.SUBDATASET_ARRAYS: (1, _reply_arrays),
    control.DIR_LISTING: (1, _reply_dir_listing),
}


@dataclass
class ServerConnection:
    """Holds all resources for one live remote connection."""

    job_id: str
    ssh_proc: subprocess.Popen
    websocket: object          # websockets.ClientConnection
    profile: object            # cluster.config.ClusterProfile
    local_port: int
    remote_port: int
    token_plaintext: str = ""  # plaintext sent in HELLO; "" = no token / READ_ONLY

    # ── array transfer cache ─────────────────────────────────────────────────
    # fingerprint → {R, F, z, n} dicts of numpy arrays
    _array_cache: dict = None
    # All in-flight request/reply correlation (arrays, probes, predictions,
    # metrics) lives in one channel-namespaced correlator. See PendingRequests.
    _pending: PendingRequests = None

    def __post_init__(self):
        self._array_cache = {}
        self._pending = PendingRequests()

    async def ping(self) -> bool:
        """Send ping, return True if pong received within 5 s."""
        try:
            await asyncio.wait_for(self.websocket.send("ping"), timeout=5)
            reply = await asyncio.wait_for(self.websocket.recv(), timeout=5)
            return reply == "pong"
        except Exception as exc:
            logger.warning("Ping failed: %s", exc)
            return False

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

    async def start_listener(self, local_env) -> "ListenerHandle":
        """Start a background task that forwards server events to local_env.

        Receives msgpack messages from the server and re-injects them into
        *local_env*'s event system via the Inbound Event Router (ADR 0032),
        which drives the local UI (progress bars, plot refreshes) from remote
        task activity.

        Returns a ``ListenerHandle`` — its ``cancel()``/``wait_done()`` are the
        listener's lifecycle API; callers must not reach into the task inside.

        Note: do not call ``ping()`` while the listener is running; both
        compete for the same websocket receive stream.
        """
        from ffast.protocol.rpc import CLIENT_ENV_SAFE, unpack
        from cluster.inbound_router import InboundEventRouter, ListenerHandle

        router = InboundEventRouter(local_env)

        async def _listen():
            try:
                async for message in self.websocket:
                    if not isinstance(message, bytes):
                        continue  # skip text messages (pong etc.)
                    try:
                        event, args, kwargs = unpack(message)
                        logger.debug(
                            "Listener received: %s args=%r", event, args
                        )

                        # ── correlated request/reply ──────────────────────
                        # All five reply channels route through one correlator.
                        # The listener is a thin router: extract the key, resolve
                        # the awaiting future. Replies never forward to env.
                        channel = _REPLY_CHANNELS.get(event)
                        if channel is not None:
                            min_args, extract = channel
                            if len(args) >= min_args:
                                key, payload = extract(args, kwargs)
                                if not self._pending.resolve(
                                    event, key, payload
                                ):
                                    logger.warning(
                                        "Listener: %s for unknown key %r",
                                        event, key,
                                    )
                            else:
                                logger.warning(
                                    "Listener: malformed %s (args=%r)",
                                    event, args,
                                )
                            await asyncio.sleep(0)
                            continue  # do NOT forward to env

                        if event not in CLIENT_ENV_SAFE:
                            logger.debug(
                                "Listener: skipping non-local-safe event %s",
                                event,
                            )
                            continue

                        await router.route(event, args, kwargs)
                        # Yield to the Qt/asyncio event loop so that widget
                        # changes from this event are painted before the next
                        # event is processed.
                        await asyncio.sleep(0)
                    except Exception as exc:
                        logger.warning(
                            "Listener decode error: %s", exc
                        )
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("Listener terminated: %s", exc)

        return ListenerHandle(asyncio.create_task(_listen()))

    async def request_subdataset_arrays(
        self, fingerprint: str, timeout: float = 300.0
    ) -> dict:
        """Request numpy arrays for a dataset from the remote server.

        Sends REQUEST_SUBDATASET_ARRAYS and awaits the SUBDATASET_ARRAYS
        response.  Result is cached so repeated calls for the same fingerprint
        are instant.

        Parameters
        ----------
        fingerprint : str
            Server-side dataset fingerprint.
        timeout : float
            Maximum seconds to wait for the server response (default 300 s —
            large SubDatasets can take a while to serialise).

        Returns
        -------
        dict with two top-level keys:

        ``arrays`` : dict
            Coordinate/element arrays (keys R/F/z for uniform or
            R_flat/F_flat/z_flat/offsets for variable datasets) plus any
            cached prediction arrays prefixed ``pred__<dtype>__<model_fp>``.
        ``model_names`` : dict[str, str]
            Server-side model fingerprint → human-readable name for each
            model whose prediction data was included in ``arrays``.
        """
        # Return cached result immediately
        if fingerprint in self._array_cache:
            logger.info("Array cache hit for %r", fingerprint)
            return self._array_cache[fingerprint]

        logger.info("Requesting arrays for %r from server…", fingerprint)
        arrays = await self._pending.request(
            control.SUBDATASET_ARRAYS, fingerprint,
            lambda: self.push_event(control.REQUEST_SUBDATASET_ARRAYS, fingerprint),
            timeout=timeout,
        )
        self._array_cache[fingerprint] = arrays
        logger.info("Arrays cached for %r", fingerprint)
        return arrays

    async def request_prediction_arrays(
        self, dataset_fp: str, model_fp: str, timeout: float = 60.0
    ) -> dict:
        """Request prediction arrays (energy/forces) for a (dataset, model) pair.

        Uses the dedicated Prediction-Only Array Channel — geometry arrays are
        not re-transferred.  Sends ``REQUEST_PREDICTION_ARRAYS`` and awaits
        the ``PREDICTION_ARRAYS`` response.

        Parameters
        ----------
        dataset_fp : str
            Server-side dataset fingerprint.
        model_fp : str
            Ghost model fingerprint.
        timeout : float
            Maximum seconds to wait for the server response (default 60 s).

        Returns
        -------
        dict
            Arrays keyed as ``pred__energy__<model_fp>`` and/or
            ``pred__forces__<model_fp>``.
        """
        # Coalesce concurrent identical requests. Every panel metric fetches the
        # same (dataset, model) predictions at once; without coalescing each call
        # would overwrite the single pending future, orphaning the others (they
        # hang until timeout) and the duplicate replies log "for unknown".
        logger.info(
            "Requesting prediction arrays: dataset=%r model=%r",
            dataset_fp[:8], model_fp[:8],
        )
        return await self._pending.request(
            control.PREDICTION_ARRAYS, (dataset_fp, model_fp),
            lambda: self.push_event(
                control.REQUEST_PREDICTION_ARRAYS, dataset_fp, model_fp
            ),
            timeout=timeout, coalesce=True,
        )

    async def request_metric(
        self, metric_id, params, model_fp, dataset_fp, key, timeout: float = 120.0
    ):
        """Ask the server to compute a metric and return its MetricResult (4a).

        Server-owned metric computation: the server resolves inputs from its
        full arrays (+ ghost/remote predictions), runs the metric, and replies
        with ``METRIC_RESULT``.  Returns the reconstructed ``MetricResult`` or
        ``None`` when the server couldn't compute it (e.g. a real client-only
        model), in which case the caller falls back to in-process computation.
        """
        return await self._pending.request(
            control.METRIC_RESULT, key,
            lambda: self.push_event(
                control.REQUEST_METRIC, metric_id, key,
                params=params or {}, model_fp=model_fp, dataset_fp=dataset_fp,
            ),
            timeout=timeout,
        )

    async def probe_dataset_length(
        self, path: str, timeout: float = 60.0
    ) -> dict:
        """Ask server to count frames in a dataset file.

        Sends PROBE_DATASET_LENGTH and awaits DATASET_LENGTH_RESPONSE.

        Returns
        -------
        dict with keys:
            n     : int | None  — total frame count, or None on error
            error : str | None  — set if server-side probe failed
        """
        logger.info("Probing dataset length for %r", path)
        return await self._pending.request(
            control.DATASET_LENGTH_RESPONSE, path,
            lambda: self.push_event(control.PROBE_DATASET_LENGTH, path),
            timeout=timeout,
        )

    async def probe_dataset_keys(
        self, path: str, typ: str, timeout: float = 30.0
    ) -> dict:
        """Ask server to probe available energy/force key names for a file.

        Sends PROBE_DATASET_KEYS and awaits DATASET_KEYS_RESPONSE.  The server
        reads only the first frame so the round-trip is fast (<1 s normally).

        Parameters
        ----------
        path : str
            Cluster-side path to the dataset file.
        typ : str
            Dataset type string (e.g. ``"ase (auto)"``).
        timeout : float
            Maximum seconds to wait for the server response (default 30 s).

        Returns
        -------
        dict with keys:
            energy_keys : list[str]
            force_keys  : list[str]
            has_calculator_energy : bool
            has_calculator_forces : bool
            error : str | None  — set if server-side probe failed
        """
        logger.info("Probing dataset keys for %r (type=%s)", path, typ)
        return await self._pending.request(
            control.DATASET_KEYS_RESPONSE, path,
            lambda: self.push_event(control.PROBE_DATASET_KEYS, path, typ),
            timeout=timeout,
        )

    async def list_dir(self, path: str | None = None, timeout: float = 30.0) -> dict:
        """Ask the server to list a directory in *its own* filesystem.

        Sends LIST_DIR and awaits DIR_LISTING. ``path=None`` starts at the
        server user's home directory. Because the server lists the filesystem
        *it* can see (the cluster compute node, ADR 0028), any file the browser
        shows is one the server can actually open — unlike an SFTP browse of the
        login node, whose view can differ.

        Returns
        -------
        dict with keys:
            path    : str            — resolved absolute path listed
            parent  : str | None     — parent dir, None at filesystem root
            home    : str            — server user's home directory
            entries : list[dict]     — {name, is_dir, size}, dirs first
            error   : str | None     — set if the listing failed
        """
        return await self._pending.request(
            control.DIR_LISTING, path,
            lambda: self.push_event(control.LIST_DIR, path),
            timeout=timeout,
        )

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
        try:
            await self.push_event(control.GRACEFUL_DISCONNECT)
        except Exception:
            pass
        try:
            await self.websocket.close()
        except Exception:
            pass
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


async def _do_hello(websocket, token: str = "", renderer: str = "vispy") -> None:
    """Send HELLO and await HELLO_ACK after ping/pong completes.

    Called by both connect_direct and _establish_connection once the WebSocket
    is open and ping/pong has already been exchanged.
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
            else:
                logger.warning("Expected HELLO_ACK, got %r", ack_event)
        else:
            logger.warning("Expected binary HELLO_ACK, got text: %r", ack_msg)
    except asyncio.TimeoutError:
        logger.warning("HELLO_ACK not received within 10s — continuing as READ_ONLY")


async def connect_direct(
    host: str = "localhost",
    port: int = 8765,
    token: str = "",
    renderer: str = "vispy",
) -> "ServerConnection":
    """Connect directly to a running ffast-server without SLURM or SSH.

    Use this for local testing or when LocalServerManager started the server:

        token = SessionToken.generate()
        handle = LocalServerManager().start(port, token)
        session = await connect_direct(port=handle.port, token=token.plaintext)

    Returns a ServerConnection with job_id="local" and ssh_proc=None.
    """
    import websockets

    url = f"ws://{host}:{port}"
    logger.info("connect_direct: connecting to %s", url)

    websocket = await websockets.connect(url, max_size=None)

    # ping/pong
    await websocket.send("ping")
    reply = await asyncio.wait_for(websocket.recv(), timeout=10)
    if reply != "pong":
        await websocket.close()
        raise OSError(f"Unexpected ping reply: {reply!r}")

    # HELLO/HELLO_ACK
    await _do_hello(websocket, token, renderer)

    logger.info("connect_direct: connected to %s", url)
    return ServerConnection(
        job_id="local",
        ssh_proc=None,
        websocket=websocket,
        profile=None,
        local_port=port,
        remote_port=port,
        token_plaintext=token,
    )


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
    await _do_hello(websocket, token)

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
    )


async def connect_to_cluster(
    profile,
    remote_port: int = _DEFAULT_REMOTE_PORT,
    poll_interval: float = _POLL_INTERVAL,
    poll_timeout: float = _POLL_TIMEOUT,
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
    poll_timeout : float
        Maximum seconds to wait for the job to reach RUNNING state.
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
    loop = asyncio.get_event_loop()
    deadline = loop.time() + poll_timeout

    while True:
        status = await backend.poll_status(job_id)

        if status == JobStatus.RUNNING:
            break
        if status == JobStatus.FAILED:
            raise ClusterError(
                f"Job {job_id} failed before reaching RUNNING state"
            )
        if status == JobStatus.COMPLETED:
            raise ClusterError(
                f"Job {job_id} completed immediately — ffast-server exited early"
            )
        if loop.time() > deadline:
            await backend.cancel_job(job_id)
            raise ClusterError(
                f"Timed out waiting {poll_timeout}s for job {job_id}"
            )

        await asyncio.sleep(poll_interval)

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

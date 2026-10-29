"""The web client's way to a cluster (web parity plan 2026-10-29).

A browser page cannot run ssh. The ffast-web launcher can, so it keeps one
ClusterBroker. The page asks it, with small JSON requests under /cluster/ on
the launcher's own web port, to list profiles and to submit or find a job and
open the SSH tunnel. The broker then gives the page the tunnel's local address
and the session token, and the page connects to the cluster's ffast-server
itself. All SSH/SLURM work is cluster/connection.py, the desktop's code.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import threading

from cluster import connection as _conn
from cluster.backend import ClusterError
from cluster.config import ClusterConfig, ClusterProfile

logger = logging.getLogger("FFAST")

_MAX_MESSAGES = 50


class BrokerBusy(RuntimeError):
    """A connection is already open or opening; the page must end it first."""


def profile_from_fields(fields: dict, *, need_name: bool = False) -> ClusterProfile:
    """A ClusterProfile from the page's form values.

    Text is trimmed, numbers are parsed, `modules` may be a list or one string
    split on commas and spaces. Empty text gets the desktop form's defaults.
    Raises ValueError with a sentence the page can show.
    """
    known = {f.name: f for f in dataclasses.fields(ClusterProfile)}
    unknown = sorted(set(fields) - set(known))
    if unknown:
        raise ValueError(f"Unknown profile field: {', '.join(unknown)}")
    values: dict = {}
    for key, raw in fields.items():
        default = known[key].default
        if default is dataclasses.MISSING:
            default = None
        if key == "modules":
            if raw is None:
                values[key] = []
            elif isinstance(raw, str):
                values[key] = [m for m in re.split(r"[,\s]+", raw) if m]
            else:
                values[key] = [str(m).strip() for m in raw if str(m).strip()]
        elif isinstance(default, bool):
            values[key] = bool(raw)
        elif isinstance(default, int):
            try:
                number = int(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a whole number") from None
            if number < 0:
                raise ValueError(f"{key} cannot be negative")
            values[key] = number
        else:
            values[key] = str(raw or "").strip()
    if need_name and not values.get("name"):
        raise ValueError("Give the profile a name")
    values.setdefault("name", "")
    for key, text in (("ffast_server_cmd", "ffast-server"), ("job_name", "ffast"),
                      ("time_limit", "01:00:00"), ("venv_path", "~/.ffast/venv")):
        values[key] = values.get(key) or text
    return ClusterProfile(**values)


async def _scancel(profile, job_id: str) -> None:
    """Best-effort cancel of a SLURM job (never raises)."""
    try:
        await _conn._build_backend(profile).cancel_job(job_id)
        logger.info("scancel %s succeeded", job_id)
    except Exception as exc:
        logger.warning("scancel %s failed: %s", job_id, exc)


async def _job_log(profile, job_id) -> str:
    """The SLURM job log's tail when FFAST was being installed in the job
    (ADR 0028), else ''. Never raises."""
    if not getattr(profile, "provision", False) or not job_id:
        return ""
    try:
        from cluster.bootstrap import tail_job_log
        return await tail_job_log(profile, job_id)
    except Exception as exc:
        logger.warning("Could not fetch SLURM job log for %s: %s", job_id, exc)
        return ""


def _idle_status() -> dict:
    return {"state": "idle", "profile_name": "", "job_id": None, "reconnect": False,
            "messages": [], "error": "", "details": "", "ws_url": "", "token": ""}


class ClusterBroker:
    """Cluster profiles, Session Records and one cluster connection, for the
    web page (web parity plan 2026-10-29). Safe to call from the web server's
    request threads."""

    def __init__(self, config: ClusterConfig | None = None, *, connect=None,
                 reconnect=None, cancel_job=None, job_log=None):
        self._config = config if config is not None else ClusterConfig()
        self._lock = threading.Lock()
        self._status = _idle_status()
        self._connect = connect or _conn.connect_to_cluster
        self._reconnect = reconnect or _conn.reconnect_to_cluster
        self._cancel_job = cancel_job or _scancel
        self._job_log = job_log or _job_log
        self._session = None   # the ServerConnection whose tunnel is open
        self._profile = None
        self._future = None
        self._quitting = False
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True,
                         name="ffast-cluster").start()

    def _set(self, **fields) -> None:
        with self._lock:
            self._status.update(fields)

    def _say(self, msg) -> None:
        """Log a progress line and keep the last few for the page."""
        logger.info("cluster: %s", msg)
        with self._lock:
            messages = self._status["messages"]
            messages.append(str(msg))
            del messages[:-_MAX_MESSAGES]

    def _check_tunnel(self) -> None:
        """A tunnel that closed behind our back is a failed connection; the
        Session Record stays so the job can be found again."""
        with self._lock:
            session = self._session
            if session is None or session.ssh_proc.poll() is None:
                return
            self._session = None
            self._status.update(
                state="failed", ws_url="", token="",
                error=f"The SSH tunnel to job {session.job_id} closed. "
                      "Reconnect to use the job again.")

    def state(self) -> dict:
        """Everything the cluster dialog shows: profiles, Session Records
        (without tokens) and the connection status."""
        self._check_tunnel()
        records = [{k: v for k, v in r.items() if k != "token"}
                   for r in _conn.load_session_records()]
        with self._lock:
            status = dict(self._status, messages=list(self._status["messages"]))
        return {"profiles": [dataclasses.asdict(p) for p in self._config.all_profiles()],
                "records": records, "status": status}

    def save_profile(self, fields: dict) -> None:
        self._config.add(profile_from_fields(fields, need_name=True))

    def delete_profile(self, name: str) -> None:
        self._config.delete(str(name or ""))

    def connect(self, fields: dict, reconnect_job_id: str | None = None) -> None:
        """Start submitting a new job, or finding `reconnect_job_id` again."""
        profile = profile_from_fields(fields)
        if not profile.host:
            raise ValueError("No host specified in the profile.")
        with self._lock:
            if self._status["state"] in ("connecting", "connected"):
                raise BrokerBusy("A cluster connection is already open or opening. "
                                 "End it first.")
            self._status = dict(_idle_status(), state="connecting",
                                profile_name=profile.name, job_id=reconnect_job_id,
                                reconnect=reconnect_job_id is not None)
            self._profile = profile
        self._future = asyncio.run_coroutine_threadsafe(
            self._run(profile, reconnect_job_id), self._loop)

    async def _run(self, profile, reconnect_job_id) -> None:
        """The desktop's connect rules (ConnectionManager.connectToCluster)."""
        job_id = reconnect_job_id

        def submitted(jid):
            nonlocal job_id
            job_id = jid
            self._set(job_id=jid)

        try:
            if reconnect_job_id is not None:
                session = await self._reconnect(profile, reconnect_job_id,
                                                progress_cb=self._say)
            else:
                session = await self._connect(profile, progress_cb=self._say,
                                              on_job_submitted=submitted)
            await session.close_websocket()   # the page makes its own
            with self._lock:
                self._session = session
            self._set(state="connected", job_id=session.job_id,
                      ws_url=f"ws://127.0.0.1:{session.local_port}",
                      token=session.token_plaintext or "")
            self._say(f"Connected to job {session.job_id}")
        except asyncio.CancelledError:
            if job_id is not None and reconnect_job_id is None and not self._quitting:
                asyncio.get_running_loop().create_task(self._cancel_job(profile, job_id))
            self._set(state="idle")
            self._say("Cancelled")
            raise
        except ClusterError as exc:
            if getattr(exc, "stderr", ""):
                logger.error("  remote stderr:\n%s", exc.stderr)
            if reconnect_job_id is not None:
                _conn.delete_session_record(reconnect_job_id)   # stale record (ADR 0024)
            await self._failed(profile, job_id, exc)
        except Exception as exc:   # OSError and anything unexpected
            if not isinstance(exc, OSError):
                logger.exception("Unexpected error during cluster connect")
            if reconnect_job_id is None and job_id is not None:
                await self._cancel_job(profile, job_id)
            await self._failed(profile, job_id, exc)

    async def _failed(self, profile, job_id, exc) -> None:
        details = await self._job_log(profile, job_id)
        self._set(state="failed", error=f"Connection failed: {exc}", details=details)

    def cancel(self) -> None:
        """Stop a connect in progress (a job we submitted is cancelled)."""
        if self._future is not None and not self._future.done():
            self._future.cancel()
            self._say("Cancelling…")

    def stop(self) -> None:
        """End cluster job: close the tunnel and cancel the job, also one found
        again by reconnecting (the record goes, so it could not be found)."""
        with self._lock:
            session, profile = self._session, self._profile
        if session is None:
            self.cancel()
            return
        asyncio.run_coroutine_threadsafe(
            self._end(session, profile), self._loop).result(timeout=60)

    async def _end(self, session, profile) -> None:
        await session.disconnect()
        await self._cancel_job(profile, session.job_id)
        _conn.delete_session_record(session.job_id)
        with self._lock:
            self._session = None
            self._status = _idle_status()
        self._say(f"Ended job {session.job_id}")

    def shutdown(self) -> None:
        """Launcher exit: close the tunnel, keep the job and its record
        (ADR 0024)."""
        self._quitting = True
        if self._future is not None and not self._future.done():
            self._future.cancel()
        with self._lock:
            session, self._session = self._session, None
        if session is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    session.disconnect(), self._loop).result(timeout=10)
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)

    def handle(self, method: str, path: str, body: dict) -> tuple[int, dict]:
        """Answer one /cluster/ request: (HTTP status, JSON body)."""
        try:
            if method == "GET" and path == "/cluster/state":
                return 200, self.state()
            if method == "POST" and path == "/cluster/profile":
                self.save_profile(body.get("profile") or {})
                return 200, self.state()
            if method == "POST" and path == "/cluster/profile/delete":
                self.delete_profile(body.get("name"))
                return 200, self.state()
            if method == "POST" and path == "/cluster/connect":
                self.connect(body.get("profile") or {}, body.get("reconnect_job_id"))
                return 200, self.state()
            if method == "POST" and path == "/cluster/cancel":
                self.cancel()
                return 200, self.state()
            if method == "POST" and path == "/cluster/stop":
                self.stop()
                return 200, self.state()
            return 404, {"error": "Unknown cluster request"}
        except ValueError as exc:
            return 400, {"error": str(exc)}
        except BrokerBusy as exc:
            return 409, {"error": str(exc)}
        except Exception as exc:
            logger.exception("Cluster request %s %s failed", method, path)
            return 500, {"error": str(exc)}

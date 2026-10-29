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


def _idle_status() -> dict:
    return {"state": "idle", "profile_name": "", "job_id": None, "reconnect": False,
            "messages": [], "error": "", "details": "", "ws_url": "", "token": ""}


class ClusterBroker:
    """Cluster profiles, Session Records and one cluster connection, for the
    web page (web parity plan 2026-10-29). Safe to call from the web server's
    request threads."""

    def __init__(self, config: ClusterConfig | None = None):
        self._config = config if config is not None else ClusterConfig()
        self._lock = threading.Lock()
        self._status = _idle_status()

    def state(self) -> dict:
        """Everything the cluster dialog shows: profiles, Session Records
        (without tokens) and the connection status."""
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
            return 404, {"error": "Unknown cluster request"}
        except ValueError as exc:
            return 400, {"error": str(exc)}

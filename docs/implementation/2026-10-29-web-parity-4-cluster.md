# Web client connects to a cluster: implementation plan (8 tasks)

Part of the web parity plan 2026-10-29. The desktop can connect to a SLURM
cluster (File menu, Ctrl+Shift+C): saved cluster profiles, a SLURM job that
runs `ffast-server`, an SSH tunnel to it, automatic install of FFAST on the
cluster (ADR 0028), and reconnecting to a job that is still running (ADR
0024). The web client can only connect to a server address that is already
reachable. This plan closes that gap.

A browser page cannot open SSH connections. So the SSH and SLURM work runs in
the `ffast-web` launcher's own Python process, which already serves the page.
The page asks it over plain HTTP. All SSH/SLURM code is the existing
`cluster/` code; nothing about SSH is written in JavaScript.

Do the tasks **in the order given**, one commit per task. Each task leaves the
app working.

---

## The design (ADR 0071)

Read `docs/adr/0071-the-web-launcher-opens-cluster-connections.md` first.
The user accepted it on 2026-10-29. This plan says how to build it.

---

## Decisions (confirmed by the user 2026-10-29)

1. **The `ffast-web` launcher does the SSH and SLURM work** (the Cluster
   Broker, Tasks 2–4). The local `ffast-server` gets no SSH code.
2. **Profiles live in the desktop's file, `config/clusters.json`** next to
   the `cluster` package (the `ClusterConfig()` default). No migration.
3. **"End cluster job" always cancels the job**, also a job the page found
   again by reconnecting (the record is deleted, so a job left running could
   not be found again). Task 3 `stop()`.
4. **Alt+Shift+C (Option+Shift+C on a Mac) opens "Connect to Cluster…".**
   Ctrl+Shift+C stays with the browser's developer tools. Plan 3's
   `FILE_SHORTCUTS` table (`static/shortcuts.js`,
   `2026-10-29-web-parity-3-workspace.md` Task 8) already holds the entry
   `{ action: 'connect-cluster', code: 'KeyC' }`; this plan only adds the
   File-menu action with exactly that id, `connect-cluster`. Task 6.
5. **A reloaded page goes back to a live cluster connection** before the
   URL's local server. Task 8.
6. **The web form shows the server-setup fields** (`provision`, `modules`,
   `venv_path`, `snapshot_interval_minutes`), which the desktop form hides,
   **in a folded section "Server setup", closed by default**, with one line
   of help per field and the defaults filled in. The user approved the
   dialog sketch in Task 5 on 2026-10-29.

---

## Rules for the whole job

1. **Do not touch** files this plan does not name. In particular leave
   `CONTEXT.md`, `docs/adr/*`, every
   `docs/implementation/*` file, the desktop files (`UI/*.py`,
   `ffast/core/connection_manager.py`) and every untracked file (`to_fix*.md`,
   `docs/research/...`) alone: the user commits them. Stage with
   `git add <exact paths>`, never `git add -A` or `git add .`.
2. The user may commit while you work. Run `git status --short` and
   `git log --oneline -3` before each commit. Never `git reset --hard`, never
   rebase, never amend a commit you did not just make.
3. Commit messages: subject starts with `feat:` (or `fix:` for a fix), in
   plain words about what the user sees; see `git log --oneline -20` for the
   style. Body: 2–4 short lines on what changed and why, naming "web parity
   plan 2026-10-29". **No `Co-Authored-By` line.**
4. Tests run with the project venv, never `uv run`:
   ```
   ~/.venvs/ffast_env/bin/python -m pytest <file> -q
   ```
   `-n 6` is on by default (pyproject). Add `-n 0` only to debug one test.
   Browser tests (under `tests/ffast/renderers/web/`) need Playwright's
   Chromium and are slow: run only the tests named in the task (`-k`).
5. After each task, run the fast suite once:
   ```
   ~/.venvs/ffast_env/bin/python -m pytest -m "not integration" -q
   ```
   It must stay green (one known order-dependent red test is described in
   `docs/issues/0067-01-*.md`; ignore only that one).
6. Match the surrounding code: a short docstring or JSDoc comment above each
   new function, saying *why* in plain words; 2-space indent in JS; the same
   naming style as the file. Write "web parity plan 2026-10-29" in a comment
   where a reader would ask "why is it like this?", not on every line.
7. If the plan does not match the code (a line moved, a name differs), find
   the place by searching for the quoted code, not by line number. If the plan
   is wrong about **behaviour**, stop and report; don't invent another design.
8. **Never run real `ssh`, `sbatch` or `scancel` in a test.** Every test
   injects fake connect/reconnect/cancel functions (shown below).
9. **Build the dialog as the sketch in Task 5 shows it.** The user approved
   the sketch on 2026-10-29, and ADR 0071 is accepted. If the code forces a
   different layout, stop and report.
10. **Plan 3 comes first.** This plan is built after
   `2026-10-29-web-parity-3-workspace.md`. Task 6 relies on the
   `static/shortcuts.js` that plan 3's Task 8 creates, with its
   `connect-cluster` entry. If that file or entry does not exist, stop and
   report.

Paths are relative to the repo root. `static/` means
`ffast/renderers/web/static/`.

Words used below:
- **Tunnel**: the `ssh -N -L <local port>:<node>:<remote port> <login>`
  process. While it runs, `ws://127.0.0.1:<local port>` reaches the
  `ffast-server` inside the SLURM job.
- **Session Record**: one entry in `~/.ffast/sessions.json` (job id, profile
  name, node, port, token, time) so a running job can be found again.
- **Broker**: the new `ClusterBroker` object in the launcher process.

---

## Task 1: the cluster connection can drop its own WebSocket and keep the tunnel

**What the user sees:** nothing; this is groundwork.

**Facts.** `cluster/connection.py`, class `ServerConnection` (a dataclass with
`job_id`, `ssh_proc`, `websocket`, `profile`, `local_port`, `remote_port`,
`token_plaintext`, `server_code_fingerprint`):

```python
    async def disconnect(self) -> None:
        """
        Close WebSocket and kill SSH tunnel.
        ...
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
        logger.info(...)
```

`connect_to_cluster` and `reconnect_to_cluster` both end in
`_establish_connection`, which opens the tunnel, connects a WebSocket,
does ping/pong and HELLO (with the token), **saves the Session Record**, and
returns a `ServerConnection`. The web page must make its own WebSocket, so the
broker needs to close the Python one but keep `ssh_proc` running.

**Change** (only `cluster/connection.py`):

1. Add to `ServerConnection`, just above `disconnect`:
   ```python
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
   ```
2. In `disconnect`, replace its two first `try` blocks with
   `await self.close_websocket()`. Keep the `ssh_proc` block and the log line.
   Keep the docstring.

**Test.** New file `tests/ffast/test_cluster_web_broker.py` (the later tasks
add to it). Module docstring: "The web launcher's cluster broker (web parity
plan 2026-10-29). No real SSH: every SSH/SLURM step is a fake."

- `test_close_websocket_keeps_the_tunnel` (async): build
  `ServerConnection(job_id="J1", ssh_proc=proc, websocket=ws, profile=None, local_port=1, remote_port=2)`
  with `ws = mock.MagicMock(); ws.send = mock.AsyncMock(); ws.close = mock.AsyncMock()`
  and `proc = mock.MagicMock()`. `await conn.close_websocket()`. Assert
  `ws.close.await_count == 1`, `proc.terminate.call_count == 0`, and that the
  one sent message unpacks (with `ffast.protocol.rpc.unpack`) to event
  `"GRACEFUL_DISCONNECT"`.
- `test_disconnect_still_closes_both`: same setup, `await conn.disconnect()`,
  assert `ws.close` awaited and `proc.terminate` called once.

Run: `tests/ffast/test_cluster_web_broker.py`, `tests/ffast/test_connect_panel.py`,
`tests/ffast/test_session_records.py`.

**Commit:** `feat: a cluster connection can close its WebSocket and keep the SSH tunnel`.

---

## Task 2: the broker lists, saves and deletes cluster profiles

**What the user sees:** nothing yet; the launcher's half of the profile list.

**Facts.**
- `cluster/config.py`: `ClusterProfile` is a dataclass. Fields and defaults:
  `name: str` (no default), `host=""`, `username=""`, `identity_file=""`,
  `ffast_server_cmd="ffast-server"`, `partition=""`, `account=""`, `qos=""`,
  `job_name="ffast"`, `cores=1`, `cpus_per_task=0`, `ntasks_per_node=0`,
  `gpus_per_task=0`, `gpu_count=0`, `memory_mb=4096`, `time_limit="01:00:00"`,
  `snapshot_interval_minutes=5`, `provision=False`,
  `modules: list[str] = field(default_factory=list)`, `venv_path="~/.ffast/venv"`.
- `ClusterConfig(path=_CLUSTERS_JSON)` loads on construction; `add(profile)`
  (adds or overwrites by name, then saves), `delete(name)`, `get(name)`,
  `all_profiles()`. Save errors are only logged.
- The desktop form's empty-field defaults
  (`UI/ClusterProfileDialog.py` `_read_fields`): empty `ffast_server_cmd` →
  `"ffast-server"`, empty `job_name` → `"ffast"`, empty `time_limit` →
  `"01:00:00"`; a bad integer → the default.
- `cluster/connection.py`: `load_session_records()` returns the list from
  `~/.ffast/sessions.json` (each a dict with `job_id`, `profile_name`, `node`,
  `remote_port`, `token`, `timestamp`); the file path is the module global
  `_SESSIONS_FILE` (tests redirect it with `monkeypatch.setattr`, see
  `tests/ffast/test_session_records.py` `_redirect`).

**Change.** New file `cluster/web_broker.py`. Write this part now; Task 3 adds
the connect part to the same class.

```python
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
```

Body of `profile_from_fields`:
1. `known = {f.name: f for f in dataclasses.fields(ClusterProfile)}`. Unknown
   keys → `ValueError("Unknown profile field: a, b")` (sorted, joined by
   `", "`).
2. For each given key: `default = f.default` (or `None` when it is
   `dataclasses.MISSING`).
   - `modules`: a `str` → `[m for m in re.split(r"[,\s]+", raw) if m]`; a list
     → `[str(m).strip() for m in raw if str(m).strip()]`; `None` → `[]`.
   - `isinstance(default, bool)` (check **before** int; `bool` is an `int`):
     `bool(raw)`.
   - `isinstance(default, int)`: `int(raw)`; on `TypeError`/`ValueError`
     raise `ValueError(f"{key} must be a whole number")`; a negative number
     → `ValueError(f"{key} cannot be negative")`.
   - else: `str(raw or "").strip()`.
3. `name = values.get("name", "")`; `need_name` and empty →
   `ValueError("Give the profile a name")`. Always
   `values.setdefault("name", "")`.
4. Defaults for empty text: `ffast_server_cmd` → `"ffast-server"`, `job_name`
   → `"ffast"`, `time_limit` → `"01:00:00"`, `venv_path` → `"~/.ffast/venv"`
   (only when the key is given and empty, or not given: use
   `values[k] = values.get(k) or default_text`).
5. `return ClusterProfile(**values)`.

The class (Task 3 adds the rest of `__init__` and the connect methods):

```python
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

    def save_profile(self, fields: dict) -> None:
        self._config.add(profile_from_fields(fields, need_name=True))

    def delete_profile(self, name: str) -> None:
        self._config.delete(str(name or ""))

    def handle(self, method: str, path: str, body: dict) -> tuple[int, dict]:
        """Answer one /cluster/ request: (HTTP status, JSON body)."""
```

- `_idle_status()` (module function) returns
  `{"state": "idle", "profile_name": "", "job_id": None, "reconnect": False, "messages": [], "error": "", "details": "", "ws_url": "", "token": ""}`.
  `state` is one of `idle`, `connecting`, `connected`, `failed`.
- `state()`:
  ```python
  records = [{k: v for k, v in r.items() if k != "token"}
             for r in _conn.load_session_records()]
  with self._lock:
      status = dict(self._status, messages=list(self._status["messages"]))
  return {"profiles": [dataclasses.asdict(p) for p in self._config.all_profiles()],
          "records": records, "status": status}
  ```
  Call `_conn.load_session_records()` through the module (not a
  `from … import`), so a test's `_SESSIONS_FILE` redirect applies.
- `handle()` routes (`path` has no query string):
  - `GET /cluster/state` → `(200, self.state())`
  - `POST /cluster/profile` with `{"profile": {...}}` → `save_profile`, then
    `(200, self.state())`
  - `POST /cluster/profile/delete` with `{"name": "..."}` → `delete_profile`,
    then `(200, self.state())`
  - anything else → `(404, {"error": "Unknown cluster request"})`
  - Wrap the call: `ValueError` → `(400, {"error": str(exc)})`. Task 3 adds
    `BrokerBusy` → 409.

**Test.** In `tests/ffast/test_cluster_web_broker.py`. A fixture:
```python
@pytest.fixture
def broker(tmp_path, monkeypatch):
    monkeypatch.setattr(conn, "_SESSIONS_FILE", str(tmp_path / ".ffast" / "sessions.json"))
    b = ClusterBroker(ClusterConfig(str(tmp_path / "clusters.json")))
    yield b
```
(`import cluster.connection as conn`). Task 3 adds `b.shutdown()` after the
`yield`.
- `test_profile_from_fields_parses_numbers_and_modules`:
  `profile_from_fields({"name": " m ", "cores": "4", "modules": "Python, CUDA  x", "provision": True})`
  → `name == "m"`, `cores == 4`, `modules == ["Python", "CUDA", "x"]`,
  `provision is True`, `job_name == "ffast"`.
- `test_profile_from_fields_refuses_bad_input`: unknown key, `cores="x"`,
  `memory_mb=-1`, and `need_name=True` with no name each raise `ValueError`
  (use `match=` on the messages above).
- `test_saved_profiles_come_back_in_state`: `handle("POST", "/cluster/profile", {"profile": {"name": "meluxina", "host": "login.lxp.lu"}})`
  → 200; `state()["profiles"][0]["host"] == "login.lxp.lu"`; a new
  `ClusterBroker(ClusterConfig(same path))` sees it too; delete → gone.
- `test_records_are_listed_without_tokens`: `conn.save_session_record("42", "meluxina", "node07", 8765, token="tok")`
  → `state()["records"] == [{… "job_id": "42" …}]` with no `"token"` key.
- `test_unknown_request_is_404_and_bad_profile_is_400`.

Run: `tests/ffast/test_cluster_web_broker.py`.

**Commit:** `feat: the web launcher's cluster broker lists and saves cluster profiles`.

---

## Task 3: the broker opens, cancels and ends a cluster connection

**What the user sees:** nothing yet; the launcher's half of connecting.

**Facts.** The desktop's rules are in `ffast/core/connection_manager.py`
`ConnectionManager.connectToCluster` (read it, **do not change it**):
- new job: `connect_to_cluster(profile, progress_cb=..., on_job_submitted=...)`;
  reconnect: `reconnect_to_cluster(profile, job_id, progress_cb=...)` (it
  recovers the token from the Session Record by itself).
- Cancelled before connected, a job **we submitted** is cancelled with
  `scancel` (desktop `_scancel`, via `RemoteSlurmBackend(...).cancel_job`),
  unless the app is quitting.
- `ClusterError` (job failed, job dead): on a **reconnect**,
  `delete_session_record(reconnect_job_id)` (stale record purge, ADR 0024).
  With `profile.provision`, the SLURM job log is fetched with
  `cluster.bootstrap.tail_job_log(profile, job_id)` (see
  `_log_provision_diagnostics`).
- `OSError` (tunnel / WebSocket failed): a **new** job is cancelled; a
  reconnect keeps its record (the job may still be alive).
- Any other exception: logged with `logger.exception`, treated like `OSError`.
- `cluster/connection.py` `_build_backend(profile)` returns the right SLURM
  backend (`RemoteSlurmBackend` when `profile.host` is set).

**Change** (`cluster/web_broker.py`):

1. Module helpers:
   ```python
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
   ```
2. `__init__` gains keyword-only arguments, so tests can pass fakes:
   `connect=None, reconnect=None, cancel_job=None, job_log=None`, stored as
   `self._connect = connect or _conn.connect_to_cluster`,
   `self._reconnect = reconnect or _conn.reconnect_to_cluster`,
   `self._cancel_job = cancel_job or _scancel`,
   `self._job_log = job_log or _job_log`. Also
   `self._session = None` (the `ServerConnection` whose tunnel is open),
   `self._profile = None`, `self._future = None`, `self._quitting = False`,
   and a private event loop on a daemon thread:
   ```python
   self._loop = asyncio.new_event_loop()
   threading.Thread(target=self._loop.run_forever, daemon=True,
                    name="ffast-cluster").start()
   ```
3. Small private helpers, all under `self._lock`:
   `_set(**fields)` updates `self._status`; `_say(msg)` logs it and appends
   to `messages`, keeping the last `_MAX_MESSAGES`.
4. `connect(self, fields: dict, reconnect_job_id: str | None = None) -> None`:
   - `profile = profile_from_fields(fields)`; no `profile.host` →
     `ValueError("No host specified in the profile.")` (the desktop's text).
   - Under the lock: if `self._status["state"]` is `connecting` or
     `connected` → `raise BrokerBusy("A cluster connection is already open or opening. End it first.")`.
     Else set `self._status = dict(_idle_status(), state="connecting", profile_name=profile.name, job_id=reconnect_job_id, reconnect=reconnect_job_id is not None)`
     and `self._profile = profile`.
   - `self._future = asyncio.run_coroutine_threadsafe(self._run(profile, reconnect_job_id), self._loop)`.
5. `async def _run(self, profile, reconnect_job_id)`: the port of the desktop
   rules above.
   ```python
   job_id = reconnect_job_id

   def submitted(jid):
       nonlocal job_id
       job_id = jid
       self._set(job_id=jid)

   try:
       if reconnect_job_id is not None:
           session = await self._reconnect(profile, reconnect_job_id, progress_cb=self._say)
       else:
           session = await self._connect(profile, progress_cb=self._say,
                                         on_job_submitted=submitted)
       await session.close_websocket()   # the page makes its own (Task 1)
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
           _conn.delete_session_record(reconnect_job_id)
       await self._failed(profile, job_id, exc)
   except Exception as exc:   # OSError and anything unexpected
       if not isinstance(exc, OSError):
           logger.exception("Unexpected error during cluster connect")
       if reconnect_job_id is None and job_id is not None:
           await self._cancel_job(profile, job_id)
       await self._failed(profile, job_id, exc)
   ```
   `_failed(profile, job_id, exc)`: `details = await self._job_log(profile, job_id)`;
   `self._set(state="failed", error=f"Connection failed: {exc}", details=details)`.
6. `cancel(self) -> None`: if `self._future` is set and not done,
   `self._future.cancel()` (this cancels the task on the loop) and
   `self._say("Cancelling…")`.
7. `stop(self) -> None` ("End cluster job", decision 3):
   - Read `session`, `profile` under the lock. `session is None` →
     `self.cancel()`; return.
   - Else run on the loop and wait:
     `asyncio.run_coroutine_threadsafe(self._end(session, profile), self._loop).result(timeout=60)`.
   - `async def _end(...)`: `await session.disconnect()` (closes the tunnel;
     its GRACEFUL_DISCONNECT on the already-closed socket is swallowed),
     `await self._cancel_job(profile, session.job_id)`,
     `_conn.delete_session_record(session.job_id)`; then under the lock
     `self._session = None`, `self._status = _idle_status()`, and
     `self._say(f"Ended job {session.job_id}")`.
8. `shutdown(self) -> None` (launcher exit; ADR 0024: keep job and record):
   `self._quitting = True`; cancel a running `_future`; if a session is open,
   `asyncio.run_coroutine_threadsafe(session.disconnect(), self._loop).result(timeout=10)`
   inside `try/except Exception`; then
   `self._loop.call_soon_threadsafe(self._loop.stop)`.
9. Tunnel watch: at the top of `state()`, call `self._check_tunnel()`: when
   `self._session` is set and `self._session.ssh_proc.poll() is not None`,
   set `self._session = None` and
   `state="failed", error=f"The SSH tunnel to job {job} closed. Reconnect to use the job again.", ws_url="", token=""`.
   Keep the record.
10. `handle()` gains:
    - `POST /cluster/connect` with `{"profile": {...}, "reconnect_job_id": str | null}`
      → `self.connect(body.get("profile") or {}, body.get("reconnect_job_id"))`,
      `(200, self.state())`.
    - `POST /cluster/cancel` → `cancel()`, `(200, self.state())`.
    - `POST /cluster/stop` → `stop()`, `(200, self.state())`.
    - `BrokerBusy` → `(409, {"error": str(exc)})`.
    - Any other exception from a route: `logger.exception(...)` and
      `(500, {"error": str(exc)})`.

**Test.** In `tests/ffast/test_cluster_web_broker.py`. Fakes:

```python
class _FakeSession:
    def __init__(self, job_id="J1", local_port=4321, token="tok"):
        self.job_id, self.local_port, self.token_plaintext = job_id, local_port, token
        self.ssh_proc = mock.MagicMock(); self.ssh_proc.poll.return_value = None
        self.closed_ws = False; self.disconnected = False
    async def close_websocket(self): self.closed_ws = True
    async def disconnect(self): self.disconnected = True

def _wait(broker, state, timeout=5.0):
    """Poll state() until status.state is `state` (the broker runs on its own thread)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = broker.state()["status"]
        if s["state"] == state:
            return s
        time.sleep(0.02)
    raise AssertionError(f"still {broker.state()['status']}")
```
A helper `_broker(tmp_path, monkeypatch, **fakes)` builds a broker like the
fixture, with `cancel_job=` a recorder by default
(`cancelled = []`; `async def cancel_job(p, jid): cancelled.append(jid)`), and
`job_log=` an async fake returning `""`. Call `b.shutdown()` at the end of
each test (or in the fixture after `yield`).

- `test_connect_gives_the_page_the_tunnel`: fake connect calls
  `on_job_submitted("J1")`, `progress_cb("Submitting SLURM job…")`, returns
  `_FakeSession()`. `handle("POST", "/cluster/connect", {"profile": {"name": "m", "host": "h"}})`
  → 200. `_wait(b, "connected")` has `ws_url == "ws://127.0.0.1:4321"`,
  `token == "tok"`, `job_id == "J1"`, `"Submitting SLURM job…" in messages`;
  the session's `closed_ws` is True and `disconnected` False.
- `test_a_second_connect_is_refused`: connect waits on an `asyncio.Event`
  never set; a second POST → 409.
- `test_a_profile_without_host_is_refused`: 400, "No host".
- `test_cancel_before_running_cancels_the_submitted_job`: connect calls
  `on_job_submitted("J9")` then `await asyncio.sleep(30)`; POST
  `/cluster/cancel` → `_wait(b, "idle")`; wait until `cancelled == ["J9"]`.
- `test_a_dead_job_on_reconnect_purges_its_record`: save a record for
  `"J5"`; fake reconnect raises `ClusterError("Job J5 is no longer running")`;
  POST connect with `"reconnect_job_id": "J5"` → `_wait(b, "failed")`,
  error mentions "no longer running", no record for J5, `cancelled == []`.
- `test_a_tunnel_failure_on_reconnect_keeps_the_record`: reconnect raises
  `OSError("refused")` → failed, record J5 still there, `cancelled == []`.
- `test_a_tunnel_failure_on_a_new_job_cancels_it`: connect calls
  `on_job_submitted("J7")` then raises `OSError` → failed, `cancelled == ["J7"]`.
- `test_install_failure_shows_the_job_log`: profile with
  `"provision": True`; connect submits "J8" then raises
  `ClusterError("job failed")`; `job_log` fake returns `"pip error"` →
  `details == "pip error"`.
- `test_end_job_closes_the_tunnel_cancels_and_forgets`: connected; save a
  record for J1; POST `/cluster/stop` → idle, `session.disconnected`,
  `cancelled == ["J1"]`, no record J1.
- `test_shutdown_keeps_the_job_and_its_record`: connected, record J1 saved,
  `b.shutdown()` → `session.disconnected`, `cancelled == []`, record J1 kept.
- `test_a_dead_tunnel_shows_as_failed`: connected, then
  `session.ssh_proc.poll.return_value = 255` → `state()["status"]["state"] == "failed"`
  and `"tunnel" in error`.

Run: `tests/ffast/test_cluster_web_broker.py`, `tests/ffast/test_connect_panel.py`.

**Commit:** `feat: the web launcher's cluster broker submits or finds a job and keeps its SSH tunnel`.

---

## Task 4: the launcher answers /cluster/ requests

**What the user sees:** nothing in the page yet. `ffast-web` now owns a
cluster broker; closing `ffast-web` closes any tunnel but keeps the job.

**Facts.**
- `ffast/renderers/web/serve.py`: `_SilentStaticHandler(SimpleHTTPRequestHandler)`
  has `do_GET` with one special path:
  ```python
      def do_GET(self):
          ...
          if self.path.split("?", 1)[0] == "/code-fingerprint.json":
              ...
              return
          super().do_GET()
  ```
  There is no `do_POST` (the base answers 501).
  `start_static_server(port, host="0.0.0.0")` builds
  `_StaticHTTPServer((host, port), _SilentStaticHandler)` and starts it on a
  daemon thread. A handler reaches its server as `self.server`.
- `ffast/renderers/web/launcher.py` `run(...)`:
  `httpd = _start_web_app(web_port, host)`, then `spawn_server`, then
  `url = app_url(web_port, ws_port, host=host, uid=uid)` (which makes a uuid
  when `uid` is None), then blocks on `proc.wait()` and in `finally`
  calls `_terminate(proc)` and `httpd.shutdown()`. On a server that died at
  startup it calls `httpd.shutdown()` and raises `LauncherError`.
  `LaunchResult` is a dataclass `url, web_port, ws_port, httpd, proc`.
  `_start_web_app(web_port, host)` calls `start_static_server(...)` in two or
  three places.
- `tests/ffast/test_ffast_core_boundary.py` asserts the exact set of lazy
  imports from `ffast/` into flat packages like `cluster`:
  `ALLOWED_LAZY = {("ffast/core/connection_manager.py", "cluster.backend"), ...}`.
- `ffast-server --web-port` also calls `start_static_server` (from
  `server.py`); it must **not** get a broker.

**Change.**

1. `serve.py`:
   - `start_static_server(port, host="0.0.0.0", *, cluster_broker=None, cluster_secret=None)`:
     after creating `httpd`, set `httpd.cluster_broker = cluster_broker` and
     `httpd.cluster_secret = cluster_secret`. Docstring: only the `ffast-web`
     launcher passes a broker; the secret is the page's `launch=` id.
   - In the handler, a helper `_send_json(self, status: int, body: dict)`
     (Content-Type `application/json`, Content-Length, `end_headers`, write).
   - `_cluster_request(self, method: str)`:
     ```python
     broker = getattr(self.server, "cluster_broker", None)
     if broker is None:
         return self._send_json(404, {"error": "This page was not opened by ffast-web, so it cannot reach a cluster."})
     secret = getattr(self.server, "cluster_secret", None)
     if not secret or self.headers.get("X-FFAST-Launch") != secret:
         return self._send_json(403, {"error": "Not allowed"})
     body = {}
     if method == "POST":
         n = int(self.headers.get("Content-Length") or 0)
         if n > 65536:
             return self._send_json(413, {"error": "Request too large"})
         try:
             body = json.loads(self.rfile.read(n) or b"{}")
         except ValueError:
             return self._send_json(400, {"error": "Not JSON"})
         if not isinstance(body, dict):
             return self._send_json(400, {"error": "Not a JSON object"})
     status, reply = broker.handle(method, self.path.split("?", 1)[0], body)
     self._send_json(status, reply)
     ```
     Comment: the custom header is what stops another web site in the same
     browser from using the broker (a cross-site page cannot send it without
     a CORS preflight, which this server never allows), and the secret stops
     a DNS-rebinding page.
   - `do_GET`: before the fingerprint check,
     `if self.path.startswith("/cluster/"): return self._cluster_request("GET")`.
   - New `do_POST`: `/cluster/` → `self._cluster_request("POST")`, else
     `self.send_error(404)`.
2. `launcher.py`:
   - Module function:
     ```python
     def _make_cluster_broker():
         """The cluster broker the page uses to reach a cluster (web parity
         plan 2026-10-29), or None when the cluster package cannot load."""
         try:
             from cluster.web_broker import ClusterBroker
         except ImportError as exc:
             logger.info("Cluster connections are off: %s", exc)
             return None
         return ClusterBroker()
     ```
   - `LaunchResult` gains `broker: object = None` (last field).
   - `run(...)` gains `broker_factory: Callable[[], object] = _make_cluster_broker`.
     At the top: `uid = uid or uuid.uuid4().hex` and `broker = broker_factory()`.
     `_start_web_app(web_port, host, broker=broker, secret=uid)` passes them on
     to every `start_static_server(...)` call inside it. On the "server died
     at startup" path and in the final `finally`, call `broker.shutdown()`
     when `broker` is not None (before `httpd.shutdown()`). Return
     `LaunchResult(..., broker=broker)`.
   - Update the module docstring: one sentence that the launcher also keeps
     the cluster broker, so the page can reach a cluster.
3. `tests/ffast/test_ffast_core_boundary.py`: add
   `("ffast/renderers/web/launcher.py", "cluster.web_broker"),` to
   `ALLOWED_LAZY` and a numbered comment line `2. the web launcher -> cluster.web_broker:
   the page's way to a cluster (web parity plan 2026-10-29).`

**Test.** `tests/ffast/renderers/web/test_launcher.py`:
- `test_the_page_reaches_the_cluster_broker_only_with_the_launch_id`:
  `broker = ClusterBroker(ClusterConfig(str(tmp_path / "c.json")))`;
  `httpd = start_static_server(launcher.pick_free_port(), host="127.0.0.1", cluster_broker=broker, cluster_secret="s3")`.
  With `urllib.request`: GET `/cluster/state` with header
  `X-FFAST-Launch: s3` → 200 and JSON has `"profiles"`; without the header →
  `HTTPError` 403; POST `/cluster/profile` with the header and body
  `{"profile": {"name": "m", "host": "h"}}` → 200 and the profile is listed.
  `finally`: `httpd.shutdown(); broker.shutdown()`. Redirect
  `cluster.connection._SESSIONS_FILE` to `tmp_path` with `monkeypatch`.
- `test_a_server_without_a_broker_answers_404`: `start_static_server(..., host="127.0.0.1")`
  → GET `/cluster/state` (any header) → 404.
- In `test_run_serves_the_app_and_opens_browser`, pass a recording
  `broker_factory` returning a fake with a `shutdown()` that records; assert
  `result.broker` is that fake. In `test_run_aborts_when_server_dies_during_startup`
  pass the same kind of fake and assert `shutdown` was called.

Run: `tests/ffast/renderers/web/test_launcher.py`,
`tests/ffast/test_ffast_core_boundary.py`, `tests/test_packaging.py`,
`tests/ffast/renderers/web/test_web_server.py`.

**Commit:** `feat: ffast-web keeps a cluster broker the page can ask over HTTP`.

---

## Task 5: the page's cluster client and its pure rules

**What the user sees:** nothing yet.

**The approved sketch** (user, 2026-10-29). It is the whole cluster dialog
as Tasks 6 and 7 build it. The "Server setup" section is folded (closed) when
the dialog opens; the sketch shows it opened. `[▾]` is a drop-down list,
`[x]` a tick box, `[ ... ]` a text field, `( ... )` a button.

```
┌─ Connect to Cluster ─────────────────────────────────────────────┐
│ Connection failed: Job 123 is no longer running.  (red, if any)  │
│                                                                  │
│ Profile [ meluxina          ▾]  (Save profile)  (Delete)         │
│                                                                  │
│ ┌ Profile ─────────────────────────────────────────────────────┐ │
│ │ Profile name     [ meluxina                                ] │ │
│ └──────────────────────────────────────────────────────────────┘ │
│ ┌ SSH ─────────────────────────────────────────────────────────┐ │
│ │ Host             [ login.lxp.lu                            ] │ │
│ │ Username         [ u100123                                 ] │ │
│ │ Identity file    [ ~/.ssh/id_ed25519 (blank: ssh-agent / ..] │ │
│ │ Server command   [ ffast-server                            ] │ │
│ └──────────────────────────────────────────────────────────────┘ │
│ ┌ Scheduler ───────────────────────────────────────────────────┐ │
│ │ Partition [ cpu      ]  Account [ p200xxx ]  QoS [ default ] │ │
│ │ Job name  [ ffast    ]                                       │ │
│ └──────────────────────────────────────────────────────────────┘ │
│ ┌ Resources ───────────────────────────────────────────────────┐ │
│ │ Cores [ 8 ]  CPUs/task [ 0 ]  Ntasks/node [ 0 ]  GPUs/task [0]│ │
│ │ Memory (MB) [ 4096 ]   Time limit [ 01:00:00 ]               │ │
│ └──────────────────────────────────────────────────────────────┘ │
│ ▸ Server setup                       (folded; click to open)     │
│                                                                  │
│   …and the same section once opened:                             │
│ ┌ ▾ Server setup ──────────────────────────────────────────────┐ │
│ │ [x] Install FFAST in the job automatically                   │ │
│ │     Builds FFAST here and installs it in the job; the        │ │
│ │     Server command above is then not used.                   │ │
│ │ Modules to load  [ Python CUDA                             ] │ │
│ │     Names for "module load", space-separated; used when      │ │
│ │     installing.                                              │ │
│ │ Environment folder [ ~/.ffast/venv                         ] │ │
│ │     Folder on the cluster for FFAST's Python environment;    │ │
│ │     used when installing.                                    │ │
│ │ Auto-save every (minutes) [ 5 ]                              │ │
│ │     The server saves its session this often; 0 = never.      │ │
│ └──────────────────────────────────────────────────────────────┘ │
│                                                                  │
│ Job 123 (started 2026-10-29T10:00:00) may still be running.      │
│                                           (Reconnect to 123)     │
│ ┌ progress ────────────────────────────────────────────────────┐ │
│ │ Submitting SLURM job…                                        │ │
│ │ Waiting for job to reach RUNNING state…                      │ │
│ └──────────────────────────────────────────────────────────────┘ │
│                                                                  │
│ (End cluster job)                 (Close)  (Submit new job)      │
└──────────────────────────────────────────────────────────────────┘
```
The record line and the progress box show only when they have something to
say (Task 7). With no record, the last button reads **Connect**; while
connecting it reads **Cancel**.


**Facts.**
- `static/recent_servers.js` is the model for a small module with pure
  exports and a JSDoc header.
- `tests/ffast/renderers/web/test_web_pure_helpers.py`: `CASES` maps a name
  to a JS expression; the module imports are at the bottom
  (`const tr = await import('{origin}/tab_rules.js');` …); each test asserts
  `results[key]`.
- `UI/menuLogic.py` `latest_session_record(records, profile_name)`: the last
  record whose `profile_name` matches, or None.

**Change.** New file `static/cluster_client.js`:

```js
/**
 * The page's side of cluster connections (web parity plan 2026-10-29).
 * The ffast-web launcher does the SSH and SLURM work; the page asks it with
 * JSON requests under /cluster/, carrying the launch id from its own URL.
 */
```

Exports:

1. `CLUSTER_FIELDS`: an array of
   `{ key, label, group, type, placeholder?, help? }`, `type` one of `text`,
   `int`, `bool`, `list`, in this order (labels as on the desktop; `help`
   only where given below):
   - group `Profile`: `name` "Profile name" (text).
   - group `SSH`: `host` "Host" (placeholder `login.example.com`),
     `username` "Username" (`your_username`), `identity_file` "Identity file"
     (`~/.ssh/id_ed25519 (blank: ssh-agent / default)`), `ffast_server_cmd`
     "Server command" (`ffast-server`).
   - group `Scheduler`: `partition`, `account`, `qos` ("QoS"), `job_name`
     ("Job name", placeholder `ffast`).
   - group `Resources`: `cores` "Cores", `cpus_per_task` "CPUs/task",
     `ntasks_per_node` "Ntasks/node", `gpus_per_task` "GPUs/task",
     `memory_mb` "Memory (MB)" (all int), `time_limit` "Time limit"
     (placeholder `HH:MM:SS`).
   - group `Server setup` (decision 6; Task 6 folds it): each with a one-line
     `help`, the texts in the sketch above:
     - `provision` "Install FFAST in the job automatically" (bool), help
       "Builds FFAST here and installs it in the job; the Server command
       above is then not used." (`cluster/connection.py`: with
       `profile.provision` the command comes from `provision_node`, else from
       `ffast_server_cmd`.)
     - `modules` "Modules to load" (list, placeholder `Python CUDA`), help
       "Names for \"module load\", space-separated; used when installing."
       (`cluster/bootstrap.py` `server_launch_cmd` writes
       `module load {m}` per name.)
     - `venv_path` "Environment folder" (text), help "Folder on the cluster
       for FFAST's Python environment; used when installing."
     - `snapshot_interval_minutes` "Auto-save every (minutes)" (int), help
       "The server saves its session this often; 0 = never." (`cluster/config.py`:
       "Auto-snapshot interval in minutes (0 = disabled)"; it becomes
       `--snapshot-interval` on the server command.)
2. `emptyProfile()`: a fresh object with the `ClusterProfile` defaults (see
   Task 2 Facts; `gpu_count` is not in the form, leave it out). The form
   for "New profile" is filled from it, so the defaults show as values, not
   only as placeholders (`provision` unticked, `venv_path` `~/.ffast/venv`,
   `snapshot_interval_minutes` `5`, `job_name` `ffast`, `memory_mb` `4096`,
   `time_limit` `01:00:00`, …).
3. `profileFromForm(values)`: `values` maps key → raw input (strings, or a
   boolean for `bool`). Returns an object: `text` trimmed; `int` →
   `parseInt(v, 10)`, and when that is `NaN` the `emptyProfile()` value;
   `bool` → `!!v`; `list` → split on `/[\s,]+/`, empty parts dropped. The
   server checks again; this only makes the request tidy.
4. `formValues(profile)`: the reverse for filling the form (`list` joined
   with `' '`, numbers as strings).
5. `latestRecord(records, profileName)`: port of `latest_session_record`.
6. `launchId(search)`: `new URLSearchParams(search).get('launch')` or null.
7. `class ClusterClient`:
   ```js
   constructor(launch, fetchFn = (...a) => fetch(...a)) { ... }
   /** GET /cluster/state; rejects with the server's error text. */
   state() { return this._ask('GET', '/cluster/state'); }
   saveProfile(profile) { return this._ask('POST', '/cluster/profile', { profile }); }
   deleteProfile(name) { return this._ask('POST', '/cluster/profile/delete', { name }); }
   connect(profile, reconnectJobId = null) {
     return this._ask('POST', '/cluster/connect', { profile, reconnect_job_id: reconnectJobId });
   }
   cancel() { return this._ask('POST', '/cluster/cancel', {}); }
   stop() { return this._ask('POST', '/cluster/stop', {}); }
   ```
   `_ask(method, path, body)`: `fetchFn(path, { method, headers: {'X-FFAST-Launch': this._launch, ...(body ? {'Content-Type': 'application/json'} : {})}, body: body ? JSON.stringify(body) : undefined })`;
   parse JSON (`.catch(() => ({}))`); not `ok` → `throw Object.assign(new Error(data.error || `HTTP ${r.status}`), { status: r.status })`;
   else return `data`. Note: a `Content-Type: application/json` request is
   same-origin here, so it needs no preflight.

**Test.** `test_web_pure_helpers.py`: add
`const cc = await import('{origin}/cluster_client.js');` next to the other
imports, and a section `# ── cc: cluster client (web parity plan 2026-10-29) ──`:
- `profileFromForm({name:' m ', cores:'4', memory_mb:'x', modules:'Python, CUDA', provision:true})`
  → `name == "m"`, `cores == 4`, `memory_mb == 4096`,
  `modules == ["Python", "CUDA"]`, `provision is True`.
- `formValues(profileFromForm(...))` round trip for `modules` → `"Python CUDA"`.
- `latestRecord([{profile_name:'a', job_id:'1'}, {profile_name:'b', job_id:'2'}, {profile_name:'a', job_id:'3'}], 'a').job_id`
  → `"3"`; no match → `null`.
- `launchId('?port=1&launch=abc')` → `"abc"`; `launchId('?port=1')` → `null`.
- `cc_server_setup`: `CLUSTER_FIELDS.filter((f) => f.group === 'Server setup').map((f) => [f.key, Boolean(f.help)])`
  → `[["provision", true], ["modules", true], ["venv_path", true], ["snapshot_interval_minutes", true]]`.
- `cc_empty_defaults`: `[emptyProfile().venv_path, emptyProfile().snapshot_interval_minutes, emptyProfile().provision]`
  → `["~/.ffast/venv", 5, false]`.
- `ClusterClient` with a fake fetch: an expression that builds
  `new cc.ClusterClient('s3', async (path, init) => ({ ok: true, status: 200, json: async () => ({ path, init }) }))`
  and returns `(await c.connect({name:'m'}, 'J5'))` reduced to
  `[path, init.method, init.headers['X-FFAST-Launch'], JSON.parse(init.body).reconnect_job_id]`
  → `["/cluster/connect", "POST", "s3", "J5"]`. The cases run inside an
  async function, so write it as `await (async () => { ... })()` like the
  existing case that starts with `"await (async () => {"`.
- A failing fetch (`ok: false, status: 409, json: async () => ({error: 'busy'})`)
  rejects with message `"busy"`: `await (async () => { try { await c.stop(); return null; } catch (e) { return [e.message, e.status]; } })()`
  → `["busy", 409]`.

Run: `tests/ffast/renderers/web/test_web_pure_helpers.py -k cc_` (name the
cases with a `cc_` prefix).

**Commit:** `feat: the web page has a client for the launcher's cluster broker`.

---

## Task 6: File → "Connect to Cluster…" opens a dialog with the saved profiles

**What the user sees:** the File menu has **Connect to Cluster…** after
"Connect to Server…". In a page opened by `ffast-web` it opens a dialog:
a **Profile** list (first item "New profile"), the profile's fields in
groups (Profile, SSH, Scheduler, Resources), then a folded **Server setup**
section (closed until clicked) with one line of help under each of its
fields, **Save profile** and **Delete** (the sketch in Task 5). A new
profile shows the default values filled in. **Alt+Shift+C** (⌥⇧C on a Mac)
opens the same dialog, and the File menu shows those keys next to the item.
In a page not opened by `ffast-web` the item is greyed out with the reason
"Start FFAST with ffast-web to reach a cluster"; the key then puts that
reason in the status line.

**Facts.**
- `static/app.js` `_buildActions()` holds the File items; the connect item is
  `{ id: 'connect', label: 'Connect to Server…', menu: 'file', run: () => this._openConnDialog() },`.
  An action with `unavailable: () => reason` is greyed out with the reason as
  its title (`static/actions.js`).
- The connection dialog in `static/index.html` (search
  `<div id="conn-dialog"`) is the model: classes `warning-modal hidden`,
  `role="dialog" aria-modal="true"`, a `.warning-modal-panel` with a
  `.warning-modal-title`, a `.warning-modal-message hidden` paragraph, a body,
  and a `.warning-modal-footer` with `<span class="spacer"></span>`.
  `.edit-modal .warning-modal-panel { width: 520px; }` gives a wider panel.
  CSS for the conn dialog is under `#conn-body { ... }`.
- `app.js` opens/closes the conn dialog with `trapFocus` from
  `static/dialogs.js` (`_openConnDialog`, `_closeConnDialog`), and asks
  yes/no questions with `askDialog({ title, message, buttons, danger })`.
- `_applyUrlParams()` runs at the end of the constructor's init; it reads
  `new URLSearchParams(window.location.search)`.
- `tests/ffast/renderers/web/test_web_runtime.py` has
  `FILE_MENU = ["Load Dataset…", ..., "Connect to Server…"]` checked with
  `to_have_text(FILE_MENU)`.
- Keyboard shortcuts (built by plan 3, `2026-10-29-web-parity-3-workspace.md`
  Task 8; read that task and `static/shortcuts.js` first):
  `FILE_SHORTCUTS` entries are `{ action, code }`, matched by `event.code`;
  `matchShortcut(e, { inText })` requires Alt and Shift both down and Ctrl
  and ⌘ up, and returns null while focus is in a text field. The table
  already has `{ action: 'connect-cluster', code: 'KeyC' }`, and plan 3's
  pure test `key_cluster` already checks it. Right after
  `this._actions = this._buildActions();`, `app.js` gives each action named
  in the table its `keys` label (the File menu shows it as `data-keys`), and
  one keydown listener runs the action, or puts `whyUnavailable(action)` in
  the status line when it cannot run. Until an action with that id exists,
  `this._action(id)` returns nothing and the key does nothing. So this plan
  needs **no** change to `shortcuts.js`: naming the action
  `connect-cluster` is what binds the key.

**Change.**

1. `static/index.html`, right after the conn dialog:
   ```html
   <!-- Cluster dialog (web parity plan 2026-10-29): the ffast-web launcher
        does the SSH and SLURM work; this page only fills in a profile. -->
   <div id="cluster-dialog" class="warning-modal edit-modal hidden" role="dialog"
        aria-modal="true" aria-labelledby="cluster-title">
     <div class="warning-modal-panel">
       <div class="warning-modal-title" id="cluster-title">Connect to Cluster</div>
       <p id="cluster-message" class="warning-modal-message hidden"></p>
       <div id="cluster-body">
         <div class="cluster-row">
           <label for="cluster-profile">Profile</label>
           <select id="cluster-profile"></select>
           <button id="cluster-save-btn">Save profile</button>
           <button id="cluster-delete-btn">Delete</button>
         </div>
         <div id="cluster-form"></div>
       </div>
       <div class="warning-modal-footer">
         <span class="spacer"></span>
         <button id="cluster-close">Close</button>
       </div>
     </div>
   </div>
   ```
   CSS next to the `#conn-body` rules: `#cluster-body` like `#conn-body` plus
   `max-height: 70vh; overflow-y: auto;`; `.cluster-row { display: flex; gap: 6px; align-items: center; }`;
   `#cluster-form fieldset, #cluster-form details { border: 1px solid var(--bg3); border-radius: 4px; margin: 4px 0; padding: 6px; }`;
   `#cluster-form fieldset, #cluster-form details[open] .cl-grid { display: grid; grid-template-columns: max-content 1fr; gap: 4px 8px; }`;
   `#cluster-form summary { cursor: pointer; font-weight: 600; }`;
   `#cluster-form .cl-help { grid-column: 2; font-size: var(--fs-s); color: var(--muted); margin-top: -2px; }`
   (`--fs-s` and `--muted` are defined in `index.html`'s `:root`);
   `#cluster-message.hidden { display: none; }`. Text inputs reuse the
   `#conn-body input[type="text"]` look: add `#cluster-body input[type="text"]`
   to those selectors (and the `:focus` one). Also add
   `body.loupe-only #cluster-dialog { display: none; }` next to the conn
   dialog's rule.
2. New file `static/cluster_dialog.js`, `export class ClusterDialog`:
   ```js
   /**
    * The cluster dialog (web parity plan 2026-10-29): profiles here, the SSH
    * work in the launcher (cluster_client.js).
    * @param {{client: ClusterClient}} deps
    */
   constructor(deps) { ... }
   ```
   - `open()`: show `#cluster-dialog`, trap focus (`trapFocus`), then
     `this.refresh()`; on error show it in `#cluster-message`.
   - `close()`: hide, untrap.
   - `refresh()`: `const s = await this._client.state(); this._state = s;`
     fill `#cluster-profile` (`New profile` with value `''`, then each
     profile name), keep the current choice if it still exists, fill the
     form for it (`emptyProfile()` for New).
   - `_buildForm()` (once, in the constructor): one `<fieldset>` with a
     `<legend>` per `group` of `CLUSTER_FIELDS`, **except** the group
     `Server setup`: that one is
     `<details id="cl-server-setup"><summary>Server setup</summary><div class="cl-grid">…</div></details>`,
     closed (no `open` attribute) every time the dialog is built (decision 6).
     Per field a `<label for=cl-<key>>` and an `<input id=cl-<key>>`
     (`type="checkbox"` for `bool`, `type="text"` otherwise,
     `inputmode="numeric"` for `int`, the placeholder). A field with `help`
     gets `<small class="cl-help" id="cl-<key>-help">` right after its input,
     and the input gets `aria-describedby="cl-<key>-help"`.
   - `_readForm()`: `profileFromForm(...)` from the inputs.
   - `_fillForm(profile)`: from `formValues(profile)`.
   - Save: `await client.saveProfile(this._readForm())`, then set the select
     to the saved name and `refresh()`; show a server error (e.g. "Give the
     profile a name") in `#cluster-message`.
   - Delete: disabled for "New profile"; asks
     `askDialog({ title: 'Delete profile', message: `Delete profile "${name}"?`, buttons: ['Cancel', 'Delete'], danger: 'Delete' })`,
     on "Delete" calls `client.deleteProfile(name)` and `refresh()` with
     "New profile" selected.
   - Escape closes it (as the conn dialog's keydown does).
3. `static/app.js`:
   - Import `ClusterClient, launchId` and `ClusterDialog`.
   - In the constructor before `_initUI()`:
     `const launch = launchId(window.location.search);`
     `this._clusterClient = launch ? new ClusterClient(launch) : null;`
     `this._clusterReady = false;` (true once the launcher answered).
   - In `_initUI()`, create `this._clusterDialog = this._clusterClient ? new ClusterDialog({ client: this._clusterClient }) : null;`
     and bind `#cluster-close` to its `close()`.
   - In `_applyUrlParams()`, at the very top (does not delay the connect):
     `this._clusterClient?.state().then(() => { this._clusterReady = true; }).catch(() => {});`
     (a 404 from `ffast-server --web-port` leaves it false).
   - In `_buildActions()`, after the connect item:
     ```js
     { id: 'connect-cluster', label: 'Connect to Cluster…', menu: 'file',
       run: () => this._clusterDialog.open(),
       unavailable: () => (this._clusterReady ? '' : 'Start FFAST with ffast-web to reach a cluster') },
     ```
     The id must be exactly `connect-cluster`: plan 3's `FILE_SHORTCUTS`
     entry for Alt+Shift+C names it (decision 4). Do not edit
     `static/shortcuts.js`.
4. `test_web_runtime.py`: add `"Connect to Cluster…"` at the end of
   `FILE_MENU`, and in `test_web_file_menu_holds_the_session_actions` assert
   that item is disabled with title matching "ffast-web" (that test's page is
   served by `ffast-server --web-port`, which has no broker) and has a
   `data-keys` attribute matching `/^(Alt\+Shift\+C|⌥⇧C)$/` and
   `aria-keyshortcuts` "Alt+Shift+C" (the label format plan 3's test checks
   for "Save Session…"). In the same test, click the page body, press
   `Alt+Shift+KeyC` → `#cluster-dialog` stays hidden and `#status` says
   "Start FFAST with ffast-web to reach a cluster" (the key reports why).

**Test.** `test_web_runtime.py`. A helper context manager next to
`_spawn_server`:
```python
@contextlib.asynccontextmanager
async def _cluster_page(broker, secret="s3cret"):
    """Serve the web app with a cluster broker, as ffast-web does (web parity
    plan 2026-10-29). Yields the page's port."""
    from ffast.renderers.web.serve import start_static_server
    httpd = start_static_server(_free_port(), host="127.0.0.1",
                                cluster_broker=broker, cluster_secret=secret)
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        broker.shutdown()
```
`test_web_cluster_dialog_saves_and_deletes_profiles(ffast_web_server, tmp_path, monkeypatch)`:
- Redirect `cluster.connection._SESSIONS_FILE` into `tmp_path`; broker =
  `ClusterBroker(ClusterConfig(str(tmp_path / "clusters.json")))`.
- Open `http://127.0.0.1:{page_port}/?port={ws_port}&launch=s3cret`,
  `_expect_connected(page)`.
- `_file_menu(page, "Connect to Cluster…")` → `#cluster-dialog` visible;
  `#cluster-profile` has one option "New profile".
- The Server setup section starts folded:
  `await expect(page.locator("#cl-server-setup")).not_to_have_attribute("open", "")`
  and `#cl-venv_path` is hidden. Click `#cl-server-setup summary` →
  `#cl-venv_path` visible with value `~/.ffast/venv`,
  `#cl-snapshot_interval_minutes` value `5`, `#cl-provision` not checked,
  `#cl-provision-help` contains "installs it in the job" (defaults filled,
  help shown).
- Fill `#cl-name` "meluxina", `#cl-host` "login.lxp.lu", `#cl-cores` "8",
  `#cl-modules` "Python CUDA", tick `#cl-provision`; click
  `#cluster-save-btn` → the select's value is "meluxina";
  `json.loads((tmp_path / "clusters.json").read_text())["profiles"][0]`
  has `cores == 8`, `modules == ["Python", "CUDA"]`, `provision is True`
  and `venv_path == "~/.ffast/venv"` (`cluster/config.py` `save()` writes
  `{"profiles": [asdict(p) …]}`).
- Select "New profile" → `#cl-host` is empty; select "meluxina" →
  `#cl-host` is "login.lxp.lu".
- Click `#cluster-delete-btn`, then the ask dialog's "Delete" button (search
  the existing tests for how they click an `askDialog` button) → only "New
  profile" is left.
- Save with an empty name → `#cluster-message` shows "Give the profile a name".
- Close the dialog (`#cluster-close`), click the page body (so focus is not
  in a text field: plan 3 ignores the keys there), press
  `page.keyboard.press("Alt+Shift+KeyC")` (plan 3's checked form) →
  `#cluster-dialog` visible again.

No new pure case: plan 3's `key_cluster` already checks the table entry.

Run: `test_web_runtime.py -k "cluster_dialog or file_menu_holds or shortcut"`
and `test_web_pure_helpers.py -k "key_ or cc_"`.

**Commit:** `feat: the web File menu opens a cluster dialog with the saved profiles`.

---

## Task 7: Connect, Reconnect, Cancel and End cluster job in the dialog

**What the user sees:**
- The dialog footer has **Connect** (the default button), and on the left
  **End cluster job**.
- When the chosen profile has a Session Record, a line above the footer says
  "Job 123 (started 2026-10-29T10:00:00) may still be running." with a
  **Reconnect to 123** button, and Connect reads **Submit new job**. (This is
  the desktop's "Reconnect to cluster?" question, shown in place.)
- While connecting, a progress box lists the steps ("Submitting SLURM job…",
  "Waiting for job to reach RUNNING state…", "Opening SSH tunnel…"), and
  Connect becomes **Cancel**.
- When the tunnel is up, the dialog closes and the page connects to the
  cluster's server on its own; the status label then says Connected as for
  any server. The tunnel address is **not** added to "Recent servers".
- On failure, the error shows in red in the dialog, and the job log (when
  FFAST was being installed in the job) shows in the progress box.
- **End cluster job** (enabled while a job is connected or connecting)
  asks "End job 123? Its data on the cluster stays, but the running server
  stops." (buttons Cancel / End job, danger End job), then disconnects the
  page, ends the job, and connects the page back to the launcher's local
  server.

**Facts.**
- `app.js` `_connect()` reads `#ws-url` and `#token-input`, closes any
  current connection, connects, and on success calls
  `saveRecentServers(rememberServer(loadRecentServers(), wsUrl));   // never the token`.
  The button listeners call `this._connect()` (search
  `getElementById('connect-btn').addEventListener` and the conn dialog's
  Enter key handler).
- `_disconnect()` closes the page's connection and clears the views.
- The URL's `port` is the launcher's local server (`_applyUrlParams`:
  `ws://${window.location.hostname || 'localhost'}:${port}`).
- The broker's status (Task 3): `state` `idle|connecting|connected|failed`,
  `messages`, `error`, `details`, `job_id`, `reconnect`, `ws_url`, `token`.

**Change.**

1. `static/index.html`, in `#cluster-dialog`:
   - Inside `#cluster-body`, after `#cluster-form`:
     ```html
     <div id="cluster-record" class="hidden">
       <span id="cluster-record-text"></span>
       <button id="cluster-reconnect-btn"></button>
     </div>
     <pre id="cluster-progress" class="hidden"></pre>
     ```
   - Footer: `<button id="cluster-stop-btn" disabled>End cluster job</button>`
     before the spacer, and `<button id="cluster-connect-btn">Connect</button>`
     after `#cluster-close`.
   - CSS: `#cluster-record, #cluster-progress` hidden rule; `#cluster-progress`
     with `max-height: 160px; overflow-y: auto; white-space: pre-wrap; font-size: var(--fs-s);`
     (check `--fs-s` exists; else use the size the conn message uses).
2. `static/cluster_dialog.js`:
   - The constructor's `deps` gains `onConnected(status)` and `onEnded()`.
   - `_renderStatus(state)`: from `state.status`:
     - progress box shows `messages.join('\n')` (plus a blank line and
       `details` when set), hidden when both are empty;
     - `failed` → `#cluster-message` shows `error`;
     - `connecting` → Connect button text "Cancel";
       `#cluster-profile`, the form inputs, Save and Delete disabled;
     - `#cluster-stop-btn` enabled for `connecting` and `connected`;
     - record line: when not connecting/connected, use
       `latestRecord(state.records, <selected profile name>)`; show it with
       the text above and set the Connect text to "Submit new job"; else hide
       it and Connect reads "Connect".
     Call it at the end of `refresh()`.
   - Connect click: if the status is `connecting` → `client.cancel()`, then
     poll. Else `client.connect(this._readForm(), null)`, then poll; a 409 or
     400 error goes to `#cluster-message`.
   - Reconnect click: `client.connect(this._readForm(), record.job_id)`, then
     poll.
   - `_poll()`: clear any timer; `refresh()`; if the state is `connecting`,
     `setTimeout(() => this._poll(), 1000)`; if `connected` and
     `status.ws_url !== this._handedOver`, set `this._handedOver = status.ws_url`,
     `close()` and `this._deps.onConnected(status)`. Stop polling when the
     dialog is closed **and** the state is not `connecting` (keep polling a
     connect in progress even if the dialog was closed, so the hand-over still
     happens).
   - When `open()` finds a `connecting` state, start `_poll()`.
   - Stop click: ask (text above); on "End job": `this._deps.onEnded()` first
     (the page lets go of the tunnel), then `await client.stop()`,
     `this._handedOver = null`, `refresh()`.
3. `static/app.js`:
   - `_connect({ remember = true } = {})`: wrap the
     `saveRecentServers(...)` line in `if (remember)`. The button and Enter
     listeners stay `() => this._connect()` (note: pass no event object to
     it; check the listeners call it with no arguments, as now).
   - `_useCluster(status)`:
     ```js
     /** Connect the page to the cluster's server through the launcher's
      * tunnel (web parity plan 2026-10-29). Not a recent server: the port
      * changes every time. */
     _useCluster(status) {
       document.getElementById('ws-url').value = status.ws_url;
       document.getElementById('token-input').value = status.token || '';
       this._connect({ remember: false });
     }
     ```
   - `_leaveCluster()`: `this._disconnect();` then, when the URL has a
     `port`, put `ws://${window.location.hostname || 'localhost'}:${port}` and
     the URL's `token` (or '') back in the fields and `this._connect()`.
     Write it so `_applyUrlParams` and this share one small helper
     `_localServerUrl()` returning the URL or null.
   - Pass `onConnected: (s) => this._useCluster(s), onEnded: () => this._leaveCluster()`
     to `new ClusterDialog({...})`.

**Test.** `test_web_runtime.py`
`test_web_cluster_connect_hands_the_page_the_tunnel(ffast_web_server, tmp_path, monkeypatch)`:
- `ws_port, _ = ffast_web_server`. Fakes as in Task 3, but the fake connect
  returns `_FakeSession(job_id="J1", local_port=ws_port, token="")` (the test
  server **is** the "cluster" server; it needs no token), after
  `on_job_submitted("J1")`, `progress_cb("Submitting SLURM job…")` and
  `await asyncio.sleep(1.5)`. Copy `_FakeSession` into the test file (small)
  or import it from `tests/ffast/test_cluster_web_broker.py` if imports
  between test files are used elsewhere (check; if not, copy).
- Open the page with `?launch=s3cret` and **no** `port`, so the page starts
  disconnected (the conn dialog opens by itself: close it with `#conn-close`).
- Save a profile "m" with host "h", click `#cluster-connect-btn` →
  `#cluster-progress` contains "Submitting SLURM job…", the button reads
  "Cancel"; then the dialog closes and `_expect_connected(page)`;
  `#ws-url` is `ws://127.0.0.1:{ws_port}`; `localStorage['ffast.recentServers']`
  does not contain that URL.
- Open the dialog again: `#cluster-stop-btn` enabled; click it, answer
  "End job" → `#status` shows "Disconnected" (no `port` in the URL, so there is
  no local server to go back to), `cancelled == ["J1"]`.

`test_web_cluster_dialog_offers_to_reconnect_a_recorded_job`: save profile
"m"; `conn.save_session_record("J5", "m", "node1", 8765)` (redirected file);
open the dialog, select "m" → `#cluster-record-text` contains "J5",
`#cluster-reconnect-btn` reads "Reconnect to J5", Connect reads "Submit new
job". Fake reconnect raises `ClusterError("Job J5 is no longer running")`;
click Reconnect → `#cluster-message` contains "no longer running" and the
record line is gone (the record was purged).

Run: `test_web_runtime.py -k cluster`.

**Commit:** `feat: the web cluster dialog submits or reconnects a job and connects through its tunnel`.

---

## Task 8: a reloaded page goes back to the live cluster connection

**What the user sees:** after connecting to a cluster, reloading the page
(or opening a pop-out window) connects to the cluster's server again, not to
the local server named in the URL (decision 5).

**Facts.** `app.js` `_applyUrlParams()` ends with:
```js
    if (port) this._connect();
    else this._openConnDialog();
```
Task 6 added a `state()` call at its top that sets `this._clusterReady`.
`_openPopout()` copies the page URL (so `launch=` stays) and sets `port` to
the current server's port and `token` to the current token.

**Change.** In `_applyUrlParams()`: remove the Task 6 `state()` line at the
top, and replace the last two lines with:
```js
    // A live cluster connection wins over the URL's local server, so a reload
    // goes back to the cluster (web parity plan 2026-10-29).
    const fallback = () => (port ? this._connect() : this._openConnDialog());
    if (!this._clusterClient) { fallback(); return; }
    this._clusterClient.state().then((s) => {
      this._clusterReady = true;
      if (s.status?.state === 'connected') this._useCluster(s.status);
      else fallback();
    }).catch(fallback);
```
(The `loupe-live` and main-view settings above it stay as they are; they
apply to whichever server is connected.)

**Test.** Extend `test_web_cluster_connect_hands_the_page_the_tunnel`: before
ending the job, open a new page in the same browser at
`http://127.0.0.1:{page_port}/?port=1&launch=s3cret` (port 1: nothing
listens there) → `_expect_connected(page2)` and `#ws-url` is
`ws://127.0.0.1:{ws_port}`. Also run
`-k "connects_by_itself or dialog_opens_by_itself or asks_for_a_token"`: those
pages come from `ffast-server --web-port` (state() answers 404), so they must
behave as before.

**Commit:** `feat: a reloaded web page goes back to its live cluster connection`.

---

## When done

Report: the eight commit hashes, the tests you added, anything in this plan
that did not match the code and what you did about it, and any test that
was red (with its output). Do not push. Say clearly that nothing was tried
against a real cluster: the user must test once by hand with
`ffast-web`, File → Connect to Cluster…, on a real profile (e.g. MeluXina),
including closing `ffast-web` and reconnecting to the same job.

## Not in this plan (known gaps)

- The page does not notice when a connection drops after the handshake
  (`static/connection.js` sets `ws.onclose = null`); a dead tunnel shows only
  in the cluster dialog ("The SSH tunnel … closed"). Same for any server.
- No "Browse…" for the identity file (a page cannot open a local file path);
  the user types it.
- An `ssh` process started while connecting is not killed if `ffast-web` is
  closed in the middle of the connect (same on the desktop).

# The web launcher opens cluster connections for the page

Status: Accepted 2026-10-29. Builds on ADR 0024 (reconnect), ADR 0028
(auto-bootstrap) and ADR 0045 (web replaces Qt). The build plan is
`docs/implementation/2026-10-29-web-parity-4-cluster.md`.

## The problem

The desktop connects to a cluster through `cluster/connection.py`
(`connect_to_cluster`, `reconnect_to_cluster`): submit a SLURM job over SSH,
wait for it, open `ssh -N -L <local>:<node>:<port>`, and talk WebSocket to
`ffast-server` through that tunnel. The web client must do the same to
replace the desktop. A browser page cannot run `ssh`, read `~/.ssh` keys, or
call `sbatch`. The `ffast-web` launcher is a local Python process that
already serves the page on a loopback port and lives as long as the session.

## The decision

1. **The launcher keeps one Cluster Broker** (`cluster/web_broker.py`): an
   object that runs the existing `connect_to_cluster` /
   `reconnect_to_cluster` on its own asyncio thread and holds the SSH tunnel
   open. It reuses the Session Records in `~/.ffast/sessions.json`.
2. **The profiles file is the desktop's `config/clusters.json`** (the
   `ClusterConfig()` default), so both clients see the same profiles.
3. **The page talks to the broker with small JSON requests under
   `/cluster/`** on the launcher's own web port (same origin, so no
   cross-site setup). Each request must carry the launch id from the page's
   URL (`launch=`) in an `X-FFAST-Launch` header, so another web site open in
   the same browser cannot drive SSH.
4. **The page connects to the cluster's server itself.** When the tunnel is
   up, the broker closes its own WebSocket and gives the page the tunnel's
   address (`ws://127.0.0.1:<local port>`) and the Session Token. The page
   connects through its normal connection code. The broker sends no protocol
   messages after that.
5. **`ffast-server --web-port` has no broker** (a server serving the page
   itself, possibly on a cluster); the page then greys out "Connect to
   Cluster…".
6. **Job lifecycle, as on the desktop (ADR 0024).** Closing the launcher
   closes the tunnel but keeps the job and its record, so the next launch can
   reconnect. A failed reconnect to a dead job deletes its record. A tunnel
   failure on a reconnect keeps the record. **"End cluster job" closes the
   tunnel, always cancels the job** (also a job found again by reconnecting)
   and deletes the record. The desktop cancels only a job submitted in the
   same run; that would leave a reconnected job running with no record left
   to find it.
7. **A reloaded page (or a pop-out) asks the broker first** and goes back to
   a live cluster connection before the URL's local server.
8. **Alt+Shift+C** (Option+Shift+C on a Mac) opens "Connect to Cluster…".
   Ctrl+Shift+C, the desktop key, belongs to the browser's developer tools.

## Consequences

- No SSH logic in JavaScript and no new WebSocket messages: the protocol is
  unchanged. The broker's HTTP API is local to the launcher and not part of
  the protocol.
- Only a page opened by `ffast-web` (or `ffast` without the desktop) can
  reach a cluster. A page opened from a bookmark without `launch=` cannot.
- The Session Token reaches the page (it already sits in plain text in
  `sessions.json`); SSH access to the cluster stays the real boundary
  (ADR 0012).
- `cluster/` becomes a dependency of the web launcher (one lazy import edge
  from `ffast/renderers/web/launcher.py`, listed in the core-boundary test).
- `config/clusters.json` sits next to the package. A pip install into a
  read-only location cannot save profiles there; moving the file to
  `~/.ffast/` is a later change if that happens.

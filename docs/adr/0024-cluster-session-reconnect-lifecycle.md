# ADR 0024: Cluster Session Reconnect Lifecycle

**Status:** Accepted  
**Date:** 2026-06-30

## Context

A remote `ffast-server` lives on a SLURM compute node for the job's walltime,
independent of the client (ADR 0001, ADR 0013). A client may quit, crash, or lose
its SSH tunnel while the job keeps running. Without a way to find that job again,
the user would resubmit — wasting an allocation and orphaning the still-running
server.

Three problems had to be solved together:

1. **Rediscovery across client restarts.** A freshly launched client has no
   in-memory handle to a job submitted by a previous run. Job coordinates
   (id, node, forwarded port, session token) must survive client death.
2. **Stale records.** A persisted record for a job that has since died must not
   keep re-offering a reconnect that can only fail — the reconnect dialog would
   reappear on every connect attempt.
3. **Task-ID collisions.** The server replays `TASK_PROGRESS` / `TASK_DONE`
   Broadcast Events for its own work. If server task IDs share the local
   TaskManager's integer namespace, a replayed remote task can collide with an
   unrelated local task.

Separately, loading a large remote trajectory needs the frame count *before*
load so the user can choose a sensible stride, but the file lives on the cluster.

## Decision

- **Client-side Session Records.** On establishing a cluster session, write a
  **Session Record** to `~/.ffast/sessions.json` holding only job coordinates —
  `job_id`, `profile_name`, `node`, `remote_port`, `token`, `timestamp`
  (`save_session_record` in `cluster/connection.py`). This is distinct from the
  server-side **Auto-Snapshot** (ADR 0013): the record holds reconnect
  coordinates, not scientific state. The reconnect UI reads it via
  `load_session_records`.
- **Reconnect reuses the job.** `reconnect_to_cluster(job_id, …)` re-opens the
  SSH tunnel to a record's node/port instead of submitting a new SLURM job;
  `connect_to_cluster` and `reconnect_to_cluster` share the post-address-resolution
  path. The server replays `REMOTE_DATASET_META` (and the rest of its
  **Server Session** state) so the client rebuilds its proxy view.
- **Purge stale records.** Delete the record (`delete_session_record`) on
  user-initiated disconnect **and** when a job is found definitively dead, so the
  reconnect dialog never re-appears for a job that can no longer be reached. A
  bare tunnel failure on a *reconnect* attempt leaves the record alone (the job
  may still be alive).
- **Namespace remote task IDs.** Server-issued task IDs are namespaced
  `remote_<n>`, kept separate from local integer task IDs, so replayed task
  events never collide with local tasks.
- **Probe before stride.** Before loading a remote dataset, the client issues a
  **Dataset Length Probe** (`PROBE_DATASET_LENGTH` → `DATASET_LENGTH_RESPONSE`,
  returning `{n, error}`) and feeds the count into a **Remote Stride Dialog** so
  `slice_num` is chosen against the true frame count.

## Consequences

- Reconnect survives client restart, not just an in-session disconnect — the
  records file is the cross-launch state.
- `~/.ffast/sessions.json` is client-local, plaintext, and contains the session
  token; it is reconnect convenience state, not a secret store. SSH access to the
  node remains the real authorization boundary (ADR 0012).
- A record can still go stale between purges (e.g. job dies while the client is
  off); the reconnect path must tolerate a dead job gracefully and purge on the
  failed attempt, which it now does.
- Remote and local task progress can be rendered in one TaskManager without
  cross-talk, at the cost of a string namespace on the remote side.
- The probe is an extra round-trip per remote load; it runs once, before the
  (far more expensive) transfer, so the cost is negligible.

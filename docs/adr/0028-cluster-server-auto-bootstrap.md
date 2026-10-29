# ADR 0028: Cluster Server Auto-Bootstrap

**Status:** Accepted
**Date:** 2026-07-01

## Context

Connecting to a cluster today assumes the user has *already* hand-built a
Python environment with `ffast` installed on the login node; the
`ClusterProfile.ffast_server_cmd` string just activates it and runs
`ffast-server` (e.g. `module load Python && source ~/env/bin/activate &&
ffast-server`). That manual step is the main friction in remote use.

The end goal is **auto-bootstrap on connect**: connecting to a cluster should
provision and launch the server there automatically — build the server artifact
locally, copy it to the node over the SSH connection, install and start it —
with no manual environment setup on the cluster.

Two facts shape the design:

1. **A headless server is already installable (ADR 0026 Step A).** `pip install
   ffast` (no `[gui]` extra) yields a Qt-free, runnable `ffast-server`; the
   import-closure guard (`tests/ffast/test_headless_closure.py`) proves nothing
   on the server path imports Qt. So bootstrap does not need the full
   `client/` → `ffast/` migration — that migration is optional #5 cleanliness,
   **not on the critical path** (the dominant install weight is `torch` ~2 GB,
   which dwarfs the inert `UI/` Python files a slim distribution would exclude).
2. **HPC nodes provide the heavy scientific stack via `module load`.** Compute
   nodes are typically air-gapped; torch/numpy/scipy/CUDA come from modules, not
   pip. So bootstrap must install *only* `ffast` + its light pure-Python deps on
   top of a module-provided base.

## Decision

Auto-bootstrap the server, staged so each piece lands independently.

- **Delivery — push the `ffast` wheel over SSH.** Build the wheel locally
  (`python -m build` / `pip wheel --no-deps`) and copy it to the login node over
  the SSH channel already used for SLURM control. Chosen over *pip-from-index*
  (the repo is private and login-node internet is not guaranteed) and over a
  *full offline bundle* (a ~2 GB transfer per version bump, and it must match the
  node's Python/CUDA exactly). Only the small `ffast` artifact crosses the wire;
  the heavy stack is already on the node.

- **Dependencies — HPC modules + light deps on top.** Bootstrap runs the
  profile's `module load`s, creates a `venv --system-site-packages` over them, and
  installs `ffast --no-deps` plus only the light pure-Python deps
  (`msgpack`, `websockets`, `pydantic`, `tomli-w`, `jaxtyping`). The heavy stack
  (torch/numpy/scipy/scikit-learn/ase) is satisfied by the modules and visible
  through `--system-site-packages`. `--no-deps` avoids pip trying to resolve/
  reinstall torch against the pins. Both the local wheel build and the node-side
  install detect the available frontend — `uv` first (its venvs ship no pip, as
  the project's own dev env does), then PyPA `build`/`pip` — so provisioning
  works whether the machine is uv- or pip-managed.

- **Staleness — wheel content hash.** After install, write a marker on the node
  (`~/.ffast/installed.sha256`) holding the sha256 of the installed wheel.
  Bootstrap re-pushes + reinstalls **iff** the local wheel's hash differs. This
  catches active development (code changes without a version bump) that a version
  string comparison would miss, and makes reconnects fast when nothing changed.

- **Provisioning runs inside the SLURM job, not on the login node.** Login nodes
  commonly cap CPU/memory or forbid `pip`, and the work belongs under an
  allocation. So the login node does only light, policy-safe steps over SSH —
  read the marker, and on a hash mismatch `mkdir` + `scp` the wheel into
  `~/.ffast/`. The venv creation + `pip install` are folded into the **job
  command** itself (`module load … && venv --system-site-packages && pip install
  --no-deps ffast.whl + light deps && write marker && ffast-server`), so they run
  on the allocated compute node. When the marker already matches, the job command
  is just `module load … && activate && ffast-server` (no install).

- **Idempotent layout on the node.** A stable `~/.ffast/` (shared filesystem,
  visible from login and compute nodes) holds the venv (`~/.ffast/venv`), the
  staged wheel, and the marker.

- **Profile — additive, backward compatible.** `ClusterProfile` gains an opt-in
  `provision` flag plus `modules` (the `module load` list) and `venv_path`.
  When `provision` is off, the existing manual `ffast_server_cmd` path is used
  unchanged.

## Stages

0. **(done, ADR 0026 Step A)** Qt-wheel-free installable `ffast-server`.
1. **(done)** Wheel build + hash primitive — `cluster/bootstrap.py`:
   `build_server_wheel` (uv/build/pip frontend detection), `wheel_sha256`, the
   pure `needs_provision` decision, and `light_dependencies` derived from
   pyproject. Unit-tested.
2. **(done)** Stage + in-job provision — `provision_node` reads the node marker
   over SSH and, on a hash mismatch, `scp`s the wheel into `~/.ffast/` (login node
   does only file staging). It returns the **job command**: `provision_launch_cmd`
   (`module load` → `venv --system-site-packages` → `pip install --no-deps
   ffast.whl` + light deps → write marker → `ffast-server`) on a mismatch, or
   `server_launch_cmd` (activate + run) when current. Both command generators are
   pure/unit-tested; the SSH/scp transport is integration (unverified without a
   real cluster).
3. **(done)** Wired into `connect_to_cluster` — when `profile.provision`, run
   `provision_node` (stage only) and submit its returned command as the SLURM job,
   so venv+install happen on the allocated node; the manual `ffast_server_cmd`
   remains the fallback. `ClusterProfile` gained `provision` / `modules` /
   `venv_path`.
4. **(covered)** UX + logging guardrails — `provision_node` threads the existing
   `progress_cb`, re-provisions only on hash mismatch, and logs every remote step
   to the `FFAST` logger (which fans out to both `debug.log` and the terminal).
   Failures raise `ClusterError` with an actionable message + the remote stderr;
   `connectToCluster` logs that stderr, has a catch-all so nothing is swallowed,
   and — because provisioning now runs *in the job* — best-effort fetches and logs
   the SLURM job log (`~/slurm-<id>.{out,err}`) on failure so in-job errors are
   visible locally. Deeper Qt progress UI can layer on later.

## Consequences

- Remote connect needs no hand-built cluster environment; first connect (or a
  code change) provisions, subsequent connects skip on a hash match.
- Bootstrap depends on the node offering the heavy stack via `module load`; a
  cluster without suitable modules needs the manual `ffast_server_cmd` path (kept).
- Wheel-only delivery means `ffast`'s own light deps must be installable on the
  node (small, pure-Python) — tracked as an explicit light-deps set so
  `--no-deps` + explicit install stays honest as dependencies change.
- **In-job install trades login-node limits for compute-node network.** Because
  the `pip install` now runs in the job, the light deps are fetched from an index
  on the *compute* node. If compute nodes are fully air-gapped, that step fails;
  the mitigation (follow-up) is to also stage platform-matched light-dep wheels
  and install with `--no-index --find-links`. First-cluster validation will show
  which regime applies.
- The `client/` → `ffast/` migration (ADR 0026 Step B) is decoupled: it improves
  boundary cleanliness but is not required for auto-bootstrap.

## Related

- [ADR 0026](0026-headless-core-migration-direction.md): headless-core boundary; Step A makes the server installable.
- [ADR 0001](0001-remote-rpc-protocol.md): the transport the bootstrapped server speaks.
- [ADR 0024](0024-cluster-session-reconnect-lifecycle.md): session records / reconnect, which sit above a provisioned server.

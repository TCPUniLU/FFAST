Status: Proposed — 2026-07-24

# The HeadlessEnvironment keystone: relocate the Environment graph `client/` → `ffast/` (ADR 0026 Step B continuation)

## Context

ADR 0026 named the boundary — `ffast/` is the **Headless Core**, the flat top-level dirs
(`UI/`, `client/`, `cluster/`, `modelLoaders/`, `datasetLoaders/`, `modules/`, root
`events.py`/`tasks.py`/`utils.py`) are the **Desktop Client** — and set the membership criterion:
*a module belongs in the core iff it is reachable from the `ffast-server` / `ffast-cli` entry
points.* Step A (make Qt deps optional) is **done** and already delivered the product goal: a
Qt-wheel-free `pip install ffast`. Step B (relocate the Qt-free server-closure flat modules *into*
`ffast/`, the "keystone") was left opportunistic.

This ADR scopes that keystone concretely, having measured the actual server import closure
(static AST trace from `server.py`, 2026-07-24).

### What the closure actually contains

The server entrypoint does `from client.environment import HeadlessEnvironment`
([server.py:526](../../server.py)). `HeadlessEnvironment` **is** `Environment`
(`class HeadlessEnvironment(Environment, threading.Thread)`,
[client/environment.py:448](../../client/environment.py)) — the same class the desktop client
uses, run in its own thread with its own event loop. So the server drags in the entire
`Environment` collaborator graph. The trace finds **27 non-`ffast/` modules** in the
`server` + `ffast.cli.main` closure, all verified **Qt-free**:

| Group | Modules | Notes |
|---|---|---|
| **Env core** (11, `client/`) | `environment`, `connection_manager`, `data_service`, `data_cache`, `dataset_registry`, `model_registry`, `object_catalog`, `prediction_source`, `session_persistence`, `loading_coordinator`, `dataType` | each also imported by `UI/Plots.py` + `UI/loupe/colorbar_overlay.py`; `environment` also by `main.py` |
| **Root spine** (3) | `events.py` (18 importers), `tasks.py` (1), `utils.py` (18) | `utils.loadModules` is the plugin-discovery snag |
| **cluster/** (6) | `backend`, `bootstrap`, `connection`, `inbound_router`, `remote_dataset`, `slurm` | pulled in **inertly** via `ConnectionManager`; server never initiates SSH/SLURM |
| **Loaders** (5) | `datasetLoaders/loader`, `modelLoaders/{loader,ghost,zeroModel}`, `modules/loaders/aseDataset` | `modules/` is the ADR 0025 plugin-discovery dir a headless install would not ship |
| **config** (1) | `config/userConfig` | 11 importers incl. `UI/loupe/*` |

### The honest cost/benefit

This is **boundary/packaging hygiene only.** ADR 0026 already records why it is *not* required:
Step A met the deployment goal, and "the dominant install weight is `torch` (~2 GB), which dwarfs
the inert `UI/` Python files a slim distribution would exclude." No Qt is removed (the closure is
already Qt-free), no capability is added, no product goal is advanced. The single win: `ffast/`
becomes a self-contained headless distribution and a separate `ffast-desktop` can depend on it,
and the `ffast/ ⇏ client/` invariant becomes real and test-enforced instead of aspirational.

It is recorded as a full phased plan (below) precisely so it can be **green-lit one phase at a
time, or paused after any phase**, rather than committed to wholesale.

## Decision

Relocate the Env-core cluster and spine into a new **`ffast/core/`**, slotting the rest into
existing `ffast/` subpackages, behind **re-export shims** so no phase is a big-bang rewrite. Two
sub-problems ADR 0026 deferred (`dataType.AtomsList`, `utils.loadModules`) are handled by a single
gated final phase, not folded into the mechanical relocation.

### Target layout

```
ffast/
  core/           # NEW
    environment.py          (Environment, HeadlessEnvironment)
    data_service.py
    dataset_registry.py  model_registry.py  object_catalog.py
    prediction_source.py  loading_coordinator.py
    connection_manager.py   (cluster-connect machinery lazy-imported)
    events.py               (← root events.py, EventClass)
    tasks.py                (← root tasks.py, TaskManager)
  cache/
    keys.py  store.py       (← client/data_cache.py)
  session/
    persistence.py          (← client/session_persistence.py)
  config/
    user.py                 (← config/userConfig.py)
  loaders/        # NEW, Phase 5 only (port-based)
  # md5/kde helpers split out of root utils.py into ffast/ (Phase 0)
  # DEFERRED into Phase 5: dataType.AtomsList
  # STAYS Desktop-Client: cluster/*, modules/loaders/*, utils.loadModules
```

### Move strategy — re-export shims

Each relocation moves the real code to `ffast/…` and leaves the old flat path as a thin shim
(`from ffast.core.environment import *`). The ~5 env-core client importers (`UI/Plots.py`,
`UI/loupe/colorbar_overlay.py`, `UI/mainMenu.py`, `main.py`) and the ~18 importers of
`events.py`/`utils.py` keep working untouched, so every phase is a small, independently
shippable/revertible diff. A final cleanup phase rewrites imports to the real paths and deletes
the shims. (Matches how the prior Step-B slices — `rpc`, `input_resolver` — minimized churn.)

### `cluster/` — break the inert over-import

`HeadlessEnvironment` constructs `self.remote = ConnectionManager(self)`, which hard-imports
`cluster/connection.py` etc. at construction — but the server never initiates an outbound cluster
connection, so all of `cluster/` (SLURM submission, SSH bootstrap) enters the core closure unused.
`connection_manager` moves to `ffast/core/`, and its cluster-connect / SLURM machinery becomes
**lazy-imported** (fires only on a client-initiated connect, which never happens server-side).
Result: `cluster/` **stays a flat Desktop-Client dir** and leaves the server-startup closure
entirely. This makes the boundary mean something — the code that *runs on* a compute node contains
no code to *submit jobs to* one.

### Loaders — gated final phase, port-based

The headless server must load ASE datasets, so `aseDataset`'s logic must be in the shipped
`ffast/` distribution — yet it lives in `modules/`, the ADR 0025 plugin dir a headless install
would not ship, and `loading_coordinator` importing `modules.loaders.aseDataset` is itself an
`ffast/ → modules/` up-import. This is the one genuinely hard seam (it is why ADR 0026 deferred
both the loaders and `AtomsList`).

Phase 5 introduces the dataset-IO **port** (Candidate #3's sanctioned direction): `ffast/` owns a
small `DatasetIO`/loader-registry interface; concrete loaders **register into** it; the coordinator
and `server_session` call the port instead of importing concrete loaders. `AtomsList` moves into
the port's `ffast/` home at the same time. **Phase 5 is gated on a separate plugin-discovery ADR**
(entry-points, or a core-baseline of essential loaders plus additive `modules/` plugins) — it does
**not** reopen ADR 0025's plugin model inside this ADR. Until Phase 5 lands, the
`ffast/ → modules/loaders/aseDataset` up-import is a **documented, test-tracked residual** (the
single allowlisted exception in the closure guard, below).

### Phases (leaf-first; ship or pause after any)

- **P0 — spine.** `events.py`, `tasks.py` → `ffast/core/`; split `utils.py` (pure `md5*` and
  KDE helpers → `ffast/`; `loadModules` stays flat, deferred). Shims at old paths.
- **P1 — leaves.** `data_cache` → `ffast/cache/store.py`; `object_catalog`, `dataset_registry`,
  `model_registry` → `ffast/core/`; `userConfig` → `ffast/config/user.py`.
- **P2 — mid-layer.** `data_service`, `prediction_source`, `session_persistence`,
  `loading_coordinator` → `ffast/core/` (+ `ffast/session/persistence.py`).
- **P3 — connection.** `connection_manager` → `ffast/core/`; make cluster-connect machinery
  lazy; `cluster/` leaves the closure.
- **P4 — keystone.** `Environment` + `HeadlessEnvironment` → `ffast/core/environment.py`; repoint
  `server.py` to `from ffast.core.environment import HeadlessEnvironment`. Server closure is now
  `client/`-free **except** the loader residual.
- **P5 — loaders (GATED).** Dataset-IO port + `AtomsList` relocation; depends on the
  plugin-discovery ADR. Removes the last residual.
- **P6 — cleanup.** Rewrite call sites to real `ffast/…` paths; delete shims; tighten the guard to
  assert an empty client-closure allowlist.

### Regression guard — ratcheting client-closure allowlist

Extend the existing subprocess guard ([tests/ffast/test_headless_closure.py](../../tests/ffast/test_headless_closure.py))
beyond the Qt tripwire: snapshot the set of **non-`ffast/`** first-party modules in the
`server` + `ffast.cli.main` closure and assert it is a subset of an allowlist that **shrinks each
phase**. After P4 the allowlist holds only the documented loader residual; after P5, it is empty.
Each phase must prove it removed something and cannot silently re-import a relocated module.

## Consequences

- `ffast/` becomes an importable, `client/`-free headless distribution; `ffast-desktop` can depend
  on it. The `ffast/ ⇏ client/` invariant is enforced, not merely asserted in prose.
- Transitional shims mean the flat paths keep working through P0–P5; the tree carries redundant
  re-export files until P6. This is deliberate (small, revertible phases) but is real transitional
  debt with a defined end.
- No functional change and no new capability at any phase; a failure at any phase bisects to a
  single small relocation.
- Reversing is cheap while shims exist; P6 (deleting shims) is the point of commitment.
- Does **not** resolve plugin discovery (ADR 0025) — it depends on a separate ADR for that and, in
  the interim, tolerates one tracked up-import. The `science` migration (compute math `modules/` →
  `ffast/`) remains separate, as ADR 0026 states.

## Open questions (to settle before P5)

- The plugin-discovery redesign: entry-points vs a configured plugin path vs a core-baseline +
  additive-plugins split. This is the true gate on completing the keystone.
- Whether `ffast/core/` should later split further (e.g. registries into `ffast/session/`), or
  stay one package. Defer until the shims are gone (P6) and the shape is visible.

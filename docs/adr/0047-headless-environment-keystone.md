Status: Accepted — Phases 0–6 implemented (2026-07-25); relocation complete. Headless plugin discovery carved out to its own ADR.

## Phase 6 implementation notes (2026-07-25)

The relocation is complete. After GUI verification of Phases 0–5, Phase 6 removed
the transitional shims and finished the config relocation.

- **6a (`1f1dfd2`):** deleted all 17 re-export shims (the 11 `client/` modules,
  root `events.py`/`tasks.py`, `modelLoaders/{loader,ghost,zeroModel}.py`,
  `datasetLoaders/loader.py`) and repointed the 34 remaining call sites (UI/,
  `main.py`, `cluster/`, the `modules/loaders/` ML plugins, tests, examples) at
  the real `ffast.*` modules. `modelLoaders/` and `datasetLoaders/` are now empty;
  `client/` keeps only its genuine Desktop-Client modules (`display_overrides`,
  `mathUtils`, `dataWatcher`).
- **6b (`eb0ddb4`):** `config/userConfig.py` → `ffast/config/user.py` and
  `config/default.json` → `ffast/config/` (read `__file__`-relative, behaviour
  unchanged; `user.json` was absent so no settings migration). 11 call sites
  repointed; the loader bases' colour/name lookups now use `ffast.core.util` /
  `ffast.config.user`.

**Final boundary state** (`tests/ffast/test_ffast_core_boundary.py`): eager
ffast/ → flat edges are EMPTY; the only remaining edges are 6 lazy ones that are
the *intended* architecture, not leaks:
- `ConnectionManager → cluster.*` (×5): the client-only connect-out / SLURM /
  bootstrap machinery. `cluster/` is a Desktop-Client dir by design — the
  node-side server never dials out — so these never move.
- `Environment → utils` (for `loadModules` + `setupLogger`): the plugin-discovery
  seam. `loadModules` globs `modules/`.

**Carved out — headless plugin discovery (its own ADR).** One shim intentionally
remains: `modules/loaders/aseDataset.py`, an 18-line re-export that keeps its
`loadData` entry point so `loadModules` still registers `"ase (auto)"` when it
globs `modules/` on the desktop. For a `pip install ffast` that ships *without*
`modules/`, the core loaders must register without the glob — i.e. explicit
core-loader registration and/or an entry-point plugin mechanism. That is a new
feature (not part of this relocation), it touches the load/registration path, and
ADR 0047 always scoped it to a separate ADR. It is the one genuinely-open item.

## Phase 5 implementation notes (2026-07-25)

Phase 5 landed as 3 commits (`5786c96` data primitives, `65e9a48` model loaders,
`3c32085` dataset loaders + ASE), full suite green each time. Outcome: the
**eager** ffast/ → Desktop-Client import set is EMPTY — the Headless Core imports
no flat module at import time.

- `client/dataType.py` → `ffast/core/data_types.py`; `modelLoaders/{loader,ghost,
  zeroModel}.py` → `ffast/loaders/{model,ghost,zero}.py`; `datasetLoaders/loader.py`
  → `ffast/loaders/dataset.py`; `modules/loaders/aseDataset.py` →
  `ffast/loaders/ase.py`. Pure colour/name/bond helpers → `ffast/core/util.py`.
- **Plugin model, pragmatic:** rather than the drafted registry-port + a new
  plugin-discovery ADR, the essential ASE loader simply became a Headless-Core
  baseline in `ffast/loaders/ase.py`, and `modules/loaders/aseDataset.py` was kept
  as a re-export **shim that retains its `loadData` entry point** — so
  `utils.loadModules` still registers `"ase (auto)"` when it globs `modules/` on
  the desktop (verified). The optional ML-backend loaders (MACE, NequIP, …) stay
  real additive plugins in `modules/loaders/`. This defers the *headless-without-
  `modules/`* registration question to Phase 6 without blocking the relocation.
- The loader bases' `config.userConfig.getConfig` lookups (colours, bond
  lenience) were lazified so `ffast/loaders` imports flat-free; those become
  allowed **lazy** edges, cleared when `userConfig` relocates (Phase 6).

Remaining ffast/ → flat edges are all lazy and documented in
`tests/ffast/test_ffast_core_boundary.py`: `utils.loadModules` (plugin discovery),
`config.userConfig` (loader colours/lenience), and the client-only `cluster/`
machinery. Phase 6 (repoint the remaining shim call sites in `UI/`/`main.py`/
tests/examples, delete the shims, relocate `userConfig`, redesign headless plugin
discovery) is deliberately left for after GUI verification of Phases 0–5 — shim
deletion is a wide, functionally-neutral change best done once the relocation is
confirmed working in the running app.

# The HeadlessEnvironment keystone: relocate the Environment graph `client/` → `ffast/` (ADR 0026 Step B continuation)

## Implementation notes (Phases 0–4, 2026-07-24)

Phases 0–4 landed as 5 commits, each green on the full suite (1176 passed) and
independently revertible behind re-export shims. Three findings corrected the
draft plan:

1. **The closure guard is static and eager/lazy-split, not a runtime snapshot.**
   The drafted "subprocess snapshot of the server closure" does not work: the
   server imports the env lazily inside `_main`, so `import server` pulls almost
   nothing, and shims keep flat module names alive in `sys.modules` until
   Phase 6. `tests/ffast/test_ffast_core_boundary.py` instead scans `ffast/`
   statically and splits **eager** (module-level — breaks a headless import;
   must reach empty) from **lazy** (function-level, client-only paths that never
   run headless; allowed). After Phase 4 the eager set is exactly 3 edges
   (`loading_coordinator`/`persistence` → `client.dataType`/`modelLoaders.ghost`),
   all clearing at Phase 5.

2. **Phase 3's "inert over-import" was already lazy.** `ConnectionManager`'s
   `cluster.*` imports are all function-level, so importing the full env graph
   pulls in **zero** `cluster/` modules at runtime — the over-import the draft
   set out to break does not happen. Phase 3 became a pure relocation; the
   cluster edges are tracked as allowed lazy edges and `cluster/` stays
   Desktop-Client.

3. **`config/userConfig.py` deferred from Phase 1.** It reads `config/*.json`
   via `__file__` and writes `user.json` at runtime, so relocating it is a
   data-file migration (where should `user.json` live — `~/.ffast/`?) orthogonal
   to the code keystone. No env-cluster module imports it, so deferring adds no
   edge. The dead KDE helpers in `utils.py` (superseded by
   `ffast/metrics/transforms.py`) were likewise left, not relocated — only the
   live `md5FromArraysAndStrings` moved.

**Net after Phase 4:** `server.py` and `ffast/cli/main.py` run
`ffast.core.environment.HeadlessEnvironment`; the Environment graph lives in
`ffast/core/` (+ `ffast/cache/store.py`, `ffast/session/persistence.py`). The
only remaining `ffast/ → client/` *eager* dependency is the 3-edge loader
residual that Phase 5 (dataset-IO port + `AtomsList`) removes. Phase 6 (repoint
`main.py`/tests/examples off the shims, delete shims, config/userConfig, plugin
discovery) remains open.

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

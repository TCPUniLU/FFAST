Status: Accepted

# Loading Coordinator: one interface for dataset/model/prediction loading

After the ADR 0020 decomposition, loading is the surviving god-slice of the client **Environment**:
~530 lines of `client/environment.py` expose 8 public entry points for 2 concepts
(`requestDatasetLoad` / `requestModelLoad` / `requestPredictionLoad` / `taskLoadDataset` /
`taskLoadModel` / `loadDataset` / `loadModel` / `loadPrepredictedDataset`), each load a branching
`taskLoadX → requestX → loadX` chain that re-decides local-task vs server-request routing.
**GhostModel** registration is copy-pasted at 3 sites, `lookForGhosts()` is sprinkled at 3 sites,
and `_loadPredictionsFromKeys` near-duplicates `loadPrepredictedDataset`. The interface is nearly as
wide as the implementation, and the flow is test-dark except end-to-end.

**Decision (proposed):** extract a Loading Coordinator module. Callers say *what* to load (dataset,
model, prediction — plus source path/stride); the coordinator decides *where* (local TaskManager task
vs server request over the **Server Connection**, including the connect-window fallback from
ADR 0030), registers loaded objects in the **Object Catalog** once, runs ghost discovery at one
defined point, and owns validation (shape checks, loader-type existence). The Environment keeps thin
delegating methods so existing call sites and the **Server Session**'s `LOAD_DATASET` / `LOAD_MODEL`
handlers are untouched initially.

## Why

- Leverage: one interface behind 15+ call sites; the local-vs-server routing rules become testable
  with a fake remote instead of a live server.
- Locality: the three copies of ghost registration and the duplicated prediction-load paths collapse
  into one implementation.
- It clears the declared keystone of the Headless Core migration (ADR 0026 Step B): the
  HeadlessEnvironment slice is stuck inside `client/environment.py` largely because loading
  orchestration tangles Qt-adjacent client concerns with server-closure concerns. A coordinator with
  an explicit environment-facing interface is the piece that can migrate.

## Consequences

- The `request*` methods' msgpack coercion moves behind the coordinator, so transport details stop
  leaking into the Environment's public surface.
- Risk concentrates during the move: loading touches every workflow. Slice by concept (datasets,
  then models, then predictions), each slice GUI-verified, matching how prior extractions were done.

## Decision addendum (as implemented)

Two refinements were made during implementation, expanding the written scope above:

1. **The remote-menu bypass paths and their probing are absorbed, not just the 8 named methods.**
   `onRemoteDatasetLoad` / `onRemotePredictionLoad` (`UI/mainMenu.py`) previously did their own
   server-side probing and pushed `LOAD_DATASET` / `LOAD_PREDICTION` directly — a second, uncoordinated
   copy of the wire contract. The coordinator now owns *all* dataset/model/prediction server transport:
   the probe round-trips (`probeDatasetLength` / `probeDatasetKeys`) and the `LOAD_*` dispatch
   (`dispatchDatasetLoad` / `dispatchModelLoad` / `dispatchPredictionLoad`, which own the `control.*`
   constants and the `prediction_keys` tuple→list coercion). The three `request*` methods and the two
   remote-menu flows now funnel through these one-owner coroutines. `requestSessionSave` /
   `requestSessionLoad` / `deleteObject` share the same fallback-guard idiom but are session-lifecycle,
   not loading — see addendum 3 below for where that idiom ended up.

2. **The coordinator stays Qt-free via a callback-orchestration interface.** Because the coordinator's
   stated purpose is to be the piece that migrates into the Headless Core, it cannot import Qt. But the
   remote-load flow interleaves server probes with Qt dialogs (stride, key selection), and the
   `QDialog.exec()`-from-inside-an-asyncio-task bridge (`QTimer.singleShot` + `asyncio.Future`, guarding
   the "Cannot enter into task" `RuntimeError`) is inherently Qt-coupled. Resolution: the coordinator
   owns the load *algorithm* (`loadRemoteDataset` / `loadRemotePrediction`: probe → stride → probe →
   keys → dispatch) and `await`s **UI-supplied dialog callbacks** at each interaction point. Callbacks
   return fully-cooked values — `get_stride(n_total) -> slice_num | None`,
   `get_keys(probe) -> (energy_key, force_key[, prediction_keys]) | None` (`None` == cancelled) — so
   `UI/menuLogic.py`'s `resolve_key_options` / `stride_to_slice_num` and the `exec()` bridge all stay
   in the Desktop Client. The upshot is the ADR's own goal made concrete: the load algorithm is now
   unit-tested with a fake session + fake callbacks (`tests/ffast/test_loading_coordinator.py`), no Qt
   and no live server.

Ghost registration collapsed to a single `registerGhostModel` chokepoint reused by all sites
(2 in the coordinator's own load paths, 2 in `ConnectionManager`'s server→client metadata handlers) —
the ADR's "3 copies" had drifted to 4 by implementation time.

## Decision addendum 3 (2026-07-24): fallback-guard fold + ghost-instantiate fold

A later architecture review (2026-07-23) flagged two duplications this ADR left open: the
"is a session live?" guard (`serverConnection is not None and _event_loop is not None`, or its
inverse) copy-pasted at 7 sites across `LoadingCoordinator`, `ConnectionManager`, `Environment`, and
`PredictionSource`; and ghost-model construction (`GhostModelLoader(env, key)` → `initialise()` →
registry `add()`) duplicated between `lookForGhosts()` and `ConnectionManager._onRemoteModelMeta`. A
9th copy of the guard (`Environment.deleteObject`) turned up during the fold, missed by the review.

Both collapsed to single chokepoints:

- `ConnectionManager.active_session()` — the one place the guard lives now. Every other site
  (including `requestSessionSave` / `requestSessionLoad` / `deleteObject`, deferred by addendum 1
  above) delegates to it; `LoadingCoordinator._remoteSession()` is now a 1-line wrapper.
- `LoadingCoordinator.instantiateGhost()` — the other half of the ghost-model chokepoint alongside
  `registerGhostModel`. `ConnectionManager._onRemoteModelMeta` calls it instead of constructing a
  `GhostModelLoader` itself (and drops its own now-dead `GhostModelLoader` import).

Folding `_onRemoteModelMeta` into the shared chokepoint surfaced a real bug: unlike every other
ghost-creation path, it mutated the models registry without holding `mutation_lock` — a genuine
Environment-Decomposition-era gap (ADR 0044 Phase 3), not something this ADR introduced. Fixed by
wrapping its existence-check-through-create sequence in `mutation_lock`, matching
`loadModel`/`loadDataset`'s own discipline.

Out of scope, still open: the Coordinator's 11-distinct-attribute reach into `Environment`
(`remote`, `newTask`, `modelTypes`, `mutation_lock`, `models`, `datasets`, `data`, `datasetTypes`,
`eventPush`, `objects`, `cache`) that the same review calls the actual blocker for migrating the
Coordinator into the Headless Core (ADR 0026). That needs a named load-port with two adapters (real
`Environment` + a headless stand-in) — a larger, separate design decision, not a mechanical fold.

## Decision addendum 4 (2026-08-03): prediction-ingest collapse; the port declined

### The migration premise expired

Addendum 3 left the Coordinator's 11-attribute reach into `Environment` open, on the reasoning
(from the 2026-07-23 architecture review) that it was "the actual blocker for migrating the
Coordinator into the Headless Core (ADR 0026)". **It was not, in the end.** ADR 0047 reached the same
destination from the other side: it relocated `Environment` *itself* into `ffast/core/`, and the
Coordinator moved with it. `ffast/core/loading_coordinator.py` is in the Headless Core today, passes
the `tests/ffast/test_ffast_core_boundary.py` guard, and imports nothing from `client/` or the flat
Desktop-Client dirs. Whatever the wide seam costs, it is no longer blocking a migration.

### What this addendum does fix

The duplication *this ADR itself* flagged at proposal time — "`_loadPredictionsFromKeys`
near-duplicates `loadPrepredictedDataset`" — was never resolved and had drifted badly. Both paths
independently implemented: choose ASE loader flavour → pull E/F → validate against the dataset →
cache energies → cache forces → register the ghost. Collapsed now into one
`_ingestPrediction` body plus two shared helpers (`_aseLoaderFor`, `_predictionMatchesDataset`) and
a module-level `_isUniformAtomsList`. `_readPredictionAtomsList` was split out of
`loadPrepredictedDataset` at the same time so the stride-vs-lazy read decision has a name.

Callers keep the one thing that legitimately differed: the fingerprint. The two paths hash different
inputs (`md5(E, F)` vs `md5(E, F, model_name)`), and a ghost's fingerprint *is* its identity in the
cache and in saved sessions, so unifying it would have silently invalidated existing sessions.
Mismatch policy also stays per-caller — `_ingestPrediction` returns a bool, the standalone-file path
aborts the load (there is no other column to fall back to, and the cause is a mis-picked file), the
prediction-keys path skips that one column and carries on.

Four real defects fell out of having one body instead of two. All four were confirmed against the
real ASE loaders, not inferred from reading:

1. **In-file prediction columns skipped ADR 0023 field extraction.** `loadPrepredictedDataset`
   extracted declared `prediction.{info,atoms}.<key>` fields; `_loadPredictionsFromKeys` did not. A
   metric referencing such a field silently resolved to `None` for any prediction that came from an
   extra column in the dataset file. This is the one with user-visible consequences.
2. **Homogeneity detection crashed below 3 frames.** The standalone path sampled
   `np.random.choice(len(atoms_list), size=3, replace=False)`, which raises
   `ValueError: Cannot take a larger sample than population` for a 1- or 2-frame prediction file.
3. **Homogeneity detection was non-deterministic.** 20 iterations of 3 random frames. Measured on
   1000 frames with one differing frame: it returned "uniform" in **184 of 200 runs**, and the
   verdict varied run to run. Since the verdict picks the loader *class*, the same file could load
   as two different dataset identities on two runs. `_isUniformAtomsList` samples at deterministic
   even spacing instead, always including the first and last frame, exhaustive at or below 60 frames.
   Note the honest limit: above 60 frames a sampled check still cannot see a lone outlier frame —
   the fix here is reproducibility, not detection power.
4. **The prediction-keys path compared atom counts, not composition.** `len(set(atom_counts)) == 1`
   accepts frames that share an atom count but not their elements into the uniform
   `aseDatasetLoader`, which reads frame 0's atomic numbers and applies them to every frame.
   Confirmed: given `[CH4, SiH4]` (both 5 atoms) that loader reports `z == [6,1,1,1,1]` and formula
   `C1H4` for *both* frames — the silicon frame is silently reported as methane. The unified check
   compares chemical formulas, which is what the uniform loader actually requires. It also stops
   forcing a full materialisation of a lazy `AtomsList` just to count atoms.

**One suspected defect that was not real.** The two copies disagreed on shape validation: the
prediction-keys copy had an `isinstance(E, list)` branch for variable datasets, the standalone copy
only `E.shape != eDataset.shape`. That looked like an `AttributeError` waiting to happen on variable
data, and an earlier draft of this addendum claimed it as a bug. It is not: `getEnergies()` returns an
`ndarray` on *every* dataset type (`VariableASEDatasetLoader` builds `self.E` with `np.array`), so the
list branch is unreachable and the standalone check was correct. The shared
`_predictionMatchesDataset` keeps both branches defensively — energies-as-list is a shape the
`getForces()` side does return — but no behaviour was fixed here.

30 unit tests added (`tests/ffast/test_loading_coordinator.py`): 16 against fakes, 8 driving the real
ASE loaders over real extxyz files (defects 2-4 are all about loader selection and cannot be reached
through a fake), and 6 covering the shared helpers introduced below. Suite: 1212 pass.

### The port: declined, and the measurement that decided it

Addendum 3 proposed resolving the wide seam with "a named load-port with two adapters (real
`Environment` + a headless stand-in)". Declined, for two reasons that only became clear once the
collapse was done and the surface could be measured rather than estimated:

- **There is no second adapter in production.** The imagined headless stand-in does not exist and is
  not planned: the server runs `HeadlessEnvironment`, an `Environment` subclass. The only second
  implementation a port would get is a test fake. An interface with one real implementation is
  renaming, not a seam.
- **The collapse did not narrow the surface enough to change that verdict.** Measured before and
  after: `self._env.*` references 38 → 34, distinct attributes 11 → 11, executable code lines
  524 → 523. The estimate going in was that folding the duplicate bodies would take the reach to
  ~26 refs and shrink the data surface from 7 sites to 2; it did neither, because the duplicated
  region was only ~4 of the 38 references and the shared helpers replacing it cost about what the
  duplication did. A port over the remaining surface still needs ~13 verbs to stand in for 11
  attributes.

The reach itself is therefore recorded as **accepted, not open**: the Coordinator holds `Environment`
and reaches domain state through it, exactly as `ConnectionManager` and `SessionPersistence` do
(ADR 0020). The routing and remote-load algorithm — the parts worth isolating — are already driven by
parameters (`session`, dialog callbacks) and already unit-tested without Qt or a live server.

What would reopen this: a genuine second `LoadPort` implementation appearing in production. The
plausible trigger is a server-side loading path that must run without a full `Environment` — an
`ffast-server` that ingests datasets without registries or a task manager. Until something needs
that, the port has one caller and one implementation.

### Reference count compresses; attribute surface does not

Asked whether the reach could be compressed further, three more idioms turned out to be spelled out
at every call site rather than shared, and were folded into helpers:

- `_progress(taskID, message, error=False)` — `eventPush("TASK_PROGRESS", …)` written out **8 times**
  across the two remote-load algorithms.
- `_queueLoad(fn, name, args, kwargs)` — the `visual=True, threaded=True` task shape written out at
  each of the three `taskLoad*` entry points.
- `_resolveLoad(kind, path, typeName)` — the file-exists + type-registered pair of checks, written
  out in both `loadModel` and `loadDataset`, plus the registry indexing that followed (5 sites).

That took `self._env.*` from 34 references to **22**, and executable code from 523 lines to **506**.
`_resolveLoad` also fixed a copy-paste artefact the duplication had been hiding: `loadModel` reported
both its failure modes as dataset problems, so a missing model file logged *"Tried to load dataset,
but path … not found"* and an unregistered model type logged *"dataset type … not recognised"*.

**But the distinct-attribute count did not move: 11 before, 11 after.** That is the real answer to
the review's premise, and it is worth stating plainly because it is not a shortcoming of the
Coordinator. Loading needs, irreducibly: read a loader registry, instantiate the loader, take the
mutation lock, write the dataset registry, write the model registry, write the cache, write the
object catalog, queue a task, push progress, resolve a session, write the data service. Eleven
capabilities, because "load a thing into a session" is the operation that touches every part of a
session.

So the seam the review measured as wide was measuring the *job*, not the coupling — which is also why
the port priced out at ~13 verbs. No port can be narrower than the operation it fronts. Helper
extraction can keep reducing how often each capability is named (and is worth doing for readability),
but it cannot reduce how many are needed.

One structural extraction does exist and was considered: `registerGhostModel` / `instantiateGhost` /
`lookForGhosts` are the only sub-cluster with *private* attributes — `cache` and `objects` are touched
by nothing else in the Coordinator — so lifting them out would take the surface from 11 attributes to
9, genuinely removed rather than renamed. It also has a real second caller already
(`ConnectionManager._onRemoteModelMeta`). Declined for now anyway: ~25 lines of behaviour, and moving
it churns call sites (`env.loading.registerGhostModel` → `env.ghosts.register`) to relocate two
attributes. Worth revisiting if ghost recovery grows, not on its own.

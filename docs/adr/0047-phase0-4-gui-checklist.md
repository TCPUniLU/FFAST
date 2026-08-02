# ADR 0047 Phases 0–4 — GUI verification checklist

Run this **before** implementing Phases 5–6. Phases 0–4 relocated the entire
Environment graph (`client/` → `ffast/core/`) behind re-export shims and turned
6 methods into lazy imports. The unit suite (1176 pass) exercises headless +
isolated-Qt paths only — it does **not** launch the desktop app, start the
managed local server, or trigger the lazy imports through the UI.

**If any Tier 1 item fails, stop — do not build Phases 5–6 on a broken base.**
Report which item broke + any traceback: a Tier 2 failure points at one lazy
import; a Tier 1 failure points at the shim import graph.

Launch: `ffast-qt` (or `python main.py`).

## Tier 1 — blocking (the relocation holds in the running app)

- [x] **App launches** — proves the whole shim import graph resolves in the Qt
      process (`main.py` → `client/environment` shim → `ffast.core.environment`).
- [x] **Local server connects — 3D View is NOT greyed out** — proves `server.py`'s
      repointed `ffast.core.environment.HeadlessEnvironment` entry starts and the
      client connects. (2D-works-but-3D-grey = server down.)
- [x] **Load a dataset** → appears in the object list (moved `LoadingCoordinator`
      + registries + `DataService`).
- [x] **3D view renders atoms + rotate/click works** — proves the event bus
      survived the move (`events.py` → `ffast.core.events`, module-global `subs`;
      broken one-bus identity = dead-silent UI).

## Tier 2 — the 6 lazy imports introduced in Phase 4 (highest risk; tests may never trigger these)

Each is a new runtime `import` inside a method; a wrong path surfaces only when
the action runs.

- [x] **Load Zero Model (Ctrl+0)** → `loadZeroModel` lazy-imports `ZeroModelLoader`
- [x] **Create a subset (subbing)** → `declareSubDataset` lazy-imports `SubDataset`
- [x] **Freeze a subset** → `freezeSubDataset` lazy-imports `FrozenSubDataset`
- [x] **Hide/filter atoms** → `createAtomFilteredDataset` lazy-imports `AtomFilteredDataset`
- [x] **A plot/list showing a model+dataset color** → `getColorMix` lazy-imports `mixColors`

## Tier 2b — Phase 5 relocated the loaders (re-exercise the load path)

Phase 5 moved the data primitives (`dataType`) and every loader (`ModelLoader`,
`GhostModelLoader`, `ZeroModelLoader`, `DatasetLoader`, `SubDataset`/`Frozen`/
`AtomFiltered`, the ASE loader) into `ffast/`, and lazified the colour/bond
config lookups. Loading anything now runs the relocated code.

- [ ] **Load an ASE dataset (xyz/extxyz/traj)** — the ASE loader is now
      `ffast/loaders/ase.py`, registered via the `modules/loaders/aseDataset.py`
      shim's `loadData`. A dataset must appear with the right colour (colour path
      = lazified `getConfig`/`hexToRGB`).
- [ ] **Load a prediction with key selection** (ASE) — exercises
      `aseDatasetLoader`/`VariableASEDatasetLoader` + the key dialog.
- [ ] **Bonds render in 3D** — `DatasetLoader` bond sizing uses the lazified
      `getConfig("loupeBondsLenience")` + `cleanBondIdxsArray`.
- [ ] **Load an ML model** (MACE/NequIP/… if available) — the ML loaders are now
      additive plugins in `modules/loaders/` subclassing `ffast.loaders.model`.

## Tier 3 — moved-module workflows (if time)

- [x] **Load a prediction** (local; + remote cluster if handy) — `LoadingCoordinator`
      + `ConnectionManager` (Phase 3 lazy cluster imports)
- [x] **A metric plot** (error panel / true-vs-pred scatter) — moved `DataService`
      metric spine
- [x] **Color atoms by a metric** — atom-coloring path / worker pool
- [ ] **Save session → reload it** — moved `SessionPersistence`

---

_Context: ADR 0047 Phases 0–4 commits `b39b33b` (P0) · `4de7f0b` (P1) ·
`690d120` (P2) · `427b425` (P3) · `09afab6` (P4), branch `2ffast`._

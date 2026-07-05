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
   not loading, and are left for a future slice.

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

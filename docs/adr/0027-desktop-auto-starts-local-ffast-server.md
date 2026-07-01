# ADR 0027: Desktop always auto-starts a managed local ffast-server

**Status:** Accepted / Implemented (renumbered from a duplicate 0017 on 2026-07-01)

> **Amended (2026-07-01):** the *decision* stands, but the entry point drifted.
> Auto-start no longer lives in `UIHandler.launch()`; the managed local server is
> started from `main.py` via `env.remote.startLocalServer` (on
> `RemoteSessionManager`). The shim-deletion below (no `_refreshLocalSceneAdapter`,
> no `remoteSession is None` guards) is still accurate. Read "Implementation order"
> below as historical intent, not the current call site.

On desktop launch the app starts a managed `ffast-server` subprocess via
`LocalServerManager` and connects to it with `env.connectDirect`. The "New 3D View" button
is disabled until the connection is established. After this, `env.remoteSession` is always
set on desktop — the same as remote mode — and the local shim path
(`_refreshLocalSceneAdapter`, all `remoteSession is None` guards) is deleted.

**Why not keep the embedded local path alongside the server path?**  
Every View Command dispatcher in `Loupe.py` carried a dual-path guard:
`if remoteSession is None: _refreshLocalSceneAdapter(); return`. As sidebar pane modules
are extracted into `loadLoupe` hooks, each new module would inherit the same guard.
The shim would leak into every extension point, making Loupe un-extensible.

**Why disable "New 3D View" until connected rather than a loading splash?**  
The main window and dataset loading are independent of the server. Blocking only Loupe
creation is the minimum restriction: the user can load datasets while the server starts.
Existing task progress UI reports the connection status without a separate modal.

**Implementation order:**
1. `UIHandler.launch()` starts `LocalServerManager`, kicks off `env.connectDirect` as a task, disables Loupe creation until `REMOTE_CONNECTED` fires.
2. Delete `_refreshLocalSceneAdapter` and all `remoteSession is None` guards in `Loupe.py`.
3. Extract `initialiseBondsPane`, `initialiseForceVectorsPane`, `initialiseUnitCellPane`, `initialiseViewSettingsPane` into `loadLoupe` module files.

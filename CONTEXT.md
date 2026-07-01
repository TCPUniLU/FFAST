# FFAST Domain Context

## Core Domain

FFAST is a tool for interactive error analysis of Machine Learning Force Field (MLFF) models. Researchers load molecular datasets and trained models, run batch predictions, and explore energy/force errors through 2D plots and a 3D molecular viewer.

The agreed target architecture and phased migration plan are documented in
[Server-Owned Visualization Architecture](docs/server-visualization-architecture.md).

---

## Glossary

### Headless Core
The `ffast/` package — engine, server, protocol, renderer-agnostic visualization, and CLI. Imports no Qt/PySide6 and runs on a SLURM compute node with no display; it is the importable, distributable surface (`import ffast.*`, entry points `ffast` / `ffast-server` / `ffast-cli`). Its goal is a `pip install`-able server with no Qt/client baggage. The agreed migration direction is *into* the Headless Core, never out; the membership criterion is the **server import closure** — a module belongs in the core iff the `ffast-server` / `ffast-cli` entry points reach it (an empirically Qt-free set, so the msgpack transport codec `ffast/protocol/rpc` is core but `UI/panels` presentation is not) (ADR 0026). Distinct from the **Desktop Client**.
_Avoid_: library, backend, src (it is the package, not a generic source root).

### Desktop Client
The flat top-level directories — `UI/` (the Qt/PySide6 GUI), `client/` (client-side Environment orchestration), `cluster/` (SLURM/SSH connection machinery), `modules/` (Plugin Modules), and the loader bases — that together form the desktop application driving the **Headless Core** over a socket. Not "un-migrated cruft": it is the application layer. Code here that is Qt-free *and* used headless is a migration candidate (ADR 0026); Qt code and client-only logic stay.
_Avoid_: frontend-only (it holds orchestration, not just widgets), legacy.

### Environment
The central compute object. Owns all loaded datasets, models, the fingerprint-keyed cache, and the TaskManager. In remote mode, the Environment runs on the cluster node; the local machine runs only the GUI. Decomposed by composition into `env.cache` / `models` / `datasets` / `data` / `remote` / `persistence` plus an injected PredictionSource (ADR 0020).

### Object Catalog
The single-owner registry of an Environment's loaded scientific objects — datasets, models, predictions (`client/object_catalog.py`: `register` / `prune` / `get` / `snapshot` / `load`). Replaces the former raw `env.info['objects']` dict that was read and mutated across many call sites; the Environment now exposes it as `self.objects` while the on-disk `info.json` shape is unchanged. Deleting an object prunes it from the catalog so it does not reappear after save/load.
_Avoid_: objects dict, info['objects']

### TaskManager
Runs heavy computations as async tasks (or threaded for IO-bound work). Emits `TASK_PROGRESS`, `TASK_DONE`, `TASK_FAILED` events. All prediction generation goes through TaskManager.

### Dataset / DatasetLoader
A loaded molecular dataset (positions, energies, forces, atomic numbers). Identified by a fingerprint (MD5 of contents). Supports stride-based sampling via `slice_num` at load time.

### SubDataset
A view over a parent Dataset restricted to a subset of indices. Created when a user zooms a plot region. Used to transfer a tractable subset of a large remote dataset to the local Loupe viewer.

### FrozenSubDataset
A permanently snapshotted SubDataset, treated as an independent Dataset.

### Model / ModelLoader
A loaded MLFF model (sGDML, MACE, Nequip, SchNet, SpookyNet). Identified by fingerprint. Used to generate predictions against Datasets.

### Prediction
A (Dataset, Model) pair used for error analysis. Either generated at runtime by a real Model, or loaded from a pre-computed file via "Load Prediction" (which creates a GhostModel placeholder). The atomic unit of comparison in all error plots. In remote mode, a Prediction can be loaded from a cluster-side file via "Load Remote Prediction"; the server creates the GhostModel and transfers prediction arrays to the client via the Prediction-Only Array Channel.

### Loupe
The 3D molecular viewer. Uses Vispy for GPU-accelerated rendering with color-based atom picking. Runs locally (requires local GPU/OpenGL context). In remote mode, Loupe only operates on transferred subsets, not the full remote dataset. Loupe is the Qt/Vispy **Renderer Client**. As code it spans layers by design: the Qt shell (`UI/loupe/`), pluggable features (`modules/loupe/`), the renderer-neutral scene/pipeline core shared with the web renderer (`ffast/visualization/`, part of the **Headless Core**), and the Vispy adapter (`ffast/renderers/vispy/`).

### Renderer Client
The local process that draws a Render Scene and owns pointer interaction, camera, picking, export, playback controls, and window layout. It holds no scientific state: it consumes Scene Snapshots and Scene Patches and emits View Commands. Loupe (Qt/Vispy) and the future web viewer are Renderer Clients.
_Avoid_: viewer, frontend (when precision matters)

### Render Path
The legacy drawing route being retired: per-feature viewer modules computed geometry and drew Vispy objects directly from global UI settings. It is replaced by the Visualization Pipeline → Render Scene → renderer-adapter route. Retiring the Render Path is distinct from retiring the Renderer Client, which survives and hosts the adapter.

### Visualization State
A renderer-neutral description of the molecular scene being inspected, including selected datasets, predictions, structures, subsets, visual encodings, and interaction state. Owned by the Environment/server; consumed by renderer backends such as local Loupe or a future web viewer.

### Visualization View
One open inspection surface for a molecular scene. Has its own Visualization State, identified independently from other open views so multiple viewers can inspect and tune the same data differently.

### Render Scene
A backend-ready representation of a Visualization View: geometry buffers, colors, sizes, line segments, labels, camera parameters, and other drawing primitives derived from Visualization State.

### Visualization Pipeline
A composable sequence of renderer-neutral stages that derives a Render Scene from Visualization State, datasets, predictions, and cached analysis data.

### Pipeline Stage
One renderer-neutral step in a Visualization Pipeline. Declares its required inputs, produced outputs, and tunable parameters; receives parameters from configuration or Visualization State rather than reading global UI settings directly.

### Pipeline Parameter
A tunable value consumed by a Pipeline Stage. Resolved from stage defaults, app configuration, saved session settings, and per-view overrides, with the most specific layer winning.

### Parameter Scope
The lifetime and ownership boundary for a Pipeline Parameter: session-wide, per Visualization View, or per Visualization View per Dataset.

### View Command
A renderer-issued request to change a Visualization View, such as changing frame, selecting atoms, setting a parameter, toggling a layer, or updating camera state. The server applies View Commands to Visualization State and remains the source of truth.

### Camera State
The restorable viewpoint for a Visualization View, including orientation, center, zoom or distance, field of view, and projection mode. Owned by Visualization State while renderer backends may update it periodically during interaction.

### Transient Selection
Client-local hover or temporary highlight state used for immediate interaction feedback; it is not persisted, synchronized, or added to scientific undo history.

### Scientific Selection
A named, server-owned atom or structure selection used by analysis, filtering, alignment, or Geometry Editing; it is persisted, synchronized, isolated by purpose, and undoable.

### Selection Scope
The rule defining where a Scientific Selection applies: current structure, stable topology by atom index, semantic element selection, per-structure index sets, or a registered future selection expression.

### Scene Snapshot
A complete, versioned Render Scene sent when opening, reconnecting, changing a view's primary data, or recovering from synchronization loss.

### Scene Patch
A versioned update containing only changed Render Scene components, such as camera, selection, parameters, colors, bonds, or force-vector buffers.

### Stage Catalog
The server-side registry of available Pipeline Stages and their dependencies. A Visualization View enables features and parameters while the server resolves a valid execution order.

### View Transformation
A reversible, presentation-only transformation such as alignment, centering, filtering, or rotation that changes the viewed geometry without changing scientific source data.

### Geometry Edit
An explicit change to scientific geometry, such as moving atoms or changing a unit cell, represented separately from the source Dataset until saved.

### Edit Target
The structures affected by a Geometry Edit: the current structure by default, an explicit structure selection when requested, or the full Dataset only through an explicit batch operation.

### Edit Log
The ordered Geometry Edit operations held by a Visualization View and deterministically applied to source geometry, preserving undo, redo, and provenance until materialized as a Derived Dataset.

### Derived Dataset
A new Dataset materialized from a source Dataset by explicitly saving Geometry Edits or transformed geometry; the source Dataset remains unchanged.

### Dataset Provenance
The reproducibility record attached to a Derived Dataset: source fingerprint, applied Geometry Edits, explicitly materialized View Transformations, creation metadata, and relevant Pipeline Stage versions and resolved parameters.

### Materialization
The explicit operation that creates a Derived Dataset from a Visualization View, choosing whether to apply Geometry Edits only or also bake selected View Transformations into scientific coordinates.

### Renderer Capability
A visualization feature supported by a renderer backend. All backends must implement the baseline capabilities and target full advanced-feature parity; capability negotiation exposes temporary implementation gaps.

### Render Primitive
A renderer-neutral drawing component in a Render Scene, such as atom instances, line segments, vector arrows, text labels, unit-cell edges, selection overlays, or camera parameters.

### Metric
A named, deterministic scientific calculation that declares its inputs and returns numeric values without deciding how those values are visualized.

### Metric ID
A stable namespaced identifier for a Metric, such as `ffast.force_error` or `my_lab.charge_deviation`, used by configuration, caches, Sessions, dependencies, and clients independently from its display label.

### Metric Presentation
Configuration that exposes a Metric through uses such as atom coloring, filtering, plots, labels, tables, or export, including normalization, ranges, units, and color mapping. For atom coloring specifically, the server delivers per-atom **values plus a colormap descriptor** (colormap, range, label, unit) and each renderer maps values to colors; the server does not bake RGBA (see [ADR 0016](docs/adr/0016-atom-color-values-plus-descriptor-client-maps.md)).

### Metric Shape
A named, extensible description of what each Metric output value corresponds to, with registered validation and adapters for visualization and analysis uses. Built-in shapes include scalar, per-structure, per-atom, per-structure-per-atom, and vector-per-structure-per-atom.

### Plugin Module
A Python file under `modules/` that is auto-discovered at startup by `loadModules` (recursive glob of `modules/**/*.py`), ordered against its declared `DEPENDENCIES` by topological sort, and registered by calling its optional `loadData` / exposing `CLIENT_FEATURES` / `DATASET_FEATURES`. Plugin Modules are organised by type into `modules/loupe/` (Loupe features), `modules/loaders/` (concrete model/dataset loaders — distinct from the abstract loader *bases* in `modelLoaders/` / `datasetLoaders/`), and `modules/tabs/` (analysis tabs). Adding a Plugin Module to the right sub-directory is all that is needed to register a feature (ADR 0025). Distinct from a **Metric Module** (loaded by configuration, not the startup glob).
_Avoid_: grab-bag, mixin, extension (reserve for Qt sense).

### Metric Module
A Python package module or direct Python file loaded by configuration to register one or more Metrics, Metric Shapes, or related adapters with the server. Distinct from a **Plugin Module** (the `modules/` startup-glob mechanism).

### Trusted Metric Module
A Metric Module explicitly allow-listed in configuration and loaded with its resolved path plus version or content hash recorded for audit and reproducibility.

### Metric Input
A symbolic numeric dependency declared by a Metric, such as reference forces, predicted energies, element numbers, structure coordinates, or another Metric; resolved by the server before metric execution.

### Dataset Field
A named numeric value carried by a loaded file beyond the core arrays (energy, forces, positions, elements, masses): a **Frame Field** (per-frame scalar, from an extxyz `atoms.info` key) or an **Atom Field** (per-atom scalar, from an `atoms.arrays` key). A Dataset Field is sourced from either the reference dataset or a loaded **Prediction** file, and is referenced as a **Metric Input** by key-in-the-ref: `reference.info.<key>` / `reference.atoms.<key>` or `prediction.info.<key>` / `prediction.atoms.<key>`. A field is either fully valid across the whole dataset and the correct shape, or it resolves to `None` (the same graceful path as an unsourced optional input); partial presence, non-numeric, and wrong-width keys all resolve `None`. Per-atom *vector* fields are out of scope.
_Avoid_: extra key, custom column, property, metadata

### Metric Graph
The acyclic dependency graph formed by Metric Inputs that reference other Metrics; used by the server to resolve execution order, cache intermediate values, and reject missing dependencies or cycles.

### Metric Failure
A structured, isolated failure of one Metric calculation that makes the metric and its dependents unavailable without stopping unrelated Metrics, Pipeline Stages, Visualization Views, or the server.

### Historical Metric Result
A read-only cached Metric result whose recorded implementation is unavailable. It may still drive presentation and export with its original identity and parameters visible, but cannot be recomputed or applied to new data.

### Metric Result
The self-describing output envelope used for live, cached, transferred, persisted, and historical Metric values, containing numeric data plus Metric identity, implementation hash, Metric Shape, Metric Unit, input fingerprints, Compute Parameters, shape, dtype, and checksum.

### Metric Test
A lightweight synthetic example or invariant registered by a Metric Module and runnable headlessly to validate calculation output, Metric Shape, dtype, Metric Unit, and compatibility without project data.

### Metric Worker
An isolated process that executes Metric calculations outside the long-lived server process, enabling cancellation, timeouts, crash containment, resource limits, and protection from plugin global-state mutation.

### Metric Worker Pool
A reusable set of Metric Workers recycled after module reload, crash, resource-limit violation, or a configurable task count to balance process isolation with import and startup cost. The whole Metric registry is pickled once to each worker, so **every registered metric function must be picklable** — module-level functions only, no lambdas or local closures. A single unpicklable metric (e.g. a compiled Transform Metric with a lambda body) fails `pickle.dumps` and breaks *all* metrics on the pool path; compiled transforms satisfy this via module-level bodies + the `_TransformFn` class. The in-process executor does not pickle, so tests that use it can mask this.

### Worker Buffer
A read-only shared-memory or memory-mapped numeric input passed to a Metric Worker by descriptor; small inputs may be serialized normally, and worker outputs become new immutable Metric Results.

### Metric Resource Hint
An optional estimate of CPU, memory, runtime, and GPU preference declared by a Metric for scheduling and concurrency; server policy defines separate hard safety limits.

### Result Buffer
The immutable numeric payload of a Metric Result or Render Primitive transferred separately from Scene Snapshots and Scene Patches and referenced by content-addressed ID so renderer clients can cache and reuse it.

### Buffer Codec
A transport encoding negotiated between server and renderer client for Result Buffer transfer. `none` is mandatory and `zstd` is preferred when supported; the uncompressed canonical buffer determines identity and checksum.

### Buffer Transfer
The ordered, progress-reporting delivery of a Result Buffer in bounded chunks, with chunk validation and final verification against the buffer's SHA-256 identity.

### Visualization Configuration
A partial, declarative override of FFAST's built-in visualization defaults. An empty configuration preserves the complete default experience; named entries add, disable, or tune features, Metrics, Metric Presentations, and Pipeline Parameters.

### Configuration Merge
The deterministic overlay rule for Visualization Configuration: maps merge recursively, scalar values replace inherited values, lists replace unless explicitly defined as named collections, `null` restores the inherited default, and `enabled: false` disables a named feature.

### Configuration Layers
The ordered sources merged into effective visualization behavior: built-in defaults, user configuration, project configuration, saved Session configuration, and per-view overrides, from least to most specific.

### Configuration Failure
A validation failure found while loading or activating configuration. Invalid structure, unknown Metric Modules, Metric dependency cycles, incompatible Metric Shapes, and unknown keys reject activation; an invalid optional Metric Presentation disables only that presentation and is reported.

### Configuration Reload
The atomic activation of a fully validated candidate Visualization Configuration while the server is running. Declarative settings may reload live; executable Metric Module code reloads only through an explicit command.

### Presentation Fallback
The replacement behavior used when a Metric Presentation becomes unavailable. FFAST provides use-specific defaults, while Visualization Configuration may override them.

### Parameter Schema
The extensible type, default, validation, and control metadata declared for a Metric or Pipeline Parameter so renderer backends can generate equivalent controls and the server can validate View Commands.

### Compute Parameter
A declared parameter that changes Metric numeric output or Pipeline Stage data and therefore participates in computation identity and cache invalidation.

### Presentation Parameter
A declared parameter that changes only how existing values are displayed, such as colormap, range, label formatting, or plot style, without changing scientific computation identity.

### Metric Unit
The declared physical dimension and canonical unit of Metric output. Computation and caching use canonical values; Metric Presentation and export convert them to configured display units.

### Unit Registry
The server-side registry of physical dimensions, canonical units, and validated conversions used consistently by Metric Presentations and exports. Trusted Metric Modules may extend it.

### Presentation Preset
A named reusable set of Metric Presentation settings. A metric use may reference a preset and override selected fields through normal Configuration Merge rules.

### Session
A saved/loaded snapshot of an Environment: all datasets, models, cache, and metadata serialized to disk (`info.json` + `cache/*.npz`). Auto-snapshots server-side enable reconnect recovery. This is the canonical bare "Session"; the live runtime that produces it is a **Server Session**, the client's transport to it is a **Server Connection**, and the coordinates to reconnect to it are a **Session Record**.
_Avoid_: session (for the connection or the runtime — those are Server Connection / Server Session)

### Session Record
The reconnect coordinates for a running server, persisted client-side in `~/.ffast/sessions.json` (job id, ports, profile, last snapshot) so the reconnect UI can re-attach to a **Server Session** whose **Server Connection** was dropped. Not server state and not a **Session** snapshot — just enough to rebuild a connection. Managed by the record helpers in `cluster/connection.py`.
_Avoid_: session file (that is the snapshot), session state

### Cache Key
The structured identity of one entry in the Environment's fingerprint-keyed cache: a leading **identity token** (a DataType key, or a **Metric ID** — which may itself contain `__`, e.g. a **Transform Metric** like `ffast.force_mae__kde__p<hash>`), plus a **Model** fingerprint and a **Dataset** fingerprint, each either a real fingerprint or the sentinel `nil` when the quantity is model- or dataset-independent. The canonical form is a structured value (`ffast/cache/keys.py`), hashable and used directly as the in-memory cache dict key; it serializes to the flat string `identity__model__dataset` only at the disk (`cache/*.npz`, `info.json`) and RPC boundaries. Deserialization is **right-anchored** — the last two `__`-segments are model then dataset (fingerprints never contain `__`), everything before is the opaque identity — and validates each fingerprint slot (`nil` or fingerprint-shaped) so a malformed key fails fast instead of mis-decoding. A Metric's **Compute Parameters** are folded into the identity token, never a separate field.
_Avoid_: cache string, key string, cache id

### Prediction Array Key
The sibling identity used only inside the **Prediction-Only Array Channel** transfer payload: `pred__<dtype>__<model_fp>`, with **no dataset slot**. A distinct namespace from a **Cache Key** — it labels arrays in a one-shot wire message, not entries in the long-lived cache, and its consumers match by prefix rather than decoding model/dataset. Owns its own `format`/`parse` (`ffast/cache/keys.py`) so the transport path carries no hand-rolled `split("__")` either.
_Avoid_: prediction cache key, pred string

### Headless Mode
Running the Environment without a GUI (`startHeadlessEnvironment()`). The basis for the remote server process (`ffast-server`).

### Panel Kind
A reusable archetype for one cell of an **Analysis Tab** (timeline, density, scatter, table). It declares which widget it builds (plot vs table), how many **Metric** inputs it takes, the **Metric Shapes** those inputs must satisfy, and how they map to axes (plots) or cells (table). A Panel Kind assembles and draws only; it performs no scientific computation and owns no reductions. Plot Panel Kinds also declare how a viewport range maps back to dataset indices for subbing.
_Avoid_: plot kind, chart type, widget type (when precision matters)

### Panel
A configured **Panel Kind** instance: a chosen Panel Kind bound to specific **Metric IDs** and parameter values, rendered as one grid cell. A Panel composes the per-axis **Metric Presentations** of the Metrics it binds; it never computes. Its interactive controls are generated from the **Parameter Schemas** of the Metrics it binds.

### Analysis Tab
A named grid of **Panels** sharing one data selector (the models/datasets the Panels draw against). Refines the legacy `ContentTab`.
_Avoid_: plot tab, page

### Series
One drawn trace within a **Panel** — the **Metric Presentation** of the Panel's Metric(s) for a single (**Model**, **Dataset**) pair. A Panel draws one Series per pair its **Analysis Tab** selector covers; the legend labels Series, **Subbing** turns a Series' viewport range into a **SubDataset**, and a Series carries the stable identity `(dataset, model)` used to match it across redraws.
_Avoid_: trace, curve, line, plot item

### Transform Metric
A **Metric** whose **Metric Input** is another Metric, applying a reduction or transform (KDE, smoothing, downsampling, per-structure reduction) through the **Metric Graph** and emitting a derived **Metric Result**. Its transform settings are **Compute Parameters**, so changing one recomputes the derived Metric rather than mutating a Panel client-side. This is why **Panels never reduce**: every axis is a Metric Result array.

---

## Remote Connection Domain

### ffast-server
The server-side process running on a cluster compute node. Wraps the Environment in headless mode, exposes it via WebSocket. Started by the client via SSH after SLURM allocates a node. Lives until job walltime regardless of client connection state.

### Server Session
The live, server-scoped object on a running `ffast-server` that represents the single controlling session. It owns the open **Visualization Views** and the server→client outbound queue, dispatches the controlling client's **Control messages** to the **Environment** through a built-once event→handler table, and **replays** current state — dataset/model metadata, the **Metric Catalog**, and open **Visualization View** snapshots — to a client on connect or reconnect. It holds a reference to the Environment rather than owning it; its persisted form is a **Session** snapshot. One exists per server process: many client connections may attach, but only the **CONTROLLING** **Client Role** drives it while the rest stay read-only. Distinct from a **Server Connection** (the *client's* transport handle to a server) and a **Local Server Session** (a desktop launch mode).
_Avoid_: dispatcher, router, event handler, ServerKernel, connection object, bare "Session" (that is the snapshot), RemoteSession (renamed to Server Connection)

### ffast-client (remote mode)
The local Qt GUI connecting to a remote `ffast-server` via WebSocket over SSH port-forward. Runs UIHandler locally; delegates all compute to the remote Environment.

### Local Server Session
A desktop session in which the renderer client automatically starts and connects to a managed local `ffast-server` process through the same protocol used for remote servers. Desktop always uses a Local Server Session — `env.remote`'s **Server Connection** (`localServerConnection`) is always set after launch. The "New 3D View" button is disabled until the connection is established. There is no separate embedded (in-process) rendering path.

### Client Recovery Window
The configurable period during which a server remains alive after an unexpected client disconnect so views, unsaved Edit Logs, and in-progress work can be restored; normal client shutdown snapshots and stops a managed local server.

### Client Role
The authority granted to a renderer client connected to a server session. The protocol supports multiple clients, while the initial implementation permits one controlling client and rejects or limits additional clients to read-only access.

### Session Token
A random secret used by a renderer client to claim or reclaim a server Session and its controlling Client Role. The server stores only its hash; local launch passes it directly and remote access combines it with the SSH tunnel.

### Protocol Version
The negotiated compatibility version for renderer/server communication, including supported message features, Renderer Capabilities, and Buffer Codecs. Incompatible major versions are rejected; compatible versions negotiate optional features.

### Protocol Schema
The formal typed definition of View Commands, Scene Snapshots, Scene Patches, Metric metadata, Result Buffer messages, and structured errors. Python models are the source of truth and publish JSON Schema while msgpack remains the wire encoding.

### ClusterBackend
An abstract interface for job schedulers. `SlurmBackend` is the first implementation. Lives in the `cluster/` package. All methods are async. Responsible for: submitting jobs (`JobSpec` → job ID), polling status, retrieving allocated node address, cancelling jobs. Raises `ClusterError` on failure.

### ClusterError
Exception raised by `ClusterBackend` methods on failure. Carries a human-readable message and raw `stderr` from the scheduler command.

### JobSpec
Scheduler-agnostic resource request: `cores` (int), `memory_mb` (int, megabytes), `time_limit` (str, `HH:MM:SS`), `gpu_count` (int), `partition` (str), `command` (str — the shell command the job script executes, e.g. activating the venv and starting `ffast-server`). Translated to a scheduler-specific submission script by the active `ClusterBackend`. `SlurmBackend` always submits with `--nodes=1`.

### JobStatus
Enum returned by `ClusterBackend.poll_status`: `PENDING`, `RUNNING`, `FAILED`, `COMPLETED`. Terminal state resolved via `sacct` when the job is no longer in `squeue`.

### Server Connection
The client's live transport handle to a running `ffast-server`: SSH tunnel, WebSocket connection, associated SLURM job ID, request/reply correlator, and array-transfer cache (`ServerConnection` in `cluster/connection.py`). It carries *transport*, not scientific state — the opposite end of the wire from a **Server Session**, which owns the runtime state. Distinct from a **Session** (the on-disk snapshot) and a **Session Record** (reconnect coordinates).
_Avoid_: RemoteSession (former name), remote session, session (bare — that is the snapshot)

### Connection Manager
The client-side owner of the **Server Connection** lifecycle (`ConnectionManager` in `client/connection_manager.py`, reached as `env.remote`). It establishes/tears down the connection (remote cluster or managed local server), holds the active `serverConnection`/`localServerConnection`, and mirrors the server-owned **Metric Catalog**. Renamed from `RemoteSessionManager`.
_Avoid_: RemoteSessionManager (former name), session manager

### RPC Channel
WebSocket connection (over SSH port-forward) between `ffast-client` and `ffast-server`. Uses `msgpack` for serialization. Carries two message classes:
- **Control messages** — messages with a structured, defined payload (user actions, request/reply metadata, metric-result metadata). Eligible to be typed against the **Protocol Schema**.
- **Array messages** — raw numpy arrays for plot data and SubDataset/Prediction transfer (binary, msgpack-numpy). Not schema-typed.

A message carrying *both* structured metadata and a numpy array (e.g. `METRIC_RESULT`) is classified by its **metadata**: the metadata envelope is a typed Control message, and the array rides inside it as an Array payload. Pure multi-array transfers (`SUBDATASET_ARRAYS`, `PREDICTION_ARRAYS`) stay untyped Array messages.

### In-Process Event
A string-keyed notification on the **EventClass** bus, dispatched *within a single process* (UI or server) between objects that inherit `EventClass` / `EventChildClass`: a subscriber registers a handler with `eventSubscribe`, a publisher calls `eventPush`, and each subscriber drains its own queue on the next `eventHandle` cycle. Distinct from a **Broadcast Event**, which crosses the server→client RPC boundary; an In-Process Event never leaves its process. A subscriber that is a Qt widget (an `EventChildClass`) holds, and is held by, the bus strongly, so its lifetime is bound to an explicit `deleteEvents` call.
_Avoid_: signal, slot (those are Qt's own mechanism), message, broadcast (that is the cross-process one).

### Broadcast Event
A fire-and-forget server→client notification that announces that something happened, carrying an identifier rather than a structured payload (e.g. a task finished, a dataset loaded, a cache entry changed). Distinct from a typed Control message that carries a defined multi-field payload; clients react to a Broadcast Event by looking the identifier up. Distinct also from an **In-Process Event** (the intra-process EventClass bus).

### Prediction-Only Array Channel
A dedicated RPC sub-protocol for transferring prediction arrays (energy, forces) from server to client *without* re-sending geometry or element arrays. Used when a Remote Prediction is loaded after a dataset's geometry arrays are already on the client. Client sends `REQUEST_PREDICTION_ARRAYS(dataset_fp, model_fp)`; server replies with `PREDICTION_ARRAYS` carrying only the cached `energy` / `forces` entries for that model+dataset pair.

### Auto-Snapshot
Periodic server-side Session save triggered automatically while `ffast-server` is running. On reconnect, client restores the last auto-snapshot. Structured to support future live state sync (C) where client mirrors full server state continuously.

### Session Record
A persisted, client-side descriptor of a launched `ffast-server` job (ADR 0024), stored under `~/.ffast/sessions.json`: `job_id`, `profile_name`, `node`, `remote_port`, `token`, and `timestamp`. Written (`save_session_record`) when a cluster session is established so the reconnect UI can rediscover still-running jobs across client restarts. Purged (`delete_session_record`) on user-initiated disconnect and when a job is found definitively dead, so a stale record never re-triggers the reconnect dialog. Distinct from an **Auto-Snapshot**: a Session Record holds only the coordinates needed to re-open the SSH tunnel and reclaim the **Server Session**, not the server's scientific state.
_Avoid_: session file, job record, sessions.json (when precision matters)

### Dataset Length Probe
A server round-trip that counts the frames in a cluster-side dataset file before loading (`PROBE_DATASET_LENGTH` → `DATASET_LENGTH_RESPONSE`; `probe_dataset_length(path)` returns `{n, error}`). Lets the client present a **Remote Stride Dialog** against the true frame count so the user picks a `slice_num` over a known size; returns `n=None` with an `error` string when the server-side probe fails.

### Remote Stride Dialog
The client dialog shown before loading a remote dataset: given the **Dataset Length Probe** result, the user chooses a stride (`slice_num`) so only a tractable subset is sampled at load time. The remote analogue of opening a local dataset with a stride.

### Remote Task ID
A task identifier namespaced `remote_<n>` for work running on `ffast-server`. Kept in a separate namespace from locally-issued task IDs so the server's replayed `TASK_PROGRESS` / `TASK_DONE` **Broadcast Events** never collide with local tasks in the client's TaskManager.

---

## Relationships

- An **Environment** owns zero or more **Visualization Views**
- Local and remote renderer clients access the **Environment** through `ffast-server`; desktop mode uses a **Local Server Session**
- Connected renderer clients have explicit **Client Roles**
- **Visualization Views** belong to the **Server Session** and may have a current client owner; client window placement and panel geometry remain client-local
- A **Visualization View** has exactly one **Visualization State**
- A **Visualization State** references **Datasets**, **Predictions**, and **SubDatasets** by fingerprint rather than owning their arrays
- A **Visualization Pipeline** derives a **Render Scene** from a **Visualization State**
- A **Render Scene** contains a standard set of **Render Primitives** translated into backend-native objects by each renderer
- A **Visualization Pipeline** is composed of one or more **Pipeline Stages**
- The **Stage Catalog** defines valid stage dependencies; a **Visualization View** chooses enabled features, not arbitrary stage order
- A **Pipeline Stage** consumes **Pipeline Parameters** resolved from defaults, app configuration, saved session settings, and per-view overrides
- A **Parameter Schema** lets each renderer backend generate controls for declared Metric and Pipeline Parameters
- **Compute Parameters** invalidate affected computation caches; **Presentation Parameters** reuse existing numeric results
- A **Metric** computes scientific numeric values, while **Metric Presentation** controls how those values are exposed and visualized
- A **Metric ID** identifies a Metric across code, configuration, cache, Session persistence, and renderer protocols
- A **Metric** declares a **Metric Unit** so display-unit changes do not alter scientific computation identity
- The **Unit Registry** performs conversions between canonical Metric values and configured display or export units
- Standard **Metric Presentations** generate equivalent controls and views in each renderer backend without metric-specific UI code
- **Presentation Presets** reuse display behavior across Metrics while allowing metric-specific overrides and one cycle-checked `extends` parent
- A **Metric** declares a **Metric Shape** so consumers can validate and adapt its values without knowing the metric implementation
- A **Metric** receives declared **Metric Inputs** and resolved parameters, not Environment, Dataset, Model, renderer, or UI objects
- Every Metric calculation produces a **Metric Result**
- Metrics may depend on other Metrics through an acyclic **Metric Graph**
- A **Metric Failure** propagates only to dependent Metrics and is reported without substituting synthetic values
- Built-in and external **Metric Modules** register Metrics by package import path or direct Python file path
- Direct Metric Module paths resolve relative to the configuration file that declares them
- The server loads only explicitly configured **Trusted Metric Modules**
- Trust approval is granted against the resolved top-level project configuration and the complete Metric Module list and hashes, including modules declared by included files
- A changed Metric Module content hash revokes prior execution approval until explicitly re-approved; existing cached results retain their original implementation identities
- Duplicate **Metric IDs** are rejected; intentional replacement requires explicit configuration plus compatible Metric Shape and Metric Unit dimension
- An intentional Metric override resolves references to the original **Metric ID** globally while provenance records both requested and resolved identities
- A cached **Metric** result is identified by its name, implementation version or content hash, resolved parameters, input fingerprints, and dependent Metric identities
- A Session with an unavailable Metric implementation still loads unaffected state; the missing Metric remains unavailable until an explicit compatible migration or recomputation is accepted
- A **Historical Metric Result** remains usable read-only when its original Metric implementation is unavailable
- A **Historical Metric Result** is verified against recorded shape, dtype, Metric Shape, Metric Unit, input fingerprints, Compute Parameters, and content checksum before use
- A **Pipeline Parameter** has a **Parameter Scope** so dataset-specific viewer settings do not leak into other views or datasets
- **Visualization Configuration** overlays built-in defaults, so users maintain only intentional differences
- **Configuration Merge** resolves nested overrides without ambiguous list merging
- **Configuration Layers** use the same merge rules at every level
- Project configuration is selected explicitly when provided, otherwise discovered as the nearest `ffast.toml` while searching upward from the opened dataset or Session directory, loading at most one project file
- Configuration files may declare ordered includes resolved relative to the including file; included files merge first, the including file overrides them, and cycles or duplicate include paths are errors
- A **Configuration Failure** occurs during configuration activation and is distinct from a runtime **Metric Failure**
- A **Configuration Reload** validates all changes before atomically replacing the active configuration
- If reload removes an active Metric Presentation, the Visualization View switches to its **Presentation Fallback**, reports the change, and does not retain stale metric values
- A **View Transformation** affects presentation only, while a **Geometry Edit** represents a change to scientific data
- A **Geometry Edit** applies to an explicit **Edit Target**
- A **Visualization View** owns an **Edit Log** for its unsaved Geometry Edits
- Unsaved **Edit Logs** are isolated between **Visualization Views**; opening edited state in another view explicitly copies the log
- **Materialization** creates a **Derived Dataset** rather than modifying the source **Dataset**
- **Materialization** applies Geometry Edits and only those View Transformations explicitly selected by the user
- A **Derived Dataset** carries **Dataset Provenance**
- A renderer backend sends **View Commands** to update a **Visualization View**
- The server applies **View Commands** to **Visualization State** and publishes the resulting **Render Scene**
- State-changing **View Commands** carry an expected view version and are rejected when stale; high-frequency Camera State updates may use explicit last-write-wins semantics
- Scientific undo and redo are server-owned View Commands over the view's command and Edit Log history; clients do not keep independent scientific undo stacks
- Undo history includes scientific edits, selections, filters, transformations, metric and feature parameters, and view dataset/model changes; it excludes camera motion, frame playback, hover, client layout, and cache activity by default
- **Transient Selections** remain client-local, while named **Scientific Selections** belong to Visualization State
- A **Scientific Selection** declares a validated **Selection Scope** instead of assuming atom indices are stable across structures
- Metrics and Pipeline Stages may reference named **Scientific Selections** supplied by configuration or interaction
- Project-configured Scientific Selections are immutable templates; editing creates a per-view copy unless an explicit project-update action is invoked
- Project configuration updates preview the minimal patch and require confirmation before atomic writing; unrelated formatting and comments are preserved when supported, otherwise a separate override file is written
- A **Visualization State** includes **Camera State**
- A renderer backend initializes or recovers from a **Scene Snapshot** and applies subsequent **Scene Patches** in version order
- **Scene Snapshots** and **Scene Patches** reference large immutable **Result Buffers** instead of embedding them repeatedly
- Renderer clients manage their own Result Buffer memory budgets, may evict unused buffers, and request missing buffers again by ID
- A **Result Buffer** ID is a SHA-256 content hash of its dtype, shape, and canonical array bytes; semantic meaning remains in Metric Result or Render Scene metadata
- Server and renderer negotiate a **Buffer Codec** without changing Result Buffer identity
- Large Result Buffers use chunked **Buffer Transfer**
- After reconnect, a renderer resumes Buffer Transfer by reporting the verified chunks it already holds; incompatible transfer parameters restart only that buffer
- Sessions persist scientific **Metric Results**; transfer-ready Result Buffers and codec/chunk artifacts are reconstructable caches rather than duplicate durable scientific data
- A renderer backend advertises its **Renderer Capabilities** when connecting
- Every renderer backend supports the required baseline and is expected to converge on all advanced **Renderer Capabilities**
- **Loupe** is one renderer backend for a **Visualization View**
- An **Analysis Tab** holds one or more **Panels** in a grid and shares one data selector across them
- A **Panel** is one **Panel Kind** bound to **Metric IDs** and parameter values; it draws **Metric Results** and never computes
- A **Panel Kind** declares its widget (plot or table), its **Metric Shape** input requirements, its axis/cell mapping, and (for plots) a `sub_indices` viewport→index map used for subbing
- A **Panel**'s interactive controls are generated from the **Parameter Schemas** of the Metrics it binds; a control change is a debounced **Compute Parameter** update routed through `SET_PARAMETER`
- A reduction (KDE, smoothing, downsampling, per-structure reduction) is a **Transform Metric** compiled from a Panel's `{metric, transform, params}` into a deterministically named concrete **Metric** with a static **Metric Graph** edge to its source
- A subbing **Panel** binds both its drawn (reduced) Metric and the indexed source Metric the **Transform Metric** declares as input; the indexed source drives `sub_indices`, while downsampling stays visual-only
- 2D **Panels** are computed and laid out client-side; the server stays unaware of **Panel Kinds** and layout, exposing only **Metric Results** — in contrast to the server-owned **Visualization State** that drives the 3D scene

---

## Key Constraints

- Python 3.9–3.11 only (`requires-python = ">=3.9,<3.12"`); PySide6 pinned `>=6.8,<6.9`
- Embedded desktop Environment access is transitional; the target architecture uses the same server protocol for local and remote renderer clients
- Managed local servers stop after normal client shutdown, but unexpected disconnects retain state for the **Client Recovery Window** before snapshotting and stopping
- A **Session Token** is required to claim the controlling Client Role; SSH secures remote transport but is not the sole session-ownership mechanism
- Legacy saved Sessions are not required to load after migration to the new server-owned visualization architecture
- Legacy third-party `loadUI` and `loadLoupe` extension compatibility is not required; new extensions use Metric, Pipeline Stage, configuration, and renderer contracts
- Loupe renders locally — requires local OpenGL context; server-based visualization means server-owned Visualization State, not server-side rendering
- Camera interaction stays responsive locally; renderer backends throttle or debounce Camera State updates sent to the server
- Renderer backends request a Scene Snapshot when a Scene Patch version is missing or out of order
- Capability negotiation is a rollout and compatibility mechanism, not a reason for permanent feature differences between renderer backends
- Pipeline Stages are deterministic and pure: identical declared inputs produce identical outputs, and stages do not mutate Datasets, Visualization State, or other stage outputs
- Unknown configuration keys are errors rather than silently ignored
- FFAST provides headless commands to validate configuration, list and inspect registered Metrics, and run a Metric against selected data without launching a renderer client
- Trusted Metric Modules may register headless **Metric Tests** that run twice in fresh contexts to compare shape, dtype, values, and checksum, using declared floating-point tolerances
- The first migration milestone proves Pydantic schemas, TOML partial configuration, built-in and external Metrics, config-generated atom coloring, cache identity, Qt/Vispy consumption, and headless metric tooling before introducing server-owned views, worker pools, web rendering, or Result Buffer transport
- Milestone-one Metrics may execute in-process only behind a MetricExecutor interface that can later be replaced by the Metric Worker Pool without changing Metric contracts or consumers
- Migration preserves the working Qt/Vispy application while introducing schemas and metrics, extracting pure pipeline stages, adding server-owned views and scene synchronization, adapting Loupe into a renderer backend, and only then making local server mode the default
- Metrics execute through a recyclable **Metric Worker Pool** rather than directly inside the long-lived server process
- Large Metric Inputs reach workers as read-only **Worker Buffers** to avoid process-to-process array copies
- Metric cancellation is cooperative first; after a configurable grace period, FFAST terminates and replaces the worker and discards incomplete outputs
- Metrics may declare **Metric Resource Hints**; exceeding an estimate is reported, while exceeding a server hard limit terminates the worker as an isolated Metric Failure
- GPU preference is none, optional, or required; CPU and GPU implementations of the same Metric agree within declared numerical tolerance
- Persisting transformed geometry is an explicit Environment operation that creates a separate Dataset; it is not a Pipeline Stage side effect
- Large datasets stay on cluster; only SubDataset subsets transfer to local Loupe
- Stride sampling (`slice_num`) set at load time; for remote datasets a **Dataset Length Probe** feeds a **Remote Stride Dialog** so the stride is chosen against the true frame count. Random sampling and lazy loading are future work
- SSH tunnel authenticates remote transport; a separate per-session token authorizes session ownership and reconnect control
- A **Session Record** persisted client-side lets the reconnect UI rediscover a running `ffast-server` job after a client restart; it is purged on user-initiated disconnect or confirmed job death so a dead job never re-triggers the reconnect dialog
- Server-issued **Remote Task IDs** are namespaced `remote_<n>` to avoid collision with local task IDs when `TASK_PROGRESS` / `TASK_DONE` events are replayed to the client

---

## Flagged Ambiguities

- "server-based visualization" means server-owned **Visualization State** consumed by swappable renderer backends; it does not mean rendering pixels on the cluster/server.

---

## Development Collaboration

This architecture is also a learning project. By default, implementation work
should be broken into understandable steps for the project owner to code
manually, with the assistant explaining design choices, identifying relevant
existing code, proposing focused exercises, reviewing changes, and helping
diagnose failures.

The assistant should not assume that a requested architectural change should be
implemented automatically. Direct implementation is appropriate when the
project owner explicitly delegates it, asks for a concrete fix, or requests
automation for a particular step.

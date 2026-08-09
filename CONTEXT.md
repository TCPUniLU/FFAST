# Vocabulary

The words this codebase uses, and what they mean here specifically. If a term
below has a looser everyday meaning, the definition here wins.

This is the glossary, not the architecture. For how the pieces fit together see
[docs/architecture.md](docs/architecture.md); for why each seam is where it is,
[docs/adr/](docs/adr/README.md).

Everything in the first two sections exists in the code today. Terms that were
coined for designs we have not built are quarantined at the bottom, under
[Vocabulary for things that do not exist yet](#vocabulary-for-things-that-do-not-exist-yet).

## The two halves

### Headless core

The `ffast/` package: engine, server, protocol, renderer-neutral visualization,
CLI. It imports no Qt and no PySide6, so it runs on a compute node with no
display. This is the importable, distributable thing (`import ffast.*`, and the
`ffast` / `ffast-server` / `ffast-cli` entry points).

Membership has a test, not an opinion: a module belongs in the core if the
`ffast-server` or `ffast-cli` entry points reach it. The msgpack codec is core;
`UI/panels.py` is not. Code moves *into* the core, never out (ADR 0026).

Don't call it: the library, the backend, `src`.

### Desktop client

The flat top-level directories: `UI/` (Qt widgets), `client/` (what's left of
client-side orchestration), `cluster/` (SLURM and SSH), `modules/` (plugin
modules). Together they are the desktop application that drives the headless core
over a socket. This is the application layer, not leftover rubbish, although some
of it is now genuinely leftover (see the architecture doc).

Don't call it: the frontend (it holds orchestration, not just widgets), legacy.

## Compute

### Environment

The central compute object: every loaded dataset and model, the fingerprint-keyed
cache, the task manager. In remote mode it runs on the cluster node and the local
machine has none of it. It used to be one class doing everything; it is now
composed of `env.cache`, `env.models`, `env.datasets`, `env.data`, `env.remote`,
`env.persistence` plus an injected prediction source (ADR 0020).

### Object catalog

The one registry of an Environment's loaded scientific objects, at
`env.objects`. It replaced a raw dict that a dozen call sites read and mutated,
which is why deleted objects used to come back after a save and reload.

### TaskManager

Runs heavy work as async tasks, or threads for IO. Emits `TASK_PROGRESS`,
`TASK_DONE`, `TASK_FAILED`. Every prediction goes through it.

### Dataset

A loaded set of configurations: positions, energies, forces, atomic numbers.
Identified by a fingerprint, which is an MD5 of the contents. Can be strided at
load time (`slice_num`).

**SubDataset** is a view onto a parent restricted to some indices, created when
you select a region of a plot. It is also how a tractable slice of a large remote
dataset reaches the local viewer. **FrozenSubDataset** is one that has been
snapshotted and now stands on its own.

### Model, GhostModel

A model is a trained MLFF (sGDML, MACE, NequIP, SchNet, SpookyNet), identified by
fingerprint. The real one lives **only inside `ffast-server`** and never in the
client process (ADR 0030).

What the client holds instead is a **GhostModel**: a fingerprint and a display
name, no weights. Built from `REMOTE_MODEL_META`. A ghost backed by a real
server-side model can still get fresh predictions, because the server reruns
inference; a ghost backed by a prediction file cannot compute anything new.

### Prediction

A (dataset, model) pair. The unit of comparison in every error plot. Inference is
always server-side. Whether a prediction was computed by a live model or read
from a file is a server concern the client never sees.

## Metrics

### Metric

A named, deterministic calculation that declares its inputs and returns numbers.
It decides nothing about how those numbers are drawn. It receives the arrays it
asked for, never the Environment, a Dataset, a Model or anything from the UI.

### Metric ID

A stable namespaced name: `ffast.force_error`, `my_lab.charge_deviation`. Used by
config, caches, sessions, dependencies and clients, separately from the human
label.

### Metric shape

What each output value corresponds to: scalar, per-structure, per-atom,
per-structure-per-atom, vector-per-structure-per-atom. Consumers validate against
the shape without knowing the implementation.

### Metric input

A symbolic dependency: reference forces, predicted energies, elements,
coordinates, a dataset field, or another metric. The server resolves it before
the metric runs.

### Metric graph

The acyclic graph formed when metric inputs refer to other metrics. The server
uses it for execution order and rejects cycles and missing dependencies.

### Metric execution context

`ffast/metrics/execution.py`. Resolves a metric's inputs, dependencies and
compute parameters exactly once into an ordered, picklable execution plan. It is
the only place optional-input and missing-dependency semantics are defined, which
is the point: they used to be defined in three places that disagreed (ADR 0035,
0046).

The executor seam is one method, `run(id, source, parameters)`. Behind it sit two
adapters that differ only in transport: an in-process one, and the worker pool.
The executor is injected when the Environment is constructed, so the server gets
the pool and the desktop gets the in-process one.

### Metric worker pool

Metrics run in separate processes, not in the long-lived server. That buys
cancellation, timeouts and crash containment.

The catch worth knowing before you write a metric: the whole registry is pickled
once to each worker, so **every registered metric must be picklable**. Module
level only, no lambdas, no closures. One unpicklable metric breaks every metric
on the pool path, not just itself. The in-process executor does not pickle, so
tests using it hide this.

### Transform metric

A metric whose input is another metric, applying a reduction: KDE, smoothing,
downsampling, per-structure reduction. Its settings are compute parameters, so
changing one recomputes rather than mutating something client-side. This is why
panels never reduce: every axis is already a result array.

### Expression metric

A metric compiled from a `[[metrics.expr]]` config entry, evaluating element-wise
arithmetic over named variables. No Python. Unlike a transform metric it
preserves shape, so all its non-scalar variables must share one shape while
scalars and literals broadcast. Non-finite output raises rather than quietly
emitting `nan` (ADR 0042).

### Dataset field

A numeric value carried by a file beyond the core arrays: a per-frame scalar from
an extxyz `info` key, or a per-atom scalar from an `arrays` key. Referenced by
key in the ref: `reference.info.<key>`, `prediction.atoms.<key>`. A field is
either fully valid across the dataset and correctly shaped, or it resolves to
`None`; partial presence, non-numeric values and wrong widths all resolve `None`
rather than half working (ADR 0023). Per-atom vector fields are out of scope.

Don't call it: an extra key, a custom column, a property, metadata.

### Compute parameter, presentation parameter

A **compute parameter** changes the numbers, so it participates in cache
identity. A **presentation parameter** changes only how existing numbers look
(colormap, range, label format) and reuses the cached result. Confusing the two
is how you get stale plots or pointless recomputation.

### Metric module

A Python file or importable module named in configuration that registers
metrics. Distinct from a **plugin module**, which is found by the startup glob
over `modules/`.

## Plots

### Panel kind

The archetype for one cell of a tab: timeline, density, scatter, table,
overlay_timeline, grouped_density, grouped_table. It declares what widget it
builds, how many metric inputs it takes, which shapes those must satisfy, and how
they map to axes or cells. It assembles and draws. It computes nothing.

### Panel

A configured panel kind: bound to specific metric IDs and parameter values,
drawn as one grid cell. Its interactive controls are generated from the parameter
schemas of the metrics it binds.

### Analysis tab

A named grid of panels sharing one data selector.

### Series

One drawn trace in a panel: the presentation of the panel's metrics for a single
(model, dataset) pair. A panel draws one per pair the selector covers. Identity is
`(dataset, model)`, which is what lets a redraw match series up instead of
clearing and rebuilding (ADR 0022).

Don't call it: a trace, a curve, a line, a plot item.

### Panel display override

A user's client-local cosmetic edit to a panel: axis label text and size, legend
text, size and position. Keyed by content (tab name, panel kind, bound metric
IDs) so it survives rebuilds and TOML reordering, and it never touches
computation identity. The app rewrites this file silently whenever you drag a
legend, which is exactly why it is kept apart from configuration, which people
author by hand (ADR 0029). **Colorbar display override** is the same idea applied
to the 3D view's colourbar.

## The 3D view

### Loupe

The 3D molecular viewer. The name predates everything else and has stuck.

As code it deliberately spans layers: the Qt shell in `UI/loupe/`, pluggable
features in `modules/loupe/`, the renderer-neutral scene code shared with the web
client in `ffast/visualization/`, and the vispy adapter in
`ffast/renderers/vispy/`.

### Renderer client

The process that draws a render scene and owns pointer interaction, camera,
picking, export, playback and window layout. It holds no scientific state: it
consumes snapshots and patches, and emits view commands. Both the browser client
and Loupe are renderer clients.

### Visualization state, visualization view

**Visualization state** is the renderer-neutral description of what is being
inspected: datasets, predictions, subsets, visual encodings, interaction state.
The server owns it.

A **visualization view** is one open inspection surface with its own state, so
several viewers can look at the same data differently.

### Render scene

The backend-ready form of a view: geometry buffers, colours, sizes, line
segments, labels, camera. Baked colours are resolved server-side and are binding;
a renderer draws the RGBA it is given and never substitutes its own default
(ADR 0052).

The deliberate exception is value-driven colouring: under `color_by` the server
ships scalar values plus a colormap name, and each renderer maps them itself
(ADR 0016). Default presentation values live in
`ffast/visualization/presentation.py`.

### Visualization pipeline, pipeline stage

The derivation of a render scene from visualization state. A **stage** is one
renderer-neutral step declaring its inputs, outputs and tunable parameters.

Composition is ordinary call order in the scene builder, not a generic executor.
There was an executor; ADR 0049 removed it after measuring that the live graph
contained exactly one stage-to-stage dependency, and that the stages which
actually needed orchestration could not use it anyway because they fetch data
conditionally mid-derivation.

The **stage catalog** survives as a declarative registry: it describes stages and
does not run them. It is what `ffast-cli stages list/inspect/test` reads.

### View command

A renderer's request to change a view: change frame, select atoms, set a
parameter, toggle a layer, move the camera. The server applies it and stays the
source of truth.

### Scene snapshot, scene patch

A **snapshot** is a complete versioned scene, sent on open, reconnect, a change
of primary data, or recovery from lost sync. A **patch** carries only what
changed. A renderer that sees a patch version out of order asks for a snapshot.

### Transient vs scientific selection

**Transient** is client-local hover and highlight, never persisted or
synchronised. **Scientific** is a named server-owned selection used by analysis,
filtering and alignment; it is persisted and synchronised.

## Sessions and connections

Four things here have similar names and mean different things. This was a real
source of confusion, so the names were changed deliberately.

### Session

A saved snapshot of an Environment: datasets, models, cache, metadata, written as
`info.json` plus `cache/*.npz`. This is the bare word "session".

### Server session

The live object on a running `ffast-server` representing the controlling
session. It owns the open views and the outbound queue, dispatches control
messages through a handler table built once, and replays current state to a
client on connect or reconnect. One per server process; several clients may
attach but only the controlling one drives it.

### Server connection

The *client's* transport handle to a server: SSH tunnel, WebSocket, SLURM job ID,
request/reply correlator, array cache. Carries transport, not science. The
opposite end of the wire from a server session. Formerly called RemoteSession.

### Session record

Reconnect coordinates, kept client-side in `~/.ffast/sessions.json`: job ID,
ports, profile, last snapshot. Just enough to rebuild a connection to a server
session whose connection dropped. Purged on deliberate disconnect and when a job
is confirmed dead, so a stale record cannot keep offering a reconnect dialog
forever (ADR 0024).

### Connection manager

Client-side owner of the connection lifecycle, at `env.remote`. Its
`active_session()` is the single "is a session live?" check that everything else
resolves through instead of re-deriving it.

### Loading coordinator

The single client-side owner of turning "load this dataset/model/prediction" into
"and do it here": an in-process task, or a dispatch to the server. It owns the
routing decision, the load implementations, the probe round-trips and the one
chokepoint where ghost models are registered.

It is Qt-free on purpose, which is what makes it migratable into the core and
testable with a fake session. The desktop keeps the dialogs and hands it
callbacks (ADR 0034).

### RPC channel

The WebSocket, msgpack-encoded, carrying two classes of message. **Control
messages** have a structured typed payload. **Array messages** are raw numpy for
plot data and subset transfer. A message with both (`METRIC_RESULT`) is
classified by its metadata: typed envelope, array riding inside.

Every event name is a constant in `ffast/protocol/control.py`. No send or handle
site spells one as a string literal (ADR 0033).

### In-process event vs broadcast event

An **in-process event** is on the EventClass bus inside one process. Subscribers
are held strongly, so a widget's lifetime is bound to an explicit `deleteEvents`
call; this is deliberate, not a leak.

A **broadcast event** crosses the server-to-client boundary and carries an
identifier rather than a payload: something happened, go look it up.

### Prediction-only array channel

A sub-protocol for sending prediction arrays without re-sending geometry the
client already has. Client asks with `REQUEST_PREDICTION_ARRAYS(dataset_fp,
model_fp)`, server replies with just the energies and forces (ADR 0004).

### Cache key

The identity of one cache entry: an identity token (a metric ID, which may itself
contain `__`), plus a model fingerprint and a dataset fingerprint, either of
which may be the sentinel `nil` when the quantity does not depend on it.

It is a structured value (`ffast/cache/keys.py`) and only flattens to the string
`identity__model__dataset` at the disk and RPC boundaries. Parsing is
right-anchored: the last two `__`-segments are model then dataset, everything
before is opaque identity. Each fingerprint slot is validated, so a malformed key
fails immediately instead of decoding into the wrong entry, which is what used to
happen. Compute parameters fold into the identity token, never a separate field.

### Cluster terms

**ClusterBackend** is the scheduler interface; `SlurmBackend` is the only
implementation. **JobSpec** is a scheduler-agnostic resource request translated
into a submission script. **JobStatus** is `PENDING`, `RUNNING`, `FAILED`,
`COMPLETED`, resolved via `sacct` once a job leaves `squeue`. **Dataset length
probe** counts frames in a cluster-side file before loading so the stride dialog
can offer a stride against the true count. **Remote task IDs** are namespaced
`remote_<n>` so replayed progress events cannot collide with local task IDs.

## Rules that hold everywhere

- Python 3.11 exactly. PySide6 pinned `>=6.8,<6.9`.
- `ffast/` imports no Qt and no OpenGL. There is a test.
- Metrics are pure, deterministic and picklable, and never see the Environment,
  a Dataset or a Model.
- Metrics raise on non-finite results rather than returning silent `inf`/`nan`.
- Unknown configuration keys are errors, not ignored.
- Pipeline stages are deterministic and mutate nothing.
- Large datasets stay on the cluster. Only selected subsets travel.
- The server owns visualization state. It does not render pixels.
- A session token, not the SSH tunnel, is what grants the controlling role.
- Old saved sessions are not migrated (ADR 0008).

## Two words that trip people up

**"Server-based visualization"** means the server owns the visualization state
and swappable renderers consume it. It does not mean rendering pixels on the
cluster.

**"Frame" and "structure"** are the same thing: one configuration in a
trajectory. Metric shapes say *per-structure*; file and trajectory terms say
*frame*. One axis, two words, no distinction intended.

## Vocabulary for things that do not exist yet

These terms come from design work that has not been built. They appear in older
ADRs and in planning documents. None of them has code behind it today, and this
section exists so nobody goes looking for it.

- **Geometry edit, edit target, edit log, derived dataset, dataset provenance,
  materialization** — editing atoms in the viewer and saving the result as a new
  dataset with a reproducibility record.
- **Trusted metric module** — allow-listing metric modules by content hash, for
  audit and reproducibility. Today, configured modules are simply loaded.
- **Presentation preset** — named reusable presentation settings with an
  `extends` parent.
- **Unit registry** — server-side registry of dimensions and validated
  conversions. Metrics do declare units today; there is no conversion registry.
- **Selection scope** — the rule saying where a scientific selection applies when
  atom indices are not stable across structures.
- **Historical metric result** — a cached result whose implementation is gone,
  usable read-only.
- **Renderer capability negotiation** — advertised feature sets per renderer.
- **Metric resource hint, worker buffer** — scheduling hints, and shared-memory
  inputs to workers.
- **Result buffer, buffer codec, buffer transfer** — content-addressed array
  transport with chunking and zstd. These are different from the others: the code
  exists and is tested, but it is deliberately not wired up, because the
  WebSocket already compresses and the lazy-proxy design rarely ships bulk arrays
  (ADR 0031).

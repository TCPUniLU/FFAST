# Server-Owned Visualization Architecture

The whole-system conceptual map. Domain **terms** are defined once in
[CONTEXT.md](../CONTEXT.md) and **decision rationale** in [docs/adr/](adr/).
This document explains how the pieces fit together.

## Goal

Separate scientific computation and visualization *state* from the technology that
draws pixels. The server (`ffast-server`) owns datasets, predictions, metrics, views,
edits, configuration, and renderer-neutral scenes. Renderer clients (Qt/Vispy today, a
web client alongside it) consume those scenes and send commands back. This keeps the
server renderer-free (no server-side OpenGL), modular, configuration-tunable, and able
to drive equivalent Qt and web backends.

## System Boundary

```text
                         ffast-server
  +----------------------------------------------------------+
  | Environment                                              |
  |  datasets | predictions | cache | tasks | sessions       |
  |  Metric registry -> metric graph -> metric results       |
  |  Visualization views                                     |
  |   state -> pipeline stages -> renderer-neutral scene     |
  |  Commands | snapshots/patches | result-buffer service    |
  +------------------------------+---------------------------+
                                 |
                    WebSocket + msgpack protocol   (ADR 0001)
                                 |
              +------------------+------------------+
        Qt/Vispy renderer                       Web renderer
        native controls, native drawing         browser controls, WebGL
```

The server does not render pixels. A renderer client owns native drawing, pointer
feedback, and window layout — never scientific state. Desktop mode starts a managed
local `ffast-server` and speaks the same protocol as remote cluster mode.

## Visualization Model

**Views and state.** Each inspection surface is a **Visualization View** (stable
`view_id`) owned by the server session; it holds its own dataset/prediction/structure
references, enabled features + resolved parameters, named selections, transformations,
geometry edit log, camera, and undo/redo history. Window position, hover, and transient
highlights stay client-local. (See *Visualization View* / *Visualization State* in
CONTEXT.)

**Commands and concurrency.** Clients mutate a view through typed **View Commands**
(`SET_FRAME`, `SET_PARAMETER`, `TOGGLE_FEATURE`, `SET_CAMERA`, `SET_SELECTION`,
`APPLY_GEOMETRY_EDIT`, `UNDO`/`REDO`, `MATERIALIZE_DERIVED_DATASET`). Each
state-changing command carries the view version it is based on; the server rejects
stale commands and returns current state. Camera is immediate locally with throttled,
last-write-wins updates. Scientific undo covers edits/selections/filters/
transformations/metric-parameters/dataset changes — not camera, playback, hover, or layout.

**Pipeline and scene.** A server-side **Stage Catalog** registers renderer-neutral
pipeline stages, each declaring a stable id, inputs, outputs, dependencies, and a
parameter schema (marking each parameter *compute* or *presentation*). Views enable
features; the server resolves a valid dependency order. Stages are pure —
`output = stage(inputs, resolved parameters)` — and never mutate datasets, view state,
or other outputs. The pipeline emits a **Render Scene** of standard primitives (atom
instances, line segments, vector arrows, text labels, unit-cell edges, selection
overlays, camera); adapters translate these into Vispy or WebGL. Backend-specific
primitives are exceptional and capability-gated.

**Scene synchronization.** A versioned **Scene Snapshot** opens or recovers a view;
typed **Scene Patches** update changed components; a missing/out-of-order version
triggers a snapshot request. Large immutable arrays travel as content-addressed
**Result Buffers** referenced by id, not re-embedded. Buffer identity is:

```text
SHA-256(dtype + shape + canonical uncompressed bytes)
```

Transfer supports mandatory uncompressed mode, negotiated `zstd`, bounded chunks with
progress, final verification, and reconnect resume. Compression and chunking never
change buffer identity.

**Renderer capabilities.** Clients advertise protocol version, message features, buffer
codecs, and renderer capabilities. All renderers implement a required baseline and are
expected to converge on parity; negotiation exists for rollout, not permanent divergence.

## Metrics

A **Metric** is a deterministic Python calculation that declares symbolic scientific
inputs and returns numeric values. It never decides display and never receives
`Environment`/`Dataset`/`Model`/UI/renderer objects. (Full contract: ADR 0011 + the
*Metric* terms in CONTEXT.) Illustrative registration:

```python
@metric(
    id="my_lab.force_error",
    inputs={"reference": "reference.forces", "predicted": "prediction.forces"},
    shape="per_structure_per_atom",
    unit="force",
    parameters={"norm": {"type": "choice", "choices": ["l1", "l2"],
                         "default": "l2", "role": "compute"}},
)
def force_error(reference, predicted, *, norm):
    diff = predicted - reference
    return np.mean(np.abs(diff), axis=-1) if norm == "l1" else np.linalg.norm(diff, axis=-1)
```

**Inputs and graph.** Inputs are symbolic (reference/predicted energies or forces,
positions, elements, masses, cells, named selections, or another metric result).
Metric-to-metric dependencies form an acyclic graph; the server resolves order, caches
intermediates, and rejects missing deps or cycles.

**Shapes** (`scalar`, `per_structure`, `per_atom`, `per_structure_per_atom`,
`vector_per_structure_per_atom`, …) are registered contracts, not a closed enum — new
shapes bring their own validation and presentation adapters.

**Results and cache identity.** Every calculation returns a self-describing **Metric
Result** (requested + resolved id, implementation hash, shape, canonical unit, input
fingerprints, compute parameters, dtype/shape, checksum, and payload-or-buffer-ref).
Cache identity includes everything that can change numeric output:

```text
metric ID + implementation hash + compute parameters
          + dataset/model/input fingerprints + dependent metric identities
```

Presentation parameters (colormap, label format) and unit-display preferences never
invalidate a cached result — a central **Unit Registry** converts canonical values for
display/export.

**Presentation, trust, failure.** Configuration exposes metrics through standard uses
(atom coloring, filtering, plots, labels, tables, vector styling, export); clients
generate controls from the parameter/presentation schema, so normal metrics need no
bespoke UI. Built-in and external modules share one registry; only content-hash-approved
modules execute, and duplicate ids fail unless replacement is explicit and
shape/unit-compatible. A runtime metric failure disables only that metric and its
dependents (structured error, never synthetic zeros/NaNs); a saved result whose
implementation is gone stays visible as a read-only historical result until migrated.

**Execution target.** Metrics run in a recyclable worker-process pool: large inputs via
read-only shared memory; cooperative-then-forced cancellation; workers recycled on
module reload, crash, resource-limit, or task-count; authors declare CPU/memory/runtime/
GPU hints while server policy owns hard limits. A single in-process executor behind the
same `MetricExecutor` interface is used for the CLI and tests. (Note: every registered
metric must be picklable — see *Metric Worker Pool* in CONTEXT.)

**Headless CLI:** `ffast config validate`, `ffast metrics list|inspect|test|run`.

## Configuration

TOML is the canonical human-authored format; JSON Schema from Pydantic models is the
validation/tooling contract (ADR 0006/0007). An empty `ffast.toml` preserves the full
default experience — configuration is a partial override.

**Layers** (least → most specific): built-in defaults < user config < project config <
saved Session config < per-view overrides. Pipeline parameters additionally scope as
session-wide / per-view / per-view-per-dataset.

**Merge rules:** maps merge recursively; scalars replace; lists replace unless declared
as named (id-keyed) collections; `null` restores the inherited value; `enabled = false`
disables a named feature; unknown keys are errors.

**Discovery + includes:** an explicit project config wins; otherwise FFAST searches
upward from the dataset/Session directory for the nearest `ffast.toml` (at most one).
Files may include ordered relative files (listed-order merge, includer wins; cycles and
duplicate paths are errors).

**Validation + reload:** Pydantic validates a full candidate before activation; invalid
structure/unknown-keys/unknown-modules/cycles/incompatible-shapes reject it, while an
invalid *optional* presentation disables only itself. Declarative config supports atomic
live reload; if a reload drops an active presentation the view falls back and discards
stale values. Project-configured selections are immutable templates — editing forks a
per-view copy; explicit project updates preview a minimal TOML patch and write atomically.

## Geometry Editing and Derived Data

View transformations (alignment, centering, filtering, rotation) are reversible
presentation changes. **Geometry edits** change scientific geometry but stay an ordered
per-view **edit log** until saved (targeting the current structure, explicit structures,
or the whole dataset only via an explicit batch op). Materialization creates a new
**Derived Dataset** — it never mutates the source — recording provenance (source
fingerprint, edit operations, baked transformations, stage versions + parameters,
metadata). Camera orientation is never silently baked into coordinates.

## Session and Client Lifecycle

Views belong to the server **Session**, not a desktop window. The initial protocol
allows one **controlling** client; others are read-only. A random **Session Token**
(server stores only its hash) authorizes control and recovery; SSH still secures remote
transport but is not the sole ownership mechanism (ADR 0012). Managed local servers
snapshot and stop on clean shutdown, stay alive for a recovery window after an
unexpected disconnect, restore views + unsaved edit logs on reconnect, and stop when the
window expires (ADR 0024). Legacy saved Sessions and `loadUI`/`loadLoupe` extensions are
not migration requirements (ADR 0008/0009).

## Deferred / not yet wired

Some capabilities are built but parked by choice — the code exists and is tested but
is not on the live path:

- **Server-side live inference.** Server + client plumbing exists
  (`server.py` `_send_prediction_arrays`, `client/environment.py` `requestModelLoad`),
  but the desktop model-load handler still runs prediction in-process. Parked pending a
  latency/correctness measurement; activating it points the handler at
  `env.requestModelLoad` when a session exists, keeping in-process as the fallback.
- **`zstd` transport + Result Buffers.** The buffer service exists; `zstd` is a
  negotiated codec but never activated (local transfer is same-machine, remote is
  SSH-compressed).
- **`run_batch` / `compute_plan`.** Implemented and tested but off the hot path; the
  live path computes metrics individually.

## Package Layout

The realized ownership (see also ADR 0026 for the headless-core boundary):

```text
ffast/                 headless core — no Qt (import ffast.*)
  metrics/             registry, shapes, units, graph, executor, cache, builtin/
  visualization/       view state + scene models, stage pipeline, buffers
  protocol/            Pydantic message/event schemas
  config/              TOML/Pydantic models, discovery/merge/validation
  renderers/vispy/     Qt/Vispy adapter        renderers/web/  browser adapter
  session/ cli/ cache/
UI/ client/ cluster/ modules/   Desktop Client — Qt app, orchestration, plugins
```

The msgpack codec stays in `cluster/rpc` (transport, on the server import closure);
typed message *schemas* live in `ffast/protocol/`.

## Related Decisions

- [ADR 0001](adr/0001-remote-rpc-protocol.md): WebSocket + msgpack transport
- [ADR 0006](adr/0006-pydantic-protocol-and-configuration-schemas.md): Pydantic schemas
- [ADR 0007](adr/0007-toml-for-user-and-project-configuration.md): TOML config
- [ADR 0008](adr/0008-no-legacy-session-migration.md): no legacy Session migration
- [ADR 0009](adr/0009-replace-legacy-module-extension-hooks.md): new extension contracts
- [ADR 0010](adr/0010-server-owned-visualization-state.md): server-owned visualization state
- [ADR 0011](adr/0011-pure-metrics-with-configuration-driven-presentation.md): pure metrics + config-driven presentation
- [ADR 0014](adr/0014-vispy-scene-adapter-replaces-loupe-render-path.md): VispySceneAdapter replaces the legacy render path
- [ADR 0015](adr/0015-client-side-ray-cast-picking.md): client-side ray-cast picking
- [ADR 0016](adr/0016-atom-color-values-plus-descriptor-client-maps.md): atom colors as values + descriptor
- [ADR 0026](adr/0026-headless-core-migration-direction.md): headless-core packaging boundary

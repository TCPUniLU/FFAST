# ClientFeature and DatasetFeature descriptors replace loadLoupe and loadUI hooks

**Status:** accepted

The `loadLoupe(UIHandler, loupe)` and `loadUI(UIHandler, env)` hook functions in
`modules/*.py` — dispatched dynamically by `utils.py:loadModules` via
`hasattr` inspection — are replaced by two declarative descriptors registered
at module import time:

- **`ClientFeature`** — a Loupe-panel feature combining an optional server-side
  stage ID with an optional client-side Qt widget factory and selection tool
  class. Targets frame/atom-level interaction (e.g. bond picking, force
  vectors, Kabsch alignment controls).

- **`DatasetFeature`** — a main-panel feature combining one or more metric IDs
  with a Qt widget factory. Targets dataset-level aggregate display (e.g. force
  error scatter plots, atomic error histograms).

Each module exposes a `CLIENT_FEATURES: list[ClientFeature]` and/or
`DATASET_FEATURES: list[DatasetFeature]` at module scope.
`utils.py:loadModules` collects these lists instead of calling
`loadLoupe`/`loadUI`.

```python
# client-side only — ffast/ has no knowledge of these classes
@dataclass
class ClientFeature:
    stage_id: str | None          # links to a stage in ffast/visualization/stages/
    widget_factory: Callable | None
    tool_class: type[AtomSelectionBase] | None

@dataclass
class DatasetFeature:
    metric_ids: list[str]         # links to metrics in ffast/metrics/
    widget_factory: Callable
```

Both descriptors live in client code only. The `stage_id` and `metric_ids`
strings are the sole coupling to `ffast/` — the server has no knowledge of
client descriptors or tool classes.

## Why the hook dies

The `loadLoupe(UIHandler, loupe)` hook receives live objects and builds Qt
widgets imperatively, allowing modules to reach into `loupe` (and transitively
`env`) at construction time. This creates implicit coupling to mutable UI
state that is incompatible with the server-owned architecture: the client
should be a thin renderer that subscribes to `SceneSnapshot`/`ScenePatch` and
emits `ViewCommand`, not a collection of modules that read and write shared UI
objects during startup.

The `loadUI(UIHandler, env)` hook has the same problem at the dataset panel
level — modules call into `env` directly rather than consuming server-computed
metric results.

## Considered alternatives

**Keep the hooks, change what they receive** — pass a restricted `loupe`
object exposing only `send_command(ViewCommand)` and `get_parameter(key)`
instead of the full loupe. Rejected: this is a migration shim, not a
destination. The hook dispatch pattern (`hasattr` inspection at runtime)
makes the dependency graph invisible and keeps modules coupled to a startup
ordering that the new architecture does not need.

**Embed client tool hints in the server stage schema** — stage schemas declare
`client_tool_id` strings; the client maps those IDs to tool classes. Rejected:
Qt concerns (multiselect count, rectangle select, picker mode) do not belong in
server schemas. The server is renderer-neutral; the client decides how to
collect a selection.

**Hardcode complex features, schema-generate simple ones** — retire hooks for
the four parameter-only modules (Kabsch, Indices, Axes, UnitCell) and keep the
ten complex ones as explicit Qt code with no extension mechanism. Rejected:
the descriptor pattern costs little and makes all features uniform; it also
gives future external contributors a defined registration point without
reopening the hook.

## Deletion gate

`loadLoupe` and `loadUI` may be deleted from all modules — and the
`hasattr`-based dispatch removed from `utils.py:loadModules` — only when:

1. Every module that had `loadLoupe` exposes `CLIENT_FEATURES`.
2. Every module that had `loadUI` exposes `DATASET_FEATURES`.
3. The Loupe client instantiates features from the registry, not from
   `registerLoupeModule` callbacks.

Client-local modules (`loupeCamera`, `loupeExport`, `loupeAxes`) have no
stage ID; their `ClientFeature` entries carry only a `widget_factory`.

## Consequences

- `utils.py:loadModules` loses `hasattr(mod, "loadLoupe")` /
  `hasattr(mod, "loadUI")` branches and gains feature-list collection.
- `UI/loupe/window.py:registerLoupeModule` and the `VisualElement` draw loop are
  removed after all modules migrate (see [ADR 0014](0014-vispy-scene-adapter-replaces-loupe-render-path.md)
  deletion gate — already satisfied by existing stage code).
- Module migration is incremental: old hook and new descriptor coexist per
  module during transition; `loadModules` honours both until the old branch
  is removed.
- `ffast/` is unchanged. The descriptors are client-only types; `stage_id`
  and `metric_ids` strings are the stable contract surface.
